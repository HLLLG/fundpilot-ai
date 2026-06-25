from __future__ import annotations

from typing import Any

from app.models import Holding
from app.services.board_fund_flow_history import (
    get_cached_board_flow_series,
    resolve_board_flow_code_for_sector,
)
from app.services.sector_labels import normalize_sector_label
from app.services.trading_session import get_effective_trade_date


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


def _pick_flow_point(series: list[dict[str, Any]], trade_date: str) -> dict[str, Any] | None:
    """按 effective_trade_date 取当日资金流；缺失则取不晚于该日的最近一条。"""
    if not series or not trade_date:
        return series[-1] if series else None
    for point in reversed(series):
        if point.get("date") == trade_date:
            return point
    on_or_before = [
        point for point in series if str(point.get("date") or "") <= trade_date
    ]
    if on_or_before:
        return on_or_before[-1]
    return series[-1]


def _series_has_date(series: list[dict[str, Any]], trade_date: str) -> bool:
    return any(point.get("date") == trade_date for point in series)


def _main_force_direction(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0.05:
        return "inflow"
    if value < -0.05:
        return "outflow"
    return "flat"


def _load_flow_series(board_code: str, trade_date: str) -> list[dict[str, Any]]:
    series = get_cached_board_flow_series(board_code)
    if series and not _series_has_date(series, trade_date):
        refreshed = get_cached_board_flow_series(board_code, force_refresh=True)
        if refreshed:
            series = refreshed
    return series


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
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    target_trade_date = trade_date or get_effective_trade_date()

    board_code, resolved_label = resolve_board_flow_code_for_sector(label)
    if not board_code:
        return {
            "available": False,
            "sector_label": label,
            "message": "未解析到板块资金流代码",
        }

    series = _load_flow_series(board_code, target_trade_date)
    if not series:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块历史资金流",
        }

    point = _pick_flow_point(series, target_trade_date)
    if point is None:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块历史资金流",
        }

    flow_date = str(point.get("date") or "")
    date_aligned = flow_date == target_trade_date
    recent_5d = _slice_tail(series, 5)
    recent_20d = _slice_tail(series, 20)
    today_flow = point.get("main_force_net_yi")
    tiers = point.get("flow_tiers")
    cumulative_5d = _sum_main_force(recent_5d)
    cumulative_20d = _sum_main_force(recent_20d)

    if date_aligned:
        pattern = _classify_flow_pattern(
            sector_return_percent=sector_return_percent,
            today_flow=today_flow,
            cumulative_5d=cumulative_5d,
            flow_tiers=tiers if isinstance(tiers, dict) else None,
        )
    else:
        pattern = {
            "pattern_label": "flow_date_mismatch",
            "pattern_hint": (
                f"板块资金流为 {flow_date} 数据，与当日 sector_return_percent"
                f"（{target_trade_date}）不同日，勿做量价背离判断。"
            ),
        }

    return {
        "available": True,
        "sector_label": resolved_label or label,
        "board_code": board_code,
        "trade_date": target_trade_date,
        "flow_date": flow_date,
        "date_aligned": date_aligned,
        "today_main_force_net_yi": today_flow,
        "main_force_direction": _main_force_direction(
            float(today_flow) if today_flow is not None else None
        ),
        "cumulative_5d_net_yi": cumulative_5d,
        "cumulative_20d_net_yi": cumulative_20d,
        "recent_5d_main_force_yi": [
            {"date": item.get("date"), "main_force_net_yi": item.get("main_force_net_yi")}
            for item in recent_5d
        ],
        "flow_tiers": tiers,
        **pattern,
    }


def build_sector_fund_flow_map(
    holdings: list[Holding],
    *,
    trade_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """按 normalized sector 名去重拉取，供多只同板块基金复用。"""
    target_trade_date = trade_date or get_effective_trade_date()
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        context = build_sector_fund_flow_context(
            label,
            sector_return_percent=holding.sector_return_percent,
            trade_date=target_trade_date,
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
        trade_date=get_effective_trade_date(),
    )
