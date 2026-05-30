from app.services.ocr_parser import parse_holdings_from_text


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
    0.87%
    +488.03
    ￥15,161.69
    中证电网设备
    +3.33%
    华夏人工智能ETF.
    -3.88%
    +190.86
    ￥7,701.83
    中证人工智能
    +2.54%
    银河创新成长混合A
    5.20%
    +299.18
    ￥4,458.63
    半导体
    +7.19%
    易方达国防军工混...
    -6.30%
    -50.72
    ￥1,949.28
    商业航天
    -2.54%
    """

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 4
    assert holdings[0].fund_code == "000000"
    assert holdings[0].fund_name == "华夏中证电网设备..."
    assert holdings[0].holding_amount == 15161.69
    assert holdings[0].return_percent == 0.87
    assert holdings[0].daily_profit == 488.03
    assert holdings[0].sector_name == "中证电网设备"
    assert holdings[0].sector_return_percent == 3.33
    assert holdings[2].fund_name == "银河创新成长混合A"
    assert holdings[3].daily_profit == -50.72
    assert holdings[3].sector_name == "商业航天"
    assert holdings[3].sector_return_percent == -2.54
