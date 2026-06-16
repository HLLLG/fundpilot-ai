from __future__ import annotations

from typing import Literal

from app.models import DecisionStyle, InvestorProfile

InvestmentPreset = Literal["conservative_hold", "aggressive_swing"]


def take_profit_threshold_percent(profile: InvestorProfile) -> float:
    fee = profile.round_trip_fee_percent if profile.round_trip_fee_percent is not None else 1.5
    net = profile.min_net_profit_percent if profile.min_net_profit_percent is not None else 1.0
    return round(fee + net, 2)


def apply_investment_preset(preset: InvestmentPreset, profile: InvestorProfile) -> InvestorProfile:
    if preset == "conservative_hold":
        return profile.model_copy(
            update={
                "investment_preset": preset,
                "style": "稳健",
                "horizon": "半年到一年",
                "decision_style": "conservative",
                "prefer_dca": True,
                "avoid_chasing": True,
                "max_drawdown_percent": 8,
                "concentration_limit_percent": 35,
                "swing_alerts_enabled": False,
                "swing_monitor_scope": "both",
            }
        )
    return profile.model_copy(
        update={
            "investment_preset": preset,
            "style": "激进",
            "horizon": "3-7天",
            "decision_style": "aggressive",
            "prefer_dca": False,
            "avoid_chasing": False,
            "max_drawdown_percent": 12,
            "concentration_limit_percent": 40,
            "round_trip_fee_percent": profile.round_trip_fee_percent or 1.5,
            "min_net_profit_percent": profile.min_net_profit_percent or 1.0,
            "hold_days_target": profile.hold_days_target or 7,
            "swing_alerts_enabled": True,
            "swing_monitor_scope": "both",
        }
    )


def is_short_term_style(decision_style: DecisionStyle | str) -> bool:
    return decision_style in {"tactical", "aggressive"}
