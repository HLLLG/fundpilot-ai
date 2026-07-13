from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import index_daily_client as service


@pytest.fixture(autouse=True)
def _clear_index_cache():
    with service._INDEX_TTL_CACHE_LOCK:
        service._INDEX_TTL_CACHE.clear()
    yield
    with service._INDEX_TTL_CACHE_LOCK:
        service._INDEX_TTL_CACHE.clear()


def test_index_cache_uses_lru_eviction(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(service, "INDEX_DAILY_RESPONSE_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        service,
        "_fetch_index_daily_history_impl",
        lambda symbol, _days: calls.append(symbol) or {"symbol": symbol},
    )

    service.fetch_index_daily_history("000001", 30)
    service.fetch_index_daily_history("000002", 30)
    assert service.fetch_index_daily_history("000001", 30) == {"symbol": "000001"}
    service.fetch_index_daily_history("000003", 30)

    assert calls == ["000001", "000002", "000003"]
    assert list(service._INDEX_TTL_CACHE) == ["000001:30", "000003:30"]


def test_index_cache_prunes_expired_entries_and_refetches(monkeypatch) -> None:
    clock = {"now": 100.0}
    calls: list[str] = []
    monkeypatch.setattr(service.time, "monotonic", lambda: clock["now"])

    def fetch(symbol: str, _days: int):
        calls.append(symbol)
        if len(calls) == 2:
            assert "000001:30" not in service._INDEX_TTL_CACHE
        return {"symbol": symbol}

    monkeypatch.setattr(service, "_fetch_index_daily_history_impl", fetch)

    service.fetch_index_daily_history("000001", 30)
    clock["now"] += service.INDEX_DAILY_RESPONSE_TTL_SECONDS + 1
    service.fetch_index_daily_history("000001", 30)

    assert calls == ["000001", "000001"]
    assert "000001:30" in service._INDEX_TTL_CACHE


def test_index_cache_preserves_negative_results(monkeypatch) -> None:
    calls = 0

    def fetch(_symbol: str, _days: int):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(service, "_fetch_index_daily_history_impl", fetch)

    assert service.fetch_index_daily_history("000001", 30) is None
    assert service.fetch_index_daily_history("000001", 30) is None
    assert calls == 1


def test_index_cache_stays_bounded_under_concurrent_writes(monkeypatch) -> None:
    monkeypatch.setattr(service, "INDEX_DAILY_RESPONSE_CACHE_MAX_ENTRIES", 16)
    monkeypatch.setattr(
        service,
        "_fetch_index_daily_history_impl",
        lambda symbol, _days: {"symbol": symbol},
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: service.fetch_index_daily_history(f"{index:06d}", 30),
                range(128),
            )
        )

    with service._INDEX_TTL_CACHE_LOCK:
        assert len(service._INDEX_TTL_CACHE) == 16
