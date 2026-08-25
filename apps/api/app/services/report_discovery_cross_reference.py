from __future__ import annotations

"""日报 ↔ 发现基金的跨报告披露（读取侧）。

## 为什么需要它

两条链路共用同一套方向打分，方向层结论不会矛盾；但**基金层**会出现"看起来打架"：
发现基金今天对板块 A 推荐买入新基金 Y（Y 质量更好、且 discovery 只排除已持有的代码），
日报同一天可以因浮亏封档、载体质量、基金证据把同板块持仓 X 按在观察甚至减仓。两个结论
都对——一个说"这个方向值得进、用这只更好的载体"，一个说"你手里这只的证据不支持加"——
但没有任何一句话向用户解释这不是自相矛盾。

## 它做什么、不做什么

只做**披露**：把当日发现基金报告里与持仓同板块的买入类推荐，结构化地带进日报的
facts（LLM 可见）与对应持仓卡片的 validation_notes（用户可见）。

不做仲裁：两侧职责边界不变（发现管"能不能进"与首仓，日报管已持仓的加/减/退），
本模块不修改任何动作、比例或金额。**只读当日报告**——昨天的推荐基于昨天的方向状态，
引用它只会制造新的矛盾；用户今天没跑发现基金就没有披露，如实返回 unavailable。

匹配按**同族板块**（`sector_labels.SECTOR_FAMILY_PARENT`）而非仅精确同名：持仓
「医疗」对推荐「CXO」这类细分↔父行业组合正是最容易"看起来打架"的地方——同一只
医药基金会因季报穿透结果在两个键之间漂移，而两个键的行情代理不同、方向状态分开
计算，同一天完全可以一边判退出、一边判可布局（2026-08 线上实测：011373/医疗被
建议大幅减仓的同日，000960/CXO 被建议分批买入，且此前双侧披露都因精确匹配而漏掉）。
"""

from datetime import datetime, timezone
import logging
from typing import Any

from app.models import Holding
from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    classify_action_bucket,
)
from app.services.sector_labels import normalize_sector_label, sector_family_root
from app.services.trading_session import CN_TZ

logger = logging.getLogger(__name__)

#: v2：匹配范围从精确同名扩展到同族板块；行内新增 `sector_label`（推荐自己的标签）
#: 与 `relation`（same_sector / same_family）。
DISCOVERY_CROSS_REFERENCE_SCHEMA_VERSION = "discovery_cross_reference.v2"

_UNAVAILABLE: dict[str, Any] = {
    "schema_version": DISCOVERY_CROSS_REFERENCE_SCHEMA_VERSION,
    "available": False,
    "reason": None,
    "report_id": None,
    "report_created_at": None,
    "buy_recommendations_by_sector": {},
}


def build_discovery_cross_reference(
    holdings: list[Holding],
    *,
    decision_at: datetime | None = None,
) -> dict[str, Any]:
    """当日发现基金报告里与持仓同板块的买入类推荐；没有就如实 unavailable。

    "当日"按 `decision_at`（决策时钟）的 Asia/Shanghai 自然日判定，而不是墙钟——日报
    读取的一切证据都以决策时钟为时点基准。整段 best-effort：任何一步失败只丢这层披露，
    绝不阻塞日报。
    """
    held_labels_by_root: dict[str, set[str]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if label:
            held_labels_by_root.setdefault(sector_family_root(label), set()).add(label)
    if not held_labels_by_root:
        return {**_UNAVAILABLE, "reason": "no_held_sectors"}
    try:
        from app.database import get_discovery_report, list_discovery_reports

        summaries = list_discovery_reports(limit=1)
        if not summaries:
            return {**_UNAVAILABLE, "reason": "no_discovery_reports"}
        summary = summaries[0]
        created_at = str(summary.get("created_at") or "")
        report_date = _cn_date(created_at)
        as_of = (decision_at or datetime.now(timezone.utc)).astimezone(CN_TZ).date()
        if report_date is None or report_date != as_of.isoformat():
            return {
                **_UNAVAILABLE,
                "reason": "no_same_day_discovery_report",
                "report_created_at": created_at or None,
            }
        report_id = str(summary.get("id") or "")
        payload = get_discovery_report(report_id) if report_id else None
        if not isinstance(payload, dict):
            return {**_UNAVAILABLE, "reason": "discovery_report_unreadable"}

        # 键是**持仓**的板块标签（卡片按持仓行反查），值里的 `sector_label` 是推荐
        # 自己的标签：族级命中时两者不同（持仓「医疗」← 推荐「CXO」），披露文案要能
        # 说清这是同族的另一个口径，而不是同一行方向状态。
        by_sector: dict[str, list[dict[str, Any]]] = {}
        for raw in payload.get("recommendations") or []:
            if not isinstance(raw, dict):
                continue
            label = normalize_sector_label(str(raw.get("sector_name") or ""))
            if not label:
                continue
            held_in_family = held_labels_by_root.get(sector_family_root(label))
            if not held_in_family:
                continue
            action = str(raw.get("action") or "").strip()
            # 只披露买入类动作：观察/等待类候选不构成"两侧看起来矛盾"的素材。
            if classify_action_bucket(action) < ACTION_BUCKET_ADD:
                continue
            row = {
                "fund_code": str(raw.get("fund_code") or "").strip(),
                "fund_name": str(raw.get("fund_name") or "").strip(),
                "action": action,
                "entry_path": str(raw.get("entry_path") or "").strip() or None,
                "sector_label": label,
            }
            for held_label in held_in_family:
                by_sector.setdefault(held_label, []).append(
                    {
                        **row,
                        "relation": (
                            "same_sector" if held_label == label else "same_family"
                        ),
                    }
                )
        # 精确同板块的行排在同族口径之前：披露文案只点名第一行，同名命中信息量更高。
        for rows in by_sector.values():
            rows.sort(key=lambda item: item.get("relation") != "same_sector")
        return {
            "schema_version": DISCOVERY_CROSS_REFERENCE_SCHEMA_VERSION,
            "available": True,
            "reason": None,
            "report_id": report_id,
            "report_created_at": created_at,
            "buy_recommendations_by_sector": by_sector,
            # 给 LLM 的职责边界：披露不是仲裁，更不是把新基金买入建议搬进日报。
            "instruction": (
                "以下是用户今日发现基金报告中与持仓同板块或同族细分板块的买入类推荐，"
                "仅作跨报告一致性披露：发现基金负责新资金能不能进、买哪只；日报只对"
                "已持仓给出加/减/退。同名板块两侧共用同一套方向打分；同族的细分与父"
                "行业（如 CXO 与医疗）是两条分开计算的方向状态，结论可以不同，不得互相"
                "改写对方的结论，也不得在日报中把这些新基金写成可执行建议。"
            ),
        }
    except Exception:  # noqa: BLE001 — 披露层，绝不阻塞日报
        logger.warning("读取当日发现基金报告做跨报告披露失败", exc_info=True)
        return {**_UNAVAILABLE, "reason": "cross_reference_error"}


def _cn_date(created_at: str) -> str | None:
    text = str(created_at or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CN_TZ).date().isoformat()


__all__ = [
    "DISCOVERY_CROSS_REFERENCE_SCHEMA_VERSION",
    "build_discovery_cross_reference",
]
