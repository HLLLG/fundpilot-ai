from __future__ import annotations

import threading

from app.services import portfolio_sector_refresh as sector_refresh
from app.services import sector_quote_provider as provider
from app.services.portfolio_refresh_gate import (
    begin_background_sector_refresh,
    begin_nav_work,
    begin_shared_spot_refresh,
    end_background_sector_refresh,
    end_nav_work,
    end_shared_spot_refresh,
    nav_work_in_flight,
    wait_nav_work,
)
from app.services.sector_quote_provider import SpotBoardFetchResult


def _boards() -> SpotBoardFetchResult:
    return SpotBoardFetchResult(
        boards={"index": {"沪深300": 1.0}, "concept": {}, "industry": {}},
        provider_path="eastmoney_live",
    )


def test_force_refresh_joins_in_flight_spot_pull(monkeypatch) -> None:
    calls: list[bool] = []
    started = threading.Event()
    release = threading.Event()

    def fake(*, force_refresh: bool = False, timeout_seconds: float | None = None):
        calls.append(force_refresh)
        if force_refresh:
            started.set()
            release.wait(timeout=2.0)
        return _boards()

    monkeypatch.setattr(provider, "_fetch_spot_boards_result", fake)

    def runner() -> None:
        provider.fetch_spot_boards_result(force_refresh=True)

    thread = threading.Thread(target=runner)
    thread.start()
    assert started.wait(timeout=2.0)
    joined = provider.fetch_spot_boards_result(force_refresh=True)
    release.set()
    thread.join(timeout=2.0)

    assert joined.joined_in_flight is True
    assert calls.count(True) == 1
    assert False in calls


def test_background_sector_refresh_skips_overlapping_job(monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(
        sector_refresh,
        "refresh_shared_spot_boards",
        lambda **_kwargs: called.__setitem__("n", called["n"] + 1),
    )
    assert begin_background_sector_refresh() is True
    try:
        sector_refresh.refresh_all_portfolio_sectors()
        assert called["n"] == 0
    finally:
        end_background_sector_refresh()


def test_nav_work_gate_is_single_flight() -> None:
    assert begin_nav_work(9) is True
    assert begin_nav_work(9) is False
    assert nav_work_in_flight(9) is True
    ended = threading.Event()

    def release() -> None:
        end_nav_work(9)
        ended.set()

    threading.Timer(0.05, release).start()
    assert wait_nav_work(9, timeout=2.0) is True
    assert ended.wait(timeout=2.0)
    assert nav_work_in_flight(9) is False
    assert begin_nav_work(9) is True
    end_nav_work(9)


def test_spot_refresh_gate_clears_after_end() -> None:
    assert begin_shared_spot_refresh() is True
    assert begin_shared_spot_refresh() is False
    end_shared_spot_refresh()
    assert begin_shared_spot_refresh() is True
    end_shared_spot_refresh()
