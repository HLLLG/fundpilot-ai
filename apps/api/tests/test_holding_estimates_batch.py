from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.holding_estimates import (
    enrich_holding_estimates,
    enrich_holdings_estimates,
    overlay_official_nav_returns,
    portfolio_official_nav_settled,
)


def _holding(code: str, amount: float, **updates) -> Holding:
    values = {
        "fund_code": code,
        "fund_name": f"Fund {code}",
        "holding_amount": amount,
        "settled_holding_amount": amount,
        "holding_profit": round(amount / 101, 2),
        "holding_return_percent": 1.0,
        "return_percent": 1.0,
    }
    values.update(updates)
    return Holding(**values)


def _profile(code: str, **updates) -> FundProfile:
    values = {
        "fund_code": code,
        "fund_name": f"Fund {code}",
        "holding_return_percent": 1.0,
    }
    values.update(updates)
    return FundProfile(**values)


def test_enrich_batch_loads_profiles_once_and_fetches_nav_once_per_code(monkeypatch):
    calls = {"profiles": 0, "nav": [], "batches": []}
    profiles = [_profile("000001"), _profile("000002", yesterday_profit=7.0)]

    def _list_profiles():
        calls["profiles"] += 1
        return profiles

    def _nav_returns(code: str, _trade_date: str):
        calls["nav"].append(code)
        return (1.0, 2.0) if code == "000001" else None

    def _map_concurrently(items, worker):
        calls["batches"].append(list(items))
        return [worker(item) for item in items]

    monkeypatch.setattr("app.database.list_fund_profiles", _list_profiles)
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("point query should not run")),
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_yesterday_profit_nav_returns",
        _nav_returns,
    )
    monkeypatch.setattr(
        "app.services.fund_data._map_holdings_concurrently",
        _map_concurrently,
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kwargs: "2026-07-10",
    )

    result = enrich_holdings_estimates(
        [
            _holding("000001", 1000.0),
            _holding("000001", 2000.0),
            _holding("000002", 3000.0),
        ]
    )

    assert calls["profiles"] == 1
    assert calls["batches"] == [["000001", "000002"]]
    assert sorted(calls["nav"]) == ["000001", "000002"]
    assert [holding.yesterday_profit for holding in result] == [19.8, 39.6, 7.0]


def test_empty_enrich_batch_does_not_query_profiles_or_nav(monkeypatch):
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("profiles should not load")),
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_yesterday_profit_nav_returns",
        lambda *_args: (_ for _ in ()).throw(AssertionError("NAV should not load")),
    )

    assert enrich_holdings_estimates([]) == []


def test_single_holding_entrypoint_keeps_point_profile_lookup(monkeypatch):
    calls = {"profile": 0}
    profile = _profile("000001")

    def _get_profile(_holding: Holding):
        calls["profile"] += 1
        return profile

    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _get_profile,
    )
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("bulk query should not run")),
    )

    result = enrich_holding_estimates(_holding("000001", 1000.0))

    assert calls["profile"] == 1
    assert result.fund_code == "000001"


def test_profile_based_batch_entrypoints_each_use_one_bulk_query(monkeypatch):
    calls = {"profiles": 0}
    profiles = [_profile("000001"), _profile("000002")]

    def _list_profiles():
        calls["profiles"] += 1
        return profiles

    monkeypatch.setattr("app.database.list_fund_profiles", _list_profiles)
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("point query should not run")),
    )
    monkeypatch.setattr(
        "app.services.trading_session.build_trading_session",
        lambda: {"session_kind": "non_trading_day"},
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kwargs: "2026-07-10",
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda _code, _date: 1.0,
    )
    holdings = [
        _holding("000001", 1000.0),
        _holding("000002", 2000.0),
    ]

    overlaid = overlay_official_nav_returns(holdings)
    assert calls["profiles"] == 1
    assert all(holding.daily_return_percent_source == "official_nav" for holding in overlaid)

    assert portfolio_official_nav_settled(overlaid) is True
    assert calls["profiles"] == 2
