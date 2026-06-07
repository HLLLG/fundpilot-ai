import pytest

from app.models import Holding
from app.services.holding_estimates import (
    apply_sector_daily_estimates,
    compute_daily_profit,
    compute_estimated_holding_return_percent,
    compute_holding_profit,
    compute_official_daily_profit,
    compute_yesterday_profit,
    overlay_official_nav_returns,
)
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    holding_analysis_payload,
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


def test_actual_daily_return_is_not_reestimated():
    holding = Holding(
        fund_code="000000",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=2.74,
        daily_return_percent=-0.5,
        sector_return_percent=-0.16,
        holding_return_percent=2.74,
    )

    assert compute_estimated_daily_return_percent(holding) == -0.5
    assert holding_daily_return_is_estimated(holding) is False


def test_holding_analysis_payload_includes_estimate_fields():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=7427.01,
        return_percent=-1.12,
        sector_return_percent=-2.23,
        holding_return_percent=-1.12,
    )
    payload = holding_analysis_payload(holding)

    assert payload["estimated_daily_return_percent"] == -3.35
    assert payload["daily_return_is_estimated"] is True


def test_migrate_legacy_official_nav_on_sector_field():
    from app.services.holding_migration import migrate_legacy_holding_payload

    raw = {
        "fund_code": "025856",
        "fund_name": "测试",
        "holding_amount": 10000,
        "sector_return_percent": -1.75,
        "sector_return_percent_source": "official_nav",
    }
    migrated = migrate_legacy_holding_payload(raw)
    holding = Holding.model_validate(migrated)
    assert holding.daily_return_percent == -1.75
    assert holding.daily_return_percent_source == "official_nav"
    assert holding.sector_return_percent is None
    assert holding.sector_return_percent_source is None


def test_holding_profit_adds_settled_and_intraday_sector():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8186.06,
        holding_return_percent=-1.50,
        holding_profit=-124.91,
        sector_return_percent=-3.56,
        sector_return_percent_source="closing_estimate",
    )
    assert compute_estimated_holding_return_percent(holding) == pytest.approx(-5.06)
    assert compute_holding_profit(holding) == pytest.approx(-416.33)


def test_holding_profit_uses_official_nav_total_from_ocr(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda fund_code, trade_date: -3.36,
    )
    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8186.06,
            holding_return_percent=-1.50,
            holding_profit=-124.91,
            sector_return_percent=-3.56,
            sector_return_percent_source="closing_estimate",
        )
    ]
    with_nav = overlay_official_nav_returns(holdings)
    assert with_nav[0].daily_return_percent_source == "official_nav"
    assert with_nav[0].daily_return_percent == pytest.approx(-3.36)
    assert compute_estimated_holding_return_percent(with_nav[0]) == pytest.approx(-1.50)
    assert compute_holding_profit(with_nav[0]) == pytest.approx(-124.91)


def test_official_daily_profit_uses_amount_before_settlement():
    assert compute_official_daily_profit(9508.74, -1.75) == pytest.approx(-169.37)


def test_official_daily_profit_overrides_wrong_ocr_daily_profit():
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9508.74,
        daily_profit=-166.40,
        daily_return_percent=-1.75,
        daily_return_percent_source="official_nav",
    )
    assert compute_daily_profit(holding) == pytest.approx(-169.37)


def test_yesterday_profit_falls_back_to_ocr_without_nav(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_nav_service.compute_yesterday_profit_from_official_nav",
        lambda fund_code, holding_amount, trade_date: None,
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9508.74,
        yesterday_profit=-86.23,
        daily_return_percent=-1.75,
        daily_return_percent_source="official_nav",
    )
    assert compute_yesterday_profit(holding) == pytest.approx(-86.23)


def test_grid_fund_official_nav_holding_profit_not_double_counted():
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9508.74,
        holding_return_percent=2.43,
        holding_profit=225.90,
        daily_return_percent=-1.75,
        daily_profit=-166.40,
        daily_return_percent_source="official_nav",
        sector_return_percent=-1.88,
    )
    assert compute_estimated_holding_return_percent(holding) == pytest.approx(2.43)
    assert compute_holding_profit(holding) == pytest.approx(225.90)


def test_apply_sector_daily_estimates_preserves_official_nav():
    holding = Holding(
        fund_code="015945",
        fund_name="测试",
        holding_amount=10000,
        sector_return_percent=1.36,
        daily_return_percent=-2.45,
        daily_profit=-245.0,
        daily_return_percent_source="official_nav",
    )
    result = apply_sector_daily_estimates(holding)
    assert result.daily_return_percent == -2.45
    assert result.daily_profit == -245.0
    assert result.sector_return_percent == 1.36
