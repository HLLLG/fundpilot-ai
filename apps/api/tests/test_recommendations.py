from app.models import Holding, InvestorProfile
from app.services.recommendations import (
    build_offline_fund_recommendation,
    group_strings_to_fund_recommendations,
    merge_fund_recommendations,
    parse_fund_recommendations_raw,
    suggest_trade_amount,
)
from app.models import FundRecommendation


def test_suggest_reduce_amount_when_over_concentration():
    holding = Holding(
        fund_code="025856",
        fund_name="电网ETF",
        holding_amount=15000,
        return_percent=1.0,
    )
    profile = InvestorProfile(concentration_limit_percent=35)
    amount, note = suggest_trade_amount(holding, 52.8, 28400, profile, "减仓评估")
    assert amount is not None
    assert amount > 0
    assert note and "减仓" in note


def test_merge_same_fund_code():
    merged = merge_fund_recommendations(
        [
            FundRecommendation(
                fund_code="025856",
                fund_name="A",
                action="观察",
                points=["第一点"],
            ),
            FundRecommendation(
                fund_code="025856",
                fund_name="A",
                action="减仓评估",
                points=["第二点"],
                amount_yuan=3000,
                amount_note="减仓约3000元",
            ),
        ]
    )
    assert len(merged) == 1
    assert merged[0].action == "减仓评估"
    assert merged[0].amount_yuan == 3000
    assert len(merged[0].points) == 2


def test_group_bracket_style_lines():
    lines = [
        "[025856 · 减仓评估] 集中度52.8%严重超标",
        "[025856 · 暂停加仓] 等待净值回落",
        "组合已触发风险复核线。",
    ]
    holdings = [
        Holding(
            fund_code="025856",
            fund_name="电网ETF",
            holding_amount=15000,
            return_percent=1.0,
        )
    ]
    grouped = group_strings_to_fund_recommendations(lines, holdings)
    assert len(grouped) == 1
    assert grouped[0].action == "减仓评估"
    assert len(grouped[0].points) == 2


def test_parse_fund_recommendations_raw():
    raw = parse_fund_recommendations_raw(
        [
            {
                "fund_code": "008586",
                "fund_name": "人工智能",
                "action": "观察",
                "amount_yuan": 500,
                "amount_note": "加仓约500元",
                "points": ["跟踪偏差大"],
            }
        ]
    )
    assert raw[0].fund_code == "008586"
    assert raw[0].amount_yuan == 500


def test_offline_fund_recommendation_includes_amount_when_concentrated():
    holding = Holding(
        fund_code="025856",
        fund_name="电网ETF",
        holding_amount=15000,
        return_percent=1.0,
    )
    profile = InvestorProfile(concentration_limit_percent=35)
    rec = build_offline_fund_recommendation(holding, 52.8, 28400, profile)
    assert rec.action == "减仓评估"
    assert rec.amount_yuan is not None
