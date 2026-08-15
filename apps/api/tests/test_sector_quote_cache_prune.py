"""持久缓存只在读取时看 TTL，过期行必须按 updated_at / 废弃前缀真正删掉。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import _connect, prune_expired_ocr_text_cache
from app.services import news_cache
from app.services import sector_quote_cache as cache


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_prune_state():
    cache._MEMORY.clear()
    cache._MEMORY_REVALIDATED_AT.clear()
    cache._last_prune_monotonic = 0.0
    yield
    cache._MEMORY.clear()
    cache._MEMORY_REVALIDATED_AT.clear()
    cache._last_prune_monotonic = 0.0


def _insert_spot(key: str, updated_at: str) -> None:
    with _connect() as connection:
        cache._ensure_cache_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO sector_spot_cache (cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, "{}", updated_at),
        )
        connection.commit()


def _spot_keys() -> set[str]:
    with _connect() as connection:
        rows = connection.execute("SELECT cache_key FROM sector_spot_cache").fetchall()
    return {str(row["cache_key"]) for row in rows}


def _insert_news(key: str, updated_at: str) -> None:
    with _connect() as connection:
        news_cache._ensure_cache_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO news_cache (cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, "[]", updated_at),
        )
        connection.commit()


def _news_keys() -> set[str]:
    with _connect() as connection:
        rows = connection.execute("SELECT cache_key FROM news_cache").fetchall()
    return {str(row["cache_key"]) for row in rows}


def _insert_ocr(*, cache_key: str, updated_at: str, user_id: int = 1) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO ocr_text_cache (userId, cache_key, raw_text, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, cache_key, "ocr", updated_at),
        )
        connection.commit()


def _ocr_keys() -> set[str]:
    with _connect() as connection:
        rows = connection.execute("SELECT cache_key FROM ocr_text_cache").fetchall()
    return {str(row["cache_key"]) for row in rows}


def test_prune_keeps_fresh_spot_rows_and_drops_old_ones() -> None:
    fresh_at = NOW.isoformat()
    stale_at = (NOW - timedelta(days=20)).isoformat()
    _insert_spot("fund:nav:v2:011373", fresh_at)
    _insert_spot("intraday:v6:index:518880:2026-07-01", stale_at)

    removed = cache.prune_expired_spot_snapshots(retention_days=14, now=NOW)

    assert removed >= 1
    assert _spot_keys() == {"fund:nav:v2:011373"}


def test_prune_deletes_obsolete_version_prefix_even_if_fresh() -> None:
    fresh_at = NOW.isoformat()
    _insert_spot("intraday:v5:index:518880:2026-08-14", fresh_at)
    _insert_spot("intraday:v6:index:518880:2026-08-14", fresh_at)
    cache._save_memory_snapshot("intraday:v5:index:518880:2026-08-14", NOW.timestamp(), {})
    cache._save_memory_snapshot("intraday:v6:index:518880:2026-08-14", NOW.timestamp(), {})

    cache.prune_expired_spot_snapshots(retention_days=14, now=NOW)

    assert _spot_keys() == {"intraday:v6:index:518880:2026-08-14"}
    assert "intraday:v5:index:518880:2026-08-14" not in cache._MEMORY
    assert "intraday:v6:index:518880:2026-08-14" in cache._MEMORY


def test_prune_keeps_fresh_nav_v1_fallback_rows() -> None:
    fresh_at = NOW.isoformat()
    _insert_spot("fund:nav:v1:011373", fresh_at)
    _insert_spot("board-flow-hist:v1:BK0475", fresh_at)

    cache.prune_expired_spot_snapshots(retention_days=14, now=NOW)

    assert _spot_keys() == {"fund:nav:v1:011373", "board-flow-hist:v1:BK0475"}


def test_prune_expired_news_cache_drops_old_daily_keys() -> None:
    _insert_news("a-share:2026-08-14", NOW.isoformat())
    _insert_news("a-share:2026-07-01", (NOW - timedelta(days=20)).isoformat())

    removed = news_cache.prune_expired_news_cache(retention_days=14, now=NOW)

    assert removed == 1
    assert _news_keys() == {"a-share:2026-08-14"}


def test_prune_expired_ocr_text_cache_drops_old_hashes() -> None:
    _insert_ocr(cache_key="fresh-hash", updated_at="2026-08-14 10:00:00")
    _insert_ocr(cache_key="stale-hash", updated_at="2026-07-01 10:00:00")

    removed = prune_expired_ocr_text_cache(retention_days=14, now=NOW)

    assert removed == 1
    assert _ocr_keys() == {"fresh-hash"}


def test_prune_durable_caches_covers_spot_news_and_ocr() -> None:
    stale_at = (NOW - timedelta(days=20)).isoformat()
    _insert_spot("intraday:v6:index:518880:2026-07-01", stale_at)
    _insert_news("a-share:2026-07-01", stale_at)
    _insert_ocr(cache_key="stale-hash", updated_at="2026-07-01 10:00:00")

    removed = cache.prune_durable_caches(retention_days=14, now=NOW)

    assert removed["spot"] >= 1
    assert removed["news"] == 1
    assert removed["ocr"] == 1
    assert _spot_keys() == set()
    assert _news_keys() == set()
    assert _ocr_keys() == set()


def test_maybe_prune_throttles_within_interval(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cache, "prune_durable_caches", lambda: calls.append(1) or {"spot": 0, "news": 0, "ocr": 0})

    assert cache.maybe_prune_durable_caches() == {"spot": 0, "news": 0, "ocr": 0}
    assert cache.maybe_prune_durable_caches() is None
    assert calls == [1]
