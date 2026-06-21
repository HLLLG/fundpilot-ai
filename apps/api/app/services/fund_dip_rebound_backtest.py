from __future__ import annotations

from typing import Any

from app.services.sector_canonical import get_canonical_sector
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.trade_calendar_cache import get_trade_date_set

_DEFAULT_LOOKBACK_DAYS = 120
_DEFAULT_NOTE = "板块代理统计，非单基承诺"


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_sector_dip_rebound_stats(
    series: list[dict[str, Any]],
    dip_threshold_percent: float,
    rebound_threshold_percent: float,
    forward_days: int,
) -> dict[str, Any]:
    """Sliding window: dip day (change <= -threshold) → forward N-day cumulative rebound."""
    if not series or forward_days < 1:
        return {
            "sample_count": 0,
            "rebound_rate_3d_percent": None,
            "note": _DEFAULT_NOTE,
        }

    dip_threshold = abs(float(dip_threshold_percent))
    rebound_threshold = float(rebound_threshold_percent)
    window = max(1, int(forward_days))

    sample_count = 0
    rebound_hits = 0

    for index, bar in enumerate(series):
        change = _as_float(bar.get("change_percent"))
        if change is None or change > -dip_threshold:
            continue
        if index + window >= len(series):
            continue

        cumulative = 0.0
        valid = True
        for offset in range(1, window + 1):
            forward_change = _as_float(series[index + offset].get("change_percent"))
            if forward_change is None:
                valid = False
                break
            cumulative += forward_change
        if not valid:
            continue

        sample_count += 1
        if cumulative >= rebound_threshold:
            rebound_hits += 1

    rebound_rate = round(rebound_hits / sample_count * 100, 1) if sample_count else None
    return {
        "sample_count": sample_count,
        "rebound_rate_3d_percent": rebound_rate,
        "note": _DEFAULT_NOTE,
    }


def _filter_trading_days(series: list[dict[str, Any]], *, lookback_days: int) -> list[dict[str, Any]]:
    trade_dates = get_trade_date_set()
    if trade_dates:
        filtered = [
            bar
            for bar in series
            if str(bar.get("date", ""))[:10] in trade_dates
        ]
    else:
        filtered = list(series)
    window = max(30, min(int(lookback_days), 400))
    if len(filtered) > window:
        filtered = filtered[-window:]
    return filtered


def build_sector_dip_rebound_hint(
    sector_label: str,
    *,
    dip_threshold_percent: float = 3.0,
    rebound_threshold_percent: float = 2.5,
    forward_days: int = 3,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    fetch_series: Any | None = None,
) -> dict[str, Any] | None:
    """Fetch sector index kline and return historical_hint payload for dip radar."""
    label = (sector_label or "").strip()
    if not label:
        return None

    canon = get_canonical_sector(label)
    if canon is None:
        return None

    if fetch_series is None:
        series = fetch_canonical_daily_kline_series(
            canon,
            max_days=lookback_days + forward_days + 5,
            timeout=6.0,
            allow_akshare=False,
        )
    else:
        series = fetch_series(canon)

    filtered = _filter_trading_days(series, lookback_days=lookback_days)
    if len(filtered) < forward_days + 2:
        return None

    stats = compute_sector_dip_rebound_stats(
        filtered,
        dip_threshold_percent=dip_threshold_percent,
        rebound_threshold_percent=rebound_threshold_percent,
        forward_days=forward_days,
    )
    if stats["sample_count"] < 1:
        return None
    return stats
