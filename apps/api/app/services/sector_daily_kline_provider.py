from __future__ import annotations

import logging

from app.services.akshare_subprocess import (
    fetch_board_daily_kline_series,
    fetch_index_daily_history as fetch_index_daily_via_akshare,
)
from app.services.index_daily_client import fetch_index_daily_history as fetch_index_daily_via_sina
from app.services.eastmoney_trends_client import (
    DailyKlineBar,
    fetch_eastmoney_daily_kline_series,
)
from app.services.sector_canonical import CanonicalSector
from app.services.sector_quote_relay_provider import fetch_daily_kline_via_relay

logger = logging.getLogger(__name__)


def fetch_canonical_daily_kline_series(
    canon: CanonicalSector,
    *,
    max_days: int = 20,
    timeout: float = 4.0,
    allow_akshare: bool = True,
) -> list[DailyKlineBar]:
    """板块日 K：东财 → sector-relay → AkShare（与板块信号回测/分时图兜底同源）。

    ``allow_akshare=False`` 时跳过 AkShare 子进程兜底（每板块一次子进程，主题板块
    批量刷新 ~100 板块时会非常慢），只走快速 HTTP 源（东财 push2delay + relay + 新浪指数）。
    """
    days = max(8, min(max_days, 400))

    if allow_akshare and canon.source_type == "index" and canon.source_code:
        index_hist = fetch_index_daily_via_akshare(canon.source_code, trading_days=days + 5)
        if index_hist:
            converted = _index_history_to_daily_bars(index_hist, max_days=days)
            if converted:
                logger.debug(
                    "canonical daily kline via akshare index for %s",
                    canon.label,
                )
                return converted

    series = fetch_eastmoney_daily_kline_series(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        max_days=days,
        timeout=timeout,
        max_retries=1,
    )
    if series:
        return series

    relay_series = fetch_daily_kline_via_relay(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        max_days=days,
        timeout_seconds=max(timeout * 2, 8.0),
    )
    if relay_series:
        return relay_series

    if allow_akshare and canon.source_type in {"concept", "industry"}:
        fallback = fetch_board_daily_kline_series(
            canon.source_type,
            canon.source_name,
            source_code=canon.source_code,
            max_days=days,
        )
        if fallback:
            return fallback

    if canon.source_type == "index" and canon.source_code:
        sina_hist = fetch_index_daily_via_sina(canon.source_code, trading_days=days + 5)
        if sina_hist:
            converted = _index_history_to_daily_bars(sina_hist, max_days=days)
            if converted:
                logger.debug(
                    "canonical daily kline via sina index for %s",
                    canon.label,
                )
                return converted

        if allow_akshare:
            index_hist = fetch_index_daily_via_akshare(canon.source_code, trading_days=days + 5)
            if index_hist:
                converted = _index_history_to_daily_bars(index_hist, max_days=days)
                if converted:
                    logger.debug(
                        "canonical daily kline via akshare index for %s",
                        canon.label,
                    )
                    return converted

    return []


def _index_history_to_daily_bars(
    index_hist: dict,
    *,
    max_days: int,
) -> list[DailyKlineBar]:
    rows = index_hist.get("data") or []
    bars: list[DailyKlineBar] = []
    prior_close: float | None = None
    for row in rows:
        day = str(row.get("date", ""))[:10]
        close = _as_float(row.get("close"))
        if not day or close is None or close <= 0:
            continue
        if prior_close is None or prior_close <= 0:
            prior_close = close
            continue
        change = round((close / prior_close - 1) * 100, 4)
        bars.append(
            {
                "date": day,
                "change_percent": change,
                "high_change_percent": None,
                "close": close,
            }
        )
        prior_close = close

    if len(bars) > max_days:
        bars = bars[-max_days:]
    return bars


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
