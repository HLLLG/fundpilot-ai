from app.models import Holding
from app.services.sector_momentum import build_sector_momentum_context


def test_two_day_reversal_down_pattern():
    holding = Holding(
        fund_code="015608",
        fund_name="测试基金",
        holding_amount=5000,
        sector_return_percent=2.0,
    )
    nav_trend = {
        "recent_nav_series": [
            {"date": "2026-06-06", "nav": 1.0},
            {"date": "2026-06-09", "nav": 1.02},
            {"date": "2026-06-10", "nav": 1.008},
        ]
    }
    ctx = build_sector_momentum_context(holding, nav_trend)
    assert ctx is not None
    assert ctx["pattern_label"] == "two_day_reversal_down"
    assert ctx["reversal_risk"] == "high"


def test_sector_momentum_none_without_data():
    holding = Holding(fund_code="015608", fund_name="测试", holding_amount=1000)
    assert build_sector_momentum_context(holding, None) is None
