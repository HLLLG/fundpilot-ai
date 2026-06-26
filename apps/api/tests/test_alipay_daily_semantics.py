"""支付宝 OCR「日收益」= 上一交易日官方净值收益，不得当作当日估算。"""

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
