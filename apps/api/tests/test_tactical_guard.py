from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile, NewsItem, RiskAssessment
from app.services.recommendation_guard import apply_recommendation_guards


def test_tactical_mode_allows_add_without_today_news():
    request = AnalysisRequest(
        holdings=[
            Holding(
                fund_code="015608",
                fund_name="测试",
                holding_amount=5000,
                return_percent=1,
                sector_return_percent=6.0,
            )
        ],
        profile=InvestorProfile(decision_style="tactical", avoid_chasing=True),
    )
    risk = RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=1,
        alerts=[],
    )
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试",
        action="分批加仓",
        points=[],
    )
    news = [NewsItem(topic="半导体", title="旧闻", is_today=False)]

    _, guarded = apply_recommendation_guards([rec], [], request, risk, news, [])

    assert guarded[0].action == "分批加仓"
