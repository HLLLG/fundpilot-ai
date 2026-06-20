from __future__ import annotations

import re

from app.models import ParsedTransaction
from app.services.fund_name_utils import sanitize_fund_name
from app.services.trading_session import resolve_confirm_date

# 交易记录 / 交易分析页标志词（任一命中即判定为交易页）。
TRANSACTION_PAGE_MARKERS = (
    "交易分析",
    "全部交易汇总",
    "清仓分析",
    "定投",
    "发车",
    "成交时间",
)

# 成交时间：YYYY-MM-DD HH:MM:SS
TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b")
# 金额行：1,500.00元 / 500.00元
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*元")
# 「基金 |」「基金|」前缀
FUND_PREFIX_RE = re.compile(r"^基金\s*[|｜]\s*")
# 「全部交易汇总」统计行：47次 买入 / 38次 卖出
SUMMARY_COUNT_RE = re.compile(r"^\d+\s*次")
# 汇总合计行：共91,000.00元
SUMMARY_TOTAL_RE = re.compile(r"^共\s*[\d,]")

_DIRECTION_BY_ANCHOR = {"买入": "buy", "卖出": "sell"}
IN_PROGRESS_MARKER = "交易进行中"


def is_alipay_transaction_page(lines: list[str]) -> bool:
    cleaned = [line.strip() for line in lines if line.strip()]
    joined = "\n".join(cleaned)
    if any(marker in joined for marker in TRANSACTION_PAGE_MARKERS):
        return True
    has_buy = any("买入" in line for line in cleaned)
    has_sell = any("卖出" in line for line in cleaned)
    has_timestamp = any(TIME_RE.search(line) for line in cleaned)
    return has_buy and has_sell and has_timestamp


def parse_alipay_transactions(text: str) -> list[ParsedTransaction]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    anchors = [
        index
        for index, line in enumerate(lines)
        if _is_direction_anchor(line)
    ]
    if not anchors:
        return []

    transactions: list[ParsedTransaction] = []
    for position, anchor_index in enumerate(anchors):
        next_index = anchors[position + 1] if position + 1 < len(anchors) else len(lines)
        block = lines[anchor_index + 1 : next_index]
        parsed = _parse_transaction_block(lines[anchor_index], block)
        if parsed is not None:
            transactions.append(parsed)
    return transactions


def _is_direction_anchor(line: str) -> bool:
    return line.strip() in _DIRECTION_BY_ANCHOR


def _parse_transaction_block(
    anchor_line: str,
    block_lines: list[str],
) -> ParsedTransaction | None:
    direction = _DIRECTION_BY_ANCHOR[anchor_line.strip()]

    amount_index: int | None = None
    amount_yuan: float | None = None
    trade_time: str | None = None
    in_progress = False
    name_fragments: list[str] = []

    for index, line in enumerate(block_lines):
        if IN_PROGRESS_MARKER in line:
            in_progress = True
            continue

        time_match = TIME_RE.search(line)
        if time_match and trade_time is None:
            trade_time = time_match.group(1)
            continue

        amount_match = AMOUNT_RE.search(line)
        if amount_match and amount_index is None and not _is_summary_line(line):
            amount_yuan = float(amount_match.group(1).replace(",", ""))
            amount_index = index
            continue

        if amount_index is None and not _is_summary_line(line):
            fragment = _clean_name_fragment(line)
            if fragment:
                name_fragments.append(fragment)

    if amount_yuan is None or trade_time is None:
        return None

    fund_name = sanitize_fund_name("".join(name_fragments))
    if not fund_name:
        return None

    confirm_date = resolve_confirm_date(trade_time)

    return ParsedTransaction(
        direction=direction,
        fund_name=fund_name,
        amount_yuan=amount_yuan,
        trade_time=trade_time,
        confirm_date=confirm_date,
        in_progress=in_progress,
    )


def _clean_name_fragment(line: str) -> str:
    cleaned = FUND_PREFIX_RE.sub("", line.strip())
    return cleaned.strip()


def _is_summary_line(line: str) -> bool:
    cleaned = line.strip()
    if SUMMARY_COUNT_RE.match(cleaned):
        return True
    if SUMMARY_TOTAL_RE.match(cleaned):
        return True
    return False
