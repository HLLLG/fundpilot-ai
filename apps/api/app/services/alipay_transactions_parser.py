from __future__ import annotations

import re

from app.models import ParsedTransaction, TransactionDirection
from app.services.fund_name_utils import sanitize_fund_name
from app.services.trading_session import resolve_confirm_date, resolve_first_return_date

# 交易页专有标志。不要把「交易分析 / 定投 / 清仓分析」单独当作判定——
# 那些词会出现在「全部持有」总览的顶栏或入口按钮上。
TRANSACTION_PAGE_MARKERS = (
    "全部交易汇总",
    "成交时间",
    "交易记录",
)

# 成交时间：YYYY-MM-DD HH:MM:SS
TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b")
# 金额：1,500.00元 / 500.00元
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*元")
# 「基金 |」「基金|」前缀（交易记录页）
FUND_PREFIX_RE = re.compile(r"^基金\s*[|｜]\s*")
# 「全部交易汇总」统计：47次 买入 / 65笔 / 共91,000.00元
SUMMARY_COUNT_RE = re.compile(r"(?:^|\s)\d+\s*(?:次|笔)(?:\s|$)")
SUMMARY_TOTAL_RE = re.compile(r"^共\s*[\d,]")
# 交易分析明细常见「买入 基金 / 买入基金 / 定投 基金」；汇总行「买入 65笔 …」另由 summary 排除。
DIRECTION_LINE_RE = re.compile(r"^(买入|卖出|定投)(?:\s*基金)?(?:\s+(.*))?$")
DIRECTION_TOKEN_RE = re.compile(r"(买入|卖出|定投)")
NAME_NOISE_RE = re.compile(
    r"^(交易成功|交易进行中|明细|基金|全部类型|全部基金|筛选|近一年|近三个月|近一月|近一周)$"
)

_DIRECTION_BY_ANCHOR: dict[str, TransactionDirection] = {
    "买入": "buy",
    "卖出": "sell",
    "定投": "buy",
}
IN_PROGRESS_MARKER = "交易进行中"
_SUMMARY_MARKERS = (
    "全部交易汇总",
    "现金分红",
    "红利再投",
    "定投/发车",
    "近一年",
    "近三个月",
    "近一月",
    "近一周",
)


def is_alipay_transaction_page(lines: list[str]) -> bool:
    cleaned = [line.strip() for line in lines if line.strip()]
    joined = "\n".join(cleaned)
    if any(marker in joined for marker in TRANSACTION_PAGE_MARKERS):
        return True
    has_direction = any(DIRECTION_TOKEN_RE.search(line) for line in cleaned)
    has_timestamp = any(TIME_RE.search(line) for line in cleaned)
    return has_direction and has_timestamp


def parse_alipay_transactions(text: str) -> list[ParsedTransaction]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    time_indices = [index for index, line in enumerate(lines) if TIME_RE.search(line)]
    if not time_indices:
        return []

    transactions: list[ParsedTransaction] = []
    seen: set[tuple[str, str, float, str]] = set()
    for order, time_index in enumerate(time_indices):
        prev_time = time_indices[order - 1] if order else -1
        next_time = time_indices[order + 1] if order + 1 < len(time_indices) else len(lines)
        window = _window_for_timestamp(lines, time_index, prev_time, next_time)
        parsed = _parse_window(window)
        if parsed is None:
            continue
        key = (parsed.direction, parsed.fund_name, parsed.amount_yuan, parsed.trade_time)
        if key in seen:
            continue
        seen.add(key)
        transactions.append(parsed)
    return transactions


def _window_for_timestamp(
    lines: list[str],
    time_index: int,
    prev_time: int,
    next_time: int,
) -> list[str]:
    start = time_index
    found_direction = False
    for index in range(time_index, prev_time, -1):
        start = index
        if _is_direction_anchor(lines[index]):
            found_direction = True
            break
    if not found_direction:
        start = max(prev_time + 1, time_index - 4)
    if found_direction and start - 1 > prev_time:
        previous = lines[start - 1]
        if (
            AMOUNT_RE.search(previous)
            and not _is_summary_line(previous)
            and not _is_direction_anchor(previous)
        ):
            start -= 1
    end = min(time_index + 4, next_time)
    for index in range(time_index + 1, end):
        if _is_direction_anchor(lines[index]):
            end = index
            break
    return lines[start:end]


def _is_direction_anchor(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned or _is_summary_line(cleaned):
        return False
    if cleaned in _DIRECTION_BY_ANCHOR:
        return True
    match = DIRECTION_LINE_RE.match(cleaned)
    if not match:
        return False
    rest = (match.group(2) or "").strip()
    return not rest or not _is_summary_line(rest)


def _parse_window(block_lines: list[str]) -> ParsedTransaction | None:
    direction: TransactionDirection | None = None
    amount_yuan: float | None = None
    trade_time: str | None = None
    in_progress = False
    name_fragments: list[str] = []

    for raw in block_lines:
        line = raw.strip()
        if not line:
            continue
        if IN_PROGRESS_MARKER in line:
            in_progress = True

        time_match = TIME_RE.search(line)
        if time_match and trade_time is None:
            trade_time = time_match.group(1)
            line = f"{line[: time_match.start()]} {line[time_match.end() :]}".strip()

        if not line or _is_summary_line(line):
            continue

        amount_match = AMOUNT_RE.search(line)
        if amount_match:
            if amount_yuan is None or trade_time is None:
                amount_yuan = float(amount_match.group(1).replace(",", ""))
            line = f"{line[: amount_match.start()]} {line[amount_match.end() :]}".strip()

        dir_match = DIRECTION_TOKEN_RE.search(line)
        if dir_match and not _is_summary_line(line):
            token = dir_match.group(1)
            mapped = _DIRECTION_BY_ANCHOR.get(token)
            if mapped is not None:
                direction = mapped
                line = f"{line[: dir_match.start()]} {line[dir_match.end() :]}".strip()

        fragment = _clean_name_fragment(line)
        if fragment:
            name_fragments.append(fragment)

    if direction is None or amount_yuan is None or trade_time is None:
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
        first_return_date=resolve_first_return_date(trade_time),
        in_progress=in_progress,
    )


def _clean_name_fragment(line: str) -> str:
    cleaned = line.strip()
    if not cleaned or NAME_NOISE_RE.match(cleaned):
        return ""
    cleaned = FUND_PREFIX_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"^基金\s+", "", cleaned).strip()
    cleaned = cleaned.strip(" |｜")
    if not cleaned or NAME_NOISE_RE.match(cleaned):
        return ""
    return cleaned


def _is_summary_line(line: str) -> bool:
    cleaned = line.strip()
    if any(marker in cleaned for marker in _SUMMARY_MARKERS):
        return True
    if SUMMARY_COUNT_RE.search(cleaned):
        return True
    if SUMMARY_TOTAL_RE.match(cleaned):
        return True
    return False
