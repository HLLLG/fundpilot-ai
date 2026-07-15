from __future__ import annotations

from app.models import FundNavHistory, FundNavPoint


def summarize_nav_history(
    history: FundNavHistory | None,
    *,
    recent_sample: int = 8,
    window_days: int | None = 66,
) -> dict | None:
    """从 NAV 历史压成摘要。

    window_days 控制摘要窗口（默认 66 个交易日，保留 LLM 决策口径）；
    传 None 则使用全部点。recent_5d / recent_nav_series 始终基于真实尾部点，
    不受 window 影响——保留与现状一致的喂 LLM 行为。
    """
    if history is None or not history.points:
        return None
    if history.source in {"unavailable", "error"}:
        return None

    all_points = history.points
    if window_days and len(all_points) > window_days:
        points = all_points[-window_days:]
    else:
        points = all_points

    navs = [point.nav for point in points]
    high_nav = max(navs)
    low_nav = min(navs)
    latest = points[-1]
    start = points[0]

    period_change = None
    if start.nav > 0:
        period_change = round((latest.nav / start.nav - 1) * 100, 2)

    recent_5d_change = None
    if len(all_points) >= 6 and all_points[-6].nav > 0:
        # recent_5d 看真实最后 6 点，不被 window 影响
        recent_5d_change = round((latest.nav / all_points[-6].nav - 1) * 100, 2)

    distance_from_high = None
    if high_nav > 0:
        distance_from_high = round((latest.nav / high_nav - 1) * 100, 2)

    distance_from_low = None
    if low_nav > 0:
        distance_from_low = round((latest.nav / low_nav - 1) * 100, 2)

    sample_size = max(3, min(recent_sample, len(all_points)))
    recent_nav_series = [
        {"date": point.date, "nav": round(point.nav, 4)}
        for point in all_points[-sample_size:]
    ]
    recent_5d_daily_change_percent = _recent_daily_nav_changes(all_points, max_days=5)
    horizon_20d = _window_return_and_drawdown(all_points, trading_days=20)
    horizon_60d = _window_return_and_drawdown(all_points, trading_days=60)

    return {
        "period_days": len(points),
        "period_change_percent": period_change,
        "recent_5d_change_percent": recent_5d_change,
        "recent_5d_daily_change_percent": recent_5d_daily_change_percent,
        "latest_nav": latest.nav,
        "latest_date": latest.date,
        "high_nav": round(high_nav, 4),
        "low_nav": round(low_nav, 4),
        "distance_from_high_percent": distance_from_high,
        "distance_from_low_percent": distance_from_low,
        "trend_label": _trend_label(period_change, recent_5d_change),
        "return_20d_percent": horizon_20d.get("return_percent"),
        "max_drawdown_20d_percent": horizon_20d.get("max_drawdown_percent"),
        "return_60d_percent": horizon_60d.get("return_percent"),
        "max_drawdown_60d_percent": horizon_60d.get("max_drawdown_percent"),
        "source": history.source,
        "recent_nav_series": recent_nav_series,
    }


def _window_return_and_drawdown(
    points: list[FundNavPoint],
    *,
    trading_days: int,
) -> dict[str, float]:
    """Return horizon-matched metrics only when the full window is available."""

    if trading_days <= 0 or len(points) < trading_days + 1:
        return {}
    window = points[-(trading_days + 1) :]
    if window[0].nav <= 0:
        return {}
    period_return = (window[-1].nav / window[0].nav - 1) * 100
    peak = window[0].nav
    max_drawdown = 0.0
    for point in window:
        if point.nav <= 0:
            return {}
        peak = max(peak, point.nav)
        max_drawdown = min(max_drawdown, (point.nav / peak - 1) * 100)
    return {
        "return_percent": round(period_return, 2),
        "max_drawdown_percent": round(max_drawdown, 2),
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


def _recent_daily_nav_changes(points: list[FundNavPoint], *, max_days: int = 5) -> list[float]:
    changes: list[float] = []
    start_index = max(1, len(points) - max_days)
    for index in range(start_index, len(points)):
        prev = points[index - 1].nav
        curr = points[index].nav
        if prev <= 0:
            continue
        changes.append(round((curr / prev - 1) * 100, 2))
    return changes
