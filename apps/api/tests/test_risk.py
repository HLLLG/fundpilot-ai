import pytest

from app.models import Holding, InvestorProfile
from app.services.risk import evaluate_portfolio_risk


def test_drawdown_at_limit_raises_high_risk_alert():
    profile = InvestorProfile(max_drawdown_percent=8)
    holdings = [
        Holding(
            fund_code="000001",
            fund_name="稳健成长混合",
            holding_amount=10000,
            return_percent=-8.2,
        )
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert result.level == "high"
    assert any(alert.code == "MAX_DRAWDOWN" for alert in result.alerts)
    assert result.suggested_action == "risk_review"


def test_single_holding_above_concentration_limit_is_flagged():
    profile = InvestorProfile(concentration_limit_percent=35)
    holdings = [
        Holding(
            fund_code="000001",
            fund_name="主题成长混合",
            holding_amount=7000,
            return_percent=1.5,
        ),
        Holding(
            fund_code="000002",
            fund_name="债券增强",
            holding_amount=3000,
            return_percent=0.8,
        ),
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert result.level == "medium"
    assert any(alert.code == "CONCENTRATION" for alert in result.alerts)
    concentration = next(alert for alert in result.alerts if alert.code == "CONCENTRATION")
    assert "主题成长混合" in concentration.message


def test_concentration_uses_expected_investment_denominator():
    profile = InvestorProfile(concentration_limit_percent=35, expected_investment_amount=30000)
    holdings = [
        Holding(
            fund_code="000001",
            fund_name="主题成长混合",
            holding_amount=10000,
            return_percent=1.5,
        ),
        Holding(
            fund_code="000002",
            fund_name="债券增强",
            holding_amount=8000,
            return_percent=0.8,
        ),
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert not any(alert.code == "CONCENTRATION" for alert in result.alerts)


def test_balanced_portfolio_returns_watch_action():
    profile = InvestorProfile(max_drawdown_percent=8, concentration_limit_percent=35)
    holdings = [
        Holding(fund_code="000001", fund_name="均衡 A", holding_amount=3300, return_percent=-1.0),
        Holding(fund_code="000002", fund_name="均衡 B", holding_amount=3300, return_percent=0.5),
        Holding(fund_code="000003", fund_name="均衡 C", holding_amount=3400, return_percent=1.0),
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert result.level == "medium"
    assert result.suggested_action == "watch"


def test_drawdown_uses_estimated_holding_return_with_sector_rebound():
    """盘中反弹后：结算 -9.08% + 板块 +2.48% ≈ -6.60%，不应触发 8% 浮亏线。"""
    profile = InvestorProfile(max_drawdown_percent=8)
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=815.57,
            return_percent=-9.08,
            holding_return_percent=-9.08,
            sector_return_percent=2.48,
            daily_return_percent=2.48,
            daily_return_percent_source="sector_estimate",
        )
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert result.level == "medium"
    assert result.weighted_return_percent == pytest.approx(-6.6, abs=0.05)
    assert not any(alert.code == "MAX_DRAWDOWN" for alert in result.alerts)
    assert not any(alert.code == "HOLDING_DRAWDOWN" for alert in result.alerts)


def test_no_holding_drawdown_after_intraday_sector_rebound():
    profile = InvestorProfile(max_drawdown_percent=8)
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=815.57,
            return_percent=-9.08,
            holding_return_percent=-9.08,
            sector_return_percent=2.48,
            daily_return_percent=2.48,
            daily_return_percent_source="sector_estimate",
        )
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    assert not any(alert.code == "HOLDING_DRAWDOWN" for alert in result.alerts)


def test_holding_drawdown_alert_when_effective_return_breaches_limit():
    profile = InvestorProfile(max_drawdown_percent=8)
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=815.57,
            return_percent=-9.08,
            holding_return_percent=-9.08,
            sector_return_percent=-0.5,
            daily_return_percent=-0.5,
            daily_return_percent_source="sector_estimate",
        )
    ]

    result = evaluate_portfolio_risk(holdings, profile)

    holding_alert = next(alert for alert in result.alerts if alert.code == "HOLDING_DRAWDOWN")
    assert "易方达国防军工混合C" in holding_alert.message
    assert "-9.58" in holding_alert.message or "-9.5" in holding_alert.message
