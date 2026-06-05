import math
import time
import logging
import pandas as pd

logger = logging.getLogger(__name__)

TTL_HIT = 86400   # 24h — nav published, won't change
TTL_MISS = 300    # 5min — nav not yet published, retry soon

# In-memory cache: key -> (value: float | None, expires_at: float)
_NAV_CACHE: dict[str, tuple[float | None, float]] = {}


def _fetch_nav_df(fund_code: str) -> pd.DataFrame:
    import akshare as ak
    return ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")


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
