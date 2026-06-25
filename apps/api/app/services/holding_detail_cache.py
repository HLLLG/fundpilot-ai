from __future__ import annotations

from datetime import datetime, timezone

from app.config import get_settings
from app.request_context import get_request_user_id

_MEMORY: dict[str, tuple[int, float, dict]] = {}
_GENERATION = 0


def _cache_ttl_seconds() -> float:
    return float(get_settings().holding_detail_cache_ttl_seconds)


def bump_holding_detail_cache_generation() -> None:
    global _GENERATION
    _GENERATION += 1


def _cache_key(fund_code: str, fingerprint: str) -> str:
    return f"holding:detail:{get_request_user_id()}:{fund_code}:{fingerprint}"


def holding_detail_fingerprint(*, fund_code: str, holding_amount: float) -> str:
    return f"{fund_code}:{round(float(holding_amount), 2)}"


def get_cached_holding_detail(fund_code: str, fingerprint: str) -> dict | None:
    key = _cache_key(fund_code, fingerprint)
    entry = _MEMORY.get(key)
    if entry is None:
        return None
    generation, cached_at_ts, payload = entry
    if generation != _GENERATION:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if now - cached_at_ts > _cache_ttl_seconds():
        return None
    return payload


def save_cached_holding_detail(fund_code: str, fingerprint: str, payload: dict) -> None:
    key = _cache_key(fund_code, fingerprint)
    _MEMORY[key] = (
        _GENERATION,
        datetime.now(timezone.utc).timestamp(),
        payload,
    )


def invalidate_holding_detail_cache_for_user() -> None:
    bump_holding_detail_cache_generation()
