from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from typing import Any, Callable

PositionFetchFn = Callable[[str], list[dict]]


def summarize_sector_position(sector_label: str, rows: list[dict]) -> dict[str, Any]:
    valid_rows = [
        row
        for row in sorted(rows or [], key=lambda item: str(item.get("date") or ""))
        if _num(row.get("close")) is not None and (_num(row.get("close")) or 0) > 0
    ]
    if len(valid_rows) < 20:
        return {
            "sector_label": sector_label,
            "available": False,
            "reason": "insufficient_daily_kline",
        }

    window = valid_rows[-20:]
    closes = [float(row["close"]) for row in window]
    latest_close = closes[-1]
    high_20d = max(closes)
    low_20d = min(closes)
    prior_high = max(closes[:-1]) if len(closes) > 1 else None
    drawdown = _pct((high_20d - latest_close) / high_20d * 100) if high_20d > 0 else None
    distance_high = _pct((latest_close - high_20d) / high_20d * 100) if high_20d > 0 else None
    distance_low = _pct((latest_close - low_20d) / low_20d * 100) if low_20d > 0 else None
    breakout = (
        _pct((latest_close - prior_high) / prior_high * 100)
        if prior_high is not None and prior_high > 0
        else None
    )
    up_days, down_days = _count_recent_directions(closes, lookback_changes=5)
    volume_ratio = _volume_ratio_5d_vs_20d(window)
    label = _position_label(
        drawdown=drawdown,
        distance_low=distance_low,
        breakout=breakout,
        volume_ratio=volume_ratio,
        up_days=up_days,
        down_days=down_days,
        high_20d=high_20d,
        low_20d=low_20d,
    )

    return {
        "sector_label": sector_label,
        "available": True,
        "position_label": label,
        "latest_close": _pct(latest_close),
        "twenty_day_high": _pct(high_20d),
        "twenty_day_low": _pct(low_20d),
        "distance_from_20d_high_percent": distance_high,
        "distance_from_20d_low_percent": distance_low,
        "drawdown_from_20d_high_percent": drawdown,
        "breakout_over_prior_20d_high_percent": breakout,
        "volume_ratio_5d_vs_20d": volume_ratio,
        "up_days_5d": up_days,
        "down_days_5d": down_days,
    }


def build_sector_position_map_for_opportunities(
    sector_labels: list[str],
    *,
    fetch_series: PositionFetchFn | None = None,
    total_timeout_seconds: float = 24.0,
    max_workers: int = 5,
) -> dict[str, dict[str, Any]]:
    labels = _unique_labels(sector_labels)
    if not labels:
        return {}
    fetch = fetch_series or _default_fetch_series_for_label

    def load(label: str) -> tuple[str, dict[str, Any] | None]:
        try:
            context = summarize_sector_position(label, fetch(label))
        except Exception:  # noqa: BLE001 - position context is best-effort
            return label, None
        if not context.get("available"):
            return label, None
        return label, context

    result: dict[str, dict[str, Any]] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="discovery-sector-position",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        try:
            for future in as_completed(futures, timeout=max(0.0, total_timeout_seconds)):
                label, context = future.result()
                if context:
                    result[label] = context
        except FutureTimeoutError:
            pass
        finally:
            for future in futures:
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def _default_fetch_series_for_label(label: str) -> list[dict]:
    from app.services.sector_canonical import get_canonical_sector
    from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series

    canon = get_canonical_sector(label)
    if canon is None:
        return []
    return fetch_canonical_daily_kline_series(
        canon,
        max_days=40,
        timeout=3.0,
        allow_akshare=True,
    )


def _position_label(
    *,
    drawdown: float | None,
    distance_low: float | None,
    breakout: float | None,
    volume_ratio: float | None,
    up_days: int,
    down_days: int,
    high_20d: float,
    low_20d: float,
) -> str:
    ratio = volume_ratio or 0.0
    if breakout is not None and breakout > 0 and ratio >= 1.25 and up_days >= 3:
        return "early_breakout"
    if drawdown is not None and drawdown >= 10.0 and down_days >= 3:
        return "weak_breakdown"
    if drawdown is not None and drawdown <= 2.0:
        return "high_extended"
    if drawdown is not None and 2.0 <= drawdown <= 8.0 and down_days <= 3:
        return "pullback_acceptance"
    range_percent = (high_20d - low_20d) / low_20d * 100 if low_20d > 0 else None
    if (
        range_percent is not None
        and range_percent <= 12.0
        and distance_low is not None
        and distance_low <= 8.0
    ):
        return "base_building"
    return "neutral"


def _count_recent_directions(closes: list[float], *, lookback_changes: int) -> tuple[int, int]:
    recent = closes[-(lookback_changes + 1) :]
    up = 0
    down = 0
    for prev, cur in zip(recent, recent[1:]):
        if cur > prev:
            up += 1
        elif cur < prev:
            down += 1
    return up, down


def _volume_ratio_5d_vs_20d(rows: list[dict]) -> float | None:
    values = [_volume_value(row) for row in rows]
    if any(value is None or value <= 0 for value in values):
        return None
    recent = values[-5:]
    avg_20d = sum(values) / len(values)
    avg_5d = sum(recent) / len(recent)
    if avg_20d <= 0:
        return None
    return _pct(avg_5d / avg_20d)


def _volume_value(row: dict) -> float | None:
    volume = _num(row.get("volume"))
    if volume is not None and volume > 0:
        return volume
    amount = _num(row.get("amount"))
    if amount is not None and amount > 0:
        return amount
    return None


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value: float) -> float:
    return round(float(value), 2)


def _unique_labels(labels: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result
