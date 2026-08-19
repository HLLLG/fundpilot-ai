"""日报输出瘦身：公告不进战术新闻、模型不再被要求复述证据栏。"""

from __future__ import annotations

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.news_citation import apply_news_citation_guards
from app.services.news_service import skipped_daily_announcement_facts
from app.services.recommendation_guard import apply_recommendation_guards


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def test_skipped_daily_announcement_facts_are_explicit() -> None:
    facts = skipped_daily_announcement_facts()
    assert facts["status"] == "skipped_for_daily_tactical"
    assert facts["requested"] == 0
    assert facts["reason"] == "daily_report_excludes_fund_announcements"


def test_news_citation_drops_placeholders_instead_of_filling_them() -> None:
    recs = apply_news_citation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="观察",
                news_bullish=["暂无明确利好"],
                news_bearish=["暂无明确利空"],
            )
        ],
        [NewsItem(topic="半导体", title="半导体规划落地", is_today=True)],
    )

    assert recs[0].news_bullish == []
    assert recs[0].news_bearish == []


def test_guard_does_not_repeat_final_action_or_redemption_disclaimer_in_points(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )

    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="观察",
                points=[
                    "半导体资金持续流出，先观察。",
                    "系统校验后的最终动作：观察。",
                    "赎回开放已核验，但缺少逐笔申购时间，无法确认锁定期与适用赎回费；保留减仓比例。",
                ],
            )
        ],
        [],
        _request(),
        RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=1.2,
            alerts=[],
        ),
        [NewsItem(topic="半导体", title="半导体规划落地", is_today=True)],
        facts={"holdings": [{"fund_code": "519674"}], "allowed_actions": ["观察", "暂停追涨"]},
    )

    rec = guarded[0]
    assert all("系统校验后的最终动作" not in point for point in rec.points)
    assert all(not point.startswith("赎回开放已核验，但缺少逐笔申购时间") for point in rec.points)
    assert "调整比例已由系统按最终动作重新计算" not in "\n".join(rec.validation_notes)
    assert rec.action == "观察"
