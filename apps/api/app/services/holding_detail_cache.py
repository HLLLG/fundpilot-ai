from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock
from typing import TypeAlias

from app.config import get_settings
from app.request_context import get_request_user_id

_CacheEntry: TypeAlias = tuple[int, float, dict]

# A detail payload can include NAV history and intraday data, so keep this cache
# deliberately bounded even when a long-running worker serves many users.
_MAX_ENTRIES = 512
_MEMORY: OrderedDict[str, _CacheEntry] = OrderedDict()
_LOCK = RLock()
_GENERATION = 0


def _cache_ttl_seconds() -> float:
    return float(get_settings().holding_detail_cache_ttl_seconds)


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def bump_holding_detail_cache_generation() -> None:
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        # Entries from an older generation can never be read again. Releasing
        # them here prevents every invalidation from retaining unreachable data.
        _MEMORY.clear()


def _cache_key(fund_code: str, fingerprint: str) -> str:
    return f"holding:detail:{get_request_user_id()}:{fund_code}:{fingerprint}"


def holding_detail_fingerprint(*, fund_code: str, holding_amount: float) -> str:
    return f"{fund_code}:{round(float(holding_amount), 2)}"


def get_cached_holding_detail(fund_code: str, fingerprint: str) -> dict | None:
    key = _cache_key(fund_code, fingerprint)
    now = _now_timestamp()
    ttl_seconds = _cache_ttl_seconds()
    with _LOCK:
        entry = _MEMORY.get(key)
        if entry is None:
            return None
        generation, cached_at_ts, payload = entry
        if generation != _GENERATION or now - cached_at_ts > ttl_seconds:
            # Invalid entries should not continue occupying the bounded cache.
            _MEMORY.pop(key, None)
            return None
        _MEMORY.move_to_end(key)
        return payload


def save_cached_holding_detail(fund_code: str, fingerprint: str, payload: dict) -> None:
    key = _cache_key(fund_code, fingerprint)
    with _LOCK:
        if _MAX_ENTRIES <= 0:
            _MEMORY.clear()
            return
        _MEMORY[key] = (_GENERATION, _now_timestamp(), payload)
        _MEMORY.move_to_end(key)
        while len(_MEMORY) > _MAX_ENTRIES:
            _MEMORY.popitem(last=False)


def invalidate_holding_detail_cache_for_user() -> None:
    bump_holding_detail_cache_generation()
