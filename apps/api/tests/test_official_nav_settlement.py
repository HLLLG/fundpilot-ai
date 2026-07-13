from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.models import FundProfile, Holding


def _holding(**updates) -> Holding:
    defaults = {
        "fund_code": "519674",
        "fund_name": "Test Fund",
        "holding_amount": 10000.0,
        "settled_holding_amount": 10000.0,
        "sector_return_percent": 0.8,
        "daily_return_percent": 0.8,
        "daily_profit": 80.0,
        "daily_return_percent_source": "sector_estimate",
        "amount_includes_today": False,
    }
    defaults.update(updates)
    return Holding(**defaults)


def _session(kind: str = "non_trading_day") -> dict:
    return {
        "timezone": "Asia/Shanghai",
        "local_datetime": "2026-06-27 10:00",
        "calendar_date": "2026-06-27",
        "effective_trade_date": "2026-06-26",
        "is_trading_day": False,
        "session_kind": kind,
        "minutes_to_close": None,
    }


def test_non_trading_day_settles_official_nav_and_persists_without_refetch(monkeypatch):
    from app.services import official_nav_settlement as service

    persisted: dict = {}
    primed: list[tuple[list[str], str]] = []
    holding = _holding()

    monkeypatch.setattr(service, "build_trading_session", lambda: _session())
    monkeypatch.setattr(
        service,
        "_load_settlement_holdings",
        lambda: (
            [holding],
            "snapshot",
            "2026-06-27",
            datetime(2026, 6, 27, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: 1.5)
    monkeypatch.setattr(
        service,
        "prime_official_nav_cache",
        lambda codes, date: primed.append((list(codes), date)) or {},
    )
    monkeypatch.setattr(service, "list_fund_profiles", lambda: [])
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: False)

    def _persist(holdings, *, fetched_at=None):
        persisted["holdings"] = holdings
        persisted["fetched_at"] = fetched_at
        return holdings, {"daily_profit": sum(item.daily_profit or 0 for item in holdings)}

    monkeypatch.setattr(
        service,
        "_persist_settlement_holdings",
        _persist,
    )
    monkeypatch.setattr(
        service,
        "_serialize_settlement_holdings_for_client",
        lambda holdings: [item.model_dump() for item in holdings],
    )

    result = service.settle_official_nav_for_portfolio()

    assert result["ok"] is True
    assert result["skipped"] is False
    assert result["settlement_date"] == "2026-06-26"
    assert result["updated_count"] == 1
    assert primed == [(["519674"], "2026-06-26")]
    settled = persisted["holdings"][0]
    assert settled.daily_return_percent_source == "official_nav"
    assert settled.daily_return_percent == 1.5
    assert settled.daily_profit == 150.0
    assert result["source"] == "official_nav_settlement"
    assert result["snapshot_date"] == "2026-06-27"
    assert result["portfolio_summary"] == {"daily_profit": 150.0}


def test_settlement_serializer_drops_untrusted_legacy_sector_return():
    from app.services.official_nav_settlement import _serialize_settlement_holdings_for_client

    payload = _serialize_settlement_holdings_for_client(
        [
            _holding(
                sector_return_percent=3.66,
                sector_return_percent_source=None,
                daily_return_percent=3.66,
                daily_return_percent_source="official_nav",
                daily_profit=366.0,
            )
        ]
    )[0]

    assert payload["sector_return_percent"] is None
    assert payload["sector_return_percent_source"] is None
    assert payload["estimated_daily_return_percent"] == 3.66


def _empty_payload(
    reason: str,
    session: dict,
    settlement_date: str,
    *,
    snapshot_date: str | None = None,
) -> dict:
    return {
        "ok": True,
        "skipped": True,
        "reason": reason,
        "session": session,
        "settlement_date": settlement_date,
        "updated_count": 0,
        "holdings": [],
        "portfolio_summary": None,
        "source": "official_nav_settlement",
        "snapshot_date": snapshot_date,
        "refreshed_at": None,
    }


@pytest.mark.parametrize("session_kind", ["trading_day_intraday", "trading_day_pre_close"])
def test_live_session_skips_with_complete_payload_without_nav_or_persistence(monkeypatch, session_kind):
    from app.services import official_nav_settlement as service

    monkeypatch.setattr(service, "build_trading_session", lambda: _session(session_kind))
    monkeypatch.setattr(
        service,
        "get_official_nav_return",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nav should not be called")),
    )
    monkeypatch.setattr(
        service,
        "_persist_settlement_holdings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persist should not be called")),
    )

    result = service.settle_official_nav_for_portfolio()

    assert result == _empty_payload(
        "intraday_session",
        _session(session_kind),
        "2026-06-26",
    )


def test_no_holdings_skips_with_complete_payload(monkeypatch):
    from app.services import official_nav_settlement as service

    monkeypatch.setattr(service, "build_trading_session", lambda: _session())
    monkeypatch.setattr(
        service,
        "_load_settlement_holdings",
        lambda: ([], "snapshot", None, None),
    )
    monkeypatch.setattr(
        service,
        "get_official_nav_return",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nav should not be called")),
    )
    monkeypatch.setattr(
        service,
        "_persist_settlement_holdings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persist should not be called")),
    )

    result = service.settle_official_nav_for_portfolio()

    assert result == _empty_payload("no_holdings", _session(), "2026-06-26")


def test_portfolio_settlement_without_nav_skips_without_persistence(monkeypatch):
    from app.services import official_nav_settlement as service

    holding = _holding()

    monkeypatch.setattr(service, "build_trading_session", lambda: _session())

    def _load_settlement_holdings():
        return [holding], "snapshot", "2026-06-27", None

    monkeypatch.setattr(service, "_load_settlement_holdings", _load_settlement_holdings)
    monkeypatch.setattr(service, "list_fund_profiles", lambda: [])
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: False)
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: None)
    monkeypatch.setattr(service, "prime_official_nav_cache", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        service,
        "_persist_settlement_holdings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persist should not be called")),
    )

    result = service.settle_official_nav_for_portfolio()

    assert result == _empty_payload(
        "no_nav_available",
        _session(),
        "2026-06-26",
        snapshot_date="2026-06-27",
    )


def test_missing_official_nav_keeps_sector_estimate(monkeypatch):
    from app.services import official_nav_settlement as service

    holding = _holding()
    monkeypatch.setattr(service, "list_fund_profiles", lambda: [])
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: None)

    updated, count = service.settle_official_nav_for_holdings(
        [holding],
        settlement_date="2026-06-26",
    )

    assert count == 0
    assert updated == [holding]


def test_deferred_holding_is_not_overwritten_by_official_nav(monkeypatch):
    from app.services import official_nav_settlement as service

    profile = FundProfile(
        fund_code="519674",
        fund_name="Test Fund",
        profit_accrual_deferred_until="2026-06-26",
    )
    holding = _holding(
        daily_profit=0.0,
        daily_return_percent=0.0,
        daily_return_percent_source="pending_accrual",
    )

    monkeypatch.setattr(
        service,
        "get_official_nav_return",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nav should not be called")),
    )
    monkeypatch.setattr(service, "list_fund_profiles", lambda: [profile])
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: True)

    updated, count = service.settle_official_nav_for_holdings(
        [holding],
        settlement_date="2026-06-26",
    )

    assert count == 0
    assert updated[0].daily_return_percent_source == "pending_accrual"
    assert updated[0].daily_return_percent == 0.0
    assert updated[0].daily_profit == 0.0


def test_settlement_batches_profile_matches_without_point_queries(monkeypatch):
    from app import database
    from app.services import official_nav_settlement as service

    profile = FundProfile(
        fund_code="111111",
        fund_name="Canonical Deferred Fund",
        aliases=["Deferred Fund Alias"],
        profit_accrual_deferred_until="2026-06-26",
    )
    profile_reads = 0
    nav_calls: list[str] = []

    def _list_profiles():
        nonlocal profile_reads
        profile_reads += 1
        return [profile]

    def _get_nav(code: str, _date: str) -> float:
        nav_calls.append(code)
        return 1.25

    monkeypatch.setattr(service, "list_fund_profiles", _list_profiles)
    monkeypatch.setattr(service, "get_official_nav_return", _get_nav)
    monkeypatch.setattr(
        service,
        "is_profit_accrual_deferred",
        lambda item: bool(item and item.profit_accrual_deferred_until),
    )
    monkeypatch.setattr(
        database,
        "get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("point profile query")),
    )
    holdings = [
        _holding(fund_code="111111", fund_name="Canonical Deferred Fund"),
        _holding(fund_code="111111", fund_name="Duplicate Code Row"),
        _holding(fund_code="999999", fund_name="Deferred Fund Alias"),
        _holding(fund_code="222222", fund_name="No Profile Fund"),
        _holding(fund_code="000000", fund_name="Placeholder Fund"),
    ]

    updated, count = service.settle_official_nav_for_holdings(
        holdings,
        settlement_date="2026-06-26",
    )

    assert profile_reads == 1
    assert nav_calls == ["222222"]
    assert count == 1
    assert [item.daily_return_percent_source for item in updated] == [
        "sector_estimate",
        "sector_estimate",
        "sector_estimate",
        "official_nav",
        "sector_estimate",
    ]
    assert updated[3].daily_return_percent == 1.25
    assert updated[3].daily_profit == 125.0


def test_overlay_sector_fields_preserves_official_nav_daily_fields_without_sector_return():
    from app.services.portfolio_persistence import _overlay_sector_fields

    previous = _holding(
        daily_return_percent=0.8,
        daily_profit=80.0,
        daily_return_percent_source="sector_estimate",
    )
    official = _holding(
        sector_return_percent=None,
        sector_return_percent_source=None,
        daily_return_percent=1.5,
        daily_profit=150.0,
        daily_return_percent_source="official_nav",
    )

    result = _overlay_sector_fields(previous, official)

    assert result.daily_return_percent == 1.5
    assert result.daily_profit == 150.0
    assert result.daily_return_percent_source == "official_nav"


@pytest.mark.parametrize(
    ("skipped", "expected_saved"),
    [
        (False, "saves"),
        (True, "skips"),
    ],
)
def test_settle_official_nav_endpoint_cache_save_follows_skipped_flag(
    client: TestClient,
    monkeypatch,
    skipped: bool,
    expected_saved: str,
):
    from app import main

    payload = {
        "ok": True,
        "skipped": skipped,
        "updated_count": 0 if skipped else 1,
        "holdings": [{"fund_code": "519674", "fund_name": "Test Fund"}],
        "source": "official_nav_settlement",
    }
    if skipped:
        payload["reason"] = "intraday_session"
    saved: list[dict] = []

    monkeypatch.setattr(main, "settle_official_nav_for_portfolio", lambda: payload)
    monkeypatch.setattr(main, "save_cached_holdings_response", lambda item: saved.append(item))

    response = client.post("/api/portfolio/settle-official-nav")

    assert response.status_code == 200
    assert response.json() == payload
    assert saved == ([] if expected_saved == "skips" else [payload])


def test_prime_official_nav_cache_batches_daily_nav_and_unit_nav(monkeypatch):
    from app.services import fund_nav_service as service

    service._NAV_CACHE.clear()
    service._UNIT_NAV_CACHE.clear()
    calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(service, "_cached_persisted_nav_return", lambda _code, _date: None)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_args, **_kwargs: None)

    def _fetch(codes, trade_date):
        calls.append((list(codes), trade_date))
        return {
            "data": {
                "519674": {"daily_growth": 1.5, "unit_nav": 1.2345},
                "021533": {"daily_growth": -3.65, "unit_nav": 3.5906},
            }
        }

    monkeypatch.setattr(service, "fetch_fund_daily_nav_returns", _fetch)

    result = service.prime_official_nav_cache(["519674", "021533"], "2026-06-26")

    assert result == {"021533": -3.65, "519674": 1.5}
    assert calls == [(["021533", "519674"], "2026-06-26")]
    assert service.get_official_nav_return("519674", "2026-06-26") == 1.5
    assert service.get_latest_unit_nav("021533", allow_fetch=False) == 3.5906

    service.prime_official_nav_cache(["519674", "021533"], "2026-06-26")

    assert calls == [(["021533", "519674"], "2026-06-26")]
