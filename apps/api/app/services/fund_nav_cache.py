"""基金净值历史：全用户共享内存缓存（按 fund_code + trading_days）。"""

from __future__ import annotations

from app.models import FundNavHistory
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import build_trading_session

_CACHE_VERSION = "v1"
_LIVE_TTL_SECONDS = 900.0
_CLOSED_TTL_SECONDS = 3600.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}


def _cache_ttl_seconds() -> float:
    session_kind = str(build_trading_session().get("session_kind") or "")
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def nav_cache_key(fund_code: str, trading_days: int) -> str:
    return f"fund:nav:{_CACHE_VERSION}:{fund_code}:{trading_days}"


def get_cached_fund_nav(fund_code: str, trading_days: int) -> FundNavHistory | None:
    payload = get_spot_snapshot(
        nav_cache_key(fund_code, trading_days),
        ttl_seconds=_cache_ttl_seconds(),
    )
    if not payload:
        return None
    try:
        return FundNavHistory.model_validate(payload)
    except Exception:
        return None


def save_cached_fund_nav(
    fund_code: str,
    trading_days: int,
    history: FundNavHistory,
) -> None:
    if history.source != "akshare" or not history.points:
        return
    save_spot_snapshot(
        nav_cache_key(fund_code, trading_days),
        history.model_dump(mode="json"),
    )


def warm_fund_nav(
    fund_code: str,
    fund_name: str = "",
    *,
    trading_days: int = 252,
) -> bool:
    """Best-effort 预热单只基金净值缓存。"""
    if not fund_code or fund_code == "000000":
        return False
    if get_cached_fund_nav(fund_code, trading_days) is not None:
        return True
    from app.services.fund_data import FundDataService

    history = FundDataService().get_nav_history(
        fund_code,
        fund_name,
        trading_days=trading_days,
    )
    return history.source == "akshare" and bool(history.points)
