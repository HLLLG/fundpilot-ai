from app.models import InvestorProfile
from app.services.investment_presets import (
    apply_investment_preset,
    take_profit_threshold_percent,
)


def test_take_profit_threshold_default():
    profile = InvestorProfile(decision_style="aggressive")
    assert take_profit_threshold_percent(profile) == 2.5


def test_apply_aggressive_swing_preset():
    base = InvestorProfile()
    result = apply_investment_preset("aggressive_swing", base)
    assert result.decision_style == "aggressive"
    assert result.avoid_chasing is False
    assert result.prefer_dca is False
    assert result.horizon == "3-7天"


def test_apply_conservative_hold_preset():
    aggressive = apply_investment_preset("aggressive_swing", InvestorProfile())
    result = apply_investment_preset("conservative_hold", aggressive)
    assert result.decision_style == "conservative"
    assert result.avoid_chasing is True
