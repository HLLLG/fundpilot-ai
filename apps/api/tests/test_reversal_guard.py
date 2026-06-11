from unittest.mock import patch

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile, RiskAssessment
from app.services.recommendation_guard import apply_recommendation_guards


def _request(style: str = "conservative") -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="015608",
                fund_name="测试基金",
                holding_amount=5000,
                return_percent=2,
                sector_return_percent=2.0,
                sector_name="半导体",
            )
        ],
        profile=InvestorProfile(decision_style=style),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=2,
        alerts=[],
    )


def _strict_guard_policy() -> dict:
    return {
        "tighten_tactical": False,
        "enforce_reversal_block": True,
        "enforce_pullback_block": True,
        "hints": [],
        "reason": None,
    }


def test_conservative_blocks_add_on_two_day_reversal_down():
    nav_trend = {
        "recent_nav_series": [
            {"date": "2026-06-06", "nav": 1.0},
            {"date": "2026-06-09", "nav": 1.02},
            {"date": "2026-06-10", "nav": 1.008},
        ]
    }
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试基金",
        action="分批加仓",
        points=[],
    )

    with (
        patch(
            "app.services.recommendation_guard.resolve_signal_guard_policy",
            return_value=_strict_guard_policy(),
        ),
        patch(
            "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
            return_value=None,
        ),
    ):
        _, guarded = apply_recommendation_guards(
            [rec],
            [],
            _request("conservative"),
            _risk(),
            nav_trends_by_code={"015608": nav_trend},
        )

    assert guarded[0].action == "暂停追涨"
    assert any("回吐" in point for point in guarded[0].points)


def test_tactical_blocks_add_on_intraday_pullback():
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试基金",
        action="分批加仓",
        points=[],
    )
    intraday = {"pattern_label": "intraday_pullback"}

    with (
        patch(
            "app.services.recommendation_guard.resolve_signal_guard_policy",
            return_value=_strict_guard_policy(),
        ),
        patch(
            "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
            return_value=intraday,
        ),
    ):
        _, guarded = apply_recommendation_guards(
            [rec],
            [],
            _request("tactical"),
            _risk(),
        )

    assert guarded[0].action == "观察"
    assert any("冲高回落" in point for point in guarded[0].points)
