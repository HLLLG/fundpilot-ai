"""开放式基金排行榜：全用户共享缓存（因子分横截面）。"""

from __future__ import annotations

from app.services.cache_policy import jittered_ttl
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

_CACHE_VERSION = "v1"
_LIVE_TTL_SECONDS = 3600.0
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


def rank_cache_key(limit: int) -> str:
    cap = max(50, min(int(limit), 500))
    return f"fund:open_rank:{_CACHE_VERSION}:{cap}"


def get_cached_open_fund_rank(*, limit: int = 300) -> list[dict] | None:
    key = rank_cache_key(limit)
    payload = get_spot_snapshot(
        key,
        ttl_seconds=jittered_ttl(key, _cache_ttl_seconds()),
    )
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return rows
    return None


def save_cached_open_fund_rank(*, limit: int, rows: list[dict]) -> None:
    if not rows:
        return
    save_spot_snapshot(rank_cache_key(limit), {"rows": rows})


def fetch_open_fund_rank_cached(*, limit: int = 300) -> list[dict] | None:
    cached = get_cached_open_fund_rank(limit=limit)
    if cached is not None:
        return cached
    key = rank_cache_key(limit)
    try:
        with cross_process_lock(
            f"fund-rank-refresh:{key}",
            timeout_seconds=3.0,
        ):
            cached = get_cached_open_fund_rank(limit=limit)
            if cached is not None:
                return cached
            from app.services.akshare_subprocess import fetch_open_fund_rank

            rows = fetch_open_fund_rank(limit=limit)
            if rows:
                save_cached_open_fund_rank(limit=limit, rows=rows)
            return rows
    except CrossProcessLockError:
        payload = get_spot_snapshot_any_age(key)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return payload["rows"]
        return None
