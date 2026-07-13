from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.services import fund_primary_sector_service as service


@pytest.fixture(autouse=True)
def _clear_cache():
    with service._benchmark_miss_cache_lock:
        service._benchmark_miss_cache.clear()
    yield
    with service._benchmark_miss_cache_lock:
        service._benchmark_miss_cache.clear()


def test_benchmark_miss_cache_is_lru_bounded(monkeypatch):
    monkeypatch.setattr(service, "_BENCHMARK_MISS_CACHE_MAX_ENTRIES", 3)

    for code in ("000001", "000002", "000003"):
        service._remember_benchmark_miss(code)

    assert service._benchmark_miss_cached("000001") is True
    service._remember_benchmark_miss("000004")

    assert list(service._benchmark_miss_cache) == ["000003", "000001", "000004"]
    assert service._benchmark_miss_cached("000002") is False


def test_benchmark_miss_cache_prunes_all_expired_entries():
    now = datetime.now(timezone.utc)
    stale = now - service._BENCHMARK_MISS_TTL - timedelta(seconds=1)
    with service._benchmark_miss_cache_lock:
        service._benchmark_miss_cache.update(
            {
                "000001": stale,
                "000002": now,
                "000003": stale,
            }
        )

    assert service._benchmark_miss_cached("000002") is True
    assert list(service._benchmark_miss_cache) == ["000002"]


def test_benchmark_miss_cache_stays_bounded_under_concurrent_writes(monkeypatch):
    monkeypatch.setattr(service, "_BENCHMARK_MISS_CACHE_MAX_ENTRIES", 16)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(service._remember_benchmark_miss, (f"{code:06d}" for code in range(200))))

    assert len(service._benchmark_miss_cache) == 16
