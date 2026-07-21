from __future__ import annotations

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile
from app.services.rebalance_simulator import simulate_rebalance


def _request(holdings: list[Holding]) -> AnalysisRequest:
    return AnalysisRequest(
        holdings=holdings,
        profile=InvestorProfile(
            expected_investment_amount=sum(item.holding_amount for item in holdings),
            concentration_limit_percent=100,
            avoid_chasing=False,
        ),
    )


def _holding(code: str, name: str, amount: float) -> Holding:
    return Holding(fund_code=code, fund_name=name, holding_amount=amount)


def _recommendation(
    code: str,
    name: str,
    action: str,
    amount: float,
) -> FundRecommendation:
    return FundRecommendation(
        fund_code=code,
        fund_name=name,
        action=action,
        amount_yuan=amount,
    )


def test_simulator_keeps_multiple_unknown_codes_bound_to_holding_index() -> None:
    holdings = [
        _holding("000000", "未知甲", 1_000),
        _holding("000000", "未知乙", 9_000),
    ]
    recommendations = [
        _recommendation("000000", "未知甲", "分批加仓", 100),
        _recommendation("000000", "未知乙", "减仓评估", 900),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["fund_name"] for row in result["rows"]] == ["未知甲", "未知乙"]
    assert [row["action"] for row in result["rows"]] == ["分批加仓", "减仓评估"]
    assert [row["delta_yuan"] for row in result["rows"]] == [100, -900]


def test_simulator_uses_index_for_indistinguishable_unknown_identities() -> None:
    holdings = [
        _holding("000000", "同名基金", 1_000),
        _holding("000000", "同名基金", 9_000),
    ]
    recommendations = [
        _recommendation("000000", "同名基金", "分批加仓", 100),
        _recommendation("000000", "同名基金", "减仓评估", 900),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["action"] for row in result["rows"]] == ["分批加仓", "减仓评估"]
    assert [row["delta_yuan"] for row in result["rows"]] == [100, -900]


def test_simulator_keeps_duplicate_real_codes_bound_to_holding_index() -> None:
    holdings = [
        _holding("000001", "真实甲", 2_000),
        _holding("000001", "真实乙", 8_000),
    ]
    recommendations = [
        _recommendation("000001", "真实甲", "分批加仓", 200),
        _recommendation("000001", "真实乙", "减仓评估", 800),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["fund_name"] for row in result["rows"]] == ["真实甲", "真实乙"]
    assert [row["action"] for row in result["rows"]] == ["分批加仓", "减仓评估"]
    assert [row["delta_yuan"] for row in result["rows"]] == [200, -800]


def test_simulator_uses_exact_identity_fallback_for_reordered_recommendations() -> None:
    holdings = [
        _holding("000000", "未知甲", 1_000),
        _holding("000000", "未知乙", 9_000),
    ]
    recommendations = [
        _recommendation("000000", "未知乙", "减仓评估", 900),
        _recommendation("000000", "未知甲", "分批加仓", 100),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["action"] for row in result["rows"]] == ["分批加仓", "减仓评估"]
    assert [row["delta_yuan"] for row in result["rows"]] == [100, -900]


def test_simulator_does_not_reuse_one_recommendation_for_a_duplicate_code() -> None:
    holdings = [
        _holding("000001", "真实甲", 2_000),
        _holding("000001", "真实乙", 8_000),
    ]
    recommendations = [
        _recommendation("000001", "真实乙", "减仓评估", 800),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["action"] for row in result["rows"]] == ["观察", "减仓评估"]
    assert [row["delta_yuan"] for row in result["rows"]] == [0, -800]


def test_simulator_fails_closed_for_incomplete_indistinguishable_identity_group() -> None:
    holdings = [
        _holding("000000", "同名基金", 1_000),
        _holding("000000", "同名基金", 9_000),
    ]
    recommendations = [
        _recommendation("000000", "同名基金", "减仓评估", 900),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert [row["action"] for row in result["rows"]] == ["观察", "观察"]
    assert [row["delta_yuan"] for row in result["rows"]] == [0, 0]


def test_simulator_matches_a_unique_real_code_after_a_historical_name_change() -> None:
    holdings = [_holding("000001", "基金新名称", 2_000)]
    recommendations = [
        _recommendation("000001", "基金旧名称", "减仓评估", 200),
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert result["rows"][0]["action"] == "减仓评估"
    assert result["rows"][0]["delta_yuan"] == -200


def test_simulator_uses_server_percentage_estimate_before_legacy_fallback() -> None:
    holdings = [_holding("000001", "测试基金", 10_000)]
    recommendations = [
        FundRecommendation(
            fund_code="000001",
            fund_name="测试基金",
            action="减仓评估",
            suggested_position_change_percent=-(100 / 3),
            estimated_position_change_amount_yuan=3333.33,
        )
    ]

    result = simulate_rebalance(_request(holdings), recommendations)

    assert result["rows"][0]["delta_yuan"] == -3333.33
    assert result["rows"][0]["amount_note"] == "按日报仓位比例和报告持仓估值折算"
