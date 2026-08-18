from __future__ import annotations

from app.models import InvestorProfile


def take_profit_threshold_percent(profile: InvestorProfile) -> float:
    fee = profile.round_trip_fee_percent if profile.round_trip_fee_percent is not None else 1.5
    net = profile.min_net_profit_percent if profile.min_net_profit_percent is not None else 1.0
    return round(fee + net, 2)
