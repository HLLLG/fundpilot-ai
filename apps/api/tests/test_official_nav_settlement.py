from __future__ import annotations

from datetime import datetime, timezone

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
    monkeypatch.setattr(service, "get_profile_for_holding", lambda _holding: None)
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


def test_intraday_session_skips_with_complete_payload_without_nav_or_persistence(monkeypatch):
    from app.services import official_nav_settlement as service

    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_intraday"))
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
        _session("trading_day_intraday"),
        "2026-06-26",
    )


def test_pre_close_session_skips_with_complete_payload_without_nav_or_persistence(monkeypatch):
    from app.services import official_nav_settlement as service

    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_pre_close"))
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
        _session("trading_day_pre_close"),
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
    monkeypatch.setattr(service, "get_profile_for_holding", lambda _holding: None)
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
    monkeypatch.setattr(service, "get_profile_for_holding", lambda _holding: profile)
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: True)

    updated, count = service.settle_official_nav_for_holdings(
        [holding],
        settlement_date="2026-06-26",
    )

    assert count == 0
    assert updated[0].daily_return_percent_source == "pending_accrual"
    assert updated[0].daily_return_percent == 0.0
    assert updated[0].daily_profit == 0.0


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


def test_settle_official_nav_endpoint_saves_cache_when_holdings_returned(
    client: TestClient,
    monkeypatch,
):
    from app import main

    payload = {
        "ok": True,
        "skipped": False,
        "updated_count": 1,
        "holdings": [{"fund_code": "519674", "fund_name": "Test Fund"}],
        "source": "official_nav_settlement",
    }
    saved: list[dict] = []

    monkeypatch.setattr(main, "settle_official_nav_for_portfolio", lambda: payload)
    monkeypatch.setattr(main, "save_cached_holdings_response", lambda item: saved.append(item))

    response = client.post("/api/portfolio/settle-official-nav")

    assert response.status_code == 200
    assert response.json() == payload
    assert saved == [payload]


def test_settle_official_nav_endpoint_skipped_does_not_save_cache(
    client: TestClient,
    monkeypatch,
):
    from app import main

    payload = {
        "ok": True,
        "skipped": True,
        "reason": "intraday_session",
        "updated_count": 0,
        "holdings": [{"fund_code": "519674", "fund_name": "Test Fund"}],
        "source": "official_nav_settlement",
    }
    saved: list[dict] = []

    monkeypatch.setattr(main, "settle_official_nav_for_portfolio", lambda: payload)
    monkeypatch.setattr(main, "save_cached_holdings_response", lambda item: saved.append(item))

    response = client.post("/api/portfolio/settle-official-nav")

    assert response.status_code == 200
    assert response.json() == payload
    assert saved == []


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
