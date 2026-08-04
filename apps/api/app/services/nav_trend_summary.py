from __future__ import annotations

from math import sqrt
from statistics import pstdev

from app.models import FundNavHistory, FundNavPoint
from app.services.fund_factor_nav import total_return_navs_from_points


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

    total_return_series = total_return_navs_from_points(all_points)
    total_return_points = total_return_series.points
    window_start_date = start.date
    window_return_points = [
        point for point in total_return_points if point[0] >= window_start_date
    ]

    period_change = _series_return_percent(window_return_points)

    recent_5d_change = None
    if len(total_return_points) >= 6:
        # recent_5d 看真实最后 6 个有效总收益点，不被 window 影响。
        recent_5d_change = _series_return_percent(total_return_points[-6:])

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
        "annualized_volatility_20d_percent": horizon_20d.get(
            "annualized_volatility_percent"
        ),
        "distance_from_20d_high_percent": horizon_20d.get(
            "distance_from_high_percent"
        ),
        "rebound_from_20d_low_percent": horizon_20d.get(
            "rebound_from_low_percent"
        ),
        "drawdown_recovery_20d_percent": horizon_20d.get(
            "drawdown_recovery_percent"
        ),
        "return_60d_percent": horizon_60d.get("return_percent"),
        "max_drawdown_60d_percent": horizon_60d.get("max_drawdown_percent"),
        "annualized_volatility_60d_percent": horizon_60d.get(
            "annualized_volatility_percent"
        ),
        "distance_from_60d_high_percent": horizon_60d.get(
            "distance_from_high_percent"
        ),
        "rebound_from_60d_low_percent": horizon_60d.get(
            "rebound_from_low_percent"
        ),
        "drawdown_recovery_60d_percent": horizon_60d.get(
            "drawdown_recovery_percent"
        ),
        "return_series_basis": "total_return_daily_growth_first",
        "daily_growth_coverage_percent": round(
            total_return_series.return_coverage * 100.0, 1
        ),
        "unit_nav_distance_basis": "official_unit_nav",
        "source": history.source,
        "recent_nav_series": recent_nav_series,
    }


def _window_return_and_drawdown(
    points: list[FundNavPoint],
    *,
    trading_days: int,
) -> dict[str, float | None]:
    """Return horizon-matched momentum, volatility and recovery metrics.

    ``drawdown_recovery_percent`` is the latest total-return NAV's position
    inside the horizon's low/high range: 0 means it is still at the range low,
    100 means the drawdown has been fully repaired.  Combined with the recent
    five-day direction this distinguishes a rebound that is actually repairing
    from a fund that is merely volatile while still falling.
    """

    if trading_days <= 0 or len(points) < trading_days + 1:
        return {}
    total_return_points = total_return_navs_from_points(points).points
    if len(total_return_points) < trading_days + 1:
        return {}
    window = total_return_points[-(trading_days + 1) :]
    if window[0][1] <= 0:
        return {}
    values = [value for _day, value in window]
    period_return = (values[-1] / values[0] - 1) * 100
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        if value <= 0:
            return {}
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, (value / peak - 1) * 100)
    daily_returns = [
        current / previous - 1.0
        for previous, current in zip(values, values[1:])
        if previous > 0
    ]
    volatility = (
        pstdev(daily_returns) * sqrt(252.0) * 100.0
        if len(daily_returns) >= 2
        else None
    )
    high = max(values)
    low = min(values)
    latest = values[-1]
    distance_high = (latest / high - 1.0) * 100.0 if high > 0 else None
    rebound_low = (latest / low - 1.0) * 100.0 if low > 0 else None
    recovery = (
        (latest - low) / (high - low) * 100.0
        if high > low
        else 100.0
    )
    return {
        "return_percent": round(period_return, 2),
        "max_drawdown_percent": round(max_drawdown, 2),
        "annualized_volatility_percent": (
            round(volatility, 2) if volatility is not None else None
        ),
        "distance_from_high_percent": (
            round(distance_high, 2) if distance_high is not None else None
        ),
        "rebound_from_low_percent": (
            round(rebound_low, 2) if rebound_low is not None else None
        ),
        "drawdown_recovery_percent": round(max(0.0, min(100.0, recovery)), 2),
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
    total_return_points = total_return_navs_from_points(points).points
    changes: list[float] = []
    start_index = max(1, len(total_return_points) - max_days)
    for index in range(start_index, len(total_return_points)):
        prev = total_return_points[index - 1][1]
        curr = total_return_points[index][1]
        if prev <= 0:
            continue
        changes.append(round((curr / prev - 1) * 100, 2))
    return changes


def _series_return_percent(points: list[tuple[str, float]]) -> float | None:
    if len(points) < 2 or points[0][1] <= 0:
        return None
    return round((points[-1][1] / points[0][1] - 1.0) * 100.0, 2)
