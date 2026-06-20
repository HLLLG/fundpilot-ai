import logging
import math
import time

import pandas as pd

from app.services.akshare_subprocess import fetch_fund_nav_history

logger = logging.getLogger(__name__)

TTL_HIT = 86400   # 24h — nav published, won't change
TTL_MISS = 300    # 5min — nav not yet published, retry soon

# In-memory cache: key -> (value: float | None, expires_at: float)
_NAV_CACHE: dict[str, tuple[float | None, float]] = {}
_UNIT_NAV_CACHE: dict[str, tuple[float | None, float]] = {}


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

    cached = _NAV_CACHE.get(key)
    if cached is not None:
        value, expires_at = cached
        if now < expires_at:
            return value

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        latest = df.iloc[-1]
        latest_date = str(latest["净值日期"])[:10]
        if latest_date != trade_date:
            _NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        nav_return = float(latest["日增长率"])
        if math.isnan(nav_return):
            _NAV_CACHE[key] = (None, now + TTL_MISS)
            return None
        _NAV_CACHE[key] = (nav_return, now + TTL_HIT)
        return nav_return

    except Exception:
        logger.exception("Failed to fetch official NAV return for %s on %s", fund_code, trade_date)
        _NAV_CACHE[key] = (None, now + TTL_MISS)
        return None


def get_latest_unit_nav(fund_code: str) -> float | None:
    """Return the latest published unit NAV from AkShare."""
    key = f"unit:{fund_code}"
    now = time.monotonic()

    cached = _UNIT_NAV_CACHE.get(key)
    if cached is not None:
        value, expires_at = cached
        if now < expires_at:
            return value

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        unit_nav = float(df.iloc[-1]["单位净值"])
        if math.isnan(unit_nav) or unit_nav <= 0:
            _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        rounded = round(unit_nav, 4)
        _UNIT_NAV_CACHE[key] = (rounded, now + TTL_HIT)
        return rounded
    except Exception:
        logger.exception("Failed to fetch latest unit NAV for %s", fund_code)
        _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
        return None


def get_unit_nav_on_date(fund_code: str, trade_date: str) -> float | None:
    """返回该交易日的官方单位净值（精确匹配净值日期），未发布/不存在返回 None。"""
    if not fund_code or fund_code == "000000" or not trade_date:
        return None

    key = f"unitdate:{fund_code}:{trade_date}"
    now = time.monotonic()

    cached = _UNIT_NAV_CACHE.get(key)
    if cached is not None:
        value, expires_at = cached
        if now < expires_at:
            return value

    try:
        df = _fetch_nav_df(fund_code)
        if df is None or df.empty:
            _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        frame = df.copy()
        frame["_date"] = frame["净值日期"].astype(str).str[:10]
        matches = frame.index[frame["_date"] == trade_date].tolist()
        if not matches:
            _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        unit_nav = float(frame.loc[matches[-1], "单位净值"])
        if math.isnan(unit_nav) or unit_nav <= 0:
            _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
            return None

        rounded = round(unit_nav, 4)
        _UNIT_NAV_CACHE[key] = (rounded, now + TTL_HIT)
        return rounded
    except Exception:
        logger.exception("Failed to fetch unit NAV for %s on %s", fund_code, trade_date)
        _UNIT_NAV_CACHE[key] = (None, now + TTL_MISS)
        return None


def compute_yesterday_profit_from_official_nav(
    fund_code: str,
    holding_amount: float,
    trade_date: str,
) -> float | None:
    """上一交易日官方净值收益额：上一交易日收盘金额 × 上一交易日净值涨跌幅。"""
    if not fund_code or fund_code == "000000" or holding_amount <= 0:
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
        amount_before_latest = holding_amount / (1 + latest_return / 100)
        return round(amount_before_latest * prev_return / 100, 2)
    except Exception:
        logger.exception(
            "Failed to compute yesterday profit from NAV for %s on %s",
            fund_code,
            trade_date,
        )
        return None
