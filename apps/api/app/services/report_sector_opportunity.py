from __future__ import annotations

"""日报「持仓板块方向机会分 + 板块轮动参考」。

把荐基（discovery）验证过的双轨机会打分（`sector_opportunity_scoring.py`）接到日报：
- 给每个持仓板块一个方向判断（`held`，即使该板块暂不构成机会也会返回，标 `opportunity_available=False`）
- 给出当前全市场机会分最高的方向作为轮动参考（`market_top`），供 LLM 判断「持仓是否踏空更强方向」

数据源复用 `discovery_sector_heat.build_sector_heat_ranking_for_ui()`（市场 Tab 共享缓存，
秒级返回，无额外网络开销）与 `sector_opportunity_scoring.build_sector_flow_map_for_opportunities`
（板块资金流，带总预算超时）。全程 best-effort：任意异常/超时都不阻塞日报，返回
`{"available": False, ...}`。
"""

from typing import Any

from app.models import Holding
from app.services.sector_labels import normalize_sector_label
from app.services.sector_opportunity_scoring import (
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
    select_sector_opportunities,
)

SECTOR_FLOW_BUDGET_SECONDS = 4.0
MARKET_TOP_LIMIT = 5
MARKET_TOP_CANDIDATE_LIMIT = 8


def build_holding_sector_opportunity_context(
    holdings: list[Holding],
    *,
    fetch_sector_heat=None,
) -> dict[str, Any]:
    """返回 `{available, held: {sector_label: opportunity_row}, market_top: [opportunity_row]}`。

    `held` 按标准化后的板块 label 建索引，供 `analysis_facts.py` 按持仓行 `sector_name` 反查；
    `market_top` 是当前全市场机会分最高的若干方向（去掉已持有的，避免和 `held` 重复），
    用于日报叙述「相对更强的方向是哪些」（板块轮动参考）。
    """
    held_labels = _unique_labels(
        normalize_sector_label(holding.sector_name) for holding in holdings
    )
    if not held_labels:
        return {"available": False, "reason": "no_sector", "held": {}, "market_top": []}

    try:
        heat_fetcher = fetch_sector_heat or _default_fetch_sector_heat
        sector_heat = heat_fetcher() or []
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        return {"available": False, "reason": "sector_heat_error", "held": {}, "market_top": []}

    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    if not heat_by_label:
        return {"available": False, "reason": "sector_heat_empty", "held": {}, "market_top": []}

    top_by_heat = sorted(
        sector_heat,
        key=lambda row: _num(row.get("heat_score")) or float("-inf"),
        reverse=True,
    )
    top_labels = [
        str(row.get("sector_label") or "").strip()
        for row in top_by_heat[:MARKET_TOP_CANDIDATE_LIMIT]
        if str(row.get("sector_label") or "").strip()
    ]
    flow_labels = _unique_labels([*held_labels, *top_labels])

    try:
        flow_by_label = build_sector_flow_map_for_opportunities(
            sector_heat,
            flow_labels,
            total_timeout_seconds=SECTOR_FLOW_BUDGET_SECONDS,
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        flow_by_label = {}

    held: dict[str, dict[str, Any]] = {}
    for label in held_labels:
        heat_row = heat_by_label.get(label)
        if heat_row is None:
            continue
        opportunity = describe_sector_opportunity(
            heat_row,
            flow_by_label.get(label),
            focus={label},
        )
        if opportunity:
            held[label] = opportunity

    try:
        selected = select_sector_opportunities(
            sector_heat,
            sector_flow_by_label=flow_by_label,
            focus_sectors=held_labels,
            max_total=MARKET_TOP_LIMIT + len(held_labels),
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        selected = []

    held_label_set = set(held_labels)
    market_top = [
        item for item in selected if item.get("sector_label") not in held_label_set
    ][:MARKET_TOP_LIMIT]

    return {"available": True, "held": held, "market_top": market_top}


def _default_fetch_sector_heat() -> list[dict]:
    from app.services.discovery_sector_heat import build_sector_heat_ranking_for_ui

    return build_sector_heat_ranking_for_ui()


def _unique_labels(labels) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
