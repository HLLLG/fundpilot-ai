from __future__ import annotations

from types import SimpleNamespace

from app.services import market_shared_refresh


def _current_session(*, phase: str) -> dict:
    return {
        "is_trading_day": True,
        "calendar_date": "2026-08-03",
        "effective_trade_date": "2026-08-03",
        "session_kind": (
            "trading_day_after_close"
            if phase == "after_close"
            else "trading_day_intraday"
        ),
        "market_phase": phase,
    }


def test_fund_distribution_refreshes_every_15_minutes_during_trade_day(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: _current_session(phase="continuous"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_fund_return_distribution_snapshot",
        lambda: calls.append("refresh"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "_last_fund_return_distribution_refresh_at",
        100.0,
    )

    market_shared_refresh._maybe_refresh_fund_return_distribution(999.0)
    assert calls == []

    market_shared_refresh._maybe_refresh_fund_return_distribution(1000.0)
    assert calls == ["refresh"]
    assert market_shared_refresh._last_fund_return_distribution_refresh_at == 1000.0


def test_fund_distribution_uses_30_minute_after_close_check(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: _current_session(phase="after_close"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_fund_return_distribution_snapshot",
        lambda: calls.append("refresh"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "_last_fund_return_distribution_refresh_at",
        100.0,
    )

    market_shared_refresh._maybe_refresh_fund_return_distribution(1899.0)
    assert calls == []

    market_shared_refresh._maybe_refresh_fund_return_distribution(1900.0)
    assert calls == ["refresh"]


def test_startup_prewarms_fund_distribution_before_other_market_jobs(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_shared_refresh,
        "get_settings",
        lambda: SimpleNamespace(
            theme_board_refresh_enabled=True,
            fund_return_distribution_refresh_enabled=True,
        ),
    )
    monkeypatch.setattr(market_shared_refresh.time, "monotonic", lambda: 42.0)
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_fund_return_distribution_snapshot",
        lambda: calls.append("fund"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_a_share_market_snapshots",
        lambda: calls.append("a-share"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_market_breadth_snapshot",
        lambda: calls.append("breadth"),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_us_market_snapshot",
        lambda: calls.append("us"),
    )

    market_shared_refresh.run_startup_market_refresh()

    assert calls == ["fund", "a-share", "breadth", "us"]
    assert market_shared_refresh._last_fund_return_distribution_refresh_at == 42.0
