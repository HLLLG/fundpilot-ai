import pytest

from app.models import FundProfile, Holding
from app.services.holding_estimates import (
    compute_daily_profit,
    compute_estimated_holding_return_percent,
    compute_holding_profit,
    compute_official_daily_profit,
    overlay_official_nav_returns,
)
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    holding_daily_return_is_estimated,
)


def test_estimated_daily_is_sector_plus_settled_holding_return():
    holding = Holding(
        fund_code="000000",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=2.74,
        sector_return_percent=-0.16,
        holding_return_percent=2.74,
    )
    assert compute_estimated_daily_return_percent(holding) == 2.58
    assert holding_daily_return_is_estimated(holding) is True


def test_holding_profit_adds_settled_and_intraday_sector(monkeypatch):
    monkeypatch.setattr("app.database.get_fund_profile_by_code", lambda code: None)
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8186.06,
        holding_return_percent=-1.50,
        sector_return_percent=-3.56,
        sector_return_percent_source="closing_estimate",
    )
    assert compute_estimated_holding_return_percent(holding) == pytest.approx(-5.06)
    assert compute_holding_profit(holding) == pytest.approx(-416.33, abs=0.5)


def test_holding_profit_uses_official_nav_total_from_ocr(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda fund_code, trade_date: -3.36,
    )
    monkeypatch.setattr("app.database.get_fund_profile_by_code", lambda code: None)
    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8186.06,
            holding_return_percent=-1.50,
            sector_return_percent=-3.56,
            sector_return_percent_source="closing_estimate",
        )
    ]
    with_nav = overlay_official_nav_returns(holdings)
    assert with_nav[0].daily_return_percent_source == "official_nav"
    assert compute_holding_profit(with_nav[0]) == pytest.approx(-124.91, abs=0.5)


def test_official_daily_profit_uses_amount_before_settlement():
    assert compute_official_daily_profit(9508.74, -1.75) == pytest.approx(-169.37)


def test_daily_profit_matches_yangjibao_when_amount_includes_today(monkeypatch):
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9618.51,
        holding_shares=6734.71,
        source="test",
    )
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda code: profile if code == "025856" else None,
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9618.51,
        daily_return_percent=3.73,
        daily_return_percent_source="official_nav",
        amount_includes_today=True,
    )
    assert compute_daily_profit(holding) == pytest.approx(346.16, abs=0.5)
