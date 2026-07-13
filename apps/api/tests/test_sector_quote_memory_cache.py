from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import sector_quote_cache as cache


@pytest.fixture(autouse=True)
def _clear_memory() -> None:
    with cache._MEMORY_LOCK:
        cache._MEMORY.clear()
    yield
    with cache._MEMORY_LOCK:
        cache._MEMORY.clear()


def test_memory_snapshot_is_lru_bounded(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_MEMORY_MAX_ENTRIES", 2)
    cache._save_memory_snapshot("a", 100.0, {"key": "a"})
    cache._save_memory_snapshot("b", 100.0, {"key": "b"})
    assert cache._get_memory_snapshot("a", 100.0, ttl_seconds=None) == (
        True,
        {"key": "a"},
    )
    cache._save_memory_snapshot("c", 100.0, {"key": "c"})

    assert list(cache._MEMORY) == ["a", "c"]


def test_memory_snapshot_removes_expired_entry() -> None:
    cache._save_memory_snapshot("expired", 100.0, {"value": 1})

    assert cache._get_memory_snapshot("expired", 102.0, ttl_seconds=1.0) == (
        False,
        None,
    )
    assert "expired" not in cache._MEMORY


def test_concurrent_memory_writes_remain_bounded(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_MEMORY_MAX_ENTRIES", 16)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: cache._save_memory_snapshot(
                    f"key-{index}",
                    100.0,
                    {"index": index},
                ),
                range(128),
            )
        )

    assert len(cache._MEMORY) == 16
