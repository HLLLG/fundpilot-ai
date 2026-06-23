from __future__ import annotations

from typing import Any

from app.models import Holding
from app.services.board_fund_flow_history import (
    get_cached_board_flow_series,
    resolve_board_flow_code_for_sector,
)
from app.services.sector_labels import normalize_sector_label


def _sum_main_force(points: list[dict[str, Any]]) -> float | None:
    values = [
        float(point["main_force_net_yi"])
        for point in points
        if point.get("main_force_net_yi") is not None
    ]
    if not values:
        return None
    return round(sum(values), 2)


def _slice_tail(points: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    if len(points) <= days:
        return list(points)
    return points[-days:]


def _classify_flow_pattern(
    *,
    sector_return_percent: float | None,
    today_flow: float | None,
    cumulative_5d: float | None,
    flow_tiers: dict[str, Any] | None,
) -> dict[str, str]:
    price = sector_return_percent
    flow = today_flow
    price_up = price is not None and price > 0.5
    price_down = price is not None and price < -0.5
    flow_in = flow is not None and flow > 0.5
    flow_out = flow is not None and flow < -0.5

    tiers = flow_tiers or {}
    super_large = tiers.get("super_large_net_yi")
    small = tiers.get("small_net_yi")
    retail_buy_inst_sell = (
        super_large is not None
        and small is not None
        and super_large < -0.5
        and small > 0.5
    )

    if price_up and flow_out:
        hint = "板块收涨但主力净流出，警惕高位出货或诱多，不宜追涨。"
        if retail_buy_inst_sell:
            hint += "超大单流出而小单流入，散户接盘特征明显。"
        return {"pattern_label": "distribution", "pattern_hint": hint}

    if price_down and flow_in:
        return {
            "pattern_label": "accumulation",
            "pattern_hint": "板块下跌但主力净流入，或在低位洗盘/吸筹，勿因单日下跌盲目止损。",
        }

    if price_up and flow_in:
        return {
            "pattern_label": "price_flow_aligned_up",
            "pattern_hint": "量价资金同向偏强，短线动能较好但需防过热。",
        }

    if price_down and flow_out:
        return {
            "pattern_label": "weak_outflow",
            "pattern_hint": "板块弱势且资金持续流出，短线加仓胜率通常不高。",
        }

    if cumulative_5d is not None and cumulative_5d > 3 and flow_out:
        return {
            "pattern_label": "multi_day_inflow_then_outflow",
            "pattern_hint": "近5日累计净流入后今日转出，关注是否阶段性兑现。",
        }

    if cumulative_5d is not None and cumulative_5d < -3 and flow_in:
        return {
            "pattern_label": "multi_day_outflow_then_inflow",
            "pattern_hint": "近5日累计净流出后今日回流，或为短暂反弹或资金回补。",
        }

    return {
        "pattern_label": "neutral",
        "pattern_hint": "量价与主力流向未形成明显背离，宜结合 news 与 nav_trend 综合判断。",
    }


def build_sector_fund_flow_context(
    sector_name: str | None,
    *,
    sector_return_percent: float | None = None,
) -> dict[str, Any] | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    board_code, resolved_label = resolve_board_flow_code_for_sector(label)
    if not board_code:
        return {
            "available": False,
            "sector_label": label,
            "message": "未解析到板块资金流代码",
        }

    series = get_cached_board_flow_series(board_code)
    if not series:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块历史资金流",
        }

    recent_5d = _slice_tail(series, 5)
    recent_20d = _slice_tail(series, 20)
    latest = recent_5d[-1]
    today_flow = latest.get("main_force_net_yi")
    tiers = latest.get("flow_tiers")
    cumulative_5d = _sum_main_force(recent_5d)
    cumulative_20d = _sum_main_force(recent_20d)
    pattern = _classify_flow_pattern(
        sector_return_percent=sector_return_percent,
        today_flow=today_flow,
        cumulative_5d=cumulative_5d,
        flow_tiers=tiers if isinstance(tiers, dict) else None,
    )

    return {
        "available": True,
        "sector_label": resolved_label or label,
        "board_code": board_code,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": cumulative_5d,
        "cumulative_20d_net_yi": cumulative_20d,
        "recent_5d_main_force_yi": [
            point.get("main_force_net_yi") for point in recent_5d
        ],
        "flow_tiers": tiers,
        **pattern,
    }


def build_sector_fund_flow_map(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    """按 normalized sector 名去重拉取，供多只同板块基金复用。"""
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        context = build_sector_fund_flow_context(
            label,
            sector_return_percent=holding.sector_return_percent,
        )
        if context is not None:
            result[label] = context
    return result


def sector_fund_flow_for_holding(
    holding: Holding,
    flow_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    label = normalize_sector_label(holding.sector_name)
    if not label:
        return None
    cached = flow_map.get(label)
    if cached is not None:
        return cached
    return build_sector_fund_flow_context(
        label,
        sector_return_percent=holding.sector_return_percent,
    )
