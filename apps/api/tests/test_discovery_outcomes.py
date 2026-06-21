from datetime import datetime, timezone

from app.services.discovery_outcomes import (
    _hit_take_profit_within_days,
    build_discovery_outcomes,
)


def test_hit_take_profit_within_3_days():
    rows = [
        {"date": "2026-06-01", "nav": 1.0},
        {"date": "2026-06-02", "nav": 1.0},
        {"date": "2026-06-03", "nav": 1.02},
        {"date": "2026-06-04", "nav": 1.03},
    ]
    hit = _hit_take_profit_within_days(
        rows,
        since_date="2026-06-02",
        forward_trading_days=3,
        threshold_percent=2.5,
    )
    assert hit is True


def test_hit_take_profit_within_3_days_misses_threshold():
    rows = [
        {"date": "2026-06-01", "nav": 1.0},
        {"date": "2026-06-02", "nav": 1.0},
        {"date": "2026-06-03", "nav": 1.01},
        {"date": "2026-06-04", "nav": 1.015},
    ]
    hit = _hit_take_profit_within_days(
        rows,
        since_date="2026-06-02",
        forward_trading_days=3,
        threshold_percent=2.5,
    )
    assert hit is False


def test_build_discovery_outcomes_includes_take_profit_summary():
    report = {
        "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc).isoformat(),
        "recommendations": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "action": "分批买入",
            }
        ],
        "discovery_facts": {
            "dip_swing": {"fee_break_even_percent": 2.5},
        },
    }

    def _fetch_nav(_code, trading_days=30):
        return {
            "data": [
                {"date": "2026-06-02", "nav": 1.0},
                {"date": "2026-06-03", "nav": 1.02},
                {"date": "2026-06-04", "nav": 1.03},
                {"date": "2026-06-05", "nav": 1.04},
            ]
        }

    outcome = build_discovery_outcomes(report, days=7, fetch_nav=_fetch_nav)
    assert outcome["has_data"] is True
    assert outcome["items"][0]["hit_take_profit_within_days"] is True
    assert "扣费止盈线" in outcome["message"]
