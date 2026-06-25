from __future__ import annotations

from app.models import Holding
from app.services.sector_canonical import get_canonical_sector, get_intraday_canonical_sector
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.sector_quote_label import sector_quote_lookup_label

IntradayPoint = dict[str, str | float]


def summarize_sector_intraday_for_holding(holding: Holding) -> dict | None:
    label = sector_quote_lookup_label(holding)
    if not label:
        return None

    canon = get_intraday_canonical_sector(label) or get_canonical_sector(label)
    if canon is None:
        return None

    points, note, session_date, close_change = fetch_sector_intraday(
        canon.source_type,
        canon.source_name,
    )
    if not points:
        return None

    summary = _summarize_points(points, close_change)
    return {
        "sector_label": label,
        "session_date": session_date,
        "close_change_percent": close_change,
        "point_count": len(points),
        "note": note,
        **summary,
    }


def _summarize_points(
    points: list[IntradayPoint],
    close_change: float | None,
) -> dict:
    percents = [float(p.get("percent", 0) or 0) for p in points]
    if not percents:
        return {
            "intraday_high_percent": None,
            "intraday_low_percent": None,
            "open_change_percent": None,
            "pattern_label": "no_data",
            "pattern_hint": "分时数据不足。",
        }

    high = max(percents)
    low = min(percents)
    open_change = percents[0]
    close = close_change if close_change is not None else percents[-1]
    pullback = high - close
    rebound = close - low

    pattern_label = "range_bound"
    pattern_hint = "盘中震荡，尾盘方向不明显。"

    if high >= 2.0 and pullback >= 1.0:
        pattern_label = "intraday_pullback"
        pattern_hint = "盘中冲高后回落，短线追涨胜率偏低，宜等回踩或次日低开再评估。"
    elif low <= -1.5 and rebound >= 1.2:
        pattern_label = "intraday_rebound"
        pattern_hint = "盘中下探后反弹，可关注尾盘强势能否延续至次日开盘。"
    elif close >= 2.5 and open_change >= 0.5:
        pattern_label = "steady_rally"
        pattern_hint = "全天偏强上行，战术上可顺势但需防次日获利回吐。"
    elif close <= -2.0:
        pattern_label = "steady_decline"
        pattern_hint = "全天偏弱，短线加仓风险较高。"

    return {
        "open_change_percent": round(open_change, 2),
        "intraday_high_percent": round(high, 2),
        "intraday_low_percent": round(low, 2),
        "pullback_from_high_percent": round(pullback, 2),
        "rebound_from_low_percent": round(rebound, 2),
        "pattern_label": pattern_label,
        "pattern_hint": pattern_hint,
    }


def summarize_sector_intraday_for_label(sector_label: str) -> dict | None:
    """按板块名拉取分时摘要（荐基 target_sector 上下文用）。"""
    label = (sector_label or "").strip()
    if not label:
        return None
    stub = Holding(
        fund_code="000000",
        fund_name=label,
        sector_name=label,
        holding_amount=0.0,
    )
    return summarize_sector_intraday_for_holding(stub)
