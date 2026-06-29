"""支付宝 OCR「日收益」= 上一交易日官方净值收益，不得当作当日估算。"""

import pytest

from app.models import Holding
from app.services.alipay_holdings_parser import parse_alipay_holdings_page
from app.services.holding_estimates import (
    clear_client_daily_estimate_fields,
    enrich_holding_estimates,
)
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_overview_parser_maps_daily_column_to_yesterday_profit():
    text = (FIXTURES / "alipay_holdings_top6_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)
    ai = next(h for h in holdings if "人工智能" in h.fund_name)
    assert ai.yesterday_profit is not None
    assert ai.daily_profit is None
    assert ai.daily_return_percent is None
    assert ai.daily_return_percent_source is None


def test_clear_client_daily_moves_legacy_daily_to_yesterday():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        daily_profit=301.87,
        daily_return_percent=3.44,
        daily_return_percent_source="official_nav",
    )
    cleared = clear_client_daily_estimate_fields(holding)
    assert cleared.yesterday_profit == 301.87
    assert cleared.daily_profit is None
    assert cleared.daily_return_percent is None
    assert cleared.daily_return_percent_source is None


def test_alipay_ocr_cumulative_profit_not_double_counted_with_sector_estimate():
    """今日收益已更新后 OCR：持有收益为累计值，不得再叠加板块当日估算。"""
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13863.07,
        settled_holding_amount=13863.07,
        holding_profit=552.10,
        holding_return_percent=4.15,
        sector_return_percent=1.48,
        sector_return_percent_source="closing_estimate",
        daily_profit=204.94,
        daily_return_percent=1.48,
        daily_return_percent_source="sector_estimate",
    )
    from app.services.holding_estimates import (
        build_holding_display_metrics,
        compute_holding_profit,
        compute_estimated_holding_return_percent,
        holding_profit_is_estimated,
    )

    assert compute_holding_profit(holding) == 552.10
    assert compute_estimated_holding_return_percent(holding) == 4.15
    assert holding_profit_is_estimated(holding) is False
    metrics = build_holding_display_metrics(holding)
    assert metrics["estimated_holding_profit"] == 552.10
    assert metrics["estimated_holding_return_percent"] == 4.15
    assert metrics["holding_return_is_estimated"] is False


def test_alipay_today_profit_updated_maps_daily_column_to_daily_profit():
    text = (FIXTURES / "alipay_today_profit_updated_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)
    ai = next(h for h in holdings if "人工智能" in h.fund_name)
    grid = next(h for h in holdings if "电网" in h.fund_name)
    assert ai.daily_profit == 191.40
    assert ai.yesterday_profit is None
    assert ai.daily_return_percent_source == "official_nav"
    assert ai.amount_includes_today is True
    assert grid.daily_profit == -123.22
    cleared = clear_client_daily_estimate_fields(ai)
    assert cleared.daily_profit == 191.40


def test_ocr_grid_profit_not_overwritten_by_polluted_profile(monkeypatch):
    """OCR 持有收益 +142.18 不得被档案里旧的 -607 污染值覆盖。"""
    from app.models import FundProfile
    from app.services.holding_estimates import enrich_holding_estimates

    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        holding_profit=-607.85,
        holding_return_percent=-5.19,
    )
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda _code: profile,
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_profit=142.18,
        holding_return_percent=1.30,
        amount_includes_today=True,
        daily_return_percent_source="official_nav",
        daily_profit=-123.22,
    )
    enriched = enrich_holding_estimates(holding)
    assert enriched.holding_profit == pytest.approx(142.18, abs=0.1)
    assert enriched.holding_return_percent == pytest.approx(1.30, abs=0.05)


def test_enrich_uses_sector_not_yesterday_for_today_daily():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        holding_return_percent=8.36,
        yesterday_profit=301.87,
        sector_return_percent=-5.02,
        sector_return_percent_source="realtime",
    )
    enriched = enrich_holding_estimates(holding)
    assert enriched.daily_return_percent_source == "sector_estimate"
    assert enriched.daily_return_percent == -5.02
    assert enriched.daily_profit is not None
    assert enriched.daily_profit < 0
    assert enriched.yesterday_profit == 301.87
