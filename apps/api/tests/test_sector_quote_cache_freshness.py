from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.database import _connect
from app.services import sector_quote_cache


def test_any_age_promotion_preserves_original_cache_age() -> None:
    cache_key = "pytest:stale-promotion"
    payload = {"value": 42}
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)

    with sector_quote_cache._MEMORY_LOCK:
        sector_quote_cache._MEMORY.clear()
    sector_quote_cache.save_spot_snapshot(cache_key, payload)
    with _connect() as connection:
        connection.execute(
            "UPDATE sector_spot_cache SET updated_at = ? WHERE cache_key = ?",
            (stale_at.isoformat(), cache_key),
        )
        connection.commit()
    with sector_quote_cache._MEMORY_LOCK:
        sector_quote_cache._MEMORY.clear()

    assert sector_quote_cache.get_spot_snapshot_any_age(cache_key) == payload
    assert sector_quote_cache.get_spot_snapshot(cache_key, ttl_seconds=60) is None


def test_naive_legacy_timestamp_is_interpreted_as_utc() -> None:
    cache_key = "pytest:naive-stale-promotion"
    payload = {"value": 7}
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)

    sector_quote_cache.save_spot_snapshot(cache_key, payload)
    with _connect() as connection:
        connection.execute(
            "UPDATE sector_spot_cache SET updated_at = ? WHERE cache_key = ?",
            (stale_at.replace(tzinfo=None).isoformat(), cache_key),
        )
        connection.commit()
    with sector_quote_cache._MEMORY_LOCK:
        sector_quote_cache._MEMORY.clear()

    assert sector_quote_cache.get_spot_snapshot_any_age(cache_key) == payload
    assert sector_quote_cache.get_spot_snapshot(cache_key, ttl_seconds=60) is None
