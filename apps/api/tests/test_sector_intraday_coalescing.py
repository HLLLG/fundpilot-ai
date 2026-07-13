from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import sector_intraday_provider as service


@pytest.fixture(autouse=True)
def _clear_coalescing_state() -> None:
    with service._INTRADAY_COALESCE_LOCK:
        service._INTRADAY_COALESCE_WAITERS.clear()
        service._INTRADAY_COALESCE_RESULT.clear()
    yield
    with service._INTRADAY_COALESCE_LOCK:
        service._INTRADAY_COALESCE_WAITERS.clear()
        service._INTRADAY_COALESCE_RESULT.clear()


def test_coalesced_results_are_lru_bounded(monkeypatch) -> None:
    monkeypatch.setattr(service, "_INTRADAY_COALESCE_RESULT_MAX_ENTRIES", 8)

    for index in range(64):
        expected = ([{"time": "15:00", "percent": float(index)}], None, None, None)
        assert service._coalesce_intraday_fetch(
            f"key-{index}",
            lambda expected=expected: expected,
        ) == expected

    assert len(service._INTRADAY_COALESCE_RESULT) == 8


def test_loader_failure_releases_waiters_without_stale_result() -> None:
    def fail():
        raise ValueError("network failed")

    with pytest.raises(ValueError, match="network failed"):
        service._coalesce_intraday_fetch("failed", fail)

    assert "failed" not in service._INTRADAY_COALESCE_WAITERS
    assert "failed" not in service._INTRADAY_COALESCE_RESULT


def test_concurrent_callers_share_one_loader_result() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()
    expected = ([{"time": "15:00", "percent": 1.0}], None, "2026-07-13", 1.0)

    def load():
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        return expected

    with ThreadPoolExecutor(max_workers=8) as executor:
        leader = executor.submit(service._coalesce_intraday_fetch, "shared", load)
        assert started.wait(timeout=2.0)
        with service._INTRADAY_COALESCE_LOCK:
            coalesce_event = service._INTRADAY_COALESCE_WAITERS["shared"]
        original_wait = coalesce_event.wait
        all_followers_waiting = threading.Event()
        waiting_count = 0
        waiting_lock = threading.Lock()

        def tracked_wait(timeout=None):
            nonlocal waiting_count
            with waiting_lock:
                waiting_count += 1
                if waiting_count == 7:
                    all_followers_waiting.set()
            return original_wait(timeout)

        coalesce_event.wait = tracked_wait  # type: ignore[method-assign]
        followers = [
            executor.submit(service._coalesce_intraday_fetch, "shared", load)
            for _ in range(7)
        ]
        assert all_followers_waiting.wait(timeout=2.0)
        release.set()
        results = [leader.result(), *(future.result() for future in followers)]

    assert calls == 1
    assert results == [expected] * 8
