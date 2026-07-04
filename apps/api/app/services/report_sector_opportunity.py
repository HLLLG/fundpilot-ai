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

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.models import Holding
from app.services.sector_labels import normalize_sector_label
from app.services.sector_opportunity_scoring import (
    build_sector_divergence_map_for_opportunities,
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
    select_sector_opportunities,
)

SECTOR_FLOW_BUDGET_SECONDS = 4.0
SECTOR_DIVERGENCE_BUDGET_SECONDS = 4.0
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
        return _unavailable("no_sector")

    try:
        heat_fetcher = fetch_sector_heat or _default_fetch_sector_heat
        sector_heat = heat_fetcher() or []
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        return _unavailable("sector_heat_error")

    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    if not heat_by_label:
        return _unavailable("sector_heat_empty")

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

    # 资金流（全部候选标签）与量价背离回测（仅已持有标签，M1.4 confidence 升级判定）并发
    # 拉取——两者是独立的板块级 IO，串行执行会让本函数最坏耗时翻倍。
    flow_by_label: dict[str, dict] = {}
    divergence_by_label: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="sector-opportunity-ctx") as executor:
        flow_future = executor.submit(
            build_sector_flow_map_for_opportunities,
            sector_heat,
            flow_labels,
            total_timeout_seconds=SECTOR_FLOW_BUDGET_SECONDS,
        )
        divergence_future = executor.submit(
            build_sector_divergence_map_for_opportunities,
            held_labels,
            total_timeout_seconds=SECTOR_DIVERGENCE_BUDGET_SECONDS,
        )
        try:
            flow_by_label = flow_future.result() or {}
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            flow_by_label = {}
        try:
            divergence_by_label = divergence_future.result() or {}
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            divergence_by_label = {}

    held: dict[str, dict[str, Any]] = {}
    for label in held_labels:
        heat_row = heat_by_label.get(label)
        if heat_row is None:
            continue
        opportunity = describe_sector_opportunity(
            heat_row,
            flow_by_label.get(label),
            focus={label},
            divergence_backtest=divergence_by_label.get(label),
        )
        if opportunity:
            held[label] = opportunity

    try:
        selected = select_sector_opportunities(
            sector_heat,
            sector_flow_by_label=flow_by_label,
            sector_divergence_by_label=divergence_by_label,
            focus_sectors=held_labels,
            max_total=MARKET_TOP_LIMIT + len(held_labels),
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        selected = []

    held_label_set = set(held_labels)
    market_top = [
        item for item in selected if item.get("sector_label") not in held_label_set
    ][:MARKET_TOP_LIMIT]

    return {
        "available": True,
        "held": held,
        "market_top": market_top,
        # M1 数据契约（design 第7节）：analysis_facts.holdings[].flow_divergence_backtest
        # 由 analysis_facts.py 从这里按持仓板块 label 反查，避免重复计算同一份回测。
        "divergence_backtest": divergence_by_label,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "held": {},
        "market_top": [],
        "divergence_backtest": {},
    }


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
