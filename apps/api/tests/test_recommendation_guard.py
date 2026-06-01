from app.models import FundRecommendation, Holding, InvestorProfile, RiskAssessment, RiskAlert
from app.services.recommendation_guard import (
    apply_recommendation_guards,
    conservative_action_text,
    normalize_action_text,
)
from app.models import AnalysisRequest


def test_normalize_action_aliases():
    assert normalize_action_text("建议加仓") == "分批加仓"
    assert normalize_action_text("") == "观察"


def test_conservative_action_prefers_offline_reduce():
    assert conservative_action_text("分批加仓", "减仓评估") == "减仓评估"
    assert conservative_action_text("观察", "分批加仓") == "观察"


def test_risk_review_caps_aggressive_llm_action():
    request = AnalysisRequest(
        holdings=[
            Holding(
                fund_code="015608",
                fund_name="测试基金",
                holding_amount=10000,
                return_percent=-9,
                sector_return_percent=6,
            )
        ],
        profile=InvestorProfile(),
    )
    risk = RiskAssessment(
        level="high",
        suggested_action="risk_review",
        weighted_return_percent=-9,
        alerts=[
            RiskAlert(
                code="MAX_DRAWDOWN",
                severity="high",
                message="触发复核",
                evidence="test",
            )
        ],
    )
    llm_rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试基金",
        action="分批加仓",
        points=["模型建议加仓"],
    )

    portfolio, guarded = apply_recommendation_guards(
        [llm_rec],
        ["今日可以加仓"],
        request,
        risk,
    )

    assert guarded[0].action in {"暂停追涨", "减仓评估", "风控复核", "观察"}
    assert "风险复核" in portfolio[0] or "控风险" in portfolio[0]
