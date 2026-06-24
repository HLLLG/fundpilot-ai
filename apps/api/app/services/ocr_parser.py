from __future__ import annotations

from app.models import Holding
from app.services.alipay_holdings_parser import (
    is_alipay_holdings_page,
    is_alipay_overview_holdings_page,
    parse_alipay_holdings_page,
)
from app.services.alipay_transactions_parser import is_alipay_transaction_page


# detect_ocr_source 用：支付宝「我的持有 / 全部持有」页眉标记
ALIPAY_HOLDINGS_MARKERS = (
    "我的持有",
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
    "全部持有",
    "名称/金额",
    "日收益",
    "持有收益排序",
)


def parse_holdings_from_text(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not is_alipay_holdings_page(lines):
        return []
    try:
        return parse_alipay_holdings_page(text)
    except Exception:  # noqa: BLE001 — parse errors must not let /api/ocr 500
        return []


def detect_ocr_source(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # 全部持有总览：顶栏含「交易分析/清仓分析」，须优先于交易页判定。
    if is_alipay_overview_holdings_page(lines):
        return "alipay_holdings"
    if any(marker in line for line in lines for marker in ALIPAY_HOLDINGS_MARKERS):
        return "alipay_holdings"
    if is_alipay_holdings_page(lines):
        return "alipay_holdings"
    if is_alipay_transaction_page(lines):
        return "alipay_transactions"
    return "unknown"
