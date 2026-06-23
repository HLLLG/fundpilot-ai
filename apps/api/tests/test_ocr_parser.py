from pathlib import Path

from app.services.ocr_parser import detect_ocr_source, parse_holdings_from_text

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


def test_parse_alipay_overview_holdings_five_funds():
    text = (FIXTURES / "alipay_overview_holdings_5_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 5
    names = [item.fund_name for item in holdings]
    assert any("广发电子信息传媒产业精选股票C" in name for name in names)
    assert detect_ocr_source(text) == "alipay_holdings"
    stock = next(item for item in holdings if "股票C" in item.fund_name)
    assert stock.holding_amount == 1500.0


def test_parse_alipay_overview_layout_keeps_stock_class_fund():
    """支付宝「全部持有」总览 5 只基金须全部解析（含「精选股票C」后缀）。"""
    text = (FIXTURES / "alipay_overview_holdings_5_ocr.txt").read_text(encoding="utf-8")

    holdings = parse_holdings_from_text(text)

    assert len(holdings) == 5
    names = [item.fund_name for item in holdings]
    assert "广发电子信息传媒产业精选股票C" in names

    stock_fund = next(item for item in holdings if "精选股票C" in item.fund_name)
    assert stock_fund.holding_amount == 1500.00

    grid = next(item for item in holdings if "电网设备" in item.fund_name)
    assert grid.holding_amount == 7447.24
    assert grid.holding_profit == 485.11
    assert grid.holding_return_percent == 6.97


def test_overview_screenshot_detected_as_alipay_holdings():
    text = (FIXTURES / "alipay_overview_holdings_5_ocr.txt").read_text(encoding="utf-8")
    assert detect_ocr_source(text) == "alipay_holdings"


def test_overview_detected_as_alipay_even_without_header_markers():
    """页眉关键词被 OCR 漏读时，% 行启发式仍判定为支付宝持有页。"""
    text = (FIXTURES / "alipay_overview_holdings_5_ocr.txt").read_text(encoding="utf-8")
    stripped = "\n".join(
        line
        for line in text.splitlines()
        if line.strip() not in {"全部持有", "名称/金额", "日收益", "持有收益排序"}
    )
    assert detect_ocr_source(stripped) == "alipay_holdings"
    assert len(parse_holdings_from_text(stripped)) == 5


def test_parse_user_upload_overview_five_funds_with_split_tags():
    """CloudBase 实网 OCR：标签分行、占比无空格、底部运营条，仍须识别 5 只基金。"""
    text = (FIXTURES / "alipay_overview_holdings_5_ocr_user.txt").read_text(encoding="utf-8")

    assert detect_ocr_source(text) == "alipay_holdings"

    holdings = parse_holdings_from_text(text)
    assert len(holdings) == 5
    names = [item.fund_name for item in holdings]
    assert "广发电子信息传媒产业精选股票C" in names

    stock = next(item for item in holdings if "精选股票C" in item.fund_name)
    assert stock.holding_amount == 1500.0
    assert stock.holding_return_percent == 0.0

    defense = next(item for item in holdings if "国防军工" in item.fund_name)
    assert defense.holding_return_percent == 0.93


def test_ocr_pipeline_user_upload_preview_is_alipay_holdings(monkeypatch):
    from app.services.ocr_pipeline import run_ocr_upload_pipeline

    text = (FIXTURES / "alipay_overview_holdings_5_ocr_user.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(
        "app.services.ocr_pipeline.get_previous_holdings_for_review",
        lambda: [],
    )

    result = run_ocr_upload_pipeline(text=text, preview=True)

    assert result["ocr_source"] == "alipay_holdings"
    assert len(result["holdings"]) == 5
    assert result["amount_semantics"]["source"] == "alipay_holdings"
    assert "未识别为支付宝持有页" not in result["amount_semantics"]["note"]


def test_parse_user_upload_overview_four_funds_including_index_class():
    """用户实网截图：含指数C后缀与中间运营条，须识别全部 4 只基金。"""
    text = (FIXTURES / "alipay_overview_holdings_4_user_ocr.txt").read_text(encoding="utf-8")

    assert detect_ocr_source(text) == "alipay_holdings"

    holdings = parse_holdings_from_text(text)
    assert len(holdings) == 4
    names = [item.fund_name for item in holdings]
    assert "中航机遇领航混合C" in names
    assert "华夏中证电网设备主题ETF联接C" in names
    assert "中欧上证科创板人工智能指数C" in names
    assert "天弘科创芯片设计ETF联接C" in names

    avic = next(item for item in holdings if "中航机遇" in item.fund_name)
    assert avic.holding_amount == 10018.60
    assert avic.holding_profit == 18.60

    index_fund = next(item for item in holdings if "人工智能指数" in item.fund_name)
    assert index_fund.holding_amount == 1000.0


def test_ocr_pipeline_unresolved_fund_code_includes_hint(monkeypatch):
    from app.models import Holding
    from app.services.fund_code_resolver import UNRESOLVED_FUND_CODE_HINT
    from app.services.ocr_pipeline import _resolve_fund_codes

    class _EmptyProfileService:
        def find_match(self, _name: str):
            return None

    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: [],
    )

    holdings, resolutions = _resolve_fund_codes(
        [
            Holding(
                fund_code="000000",
                fund_name="中欧上证科创板人工智能指数C",
                holding_amount=1000,
                return_percent=0,
            )
        ],
        _EmptyProfileService(),
    )

    assert holdings[0].fund_code == "000000"
    assert resolutions[0]["resolved"] is False
    assert resolutions[0]["message"] == UNRESOLVED_FUND_CODE_HINT
