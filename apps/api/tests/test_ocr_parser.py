from pathlib import Path

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
