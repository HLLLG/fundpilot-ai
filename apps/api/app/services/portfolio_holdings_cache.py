from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock

from app.request_context import get_request_user_id

_MEMORY: OrderedDict[str, tuple[int, float, dict]] = OrderedDict()
_GENERATION = 0
CACHE_TTL_SECONDS = 240.0
_MEMORY_MAX_ENTRIES = 256
_MEMORY_LOCK = RLock()


def bump_holdings_cache_generation() -> None:
    global _GENERATION
    with _MEMORY_LOCK:
        _GENERATION += 1
        _MEMORY.clear()
    from app.services.holding_detail_cache import bump_holding_detail_cache_generation

    bump_holding_detail_cache_generation()


def get_holdings_cache_generation() -> int:
    with _MEMORY_LOCK:
        return _GENERATION


def _cache_key() -> str:
    return f"portfolio:holdings:{get_request_user_id()}"


def get_cached_holdings_response() -> dict | None:
    key = _cache_key()
    now = datetime.now(timezone.utc).timestamp()
    with _MEMORY_LOCK:
        entry = _MEMORY.get(key)
        if entry is None:
            return None
        generation, cached_at_ts, payload = entry
        if generation != _GENERATION or now - cached_at_ts > CACHE_TTL_SECONDS:
            _MEMORY.pop(key, None)
            return None
        _MEMORY.move_to_end(key)
        return payload


def save_cached_holdings_response(payload: dict, *, expected_generation: int | None = None) -> bool:
    key = _cache_key()
    now = datetime.now(timezone.utc).timestamp()
    with _MEMORY_LOCK:
        if expected_generation is not None and expected_generation != _GENERATION:
            return False
        _MEMORY[key] = (_GENERATION, now, payload)
        _MEMORY.move_to_end(key)
        while len(_MEMORY) > _MEMORY_MAX_ENTRIES:
            _MEMORY.popitem(last=False)
        return True
