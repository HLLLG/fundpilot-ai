from pathlib import Path

from app.services.alipay_holdings_parser import (
    COMPLETE_FUND_NAME_RE,
    _extract_my_holdings_metrics,
)
from app.services.fund_name_utils import looks_like_fund_product_name
from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_metrics_no_crash_when_profit_missing():
    # Reproduces the image2 crash path: amount only, no profit numbers, percent line has no inline numbers
    amount, yesterday, profit = _extract_my_holdings_metrics(
        ["1000.00"],
        percent_line="0.00%",
        percent_pending_negative=False,
    )
    assert amount == 1000.00
    assert profit is None


def test_parse_bottom_fixture_does_not_raise():
    text = (FIXTURES / "alipay_overview_qdii_bottom_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)  # previously threw TypeError
    assert isinstance(holdings, list)


def test_qdii_names_recognized():
    qdii_names = [
        "天弘全球高端制造混合（QDII）C",
        "天弘全球高端制造混合(QDII)C",
        "富国全球科技互联网股票（QDII)C",
        "广发全球精选股票(QDII)C",
        "华夏全球科技先锋混合（QDII)C",
    ]
    for name in qdii_names:
        assert COMPLETE_FUND_NAME_RE.match(name), name
        assert looks_like_fund_product_name(name), name


def test_parse_top_fixture_recovers_all_six_funds():
    text = (FIXTURES / "alipay_overview_qdii_top_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 6, names
    assert "天弘全球高端制造混合（QDII）C" in names
    assert "广发全球精选股票（QDII)C" in names
    grid = next(h for h in holdings if "电网设备" in h.fund_name)
    assert grid.holding_amount == 2000.01


def test_parse_bottom_fixture_two_funds_skips_yuebao():
    text = (FIXTURES / "alipay_overview_qdii_bottom_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 2, names
    assert any("华夏全球科技先锋" in n for n in names)
    assert any("中航机遇领航" in n for n in names)
    assert all("余额" not in n for n in names)
