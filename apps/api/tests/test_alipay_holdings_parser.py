from pathlib import Path

import pytest

from app.services.alipay_holdings_parser import (
    is_alipay_fund_name,
    is_alipay_tag_line,
    parse_alipay_holdings_page,
)
from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_tag_lines_are_not_fund_names():
    assert is_alipay_tag_line("金选")
    assert is_alipay_tag_line("指数基金")
    assert is_alipay_tag_line("金选指数基金")
    assert not is_alipay_fund_name("金选指数基金")
    assert is_alipay_fund_name("银河创新成长混合A")
    assert is_alipay_fund_name("华夏人工智能ETF联接C")
    assert is_alipay_fund_name("华夏中证电网设备主题ETF联接A")


def test_parse_real_wechat_ocr_scrambled_columns():
    text = (FIXTURES / "alipay_holdings_real_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)

    assert len(holdings) == 4
    by_name = {item.fund_name: item for item in holdings}
    assert "银河创新成长混合A" in by_name
    assert by_name["银河创新成长混合A"].holding_amount == 4001.68
    assert by_name["银河创新成长混合A"].holding_profit == -157.77
    assert by_name["银河创新成长混合A"].holding_return_percent == -3.79

    ai = next(item for item in holdings if "人工智能" in item.fund_name)
    assert ai.holding_amount == 8186.06
    assert ai.holding_profit == -124.91
    assert ai.holding_return_percent == -1.50

    grid = next(item for item in holdings if "电网设备" in item.fund_name)
    assert grid.holding_amount == 9508.74
    assert grid.holding_profit == 225.90
    assert grid.holding_return_percent == 2.43

    defense = next(item for item in holdings if "国防军工" in item.fund_name)
    assert defense.holding_amount == 814.29
    assert defense.holding_profit == -74.59
    assert defense.holding_return_percent == -8.39


def test_parse_standard_alipay_fixture():
    text = (FIXTURES / "alipay_holdings_list_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)

    assert len(holdings) == 4
    assert holdings[0].holding_profit == -157.77
    assert holdings[1].holding_profit == -124.91
    assert holdings[2].holding_profit == 225.90
    assert holdings[3].holding_profit == -74.59


def test_parse_promo_and_merged_tag_lines():
    text = (FIXTURES / "alipay_holdings_with_promo_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)

    assert len(holdings) == 4
    assert all("基金经理说" not in item.fund_name for item in holdings)
    assert all("金选" not in item.fund_name for item in holdings)


def test_parse_inline_amount_and_profit_columns():
    text = """
    我的持有
    银河创新成长混合A
    金选
    超额收益
    4,001.68 0.00
    -157.77 -3.79%
    华夏人工智能ETF联接C
    8,186.06 0.00
    157.77 -1.50%
    """

    holdings = parse_alipay_holdings_page(text)

    assert len(holdings) == 2
    assert holdings[0].holding_amount == 4001.68
    assert holdings[0].holding_profit == pytest.approx(-157.77, abs=0.2)
    assert holdings[1].holding_profit == pytest.approx(-157.77, abs=0.5)
    assert holdings[1].holding_return_percent == -1.50


def test_parse_negative_on_separate_lines():
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


def test_parse_tags_on_separate_lines_not_treated_as_funds():
    text = """
    我的持有
    银河创新成长混合A
    金选
    指数基金
    4,001.68
    0.00
    157.77
    -3.79%
    华夏人工智能ETF联接C
    金选
    指数基金
    8,186.06
    0.00
    124.91
    -1.50%
    华夏中证电网设备主题ETF联接A
    9,508.74
    0.00
    225.90
    2.43%
    易方达国防军工混合C
    814.29
    0.00
    74.59
    -8.39%
    """

    holdings = parse_alipay_holdings_page(text)

    assert len(holdings) == 4
    assert holdings[0].holding_profit == -157.77
    assert holdings[1].holding_profit == -124.91
    assert holdings[2].holding_profit == 225.90
    assert holdings[3].holding_profit == -74.59


def test_infer_profit_when_only_percent_survives_ocr():
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
