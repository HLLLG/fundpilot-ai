"""基金诊断信息（类型/管理费/规模/1年收益）全用户共享缓存。"""

from __future__ import annotations

from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import build_trading_session

_CACHE_VERSION = "v1"
_LIVE_TTL_SECONDS = 3600.0
_CLOSED_TTL_SECONDS = 86400.0
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


def diagnostics_cache_key(fund_code: str) -> str:
    return f"fund:diagnostics:{_CACHE_VERSION}:{fund_code}"


def get_cached_fund_diagnostics(fund_code: str) -> dict | None:
    payload = get_spot_snapshot(
        diagnostics_cache_key(fund_code),
        ttl_seconds=_cache_ttl_seconds(),
    )
    if isinstance(payload, dict) and payload:
        return payload
    return None


def save_cached_fund_diagnostics(fund_code: str, diagnostics: dict) -> None:
    if not diagnostics:
        return
    save_spot_snapshot(diagnostics_cache_key(fund_code), diagnostics)


def load_fund_diagnostics(fund_code: str) -> dict:
    """cache-aside：AkShare 基金概况 + 累计收益率。"""
    cached = get_cached_fund_diagnostics(fund_code)
    if cached is not None:
        return dict(cached)

    diagnostics = _fetch_fund_diagnostics_via_akshare(fund_code)
    if diagnostics:
        save_cached_fund_diagnostics(fund_code, diagnostics)
    return diagnostics


def _fetch_fund_diagnostics_via_akshare(fund_code: str) -> dict:
    from app.services.fund_data import _load_fund_diagnostics

    try:
        import akshare as ak  # type: ignore[import-not-found]

        return _load_fund_diagnostics(ak, fund_code)
    except Exception:
        return {}
