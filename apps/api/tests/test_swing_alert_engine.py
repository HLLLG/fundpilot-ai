from __future__ import annotations

import pytest

from app.models import Holding, InvestorProfile
from app.services.swing_alert_engine import evaluate_swing_alerts


@pytest.fixture(autouse=True)
def intraday_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.swing_alert_engine.build_trading_session",
        lambda when=None: {
            "effective_trade_date": "2026-06-16",
            "session_kind": "trading_day_intraday",
        },
    )


def test_take_profit_alert():
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=35_000,
        holding_return_percent=2.0,
        sector_return_percent=0.8,
        sector_name="半导体",
    )
    profile = InvestorProfile(
        decision_style="aggressive",
        swing_alerts_enabled=True,
        round_trip_fee_percent=1.5,
        min_net_profit_percent=1.0,
    )
    items, trade_date, session_kind = evaluate_swing_alerts([holding], profile)
    assert trade_date
    assert session_kind
    types = {item.alert_type for item in items}
    assert "take_profit" in types


def test_dip_buy_holding_alert():
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=10_000,
        holding_return_percent=-2.0,
        sector_return_percent=-2.2,
        sector_name="半导体",
    )
    profile = InvestorProfile(
        decision_style="aggressive",
        swing_alerts_enabled=True,
        concentration_limit_percent=100,
        expected_investment_amount=100_000,
    )
    items, _, _ = evaluate_swing_alerts(
        [holding],
        profile,
        nav_trends_by_code={"519674": {"recent_5d_change_percent": -5.0}},
    )
    assert any(item.alert_type == "dip_buy" for item in items)


def test_sector_dip_alert_full_market():
    profile = InvestorProfile(decision_style="aggressive", swing_alerts_enabled=True)
    sector_heat = [
        {"sector_label": "半导体", "change_1d_percent": -3.2, "change_5d_percent": -1.0},
        {"sector_label": "商业航天", "change_1d_percent": 1.0, "change_5d_percent": 2.0},
    ]
    items, _, _ = evaluate_swing_alerts(
        [],
        profile,
        monitor_scope="full_market",
        sector_heat=sector_heat,
    )
    assert len(items) == 1
    assert items[0].alert_type == "sector_dip"
    assert items[0].sector_label == "半导体"


def test_disabled_when_conservative_without_flag():
    holding = Holding(
        fund_code="519674",
        fund_name="测试",
        holding_amount=10_000,
        holding_return_percent=5.0,
        sector_return_percent=1.0,
    )
    profile = InvestorProfile(decision_style="conservative", swing_alerts_enabled=False)
    items, _, _ = evaluate_swing_alerts([holding], profile)
    assert items == []
