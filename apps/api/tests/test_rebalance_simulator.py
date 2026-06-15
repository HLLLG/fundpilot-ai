from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile
from app.services.rebalance_simulator import simulate_rebalance


def test_simulate_reduce_updates_weights():
    request = AnalysisRequest(
        holdings=[
            Holding(fund_code="015608", fund_name="A", holding_amount=8000, return_percent=0),
            Holding(fund_code="008114", fund_name="B", holding_amount=2000, return_percent=0),
        ],
        profile=InvestorProfile(concentration_limit_percent=35),
    )
    recs = [
        FundRecommendation(
            fund_code="015608",
            fund_name="A",
            action="减仓评估",
            amount_yuan=1500,
            points=[],
        )
    ]

    result = simulate_rebalance(request, recs)
    row = next(item for item in result["rows"] if item["fund_code"] == "015608")

    assert row["delta_yuan"] == -1500
    assert row["simulated_amount"] == 6500
    assert row["simulated_weight_percent"] < row["current_weight_percent"]


def test_simulate_suggests_amount_when_rec_has_action_but_no_amount():
    request = AnalysisRequest(
        holdings=[
            Holding(fund_code="015608", fund_name="A", holding_amount=8000, return_percent=0),
            Holding(fund_code="008114", fund_name="B", holding_amount=2000, return_percent=0),
        ],
        profile=InvestorProfile(concentration_limit_percent=35),
    )
    recs = [
        FundRecommendation(
            fund_code="015608",
            fund_name="A",
            action="减仓评估",
            points=[],
        )
    ]

    result = simulate_rebalance(request, recs)
    row = next(item for item in result["rows"] if item["fund_code"] == "015608")

    assert row["delta_yuan"] < 0
    assert row["simulated_amount"] < row["current_amount"]


def test_simulate_over_concentration_with_observe_action_still_suggests_reduce():
    request = AnalysisRequest(
        holdings=[
            Holding(fund_code="008586", fund_name="华夏人工智能ETF联接C", holding_amount=7821.5, return_percent=0),
            Holding(fund_code="025856", fund_name="华夏中证全指电力设备主题ETF联接A", holding_amount=6962.92, return_percent=0),
            Holding(fund_code="015945", fund_name="易方达国防军工混合C", holding_amount=815.57, return_percent=0),
            Holding(fund_code="519674", fund_name="银河创新成长混合A", holding_amount=3990.95, return_percent=0),
        ],
        profile=InvestorProfile(concentration_limit_percent=35),
    )
    recs = [
        FundRecommendation(fund_code="008586", fund_name="华夏人工智能ETF联接C", action="观察", points=[]),
        FundRecommendation(fund_code="025856", fund_name="华夏中证全指电力设备主题ETF联接A", action="观察", points=[]),
        FundRecommendation(fund_code="015945", fund_name="易方达国防军工混合C", action="减仓评估", points=[]),
        FundRecommendation(fund_code="519674", fund_name="银河创新成长混合A", action="观察", points=[]),
    ]

    result = simulate_rebalance(request, recs)
    ai_row = next(item for item in result["rows"] if item["fund_code"] == "008586")
    grid_row = next(item for item in result["rows"] if item["fund_code"] == "025856")
    defense_row = next(item for item in result["rows"] if item["fund_code"] == "015945")

    assert ai_row["delta_yuan"] < 0
    assert grid_row["delta_yuan"] < 0
    assert defense_row["delta_yuan"] < 0
    assert result["simulated_total"] < result["current_total"]


def test_simulate_partial_reduce_for_small_position_risk_review():
    request = AnalysisRequest(
        holdings=[
            Holding(fund_code="015945", fund_name="易方达国防军工混合C", holding_amount=815.57, return_percent=-6),
            Holding(fund_code="008586", fund_name="华夏人工智能ETF联接C", holding_amount=7000, return_percent=0),
        ],
        profile=InvestorProfile(concentration_limit_percent=35),
    )
    recs = [
        FundRecommendation(fund_code="015945", fund_name="易方达国防军工混合C", action="减仓评估", points=[]),
    ]

    result = simulate_rebalance(request, recs)
    defense_row = next(item for item in result["rows"] if item["fund_code"] == "015945")

    assert defense_row["delta_yuan"] == -122
    assert defense_row["simulated_amount"] == 693.57
