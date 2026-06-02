from __future__ import annotations

from app.models import FundNavHistory, FundNavPoint


def summarize_nav_history(
    history: FundNavHistory | None,
    *,
    recent_sample: int = 8,
) -> dict | None:
    if history is None or not history.points:
        return None
    if history.source in {"unavailable", "error"}:
        return None

    points = history.points
    navs = [point.nav for point in points]
    high_nav = max(navs)
    low_nav = min(navs)
    latest = points[-1]
    start = points[0]

    period_change = history.period_change_percent
    if period_change is None and start.nav > 0:
        period_change = round((latest.nav / start.nav - 1) * 100, 2)

    recent_5d_change = None
    if len(points) >= 6 and points[-6].nav > 0:
        recent_5d_change = round((latest.nav / points[-6].nav - 1) * 100, 2)

    distance_from_high = None
    if high_nav > 0:
        distance_from_high = round((latest.nav / high_nav - 1) * 100, 2)

    distance_from_low = None
    if low_nav > 0:
        distance_from_low = round((latest.nav / low_nav - 1) * 100, 2)

    sample_size = max(3, min(recent_sample, len(points)))
    recent_nav_series = [
        {"date": point.date, "nav": round(point.nav, 4)}
        for point in points[-sample_size:]
    ]

    return {
        "period_days": len(points),
        "period_change_percent": period_change,
        "recent_5d_change_percent": recent_5d_change,
        "latest_nav": latest.nav,
        "latest_date": latest.date,
        "high_nav": round(high_nav, 4),
        "low_nav": round(low_nav, 4),
        "distance_from_high_percent": distance_from_high,
        "distance_from_low_percent": distance_from_low,
        "trend_label": _trend_label(period_change, recent_5d_change),
        "source": history.source,
        "recent_nav_series": recent_nav_series,
    }


def _trend_label(
    period_change: float | None,
    recent_5d_change: float | None,
) -> str:
    if period_change is None:
        return "数据不足"

    if period_change >= 5:
        base = "区间上升"
    elif period_change <= -5:
        base = "区间下行"
    elif period_change >= 1.5:
        base = "温和上行"
    elif period_change <= -1.5:
        base = "温和下行"
    else:
        base = "区间震荡"

    if recent_5d_change is None:
        return base

    if recent_5d_change >= 2 and (period_change or 0) < 1:
        return f"{base}，近5日走强"
    if recent_5d_change <= -2 and (period_change or 0) > -1:
        return f"{base}，近5日走弱"
    if recent_5d_change > 0 and period_change < 0:
        return f"{base}，近5日反弹"
    if recent_5d_change < 0 and period_change > 0:
        return f"{base}，近5日回落"

    return base
