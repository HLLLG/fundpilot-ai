from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.sector_canonical import (
    get_canonical_sector,
    get_quote_canonical_sector,
    list_discovery_sector_labels,
)
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import build_trading_session

_HEAT_LIVE_TTL_SECONDS = 60.0
_HEAT_CLOSED_TTL_SECONDS = 3600.0
_DEFAULT_NETWORK_TIMEOUT = 12.0
_UI_NETWORK_TIMEOUT = 4.0
_UI_BUDGET_SECONDS = 12.0


def fallback_sector_heat_rows() -> list[dict]:
    """无网络数据时仍返回全部关注方向标签，供前端展示可选 chips。"""
    return [
        {
            "sector_label": label,
            "change_1d_percent": None,
            "change_5d_percent": None,
            "heat_score": None,
        }
        for label in list_discovery_sector_labels()
    ]


def _merge_sector_heat_rows(partial: list[dict]) -> list[dict]:
    by_label = {row["sector_label"]: row for row in partial}
    merged: list[dict] = []
    for label in list_discovery_sector_labels():
        merged.append(
            by_label.get(label)
            or {
                "sector_label": label,
                "change_1d_percent": None,
                "change_5d_percent": None,
                "heat_score": None,
            }
        )
    return merged


def build_sector_heat_ranking(
    *,
    fetch_canon_series=None,
    force_refresh: bool = False,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
    budget_seconds: float | None = None,
) -> list[dict]:
    """canonical 板块按当日涨跌 + 近5日涨跌综合排序（降序）；结果按交易日缓存。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_ttl = (
        _HEAT_LIVE_TTL_SECONDS
        if session_kind in {"trading_day_intraday", "trading_day_pre_close"}
        else _HEAT_CLOSED_TTL_SECONDS
    )
    cache_key = f"discovery:sector_heat:v1:{trade_date}"

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached and cached.get("sectors"):
            return list(cached["sectors"])

    rows = _build_sector_heat_rows(
        trade_date=trade_date,
        fetch_canon_series=fetch_canon_series,
        lightweight=lightweight,
        network_timeout=network_timeout,
        budget_seconds=budget_seconds,
    )
    merged = _merge_sector_heat_rows(rows) if rows else fallback_sector_heat_rows()
    if rows:
        save_spot_snapshot(
            cache_key,
            {"sectors": merged, "trade_date": trade_date, "session_kind": session_kind},
        )
    return merged


def build_sector_heat_ranking_for_ui() -> list[dict]:
    """推荐基金 Tab 关注方向：限时轻量拉取，超时仍返回全部板块标签。"""
    return build_sector_heat_ranking(
        lightweight=True,
        network_timeout=_UI_NETWORK_TIMEOUT,
        budget_seconds=_UI_BUDGET_SECONDS,
    )


def _build_sector_heat_rows(
    *,
    trade_date: str | None,
    fetch_canon_series,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
    budget_seconds: float | None = None,
) -> list[dict]:
    labels = list_discovery_sector_labels()
    rows: list[dict] = []
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    series_fetcher = fetch_canon_series or _default_fetch_canon_series

    with ThreadPoolExecutor(max_workers=min(6, max(len(labels), 1))) as executor:
        futures = [
            executor.submit(
                _sector_heat_row,
                label,
                trade_date,
                series_fetcher,
                lightweight,
                network_timeout,
            )
            for label in labels
        ]
        for future in as_completed(futures):
            if deadline is not None and time.monotonic() >= deadline:
                break
            row = future.result()
            if row is not None:
                rows.append(row)

    rows.sort(
        key=lambda item: (
            item["heat_score"] if item["heat_score"] is not None else -999,
            item["change_1d_percent"] if item["change_1d_percent"] is not None else -999,
        ),
        reverse=True,
    )
    return rows


def _default_fetch_canon_series(
    canon,
    *,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
) -> list[dict]:
    return fetch_canonical_daily_kline_series(
        canon,
        max_days=8 if lightweight else 12,
        timeout=network_timeout,
    )


def _sector_heat_row(
    label: str,
    trade_date: str | None,
    fetch_canon_series,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
) -> dict | None:
    canon = get_quote_canonical_sector(label) or get_canonical_sector(label)
    if canon is None:
        return None
    series = fetch_canon_series(
        canon,
        lightweight=lightweight,
        network_timeout=network_timeout,
    )
    change_1d = _latest_change_percent(series, trade_date)
    change_5d = None
    if not lightweight:
        change_5d = _rolling_change_percent(series, days=5)
    return {
        "sector_label": label,
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "heat_score": _heat_score(change_1d, change_5d),
    }


def _heat_score(change_1d: float | None, change_5d: float | None) -> float | None:
    if change_1d is None and change_5d is None:
        return None
    one_day = change_1d if change_1d is not None else change_5d or 0.0
    five_day = change_5d if change_5d is not None else one_day
    return round(one_day * 0.6 + five_day * 0.4, 2)


def _latest_change_percent(series: list[dict], trade_date: str | None) -> float | None:
    if not series:
        return None
    if trade_date:
        for bar in reversed(series):
            if str(bar.get("date", ""))[:10] == str(trade_date)[:10]:
                value = bar.get("change_percent")
                return _as_float(value)
    value = series[-1].get("change_percent")
    return _as_float(value)


def _rolling_change_percent(series: list[dict], *, days: int) -> float | None:
    if len(series) < 2:
        return None
    tail = series[-min(len(series), days + 1) :]
    start = _as_float(tail[0].get("change_percent"))
    total = 0.0
    count = 0
    for bar in tail[1:]:
        value = _as_float(bar.get("change_percent"))
        if value is not None:
            total += value
            count += 1
    if count == 0:
        return start
    return round(total, 2)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
