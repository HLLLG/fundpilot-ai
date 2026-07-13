from __future__ import annotations

from datetime import datetime, timezone

from app.models import FundProfile, Holding
from app.services.holding_client import (
    serialize_holding_for_client,
    serialize_holdings_for_client,
)
from app.services.portfolio_holdings_service import build_portfolio_holdings_response


def _holding(
    code: str,
    name: str,
    *,
    amount: float = 1000.0,
) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=name,
        holding_amount=amount,
        settled_holding_amount=None,
        holding_profit=None,
        holding_return_percent=2.0,
        return_percent=2.0,
        sector_return_percent=1.5,
    )


def _profile() -> FundProfile:
    return FundProfile(
        fund_code="123456",
        fund_name="Alpha Fund",
        aliases=["Alpha Legacy"],
        holding_amount=900.0,
        settled_holding_amount=900.0,
        holding_return_percent=2.0,
        profit_accrual_deferred_until="2026-07-13",
    )


def test_serialize_batch_loads_profiles_once_without_point_queries(monkeypatch):
    profile = _profile()
    holdings = [
        _holding("123456", "Alpha Fund"),
        _holding("123456", "Alpha Fund", amount=1200.0),
        _holding("000000", "Alpha Legacy"),
        _holding("654321", "Missing Profile", amount=800.0),
    ]
    calls = {"list": 0}

    def _list_profiles():
        calls["list"] += 1
        return [profile]

    def _fail_point_query(*_args, **_kwargs):
        raise AssertionError("point profile query should not run")

    monkeypatch.setattr("app.database.list_fund_profiles", _list_profiles)
    monkeypatch.setattr("app.database.get_fund_profile_by_code", _fail_point_query)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        _fail_point_query,
    )
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _fail_point_query,
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kwargs: "2026-07-13",
    )

    expected = [
        serialize_holding_for_client(holdings[0], profile=profile),
        serialize_holding_for_client(holdings[1], profile=profile),
        serialize_holding_for_client(holdings[2], profile=profile),
        serialize_holding_for_client(holdings[3], profile=None),
    ]
    result = serialize_holdings_for_client(holdings)

    assert result == expected
    assert calls["list"] == 1
    assert [item["profit_accrual_deferred"] for item in result] == [
        True,
        True,
        True,
        False,
    ]
    assert result[0]["display_holding_amount"] == 900.0
    assert result[2]["display_holding_amount"] == 900.0
    assert result[3]["display_holding_amount"] == 800.0
    assert result[2]["estimated_holding_return_percent"] == 2.0
    assert result[3]["estimated_holding_return_percent"] == 3.5


def test_single_serializer_keeps_one_point_lookup(monkeypatch):
    calls = {"point": 0}
    profile = _profile().model_copy(
        update={
            "settled_holding_amount": 777.0,
            "profit_accrual_deferred_until": None,
        }
    )

    def _get_profile(_holding: Holding):
        calls["point"] += 1
        return profile

    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _get_profile,
    )
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("bulk query should not run")),
    )

    result = serialize_holding_for_client(_holding("123456", "Alpha Fund"))

    assert calls["point"] == 1
    assert result["display_holding_amount"] == 777.0
    assert result["estimated_holding_return_percent"] == 3.5


def test_empty_serializer_batch_does_not_load_profiles(monkeypatch):
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("profiles should not load")),
    )

    assert serialize_holdings_for_client([]) == []


def test_portfolio_response_reuses_resolution_profile_snapshot(monkeypatch):
    profile = _profile().model_copy(update={"profit_accrual_deferred_until": None})
    holding = _holding("123456", "Alpha Fund")
    calls = {"list": 0}

    def _list_profiles():
        calls["list"] += 1
        return [profile]

    def _fail_point_query(*_args, **_kwargs):
        raise AssertionError("point profile query should not run")

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", _list_profiles)
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        _fail_point_query,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.reconcile_holding_fund_codes",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_portfolio_summary",
        lambda: None,
    )
    monkeypatch.setattr("app.database.get_fund_profile_by_code", _fail_point_query)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        _fail_point_query,
    )
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _fail_point_query,
    )

    result = build_portfolio_holdings_response(
        [holding],
        source="snapshot",
        snapshot_date="2026-07-13",
        refreshed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        fetch_benchmark=False,
    )

    assert calls["list"] == 1
    assert result["profile_count"] == 1
    assert result["holdings"][0]["display_holding_amount"] == 900.0
    assert result["holdings"][0]["estimated_holding_return_percent"] == 3.5
