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


def test_build_analysis_facts_includes_nav_trend():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    nav_trends = {
        "015608": {
            "period_change_percent": 3.2,
            "trend_label": "温和上行",
            "recent_nav_series": [{"date": "2026-05-30", "nav": 1.05}],
        }
    }
    snapshots = [
        FundSnapshot(
            fund_code="015608",
            fund_name="基金A",
            latest_nav=1.05,
            source="akshare",
        )
    ]
    facts = build_analysis_facts(
        holdings, risk, snapshots, profile, nav_trends_by_code=nav_trends
    )

    assert facts["holdings"][0]["nav_trend"]["trend_label"] == "温和上行"
    assert facts["holdings"][0]["latest_nav"] == 1.05
