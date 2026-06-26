"""盘中板块刷新应清除快照里残留的 official_nav 当日收益。"""

from app.models import Holding
from app.services.holding_estimates import (
    apply_sector_daily_estimates,
    compute_estimated_daily_return_percent,
    enrich_holding_estimates,
)
from app.services.portfolio_persistence import merge_holdings_with_snapshot


def test_merge_clears_stale_official_nav_when_sector_refresh_clears_daily(monkeypatch):
    """板块刷新将 daily 置 None 时，merge 不得从快照捞回 official_nav。"""
    previous = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=9068.69,
            holding_return_percent=8.36,
            daily_return_percent=3.44,
            daily_profit=301.87,
            daily_return_percent_source="official_nav",
            sector_return_percent=3.44,
            sector_return_percent_source="closing_estimate",
        ),
    ]
    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_most_recent_portfolio_snapshot",
        lambda: {"holdings": [h.model_dump() for h in previous]},
    )
    incoming = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=9068.69,
            holding_return_percent=8.36,
            sector_return_percent=-5.02,
            sector_return_percent_source="realtime",
            daily_return_percent=None,
            daily_profit=None,
            daily_return_percent_source=None,
        ),
    ]
    merged = merge_holdings_with_snapshot(incoming)
    assert len(merged) == 1
    assert merged[0].sector_return_percent == -5.02
    assert merged[0].daily_return_percent_source is None
    assert merged[0].daily_return_percent is None


def test_enrich_after_merge_applies_sector_daily_estimate():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        holding_return_percent=8.36,
        sector_return_percent=-5.02,
        sector_return_percent_source="realtime",
    )
    enriched = enrich_holding_estimates(holding)
    assert enriched.daily_return_percent_source == "sector_estimate"
    assert enriched.daily_return_percent == -5.02
    assert enriched.daily_profit is not None
    assert enriched.daily_profit < 0


def test_estimated_daily_return_is_sector_not_settled_plus_sector():
    holding = Holding(
        fund_code="000000",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=2.74,
        holding_return_percent=2.74,
        sector_return_percent=-0.16,
    )
    assert compute_estimated_daily_return_percent(holding) == -0.16
    assert apply_sector_daily_estimates(holding).daily_return_percent == -0.16
