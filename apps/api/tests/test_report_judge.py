from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile
from app.services.analysis_runtime import resolve_analysis_runtime
from app.services.report_judge import _rule_judge, judge_parsed_report
from app.services.risk import evaluate_portfolio_risk
from app.config import get_settings


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


def test_judge_parsed_report_accepts_analysis_runtime_mode_field():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金",
            holding_amount=10000,
            return_percent=1.0,
        )
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    request = AnalysisRequest(holdings=holdings, profile=profile, analysis_mode="fast")
    runtime = resolve_analysis_runtime(get_settings(), request.analysis_mode)
    parsed = {
        "title": "t",
        "summary": "观察为主",
        "fund_recommendations": [
            {
                "fund_code": "015608",
                "fund_name": "基金",
                "action": "观察",
                "points": [],
            }
        ],
    }

    judged, meta = judge_parsed_report(parsed, request, risk, [], runtime)

    assert judged["fund_recommendations"][0]["action"] == "观察"
    assert meta["rule_judge"] is True
