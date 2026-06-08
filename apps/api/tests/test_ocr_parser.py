from pathlib import Path

import pytest

from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_fund_code_name_amount_and_return_from_ocr_text():
    text = """
    华夏中证电网设备主题ETF发起式联接A
    015608
    持有金额 5,280.66
    持有收益率 -3.25%
    昨日收益 -42.31
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].fund_code == "015608"
    assert "电网设备" in holdings[0].fund_name
    assert holdings[0].holding_amount == 5280.66
    assert holdings[0].return_percent == -3.25


def test_parse_multiple_holdings_from_repeated_blocks():
    text = """
    易方达沪深300ETF联接A
    110020
    持有金额 2,000.00
    持有收益率 1.20%

    天弘中证红利低波动100A
    008114
    持有金额 3,500
    持有收益率 -0.75%
    """

    holdings = parse_holdings_from_text(text)

    assert [holding.fund_code for holding in holdings] == ["110020", "008114"]
    assert holdings[1].holding_amount == 3500
    assert holdings[1].return_percent == -0.75


def test_parser_returns_empty_list_when_no_fund_code_exists():
    assert parse_holdings_from_text("暂无可识别基金持仓") == []


def test_parse_alipay_screenshot_without_fund_codes_as_editable_drafts():
    text = """
    华夏中证电网设备...
    -86.23
    -0.59%
    +401.80
    ￥15,161.69
    -0.57%
    中证电网设备
    +2.74%
    华夏人工智能ETF.
    274.82
    3.75%
    -83.96
    ￥7,427.01
    -3.57%
    中证人工智能
    -1.12%
    银河创新成长混合A
    235.67
    -4.88%
    +63.51
    ￥4,222.96
    -5.29%
    半导体
    +1.53%
    易方达国防军工混...
    -102.35
    -6.07%
    -153.07
    ￥1,846.93
    -5.25%
    商业航天
    -7.65%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 4
    assert holdings[0].fund_code == "000000"
    assert holdings[0].fund_name == "华夏中证电网设备..."
    assert holdings[0].holding_amount == 15161.69
    assert holdings[0].return_percent == 2.74
    assert holdings[0].daily_profit == -86.23
    assert holdings[0].daily_return_percent == -0.57
    assert holdings[0].holding_profit == 401.80
    assert holdings[0].holding_return_percent == 2.74
    assert holdings[0].sector_name == "中证电网设备"
    assert holdings[0].sector_return_percent == -0.59
    assert holdings[2].fund_name == "银河创新成长混合A"
    assert holdings[3].holding_amount == 1846.93
    assert holdings[3].daily_profit == -102.35
    assert holdings[3].daily_return_percent == -5.25
    assert holdings[3].holding_profit == -153.07
    assert holdings[3].holding_return_percent == -7.65
    assert holdings[3].sector_name == "商业航天"
    assert holdings[3].sector_return_percent == -6.07


def test_parse_yangjibao_when_daily_column_is_placeholder_dash():
    text = (FIXTURES / "yangjibao_holdings_no_daily_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 4
    assert [holding.fund_name for holding in holdings] == [
        "华夏中证电网设备..",
        "华夏人工智能ETF.",
        "易方达国防军工混..",
        "银河创新成长混合A",
    ]
    assert holdings[0].holding_amount == 15075.46
    assert holdings[0].daily_profit is None
    assert holdings[0].daily_return_percent is None
    assert holdings[0].holding_profit == 401.80
    assert holdings[0].holding_return_percent == 2.74
    assert holdings[0].sector_return_percent == -0.09
    assert holdings[1].daily_profit is None
    assert holdings[1].holding_profit == -83.96
    assert holdings[1].holding_return_percent == -1.12
    assert holdings[2].holding_amount == 1846.93
    assert holdings[2].daily_profit is None
    assert holdings[2].holding_profit == -153.07
    assert holdings[2].holding_return_percent == -7.65
    assert holdings[2].sector_name == "商业航天"
    assert holdings[3].daily_profit is None
    assert holdings[3].holding_profit == 63.51
    assert holdings[3].holding_return_percent == 1.53
    assert holdings[3].sector_return_percent == -3.88


def test_parse_overview_restores_negative_daily_profit_and_sector_when_ocr_drops_signs():
    text = (FIXTURES / "yangjibao_overview_signed_daily_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    ai = next(item for item in holdings if "人工智能" in item.fund_name)
    assert ai.daily_profit == -176.88
    assert ai.daily_return_percent == -2.38
    assert ai.sector_return_percent == -2.52
    assert ai.holding_profit == -260.85

    defense = next(item for item in holdings if "国防军工" in item.fund_name)
    assert defense.daily_profit == -53.48
    assert defense.sector_return_percent == -3.19

    galaxy = next(item for item in holdings if "银河创新" in item.fund_name)
    assert galaxy.daily_profit == -251.64
    assert galaxy.sector_return_percent == -4.57


def test_parse_negative_marker_on_separate_line():
    text = """
    华夏人工智能ETF.
    -
    176.88
    -
    2.38%
    ￥7,250.12
    中证人工智能
  -
    2.52%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].daily_profit == -176.88
    assert holdings[0].daily_return_percent == -2.38
    assert holdings[0].sector_return_percent == -2.52


def test_parse_alipay_holdings_list_layout():
    text = (FIXTURES / "alipay_holdings_list_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 4
    assert holdings[0].fund_name == "银河创新成长混合A"
    assert holdings[0].fund_code == "000000"
    assert holdings[0].holding_amount == 4001.68
    assert holdings[0].yesterday_profit == 0.0
    assert holdings[0].holding_profit == -157.77
    assert holdings[0].holding_return_percent == -3.79
    assert holdings[0].return_percent == -3.79

    grid = next(item for item in holdings if "电网设备" in item.fund_name)
    assert grid.holding_amount == 9508.74
    assert grid.holding_profit == 225.90
    assert grid.holding_return_percent == 2.43

    defense = next(item for item in holdings if "国防军工" in item.fund_name)
    assert defense.holding_amount == 814.29
    assert defense.holding_profit == -74.59
    assert defense.holding_return_percent == -8.39


def test_parse_alipay_holdings_ignores_fund_manager_promo_line():
    text = (FIXTURES / "alipay_holdings_with_promo_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 4
    assert all("基金经理说" not in item.fund_name for item in holdings)
    assert holdings[0].holding_profit == -157.77
    assert holdings[1].holding_profit == -124.91
    grid = next(item for item in holdings if "电网设备" in item.fund_name)
    assert grid.holding_amount == 9508.74
    assert grid.holding_profit == 225.90


def test_parse_alipay_holdings_negative_marker_on_separate_line():
    text = """
    我的持有
    银河创新成长混合A
    4,001.68
    0.00
    -
    157.77
    -
    3.79%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].holding_profit == -157.77
    assert holdings[0].holding_return_percent == -3.79


def test_parse_alipay_holdings_infers_profit_from_percent_when_ocr_drops_amount():
    text = """
    我的持有
    易方达国防军工混合C
    814.29
    0.00
    -8.39%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].holding_return_percent == -8.39
    assert holdings[0].holding_profit == pytest.approx(-74.59, abs=0.02)


def test_parse_alipay_holdings_aligns_profit_sign_when_ocr_drops_minus():
    text = """
    我的持有
    银河创新成长混合A
    4,001.68
    0.00
    157.77
    -3.79%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].holding_profit == -157.77


def test_parse_holding_profit_when_ocr_drops_minus_sign():
    text = """
    易方达国防军工混..
    2.67%
    153.07
    ￥1,846.93
    商业航天
    -7.65%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 1
    assert holdings[0].holding_profit == -153.07
    assert holdings[0].holding_return_percent == -7.65
