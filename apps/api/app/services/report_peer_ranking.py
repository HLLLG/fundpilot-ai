from __future__ import annotations

"""日报持仓的同类分位（peer percentile）。

## 这一层回答什么

日报此前只能看持仓的**绝对**收益与回撤，回答不了"这只基金在同类里排哪儿"。荐基早就
对候选算了 `peer_rank`，但候选池刻意排除已持有的基金，所以持仓侧没有任何可复用的结果，
必须自己算一份。

## 严格是描述性证据，不进确定性 guard

`fund_peer_ranking` 的 `execution_tilt_eligible` **恒为 False**，`MIN_INDEPENDENT_PEER_FAMILIES=20`
与 `MIN_METRIC_COVERAGE=0.80` 只是数据可比性门槛，不是预测力验证。因此本模块的产物只
进 prompt 与展示，**不参与仓位比例、不参与动作拦截**——这与 factor 侧
`descriptive_applicable` vs `execution_qualified` 是同一条纪律，也是移植时最容易搞错
的地方（把分位当成可执行信号）。

## 成本（实测，2026-08-08 本地）

* `build_peer_rank` 单只对 6,992 行同类桶 **14 ms**、对 5,000 行 **8 ms** —— 逐只算不贵，
  不需要预分桶优化（对 20k 行预分桶本身要 2.25 s，纯属多余）。
* 唯一的真实成本是 20,000 行目录快照加载 **约 4.07 s**（DB 读 + 反序列化），因此走
  `fetch_discovery_fund_universe_cache_only()` 只读缓存，并由调用方套独立预算。

## fail closed 的几种情形都必须能区分

目录缓存缺席、持仓不在目录里、同类组本身欠定义（`fund_peer_ranking` 对 mixed / bond /
QDII / FOF / 被动指数在分类字段不足时**按设计** fail closed，例如混合型缺
`risk_exposure` 会得到 `mixed_risk_exposure_unavailable`）——这些都不是"该基金不好"，
必须各自留下原因，否则模型会把缺席读成利空。
"""

from datetime import datetime
from typing import Any

from app.models import Holding
from app.services.fund_peer_ranking import (
    build_peer_rank,
    catalogue_aligned_peer_target,
    compact_peer_research_for_llm,
    peer_catalogue_bucket,
)

PEER_RESEARCH_UNAVAILABLE_REASON_NO_CATALOGUE = "catalogue_cache_unavailable"
PEER_RESEARCH_UNAVAILABLE_REASON_NOT_LISTED = "fund_not_in_catalogue"
PEER_RESEARCH_UNAVAILABLE_REASON_ERROR = "peer_rank_error"


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "reason": reason,
        # 恒为 False 的执行语义也要带上，避免下游把"没算出来"与"可以执行"混淆。
        "execution_tilt_eligible": False,
        "descriptive_only": True,
    }


def resolve_holding_peer_research(
    holdings: list[Holding],
    *,
    decision_at: datetime | None = None,
    fetch_universe=None,
) -> dict[str, dict[str, Any]]:
    """按 `fund_code` 返回每只持仓的同类分位紧凑投影。

    `fetch_universe` 可注入（测试用）；未注入时只读目录缓存、不触发拉源。
    """
    codes = [
        str(holding.fund_code or "").strip().zfill(6)
        for holding in holdings
        if str(holding.fund_code or "").strip()
    ]
    if not codes:
        return {}

    try:
        if fetch_universe is not None:
            rows = list(fetch_universe() or [])
        else:
            from app.services.fund_discovery_data_cache import (
                fetch_discovery_fund_universe_cache_only,
            )

            rows = fetch_discovery_fund_universe_cache_only()
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        rows = []
    catalogue = [row for row in rows if isinstance(row, dict)]
    if not catalogue:
        return {
            code: _unavailable(PEER_RESEARCH_UNAVAILABLE_REASON_NO_CATALOGUE)
            for code in codes
        }

    buckets: dict[str, list[dict]] = {}
    by_code: dict[str, dict] = {}
    for row in catalogue:
        buckets.setdefault(peer_catalogue_bucket(row), []).append(row)
        code = str(row.get("fund_code") or "").strip().zfill(6)
        if code and code not in by_code:
            by_code[code] = row

    decision = decision_at or datetime.now().astimezone()
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        if code in result:
            continue
        catalogue_row = by_code.get(code)
        if catalogue_row is None:
            result[code] = _unavailable(PEER_RESEARCH_UNAVAILABLE_REASON_NOT_LISTED)
            continue
        bucket = buckets.get(peer_catalogue_bucket(catalogue_row)) or []
        # 目标与同类全集必须用同一套分类词汇，否则会造出人为的小分组。持仓侧的目标
        # 基底就是它自己的目录行，对齐这一步在此是幂等的，但保留它是为了和荐基走
        # 完全相同的入口——将来若给持仓叠加更细的档案字段，这里的对齐仍然正确。
        target = catalogue_aligned_peer_target(
            {"fund_code": code},
            source_target=catalogue_row,
        )
        try:
            rank = build_peer_rank(target, bucket, decision_at=decision)
        except Exception:  # noqa: BLE001 - 一只算不出来不能拖垮整份日报
            result[code] = _unavailable(PEER_RESEARCH_UNAVAILABLE_REASON_ERROR)
            continue
        compact = compact_peer_research_for_llm(
            {"peer_rank": rank, "peer_group": rank.get("peer_group") or {}}
        )
        compact["available"] = str(rank.get("status") or "") != "insufficient"
        compact["descriptive_only"] = True
        result[code] = compact
    return result


def attach_holding_peer_research(
    holdings: list[dict[str, Any]],
    peer_research_by_code: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """把同类分位挂到持仓行的 `peer_research` 键。"""
    rows: list[dict[str, Any]] = []
    for row in holdings:
        if not isinstance(row, dict):
            continue
        enriched = dict(row)
        code = str(enriched.get("fund_code") or "").strip().zfill(6)
        enriched["peer_research"] = peer_research_by_code.get(code) or _unavailable(
            PEER_RESEARCH_UNAVAILABLE_REASON_NO_CATALOGUE
        )
        rows.append(enriched)
    return rows


__all__ = [
    "PEER_RESEARCH_UNAVAILABLE_REASON_ERROR",
    "PEER_RESEARCH_UNAVAILABLE_REASON_NOT_LISTED",
    "PEER_RESEARCH_UNAVAILABLE_REASON_NO_CATALOGUE",
    "attach_holding_peer_research",
    "resolve_holding_peer_research",
]
