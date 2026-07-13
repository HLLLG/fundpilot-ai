from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import fund_nav_service as service


@pytest.fixture(autouse=True)
def _clear_memory_caches():
    with service._NAV_CACHE_LOCK:
        service._NAV_CACHE.clear()
    with service._UNIT_NAV_CACHE_LOCK:
        service._UNIT_NAV_CACHE.clear()
    yield
    with service._NAV_CACHE_LOCK:
        service._NAV_CACHE.clear()
    with service._UNIT_NAV_CACHE_LOCK:
        service._UNIT_NAV_CACHE.clear()


def test_expired_nav_entry_is_removed_before_persisted_fallback(monkeypatch) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_cached_persisted_nav_return", lambda *_args: None)

    service._cache_nav_return("000001", "2026-07-10", 1.25, ttl=5)
    assert service.get_cached_official_nav_return("000001", "2026-07-10") == 1.25

    clock["now"] = 106.0
    assert service.get_cached_official_nav_return("000001", "2026-07-10") is None
    assert "000001:2026-07-10" not in service._NAV_CACHE


def test_nav_and_unit_nav_caches_evict_least_recently_used(monkeypatch) -> None:
    monkeypatch.setattr(service, "_NAV_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(service, "_UNIT_NAV_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_persisted_unit_nav", lambda *_args: None)

    trade_date = "2026-07-10"
    service._cache_nav_return("000001", trade_date, 1.0, service.TTL_HIT)
    service._cache_nav_return("000002", trade_date, 2.0, service.TTL_HIT)
    assert service.get_cached_official_nav_return("000001", trade_date) == 1.0
    service._cache_nav_return("000003", trade_date, 3.0, service.TTL_HIT)

    service._cache_unit_nav_memory("unit:000001", 1.0, service.TTL_HIT)
    service._cache_unit_nav_memory("unit:000002", 2.0, service.TTL_HIT)
    assert service.peek_cached_unit_nav("000001") == 1.0
    service._cache_unit_nav_memory("unit:000003", 3.0, service.TTL_HIT)

    assert list(service._NAV_CACHE) == [
        f"000001:{trade_date}",
        f"000003:{trade_date}",
    ]
    assert list(service._UNIT_NAV_CACHE) == ["unit:000001", "unit:000003"]


def test_nav_cache_stays_bounded_under_concurrent_writes(monkeypatch) -> None:
    monkeypatch.setattr(service, "_NAV_CACHE_MAX_ENTRIES", 16)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_args, **_kwargs: None)

    def write(index: int) -> None:
        service._cache_nav_return(
            f"{index:06d}",
            "2026-07-10",
            float(index),
            service.TTL_HIT,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(128)))

    with service._NAV_CACHE_LOCK:
        assert len(service._NAV_CACHE) == 16
