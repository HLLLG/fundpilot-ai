from app.models import Holding, InvestorProfile
from app.services.tactical_recommendations import build_tactical_offline_fund_recommendation


def test_tactical_momentum_add_on_steady_rally(monkeypatch):
    monkeypatch.setattr(
        "app.services.tactical_recommendations.summarize_sector_intraday_for_holding",
        lambda holding: {
            "pattern_label": "steady_rally",
            "pattern_hint": "test",
        },
    )
    holding = Holding(
        fund_code="015608",
        fund_name="半导体基金",
        holding_amount=5000,
        sector_return_percent=3.2,
        sector_name="半导体",
    )
    nav_trend = {
        "recent_nav_series": [
            {"date": "2026-06-09", "nav": 1.0},
            {"date": "2026-06-10", "nav": 1.02},
        ]
    }
    rec = build_tactical_offline_fund_recommendation(
        holding,
        weight_percent=20,
        weight_denominator=25000,
        profile=InvestorProfile(decision_style="tactical"),
        northbound_net_yi=35,
        nav_trend=nav_trend,
    )
    assert rec.action == "分批加仓"


def test_tactical_take_profit_on_reversal():
    holding = Holding(
        fund_code="015608",
        fund_name="半导体基金",
        holding_amount=5000,
        sector_return_percent=1.5,
        sector_name="半导体",
    )
    nav_trend = {
        "recent_nav_series": [
            {"date": "2026-06-08", "nav": 1.0},
            {"date": "2026-06-09", "nav": 1.03},
            {"date": "2026-06-10", "nav": 1.01},
        ]
    }
    rec = build_tactical_offline_fund_recommendation(
        holding,
        weight_percent=20,
        weight_denominator=25000,
        profile=InvestorProfile(decision_style="tactical"),
        nav_trend=nav_trend,
    )
    assert rec.action == "减仓评估"
