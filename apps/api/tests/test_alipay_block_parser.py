from pathlib import Path

from app.services.alipay_block_parser import (
    parse_alipay_holdings_multi_strategy,
    parse_block_anchored_holdings,
)
from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_block_anchored_user_image1_six_funds():
    lines = [
        line.strip()
        for line in (FIXTURES / "alipay_user_image1_vlm_ocr.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    holdings = parse_block_anchored_holdings(lines)
    assert len(holdings) == 6
    zhonghang = next(h for h in holdings if "中航机遇" in h.fund_name)
    assert zhonghang.holding_amount == 10210.43
    assert zhonghang.holding_profit == 210.43
    assert zhonghang.yesterday_profit == 339.5


def test_multi_strategy_beats_legacy_on_compact_layout():
    text = (FIXTURES / "alipay_user_image1_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    assert len(holdings) == 6
    assert any("广发全球精选" in h.fund_name for h in holdings)


def test_multi_strategy_top6_fixture():
    text = (FIXTURES / "alipay_holdings_top6_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    assert len(holdings) == 6
    grid = next(h for h in holdings if "电网设备" in h.fund_name)
    assert grid.holding_amount == 2000.01
    assert grid.holding_profit == 0.01


def test_multi_strategy_bottom2_skips_yuebao():
    text = (FIXTURES / "alipay_holdings_bottom2_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    assert len(holdings) == 2
    zhonghang = next(h for h in holdings if "中航机遇" in h.fund_name)
    assert zhonghang.holding_profit == -373.30


def test_multi_strategy_selects_best_score():
    lines = [
        line.strip()
        for line in (FIXTURES / "alipay_user_image2_vlm_ocr.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    holdings = parse_alipay_holdings_multi_strategy(lines)
    assert len(holdings) == 2
