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
