from __future__ import annotations

from app.models import FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_facts import build_analysis_facts


def test_holding_facts_separate_trade_date_from_latest_nav_date():
    holding = Holding(
        fund_code="008586",
        fund_name="AI ETF Link",
        holding_amount=9068.69,
        holding_return_percent=9.45,
        daily_return_percent=-4.62,
        daily_return_percent_source="sector_estimate",
        sector_name="AI",
        sector_return_percent=-4.62,
        sector_return_percent_source="realtime",
    )
    risk = RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )
    facts = build_analysis_facts(
        [holding],
        risk,
        [
            FundSnapshot(
                fund_code="008586",
                fund_name="AI ETF Link",
                latest_nav=1.9347,
                nav_date="2026-06-25",
                source="akshare",
            )
        ],
        InvestorProfile(),
        session={
            "session_kind": "trading_day_after_close",
            "effective_trade_date": "2026-06-26",
        },
    )

    row = facts["holdings"][0]
    assert row["nav_date"] == "2026-06-25"
    assert row["daily_return_trade_date"] == "2026-06-26"
    assert row["daily_return_data_source"] == "sector_estimate"
    assert row["nav_date_is_current_trade_date"] is False
    assert facts["data_freshness"]["effective_trade_date"] == "2026-06-26"
    assert facts["data_freshness"]["has_stale_nav_dates"] is True
