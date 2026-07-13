from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import sector_signal_context as service


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    with service._CACHE_LOCK:
        service._CACHE.clear()
    yield
    with service._CACHE_LOCK:
        service._CACHE.clear()


def _install_stubs(monkeypatch, calls: list[tuple[str, ...]]) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            sector_signal_backtest_enabled=True,
            sector_signal_backtest_days=100,
        ),
    )

    def fake_build(labels, *, lookback_days, fetch_series=None):
        calls.append(tuple(labels or ()))
        return {
            "has_data": False,
            "sector_count": len(labels or ()),
            "sectors": [],
            "by_rule": {},
            "summary_lines": [],
        }

    monkeypatch.setattr(service, "build_sector_signal_backtest", fake_build)


def test_cache_hit_refreshes_lru_order(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_stubs(monkeypatch, calls)
    monkeypatch.setattr(service, "_CACHE_MAX_ENTRIES", 2)
    now = [100.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    service.build_signal_backtest_context(["A"])
    service.build_signal_backtest_context(["B"])
    service.build_signal_backtest_context(["A"])
    service.build_signal_backtest_context(["C"])

    assert calls == [("A",), ("B",), ("C",)]
    assert list(service._CACHE) == ["A:100", "C:100"]


def test_expired_entries_are_pruned_before_insert(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_stubs(monkeypatch, calls)
    now = [100.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])

    service.build_signal_backtest_context(["expired"])
    now[0] += service._CACHE_TTL_SECONDS + 1
    service.build_signal_backtest_context(["fresh"])

    assert calls == [("expired",), ("fresh",)]
    assert list(service._CACHE) == ["fresh:100"]


def test_concurrent_writes_remain_bounded(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    _install_stubs(monkeypatch, calls)
    monkeypatch.setattr(service, "_CACHE_MAX_ENTRIES", 8)
    monkeypatch.setattr(service.time, "time", lambda: 100.0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: service.build_signal_backtest_context([f"sector-{index}"]),
                range(64),
            )
        )

    assert len(calls) == 64
    assert len(service._CACHE) == 8
