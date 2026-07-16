from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from math import sqrt
from statistics import pstdev
from typing import Any, Callable, Mapping

PositionFetchFn = Callable[[str], list[dict]]
BenchmarkFetchFn = Callable[[], dict | None]

_BENCHMARK_CODE = "000300"
_BENCHMARK_NAME = "沪深300"
_HK_INDEX_BY_SECTOR = {
    "\u6052\u751f\u79d1\u6280": "HSTECH",
    "\u6e2f\u80a1": "HSI",
    "\u6e2f\u80a1\u901a": "HSI",
}


def summarize_sector_position(
    sector_label: str,
    rows: list[dict],
    *,
    benchmark_rows: list[dict] | None = None,
    as_of_trade_date: str | None = None,
) -> dict[str, Any]:
    valid_rows = [
        row
        for row in sorted(rows or [], key=lambda item: str(item.get("date") or ""))
        if _num(row.get("close")) is not None and (_num(row.get("close")) or 0) > 0
        and (
            as_of_trade_date is None
            or str(row.get("date") or "")[:10] <= as_of_trade_date
        )
    ]
    if len(valid_rows) < 20:
        return {
            "sector_label": sector_label,
            "available": False,
            "reason": "insufficient_daily_kline",
            "sample_days": len(valid_rows),
            "data_end_date": (
                str(valid_rows[-1].get("date") or "")[:10] if valid_rows else None
            ),
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
    returns = {
        horizon: _return_percent(valid_rows, horizon)
        for horizon in (5, 10, 20, 60)
    }
    relative_returns = {
        horizon: _aligned_relative_return_percent(
            valid_rows,
            benchmark_rows or [],
            horizon=horizon,
            as_of_trade_date=as_of_trade_date,
        )
        for horizon in (10, 20, 60)
    }
    ma20 = sum(closes[-20:]) / 20
    ma60 = (
        sum(float(row["close"]) for row in valid_rows[-60:]) / 60
        if len(valid_rows) >= 60
        else None
    )
    drawdown_20d = _max_drawdown_percent(valid_rows[-20:])
    drawdown_60d = _max_drawdown_percent(valid_rows[-60:]) if len(valid_rows) >= 60 else None
    volatility_20d = _annualized_volatility_percent(valid_rows[-21:])
    positive_ratio_20d = _positive_day_ratio_percent(valid_rows[-21:])
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
        "sample_days": len(valid_rows),
        "data_start_date": str(valid_rows[0].get("date") or "")[:10] or None,
        "data_end_date": str(valid_rows[-1].get("date") or "")[:10] or None,
        "return_5d_percent": returns[5],
        "return_10d_percent": returns[10],
        "return_20d_percent": returns[20],
        "return_60d_percent": returns[60],
        "relative_return_10d_percent": relative_returns[10],
        "relative_return_20d_percent": relative_returns[20],
        "relative_return_60d_percent": relative_returns[60],
        "distance_from_ma20_percent": _distance_percent(latest_close, ma20),
        "distance_from_ma60_percent": _distance_percent(latest_close, ma60),
        "max_drawdown_20d_percent": drawdown_20d,
        "max_drawdown_60d_percent": drawdown_60d,
        "annualized_volatility_20d_percent": volatility_20d,
        "positive_day_ratio_20d_percent": positive_ratio_20d,
        "proxy_member_count": _proxy_member_count(valid_rows),
    }


def build_sector_position_map_for_opportunities(
    sector_labels: list[str],
    *,
    fetch_series: PositionFetchFn | None = None,
    fetch_benchmark: BenchmarkFetchFn | None = None,
    benchmark_history: Mapping[str, Any] | None = None,
    as_of_trade_date: str | None = None,
    total_timeout_seconds: float = 45.0,
    max_workers: int = 4,
) -> dict[str, dict[str, Any]]:
    labels = _unique_labels(sector_labels)
    if not labels:
        return {}
    fetch = fetch_series or _default_fetch_series_for_label
    benchmark_payload: Mapping[str, Any] | None = benchmark_history
    if benchmark_payload is None and fetch_benchmark is not None:
        try:
            benchmark_payload = fetch_benchmark()
        except Exception:  # noqa: BLE001 - benchmark is best-effort research evidence
            benchmark_payload = None
    elif benchmark_payload is None and fetch_series is None:
        try:
            benchmark_payload = _default_fetch_benchmark()
        except Exception:  # noqa: BLE001 - benchmark is best-effort research evidence
            benchmark_payload = None
    benchmark_rows = list((benchmark_payload or {}).get("data") or [])
    benchmark_source = str((benchmark_payload or {}).get("source") or "").strip() or None
    benchmark_end_date = _latest_date(benchmark_rows, as_of_trade_date=as_of_trade_date)

    def load(label: str) -> tuple[str, dict[str, Any] | None]:
        try:
            rows = fetch(label)
            context = summarize_sector_position(
                label,
                rows,
                benchmark_rows=benchmark_rows,
                as_of_trade_date=as_of_trade_date,
            )
        except Exception:  # noqa: BLE001 - position context is best-effort
            return label, None
        context["source"] = _series_source(rows)
        context["benchmark_code"] = _BENCHMARK_CODE
        context["benchmark_name"] = _BENCHMARK_NAME
        context["benchmark_source"] = benchmark_source
        context["benchmark_data_end_date"] = benchmark_end_date
        return label, context

    result: dict[str, dict[str, Any]] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="discovery-sector-position",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        done, pending = wait(futures, timeout=max(0.0, total_timeout_seconds))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                label, context = future.result()
            except Exception:
                continue
            if context:
                result[label] = context
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def _default_fetch_series_for_label(label: str) -> list[dict]:
    hk_index_rows = _hk_index_price_rows(label)
    if len(hk_index_rows) >= 61:
        return hk_index_rows

    exact_rows = _flow_history_price_rows(label)
    if len(exact_rows) >= 61:
        return exact_rows

    from app.services.sector_constituent_proxy import fetch_sector_constituent_proxy_series

    return fetch_sector_constituent_proxy_series(label, trading_days=100)


def _hk_index_price_rows(label: str) -> list[dict]:
    symbol = _HK_INDEX_BY_SECTOR.get(str(label).strip())
    if not symbol:
        return []
    from app.services.akshare_subprocess import fetch_hk_index_daily_history

    payload = fetch_hk_index_daily_history(symbol, trading_days=110) or {}
    return [
        {**dict(row), "_source": "sina_hk_index_daily"}
        for row in payload.get("data") or []
        if isinstance(row, dict)
    ]


def _flow_history_price_rows(label: str) -> list[dict]:
    """用同源板块资金流日线中的收盘价兜底，不虚构成交量。"""
    from app.services.board_fund_flow_history import (
        get_board_flow_series_cache_only,
        resolve_board_flow_code_for_sector,
    )

    board_code, _ = resolve_board_flow_code_for_sector(label)
    if not board_code:
        return []
    series = sorted(
        get_board_flow_series_cache_only(board_code),
        key=lambda item: str(item.get("date") or ""),
    )
    rows: list[dict] = []
    synthetic_close = 100.0
    for point in series:
        day = str(point.get("date") or "")[:10]
        close = _num(point.get("close_price"))
        change = _num(point.get("change_percent"))
        if close is not None and close > 0:
            synthetic_close = close
        elif change is not None:
            if rows:
                synthetic_close *= 1.0 + change / 100.0
        else:
            continue
        if not day or synthetic_close <= 0:
            continue
        rows.append(
            {
                "date": day,
                "close": synthetic_close,
                "volume": None,
                "amount": None,
                "_source": "eastmoney_board_fund_flow_daily_close",
            }
        )
    return rows


def _series_source(rows: list[dict]) -> str:
    for row in rows:
        source = str(row.get("_source") or "").strip()
        if source:
            return source
    return "provided_sector_daily_kline"


def _proxy_member_count(rows: list[dict]) -> int | None:
    for row in reversed(rows):
        value = _num(row.get("_proxy_member_count"))
        if value is not None and value > 0:
            return int(value)
    return None


def _default_fetch_benchmark() -> dict | None:
    from app.services.index_daily_client import fetch_index_daily_history

    return fetch_index_daily_history(_BENCHMARK_CODE, trading_days=110)


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


def _return_percent(rows: list[dict], horizon: int) -> float | None:
    if len(rows) < horizon + 1:
        return None
    start = _num(rows[-(horizon + 1)].get("close"))
    end = _num(rows[-1].get("close"))
    if start is None or start <= 0 or end is None or end <= 0:
        return None
    return _pct((end / start - 1.0) * 100.0)


def _aligned_relative_return_percent(
    sector_rows: list[dict],
    benchmark_rows: list[dict],
    *,
    horizon: int,
    as_of_trade_date: str | None,
) -> float | None:
    sector = {
        str(row.get("date") or "")[:10]: _num(row.get("close"))
        for row in sector_rows
        if str(row.get("date") or "")[:10]
    }
    benchmark = {
        str(row.get("date") or "")[:10]: _num(row.get("close"))
        for row in benchmark_rows
        if str(row.get("date") or "")[:10]
        and (
            as_of_trade_date is None
            or str(row.get("date") or "")[:10] <= as_of_trade_date
        )
    }
    common = sorted(
        day
        for day in set(sector) & set(benchmark)
        if sector[day] is not None
        and benchmark[day] is not None
        and (sector[day] or 0) > 0
        and (benchmark[day] or 0) > 0
    )
    if len(common) < horizon + 1:
        return None
    start_day = common[-(horizon + 1)]
    end_day = common[-1]
    sector_return = float(sector[end_day]) / float(sector[start_day]) - 1.0
    benchmark_return = float(benchmark[end_day]) / float(benchmark[start_day]) - 1.0
    return _pct((sector_return - benchmark_return) * 100.0)


def _distance_percent(value: float, reference: float | None) -> float | None:
    if reference is None or reference <= 0:
        return None
    return _pct((value / reference - 1.0) * 100.0)


def _max_drawdown_percent(rows: list[dict]) -> float | None:
    closes = [
        value
        for row in rows
        if (value := _num(row.get("close"))) is not None and value > 0
    ]
    if len(closes) < 2:
        return None
    peak = closes[0]
    drawdown = 0.0
    for value in closes[1:]:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak * 100.0)
    return _pct(drawdown)


def _annualized_volatility_percent(rows: list[dict]) -> float | None:
    closes = [
        value
        for row in rows
        if (value := _num(row.get("close"))) is not None and value > 0
    ]
    if len(closes) < 3:
        return None
    returns = [current / previous - 1.0 for previous, current in zip(closes, closes[1:])]
    return _pct(pstdev(returns) * sqrt(252.0) * 100.0)


def _positive_day_ratio_percent(rows: list[dict]) -> float | None:
    closes = [
        value
        for row in rows
        if (value := _num(row.get("close"))) is not None and value > 0
    ]
    if len(closes) < 2:
        return None
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    return _pct(sum(value > 0 for value in changes) / len(changes) * 100.0)


def _latest_date(rows: list[dict], *, as_of_trade_date: str | None) -> str | None:
    dates = [
        day
        for row in rows
        if (day := str(row.get("date") or "")[:10])
        and (as_of_trade_date is None or day <= as_of_trade_date)
    ]
    return max(dates) if dates else None


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
