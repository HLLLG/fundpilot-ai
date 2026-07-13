from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import sector_signal_backtest as service


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    with service._BACKTEST_CACHE_LOCK:
        service._BACKTEST_CACHE.clear()
    yield
    with service._BACKTEST_CACHE_LOCK:
        service._BACKTEST_CACHE.clear()


def _install_builder(monkeypatch, calls: list[tuple[str, ...]]) -> None:
    def fake_build(labels, **_kwargs):
        normalized = tuple(labels or ())
        calls.append(normalized)
        return {"has_data": True, "labels": normalized}

    monkeypatch.setattr(service, "_build_sector_signal_backtest_impl", fake_build)


def test_backtest_cache_is_lru_bounded(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_builder(monkeypatch, calls)
    monkeypatch.setattr(service, "_BACKTEST_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(service.time, "time", lambda: 100.0)

    service.build_sector_signal_backtest(["A"])
    service.build_sector_signal_backtest(["B"])
    service.build_sector_signal_backtest(["A"])
    service.build_sector_signal_backtest(["C"])

    assert calls == [("A",), ("B",), ("C",)]
    assert list(service._BACKTEST_CACHE) == [
        service._backtest_cache_key(["A"], 120, None),
        service._backtest_cache_key(["C"], 120, None),
    ]


def test_backtest_cache_prunes_expired_entries(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_builder(monkeypatch, calls)
    now = [100.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    service.build_sector_signal_backtest(["expired"])
    now[0] += service._BACKTEST_RESPONSE_TTL_SECONDS + 1
    service.build_sector_signal_backtest(["fresh"])

    assert calls == [("expired",), ("fresh",)]
    assert list(service._BACKTEST_CACHE) == [
        service._backtest_cache_key(["fresh"], 120, None)
    ]


def test_concurrent_backtest_writes_remain_bounded(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_builder(monkeypatch, calls)
    monkeypatch.setattr(service, "_BACKTEST_CACHE_MAX_ENTRIES", 8)
    monkeypatch.setattr(service.time, "time", lambda: 100.0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: service.build_sector_signal_backtest([f"sector-{index}"]),
                range(64),
            )
        )

    assert len(calls) == 64
    assert len(service._BACKTEST_CACHE) == 8
