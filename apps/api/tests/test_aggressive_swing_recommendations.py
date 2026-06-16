from app.models import Holding, InvestorProfile
from app.services.aggressive_swing_recommendations import (
    build_aggressive_swing_offline_fund_recommendation,
)


def test_aggressive_take_profit_at_threshold():
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=35_000,
        holding_return_percent=2.0,
        sector_return_percent=0.8,
        sector_name="半导体",
    )
    rec = build_aggressive_swing_offline_fund_recommendation(
        holding,
        weight_percent=35.0,
        weight_denominator=100_000,
        profile=InvestorProfile(
            decision_style="aggressive",
            round_trip_fee_percent=1.5,
            min_net_profit_percent=1.0,
        ),
        nav_trend={"recent_5d_change_percent": 1.0},
    )
    assert rec.action == "减仓评估"
    assert "止盈" in rec.points[0]


def test_aggressive_dip_buy_on_sector_decline():
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=10_000,
        holding_return_percent=-2.0,
        sector_return_percent=-2.2,
        sector_name="半导体",
    )
    rec = build_aggressive_swing_offline_fund_recommendation(
        holding,
        weight_percent=10.0,
        weight_denominator=100_000,
        profile=InvestorProfile(decision_style="aggressive"),
        nav_trend={"recent_5d_change_percent": -5.0},
    )
    assert rec.action == "分批加仓"
    assert "回调买入" in rec.points[0]
