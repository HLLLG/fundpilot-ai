from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.eastmoney_trends_client import (
    fetch_eastmoney_daily_kline_series,
    fetch_eastmoney_kline_close_percent,
)
from app.services.sector_canonical import get_canonical_sector, list_canonical_sector_labels
from app.services.trading_session import build_trading_session


def build_sector_heat_ranking(
    *,
    fetch_close_percent=fetch_eastmoney_kline_close_percent,
    fetch_series=fetch_eastmoney_daily_kline_series,
) -> list[dict]:
    """canonical 板块按当日涨跌 + 近5日涨跌综合排序（降序）。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    labels = list_canonical_sector_labels()
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=min(6, max(len(labels), 1))) as executor:
        futures = [
            executor.submit(
                _sector_heat_row,
                label,
                trade_date,
                fetch_close_percent,
                fetch_series,
            )
            for label in labels
        ]
        for future in as_completed(futures):
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


def _sector_heat_row(
    label: str,
    trade_date: str | None,
    fetch_close_percent,
    fetch_series,
) -> dict | None:
    canon = get_canonical_sector(label)
    if canon is None:
        return None
    change_1d = fetch_close_percent(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        trade_date=trade_date,
    )
    series = fetch_series(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        max_days=12,
    )
    change_5d = _rolling_change_percent(series, days=5)
    if change_1d is None:
        change_1d = _latest_change_percent(series, trade_date)
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
