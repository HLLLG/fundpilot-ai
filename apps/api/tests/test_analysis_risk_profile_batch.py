from __future__ import annotations

import pytest

from app.models import FundProfile, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_facts import build_analysis_facts
from app.services.risk import evaluate_portfolio_risk


def _holding(code: str, name: str, amount: float) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=name,
        holding_amount=amount,
        holding_return_percent=2.0,
        return_percent=2.0,
        sector_return_percent=1.5,
    )


def _profile() -> FundProfile:
    return FundProfile(
        fund_code="123456",
        fund_name="Alpha Fund",
        aliases=["Alpha Legacy"],
        holding_return_percent=2.0,
        profit_accrual_deferred_until="2026-07-13",
    )


def _holdings() -> list[Holding]:
    return [
        _holding("123456", "Alpha Fund", 100.0),
        _holding("000000", "Alpha Legacy", 200.0),
        _holding("654321", "Missing Profile", 300.0),
    ]


def _stub_analysis_enhancements(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts._build_sector_intraday_map",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": False,
            "reason": "no_sector",
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": {},
        },
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_breadth_signal",
        lambda *_args, **_kwargs: {"available": False},
    )


@pytest.mark.parametrize("budget_enhancements", [False, True])
def test_analysis_facts_batches_profiles_and_reuses_metrics(
    monkeypatch,
    budget_enhancements: bool,
):
    from app.services import analysis_facts

    profile = _profile()
    holdings = _holdings()
    calls = {"list": 0, "denominator": 0, "display": [], "daily": []}
    original_denominator = analysis_facts.resolve_weight_denominator
    original_display = analysis_facts.build_holding_display_metrics
    original_daily = analysis_facts.holding_daily_return_is_estimated

    def _list_profiles():
        calls["list"] += 1
        return [profile]

    def _denominator(items, investor_profile, **kwargs):
        calls["denominator"] += 1
        assert kwargs["actual_total"] == 600.0
        return original_denominator(items, investor_profile, **kwargs)

    def _display(holding, *, profile):
        calls["display"].append(profile)
        return original_display(holding, profile=profile)

    def _daily(holding, *, profile):
        calls["daily"].append(profile)
        return original_daily(holding, profile=profile)

    def _fail_point_query(*_args, **_kwargs):
        raise AssertionError("point profile query should not run")

    _stub_analysis_enhancements(monkeypatch)
    monkeypatch.setattr("app.database.list_fund_profiles", _list_profiles)
    monkeypatch.setattr("app.database.get_fund_profile_by_code", _fail_point_query)
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _fail_point_query,
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kwargs: "2026-07-13",
    )
    monkeypatch.setattr(analysis_facts, "resolve_weight_denominator", _denominator)
    monkeypatch.setattr(analysis_facts, "build_holding_display_metrics", _display)
    monkeypatch.setattr(analysis_facts, "holding_daily_return_is_estimated", _daily)

    facts = build_analysis_facts(
        holdings,
        RiskAssessment(
            level="medium",
            weighted_return_percent=0.0,
            suggested_action="watch",
            alerts=[],
        ),
        [],
        InvestorProfile(),
        session={"effective_trade_date": "2026-07-13"},
        budget_enhancements=budget_enhancements,
    )

    assert calls["list"] == 1
    assert calls["denominator"] == 1
    assert calls["daily"] == [profile, profile, None]
    assert calls["display"] == ([] if budget_enhancements else [profile, profile, None])
    rows = facts["holdings"]
    assert [row["weight_percent"] for row in rows] == [16.67, 33.33, 50.0]
    assert [row["daily_return_is_estimated"] for row in rows] == [False, False, True]
    expected_returns = [3.5, 3.5, 3.5] if budget_enhancements else [2.0, 2.0, 3.5]
    assert [row["estimated_holding_return_percent"] for row in rows] == expected_returns


def test_analysis_facts_explicit_empty_profiles_do_not_fallback(monkeypatch):
    _stub_analysis_enhancements(monkeypatch)

    def _fail_query(*_args, **_kwargs):
        raise AssertionError("explicit empty profiles must not query")

    monkeypatch.setattr("app.database.list_fund_profiles", _fail_query)
    monkeypatch.setattr("app.database.get_fund_profile_by_code", _fail_query)
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _fail_query,
    )

    facts = build_analysis_facts(
        [_holding("654321", "Missing Profile", 100.0)],
        RiskAssessment(
            level="medium",
            weighted_return_percent=3.5,
            suggested_action="watch",
            alerts=[],
        ),
        [],
        InvestorProfile(),
        session={"effective_trade_date": "2026-07-13"},
        profiles_snapshot=None,
    )

    assert facts["holdings"][0]["estimated_holding_return_percent"] == 3.5


def test_risk_reuses_effective_returns_and_denominator(monkeypatch):
    from app.services import risk

    profile = _profile()
    holdings = _holdings()
    calls = {"list": 0, "denominator": 0, "effective": []}
    original_denominator = risk.resolve_weight_denominator

    def _list_profiles():
        calls["list"] += 1
        return [profile]

    def _denominator(items, investor_profile, **kwargs):
        calls["denominator"] += 1
        assert kwargs["actual_total"] == 600.0
        return original_denominator(items, investor_profile, **kwargs)

    def _effective(holding, *, profile):
        calls["effective"].append(profile)
        return {
            "123456": -10.0,
            "000000": -10.0,
            "654321": 5.0,
        }[holding.fund_code]

    monkeypatch.setattr("app.database.list_fund_profiles", _list_profiles)
    monkeypatch.setattr(risk, "resolve_weight_denominator", _denominator)
    monkeypatch.setattr(risk, "resolve_effective_holding_return_percent", _effective)
    monkeypatch.setattr(
        risk,
        "holding_weight_percent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("risk loop must reuse the denominator")
        ),
    )

    assessment = evaluate_portfolio_risk(
        holdings,
        InvestorProfile(
            max_drawdown_percent=8.0,
            concentration_limit_percent=25.0,
        ),
    )

    assert calls["list"] == 1
    assert calls["denominator"] == 1
    assert calls["effective"] == [profile, profile, None]
    assert assessment.weighted_return_percent == -2.5
    assert [alert.code for alert in assessment.alerts] == [
        "HOLDING_DRAWDOWN",
        "HOLDING_DRAWDOWN",
        "CONCENTRATION",
        "CONCENTRATION",
    ]


def test_risk_explicit_empty_matches_do_not_fallback(monkeypatch):
    def _fail_query(*_args, **_kwargs):
        raise AssertionError("explicit empty matches must not query")

    monkeypatch.setattr("app.database.list_fund_profiles", _fail_query)
    monkeypatch.setattr("app.database.get_fund_profile_by_code", _fail_query)
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        _fail_query,
    )

    assessment = evaluate_portfolio_risk(
        [_holding("654321", "Missing Profile", 100.0)],
        InvestorProfile(),
        matched_profiles=[],
    )

    assert assessment.weighted_return_percent == 3.5
