from pathlib import Path

from app.services.alipay_transactions_parser import (
    is_alipay_transaction_page,
    parse_alipay_transactions,
)
from app.services.ocr_parser import detect_ocr_source

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> str:
    return (FIXTURES / "alipay_transactions_ocr.txt").read_text(encoding="utf-8")


def test_parse_returns_five_detail_transactions():
    transactions = parse_alipay_transactions(_load_fixture())
    assert len(transactions) == 5


def test_direction_mapping_buy_and_sell():
    transactions = parse_alipay_transactions(_load_fixture())
    directions = [tx.direction for tx in transactions]
    assert directions == ["buy", "buy", "sell", "sell", "sell"]


def test_amounts_and_trade_times():
    transactions = parse_alipay_transactions(_load_fixture())
    rows = [(tx.amount_yuan, tx.trade_time) for tx in transactions]
    assert rows == [
        (1500.00, "2026-06-18 14:59:53"),
        (500.00, "2026-06-16 14:43:38"),
        (2336.61, "2026-06-10 14:59:42"),
        (400.23, "2026-06-03 14:21:53"),
        (2850.23, "2026-06-03 14:21:22"),
    ]


def test_fund_prefix_stripped():
    transactions = parse_alipay_transactions(_load_fixture())
    # 第 1 条带「基金 |」前缀，应被剥离。
    assert transactions[0].fund_name == "广发电子信息传媒产业精选股票C"
    # 第 4 条同样带前缀。
    assert transactions[3].fund_name == "易方达国防军工混合C"


def test_multiline_fund_name_joined():
    transactions = parse_alipay_transactions(_load_fixture())
    # 第 5 条基金名跨两行，应拼接完整。
    assert transactions[4].fund_name == "华夏中证电网设备主题ETF联接A"


def test_summary_region_filtered_out():
    transactions = parse_alipay_transactions(_load_fixture())
    # 汇总区 47次/38次/共...元 不应被当作明细，金额不出现在结果里。
    amounts = {tx.amount_yuan for tx in transactions}
    assert 91000.00 not in amounts
    assert 85600.00 not in amounts


def test_in_progress_flag():
    transactions = parse_alipay_transactions(_load_fixture())
    assert transactions[0].in_progress is True
    assert all(tx.in_progress is False for tx in transactions[1:])


def test_confirm_date_populated_for_all():
    transactions = parse_alipay_transactions(_load_fixture())
    for tx in transactions:
        assert tx.confirm_date
        # ISO date 形式 YYYY-MM-DD
        assert len(tx.confirm_date) == 10
        assert tx.confirm_date[4] == "-" and tx.confirm_date[7] == "-"


def test_is_alipay_transaction_page_true():
    lines = [line.strip() for line in _load_fixture().splitlines() if line.strip()]
    assert is_alipay_transaction_page(lines) is True


def test_detect_ocr_source_returns_alipay_transactions():
    assert detect_ocr_source(_load_fixture()) == "alipay_transactions"


def test_holdings_page_not_detected_as_transactions():
    holdings_text = (FIXTURES / "alipay_holdings_list_ocr.txt").read_text(encoding="utf-8")
    assert detect_ocr_source(holdings_text) == "alipay_holdings"
