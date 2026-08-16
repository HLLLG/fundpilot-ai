from __future__ import annotations

from types import SimpleNamespace

from app.services.fund_return_distribution import fund_return_distribution_is_settled
from app.services import market_shared_refresh
from app.services.market_shared_refresh import (
    _FUND_DISTRIBUTION_IDLE_INTERVAL_SECONDS,
    _FUND_DISTRIBUTION_LIVE_INTERVAL_SECONDS,
    fund_distribution_refresh_interval_seconds,
)


def test_official_nav_matching_effective_trade_date_is_settled() -> None:
    session = {
        "is_trading_day": False,
        "session_kind": "non_trading_day",
        "calendar_date": "2026-08-16",
        "effective_trade_date": "2026-08-14",
    }
    payload = {
        "available": True,
        "source_mode": "official_nav",
        "as_of_date": "2026-08-14",
    }
    assert fund_return_distribution_is_settled(payload, session) is True


def test_intraday_estimate_is_not_settled() -> None:
    session = {
        "is_trading_day": True,
        "session_kind": "trading_day_intraday",
        "calendar_date": "2026-08-14",
        "effective_trade_date": "2026-08-14",
        "market_phase": "continuous",
    }
    payload = {
        "available": True,
        "source_mode": "intraday_estimate",
        "as_of_date": "2026-08-14",
    }
    assert fund_return_distribution_is_settled(payload, session) is False


def test_live_interval_is_15_minutes_and_idle_is_30() -> None:
    live = {
        "is_trading_day": True,
        "session_kind": "trading_day_intraday",
        "calendar_date": "2026-08-14",
        "effective_trade_date": "2026-08-14",
        "market_phase": "continuous",
    }
    weekend = {
        "is_trading_day": False,
        "session_kind": "non_trading_day",
        "calendar_date": "2026-08-16",
        "effective_trade_date": "2026-08-14",
        "market_phase": "closed",
    }
    assert fund_distribution_refresh_interval_seconds(live) == _FUND_DISTRIBUTION_LIVE_INTERVAL_SECONDS
    assert (
        fund_distribution_refresh_interval_seconds(weekend)
        == _FUND_DISTRIBUTION_IDLE_INTERVAL_SECONDS
    )


def test_weekend_settled_official_nav_skips_source_refresh(monkeypatch) -> None:
    session = {
        "is_trading_day": False,
        "session_kind": "non_trading_day",
        "calendar_date": "2026-08-16",
        "effective_trade_date": "2026-08-14",
        "market_phase": "closed",
    }
    cached = {
        "available": True,
        "source_mode": "official_nav",
        "as_of_date": "2026-08-14",
    }
    called = {"refresh": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "get_settings",
        lambda: SimpleNamespace(fund_return_distribution_refresh_enabled=True),
    )
    monkeypatch.setattr(market_shared_refresh, "build_trading_session", lambda: session)

    def fake_build(*, force_refresh: bool = False):
        assert force_refresh is False
        return cached

    monkeypatch.setattr(
        "app.services.fund_return_distribution.build_fund_return_distribution",
        fake_build,
    )
    monkeypatch.setattr(
        "app.services.fund_return_distribution.fund_return_distribution_is_settled",
        lambda payload, current: True,
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_fund_return_distribution_snapshot",
        lambda: called.__setitem__("refresh", called["refresh"] + 1),
    )

    market_shared_refresh._maybe_refresh_fund_return_distribution(10_000.0)
    assert called["refresh"] == 0
