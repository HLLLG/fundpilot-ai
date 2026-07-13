import logging
import math
import time
from collections import OrderedDict
from threading import RLock
from typing import TypeVar

import pandas as pd

from app.services.akshare_subprocess import fetch_fund_daily_nav_returns, fetch_fund_nav_history
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot

logger = logging.getLogger(__name__)

TTL_HIT = 86400   # 24h — nav published, won't change
TTL_MISS = 300    # 5min — nav not yet published, retry soon
_OFFICIAL_NAV_CACHE_VERSION = "v1"
_NAV_CACHE_MAX_ENTRIES = 4096
_UNIT_NAV_CACHE_MAX_ENTRIES = 4096

# In-memory cache: key -> (value: float | None, expires_at: float)
_NAV_CACHE: OrderedDict[str, tuple[float | None, float]] = OrderedDict()
_UNIT_NAV_CACHE: OrderedDict[str, tuple[float | None, float]] = OrderedDict()
_NAV_CACHE_LOCK = RLock()
_UNIT_NAV_CACHE_LOCK = RLock()

_CacheValue = TypeVar("_CacheValue")


def _get_memory_cache(
    cache: OrderedDict[str, tuple[_CacheValue, float]],
    lock: RLock,
    key: str,
    now: float,
) -> tuple[bool, _CacheValue | None]:
    with lock:
        cached = cache.get(key)
        if cached is None:
            return False, None
        value, expires_at = cached
        if now >= expires_at:
            cache.pop(key, None)
            return False, None
        cache.move_to_end(key)
        return True, value


def _set_memory_cache(
    cache: OrderedDict[str, tuple[_CacheValue, float]],
    lock: RLock,
    max_entries: int,
    key: str,
    value: _CacheValue,
    expires_at: float,
) -> None:
    with lock:
        cache[key] = (value, expires_at)
        cache.move_to_end(key)
        while len(cache) > max(1, max_entries):
            cache.popitem(last=False)


def _official_nav_cache_key(fund_code: str, trade_date: str) -> str:
    return f"fund:official-nav:{_OFFICIAL_NAV_CACHE_VERSION}:{fund_code}:{trade_date}"


def _unit_nav_cache_key(fund_code: str) -> str:
    return f"unit:{fund_code}"


def _cache_nav_return(fund_code: str, trade_date: str, value: float | None, ttl: int) -> None:
    now = time.monotonic()
    _set_memory_cache(
        _NAV_CACHE,
        _NAV_CACHE_LOCK,
        _NAV_CACHE_MAX_ENTRIES,
        f"{fund_code}:{trade_date}",
        value,
        now + ttl,
    )
    if value is not None:
        save_spot_snapshot(
            _official_nav_cache_key(fund_code, trade_date),
            {"value": value},
        )


def _cached_persisted_nav_return(fund_code: str, trade_date: str) -> float | None:
    payload = get_spot_snapshot(
        _official_nav_cache_key(fund_code, trade_date),
        ttl_seconds=TTL_HIT,
    )
    if not payload or payload.get("value") is None:
        return None
    try:
        return float(payload["value"])
    except (TypeError, ValueError):
        return None


def get_cached_official_nav_return(fund_code: str, trade_date: str) -> float | None:
    """仅读内存/持久缓存中的官方净值涨跌幅，不触发 AkShare。"""
    key = f"{fund_code}:{trade_date}"
    now = time.monotonic()
    found, value = _get_memory_cache(_NAV_CACHE, _NAV_CACHE_LOCK, key, now)
    if found:
        return value
    persisted = _cached_persisted_nav_return(fund_code, trade_date)
    if persisted is not None:
        _set_memory_cache(
            _NAV_CACHE,
            _NAV_CACHE_LOCK,
            _NAV_CACHE_MAX_ENTRIES,
            key,
            persisted,
            now + TTL_HIT,
        )
    return persisted


def prime_official_nav_cache(
    fund_codes: list[str],
    trade_date: str,
    *,
    cache_only: bool = False,
) -> dict[str, float]:
    """批量预热官方净值涨跌幅/单位净值缓存，避免逐只基金启动 AkShare 子进程。

    ``cache_only=True`` 时仅读取已有缓存，不触发 AkShare 拉取（冷启动快照 / OCR 快速确认路径）。
    """
    codes = sorted(
        {
            str(code).strip().zfill(6)
            for code in fund_codes
            if str(code).strip() and str(code).strip() != "000000"
        }
    )
    if not codes or not trade_date:
        return {}

    resolved: dict[str, float] = {}
    missing: list[str] = []
    for code in codes:
        cached = get_cached_official_nav_return(code, trade_date)
        if cached is not None:
            resolved[code] = cached
            continue
        missing.append(code)

    if not missing or cache_only:
        return resolved

    payload = fetch_fund_daily_nav_returns(missing, trade_date)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        for code in missing:
            _cache_nav_return(code, trade_date, None, TTL_MISS)
        return resolved

    for code in missing:
        row = rows.get(code)
        if not isinstance(row, dict):
            _cache_nav_return(code, trade_date, None, TTL_MISS)
            continue
        daily_growth = row.get("daily_growth")
        unit_nav = row.get("unit_nav")
        if unit_nav is not None:
            try:
                unit_value = round(float(unit_nav), 4)
                if unit_value > 0:
                    _cache_unit_nav(code, unit_value)
            except (TypeError, ValueError):
                pass
        try:
            nav_return = float(daily_growth)
        except (TypeError, ValueError):
            _cache_nav_return(code, trade_date, None, TTL_MISS)
            continue
        if math.isnan(nav_return):
            _cache_nav_return(code, trade_date, None, TTL_MISS)
            continue
        _cache_nav_return(code, trade_date, nav_return, TTL_HIT)
        resolved[code] = nav_return
    return resolved


def _fetch_nav_df(fund_code: str) -> pd.DataFrame:
    """经子进程拉取净值，避免与 PaddleOCR 同进程加载 py_mini_racer 导致 crash。"""
    payload = fetch_fund_nav_history(fund_code, trading_days=120)
    if payload is None or not payload.get("data"):
        return pd.DataFrame()

    rows = payload["data"]
    return pd.DataFrame(
        {
            "净值日期": [row.get("date") for row in rows],
            "单位净值": [row.get("nav") for row in rows],
            "日增长率": [row.get("daily_growth") for row in rows],
        }
    )


def get_official_nav_return(fund_code: str, trade_date: str) -> float | None:
    """Return official T-day NAV growth rate (%) or None if not yet published."""
    key = f"{fund_code}:{trade_date}"
    now = time.monotonic()

    found, value = _get_memory_cache(_NAV_CACHE, _NAV_CACHE_LOCK, key, now)
    if found:
        return value

    persisted = get_cached_official_nav_return(fund_code, trade_date)
    if persisted is not None:
        return persisted

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _cache_nav_return(fund_code, trade_date, None, TTL_MISS)
            return None

        latest = df.iloc[-1]
        latest_date = str(latest["净值日期"])[:10]
        if latest_date != trade_date:
            _cache_nav_return(fund_code, trade_date, None, TTL_MISS)
            return None

        nav_return = float(latest["日增长率"])
        if math.isnan(nav_return):
            _cache_nav_return(fund_code, trade_date, None, TTL_MISS)
            return None
        unit_nav = float(latest["单位净值"])
        if not math.isnan(unit_nav) and unit_nav > 0:
            _cache_unit_nav(fund_code, round(unit_nav, 4))
        _cache_nav_return(fund_code, trade_date, nav_return, TTL_HIT)
        return nav_return

    except Exception:
        logger.exception("Failed to fetch official NAV return for %s on %s", fund_code, trade_date)
        _cache_nav_return(fund_code, trade_date, None, TTL_MISS)
        return None


def _unit_nav_persist_key(fund_code: str) -> str:
    return f"fund:unit-nav:v1:{fund_code}"


def _cache_unit_nav_memory(
    key: str,
    value: float | None,
    ttl: int,
    *,
    now: float | None = None,
) -> None:
    cached_at = time.monotonic() if now is None else now
    _set_memory_cache(
        _UNIT_NAV_CACHE,
        _UNIT_NAV_CACHE_LOCK,
        _UNIT_NAV_CACHE_MAX_ENTRIES,
        key,
        value,
        cached_at + ttl,
    )


def _cache_unit_nav(fund_code: str, value: float) -> None:
    _cache_unit_nav_memory(_unit_nav_cache_key(fund_code), value, TTL_HIT)
    save_spot_snapshot(_unit_nav_persist_key(fund_code), {"value": value})


def peek_cached_unit_nav(fund_code: str) -> float | None:
    """仅读内存/持久缓存中的最近单位净值，不触发网络/子进程。"""
    key = _unit_nav_cache_key(fund_code)
    now = time.monotonic()
    found, value = _get_memory_cache(_UNIT_NAV_CACHE, _UNIT_NAV_CACHE_LOCK, key, now)
    if found:
        return value
    persisted = _persisted_unit_nav(fund_code)
    if persisted is not None:
        _cache_unit_nav_memory(key, persisted, TTL_HIT, now=now)
    return persisted


def _persisted_unit_nav(fund_code: str) -> float | None:
    payload = get_spot_snapshot(_unit_nav_persist_key(fund_code), ttl_seconds=TTL_HIT)
    if not payload or payload.get("value") is None:
        return None
    try:
        return round(float(payload["value"]), 4)
    except (TypeError, ValueError):
        return None


def get_latest_unit_nav(fund_code: str, *, allow_fetch: bool = True) -> float | None:
    """Return the latest published unit NAV from AkShare."""
    key = _unit_nav_cache_key(fund_code)
    now = time.monotonic()

    found, value = _get_memory_cache(_UNIT_NAV_CACHE, _UNIT_NAV_CACHE_LOCK, key, now)
    if found:
        return value

    if not allow_fetch:
        return _persisted_unit_nav(fund_code)

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
            return None

        unit_nav = float(df.iloc[-1]["单位净值"])
        if math.isnan(unit_nav) or unit_nav <= 0:
            _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
            return None

        rounded = round(unit_nav, 4)
        _cache_unit_nav(fund_code, rounded)
        return rounded
    except Exception:
        logger.exception("Failed to fetch latest unit NAV for %s", fund_code)
        _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
        return None


def get_unit_nav_on_date(fund_code: str, trade_date: str) -> float | None:
    """返回该交易日的官方单位净值（精确匹配净值日期），未发布/不存在返回 None。"""
    if not fund_code or fund_code == "000000" or not trade_date:
        return None

    key = f"unitdate:{fund_code}:{trade_date}"
    now = time.monotonic()

    found, value = _get_memory_cache(_UNIT_NAV_CACHE, _UNIT_NAV_CACHE_LOCK, key, now)
    if found:
        return value

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
            return None

        frame = df.copy()
        frame["_date"] = frame["净值日期"].astype(str).str[:10]
        matches = frame.index[frame["_date"] == trade_date].tolist()
        if not matches:
            _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
            return None

        unit_nav = float(frame.loc[matches[-1], "单位净值"])
        if math.isnan(unit_nav) or unit_nav <= 0:
            _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
            return None

        rounded = round(unit_nav, 4)
        _cache_unit_nav_memory(key, rounded, TTL_HIT, now=now)
        return rounded
    except Exception:
        logger.exception("Failed to fetch unit NAV for %s on %s", fund_code, trade_date)
        _cache_unit_nav_memory(key, None, TTL_MISS, now=now)
        return None


def get_yesterday_profit_nav_returns(
    fund_code: str,
    trade_date: str,
) -> tuple[float, float] | None:
    """返回计算昨日收益所需的当日、前一日官方净值涨跌幅。"""
    if not fund_code or fund_code == "000000":
        return None
    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            return None
        frame = df.copy()
        frame["_date"] = frame["净值日期"].astype(str).str[:10]
        matches = frame.index[frame["_date"] == trade_date].tolist()
        if not matches:
            return None
        latest_idx = matches[-1]
        pos = frame.index.get_loc(latest_idx)
        if isinstance(pos, slice):
            pos = pos.start or 0
        if pos < 1:
            return None
        latest_row = frame.iloc[pos]
        prev_row = frame.iloc[pos - 1]
        latest_return = float(latest_row["日增长率"])
        prev_return = float(prev_row["日增长率"])
        if math.isnan(latest_return) or math.isnan(prev_return):
            return None
        return latest_return, prev_return
    except Exception:
        logger.exception(
            "Failed to load yesterday profit NAV inputs for %s on %s",
            fund_code,
            trade_date,
        )
        return None


def compute_yesterday_profit_from_nav_returns(
    holding_amount: float,
    nav_returns: tuple[float, float] | None,
) -> float | None:
    if holding_amount <= 0 or nav_returns is None:
        return None
    latest_return, prev_return = nav_returns
    denominator = 1 + latest_return / 100
    if denominator == 0:
        return None
    amount_before_latest = holding_amount / denominator
    return round(amount_before_latest * prev_return / 100, 2)


def compute_yesterday_profit_from_official_nav(
    fund_code: str,
    holding_amount: float,
    trade_date: str,
) -> float | None:
    """上一交易日官方净值收益额：上一交易日收盘金额 × 上一交易日净值涨跌幅。"""
    if not fund_code or fund_code == "000000" or holding_amount <= 0:
        return None
    return compute_yesterday_profit_from_nav_returns(
        holding_amount,
        get_yesterday_profit_nav_returns(fund_code, trade_date),
    )
