from __future__ import annotations

from datetime import datetime, timezone

from app.request_context import get_request_user_id

_MEMORY: dict[str, tuple[int, float, dict]] = {}
_GENERATION = 0
CACHE_TTL_SECONDS = 240.0


def bump_holdings_cache_generation() -> None:
    global _GENERATION
    _GENERATION += 1
    from app.services.holding_detail_cache import bump_holding_detail_cache_generation

    bump_holding_detail_cache_generation()


def get_holdings_cache_generation() -> int:
    return _GENERATION


def _cache_key() -> str:
    return f"portfolio:holdings:{get_request_user_id()}"


def get_cached_holdings_response() -> dict | None:
    key = _cache_key()
    entry = _MEMORY.get(key)
    if entry is None:
        return None
    generation, cached_at_ts, payload = entry
    if generation != _GENERATION:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if now - cached_at_ts > CACHE_TTL_SECONDS:
        return None
    return payload


def save_cached_holdings_response(payload: dict, *, expected_generation: int | None = None) -> bool:
    if expected_generation is not None and expected_generation != _GENERATION:
        return False
    key = _cache_key()
    _MEMORY[key] = (_GENERATION, datetime.now(timezone.utc).timestamp(), payload)
    return True
