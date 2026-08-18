from __future__ import annotations

from app.models import Holding
from app.services.holding_estimates import (
    compute_portfolio_total_assets,
    portfolio_official_nav_settled,
)


def _holding(
    fund_code: str,
    *,
    settled: float,
    daily_profit: float,
    source: str,
) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_code,
        holding_amount=settled,
        settled_holding_amount=settled,
        daily_profit=daily_profit,
        daily_return_percent=1.0,
        daily_return_percent_source=source,
    )


def test_estimated_daily_profit_is_excluded_from_total_assets() -> None:
    holdings = [
        _holding("015788", settled=10000, daily_profit=50, source="sector_estimate"),
        _holding("002610", settled=8000, daily_profit=58.92, source="sector_estimate"),
    ]
    assert portfolio_official_nav_settled(holdings) is False
    assert compute_portfolio_total_assets(holdings) == 18000


def test_official_daily_profit_joins_total_after_all_navs_publish() -> None:
    holdings = [
        _holding("015788", settled=10000, daily_profit=50, source="official_nav"),
        _holding("002610", settled=8000, daily_profit=58.92, source="official_nav"),
    ]
    assert portfolio_official_nav_settled(holdings) is True
    assert compute_portfolio_total_assets(holdings) == 18108.92


def test_mixed_official_and_estimate_still_excludes_daily_profit() -> None:
    holdings = [
        _holding("015788", settled=10000, daily_profit=50, source="official_nav"),
        _holding("002610", settled=8000, daily_profit=58.92, source="sector_estimate"),
    ]
    assert portfolio_official_nav_settled(holdings) is False
    assert compute_portfolio_total_assets(holdings) == 18000
