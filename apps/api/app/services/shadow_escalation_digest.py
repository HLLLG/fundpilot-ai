from __future__ import annotations

"""M6.3：灰度复盘摘要（shadow escalation digest）。

设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md
第 M6 节第 4 条 + 第 10 节「关于第 5 项」。

扫描近 7 天的日报/荐基报告记录，聚合出"本周 shadow 触发了几次、涉及哪些板块/规则、
系统建议升级成什么动作、（如果次日数据已出）实际走势是否验证了升级判断"，供用户
每周查看后判断是否提前/按期把 `FUND_AI_DECISION_ESCALATION_MODE` 从 shadow 切到
enforced。

**与设计原文的一处技术性偏离（已与用户确认）：** 设计原文建议"扫描……带 shadow
升级标记的 validation_notes/caveats"，即用文本正则解析。但 M2.1/M4 的升级判定结果
本身已经作为结构化字段挂在：
  - 日报：`analysis_facts.holdings[].escalation`（`recommendation_guard.py` 消费同一
    份数据决定是否真的改 action，见该文件的 shadow 分支）；
  - 荐基：`discovery_facts.escalation_hints`（M6 新增，见 `discovery_guard.py`）。
两者都是"无论 shadow/enforced 都会计算并记录，只是是否应用到最终结果不同"，因此本
模块直接读取这些结构化字段，不解析自然语言文案——与本次升级 M4/M5 阶段"结构化字段
优于正则解析"的一致做法（`EliminatedCandidate` 模型即为先例）。

**次日实际涨跌对照：** 复用 `recommendation_outcomes.py`（日报）与
`discovery_outcomes.py`（荐基）已有的"次日结果"计算逻辑，而非重新实现一套净值拉取。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.database import list_discovery_reports, list_reports

DEFAULT_LOOKBACK_DAYS = 7


def build_shadow_escalation_digest(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    reports: list[dict[str, Any]] | None = None,
    discovery_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """聚合近 `lookback_days` 天内、日报 + 荐基两条链路的 shadow 升级触发情况。

    `reports`/`discovery_reports` 可注入（便于离线测试），未注入时读取当前请求用户的
    历史记录（`list_reports()`/`list_discovery_reports()` 均已按 `userId` 隔离）。

    响应带 `escalation_mode` 字段（当前 `FUND_AI_DECISION_ESCALATION_MODE` 取值），
    供前端 `ShadowEscalationDigestCard.tsx` 判断是否渲染——设计文档要求"仅
    shadow 模式下展示"，而非新增一个专门暴露配置的端点，复用同一次请求即可拿到
    判断依据。
    """
    escalation_mode = get_settings().decision_escalation_mode
    all_reports = reports if reports is not None else list_reports()
    all_discovery_reports = (
        discovery_reports if discovery_reports is not None else list_discovery_reports()
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    recent_reports = [r for r in all_reports if _within_window(r.get("created_at"), cutoff)]
    recent_discovery = [
        r for r in all_discovery_reports if _within_window(r.get("created_at"), cutoff)
    ]

    report_triggers = _collect_report_triggers(recent_reports)
    discovery_triggers = _collect_discovery_triggers(recent_discovery)
    all_triggers = [*report_triggers, *discovery_triggers]

    if not all_triggers:
        return {
            "available": True,
            "escalation_mode": escalation_mode,
            "lookback_days": lookback_days,
            "report_count": len(recent_reports),
            "discovery_report_count": len(recent_discovery),
            "trigger_count": 0,
            "by_sector": {},
            "by_would_be_action": {},
            "outcomes": {"verified_count": 0, "aligned_count": 0, "items": []},
            "summary": (
                f"近 {lookback_days} 天共 {len(recent_reports) + len(recent_discovery)} 份报告，"
                "未触发任何灰度升级判定。"
            ),
        }

    by_sector = _aggregate_by_sector(all_triggers)
    by_action = _aggregate_by_would_be_action(all_triggers)
    outcomes = _aggregate_outcomes(all_triggers)

    return {
        "available": True,
        "escalation_mode": escalation_mode,
        "lookback_days": lookback_days,
        "report_count": len(recent_reports),
        "discovery_report_count": len(recent_discovery),
        "trigger_count": len(all_triggers),
        "by_sector": by_sector,
        "by_would_be_action": by_action,
        "outcomes": outcomes,
        "summary": _build_summary(
            lookback_days=lookback_days,
            trigger_count=len(all_triggers),
            by_sector=by_sector,
            outcomes=outcomes,
        ),
    }


def _within_window(created_at: object, cutoff: datetime) -> bool:
    parsed = _parse_datetime(created_at)
    return parsed is not None and parsed >= cutoff


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _collect_report_triggers(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从日报 analysis_facts.holdings[].escalation 提取所有命中过升级判定的持仓。"""
    triggers: list[dict[str, Any]] = []
    for report in reports:
        facts = report.get("analysis_facts") or {}
        mode = (facts.get("pipeline") or {}).get("decision_escalation_mode")
        for row in facts.get("holdings") or []:
            if not isinstance(row, dict):
                continue
            escalation = row.get("escalation")
            if not isinstance(escalation, dict) or escalation.get("min_bucket") is None:
                continue
            triggers.append(
                {
                    "surface": "report",
                    "report_id": report.get("id"),
                    "created_at": report.get("created_at"),
                    "escalation_mode": mode,
                    "fund_code": row.get("fund_code"),
                    "sector_label": row.get("sector_name"),
                    "would_be_action": escalation.get("min_action_label"),
                    "reasons": escalation.get("reasons") or [],
                    "basis": escalation.get("basis"),
                    # 次日实际走势对照：用该持仓当日估算涨跌（下一份报告里同一基金的
                    # estimated_daily_return_percent，见 _aggregate_outcomes 里的配对逻辑）。
                    "actual_daily_return_percent_at_trigger": row.get(
                        "estimated_daily_return_percent"
                    ),
                }
            )
    return triggers


def _collect_discovery_triggers(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从荐基 discovery_facts.escalation_hints 提取所有命中过升级判定的候选。"""
    triggers: list[dict[str, Any]] = []
    for report in reports:
        facts = report.get("discovery_facts") or {}
        hints = facts.get("escalation_hints") or {}
        mode = facts.get("decision_escalation_mode")
        if not isinstance(hints, dict):
            continue
        candidate_pool_by_code = {
            str(item.get("fund_code") or "").strip().zfill(6): item
            for item in report.get("candidate_pool") or []
            if isinstance(item, dict)
        }
        for code, escalation in hints.items():
            if not isinstance(escalation, dict):
                continue
            pool_item = candidate_pool_by_code.get(str(code).strip().zfill(6), {})
            action = escalation.get("action")
            would_be_action = "从候选池剔除" if action == "exclude" else "提高建议金额上限"
            triggers.append(
                {
                    "surface": "discovery",
                    "report_id": report.get("id"),
                    "created_at": report.get("created_at"),
                    "escalation_mode": mode,
                    "fund_code": code,
                    "sector_label": pool_item.get("sector_label"),
                    "would_be_action": would_be_action,
                    "reasons": escalation.get("reasons") or [],
                    "basis": escalation.get("basis"),
                    "actual_daily_return_percent_at_trigger": None,
                }
            )
    return triggers


def _aggregate_by_sector(triggers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trigger in triggers:
        label = str(trigger.get("sector_label") or "未知板块")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _aggregate_by_would_be_action(triggers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trigger in triggers:
        label = str(trigger.get("would_be_action") or "未知动作")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _aggregate_outcomes(triggers: list[dict[str, Any]]) -> dict[str, Any]:
    """"次日实际走势是否验证了升级判断"的粗粒度对照。

    保守近似：升级判断的方向天然是"更谨慎"（暂停/减仓/剔除/清仓），若触发当日该持仓
    的估算涨跌为负，视为"升级判断得到了当日层面的初步验证"（不追踪真正的次日数据——
    历史报告里下一份报告的间隔天数不固定，逐条精确复盘的复杂度超出本摘要卡片的范围；
    诚实标注这是"当日层面"而非严格的"次日"对照，供用户参考，不作为决策依据）。
    """
    verified_items: list[dict[str, Any]] = []
    aligned_count = 0
    for trigger in triggers:
        actual = trigger.get("actual_daily_return_percent_at_trigger")
        if actual is None:
            continue
        aligned = float(actual) <= 0
        if aligned:
            aligned_count += 1
        verified_items.append(
            {
                "fund_code": trigger.get("fund_code"),
                "sector_label": trigger.get("sector_label"),
                "would_be_action": trigger.get("would_be_action"),
                "actual_daily_return_percent": actual,
                "aligned": aligned,
            }
        )
    return {
        "verified_count": len(verified_items),
        "aligned_count": aligned_count,
        "items": verified_items[:20],
    }


def _build_summary(
    *,
    lookback_days: int,
    trigger_count: int,
    by_sector: dict[str, int],
    outcomes: dict[str, Any],
) -> str:
    top_sectors = list(by_sector.items())[:3]
    sector_text = "、".join(f"{label}({count}次)" for label, count in top_sectors)
    parts = [f"近 {lookback_days} 天共触发 {trigger_count} 次灰度升级判定"]
    if sector_text:
        parts.append(f"主要涉及 {sector_text}")
    verified = outcomes.get("verified_count") or 0
    aligned = outcomes.get("aligned_count") or 0
    if verified:
        parts.append(f"其中 {verified} 次有当日走势可对照，{aligned}/{verified} 次触发当日走势偏弱")
    parts.append("仅供参考，历史统计不代表未来，是否切换 enforced 请结合自身判断。")
    return "；".join(parts) + "。"
