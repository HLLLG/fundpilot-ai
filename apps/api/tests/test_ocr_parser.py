from pathlib import Path

from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_parser_returns_empty_list_when_no_fund_code_exists():
    assert parse_holdings_from_text("暂无可识别基金持仓") == []


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
