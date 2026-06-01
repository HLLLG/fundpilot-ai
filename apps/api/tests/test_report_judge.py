from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile
from app.services.report_judge import _rule_judge
from app.services.risk import evaluate_portfolio_risk


def test_rule_judge_downgrades_add_under_risk_review():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金",
            holding_amount=10000,
            return_percent=-9,
        )
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    request = AnalysisRequest(holdings=holdings, profile=profile)
    parsed = {
        "title": "t",
        "summary": "建议加仓",
        "fund_recommendations": [
            {
                "fund_code": "015608",
                "fund_name": "基金",
                "action": "分批加仓",
                "points": [],
            }
        ],
    }

    judged = _rule_judge(parsed, request, risk, [])

    assert judged["fund_recommendations"][0]["action"] != "分批加仓"
