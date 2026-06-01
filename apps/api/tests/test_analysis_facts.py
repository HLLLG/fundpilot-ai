from app.models import FundSnapshot, Holding, InvestorProfile
from app.services.analysis_facts import build_analysis_facts
from app.services.risk import evaluate_portfolio_risk


def test_build_analysis_facts_marks_concentration():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=8000,
            return_percent=-2,
            sector_return_percent=1.2,
        ),
        Holding(
            fund_code="008114",
            fund_name="基金B",
            holding_amount=2000,
            return_percent=1,
        ),
    ]
    profile = InvestorProfile(concentration_limit_percent=35)
    risk = evaluate_portfolio_risk(holdings, profile)
    facts = build_analysis_facts(holdings, risk, [], profile)

    by_code = {item["fund_code"]: item for item in facts["holdings"]}
    assert by_code["015608"]["over_concentration"] is True
    assert by_code["015608"]["weight_percent"] == 80.0
    assert facts["portfolio"]["total_amount"] == 10000
