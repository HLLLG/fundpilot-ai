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
