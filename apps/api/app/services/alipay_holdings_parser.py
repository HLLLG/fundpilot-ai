from __future__ import annotations

import re

from app.models import Holding

# 支付宝「我的持有」三列排版，OCR 常按行交错读出：
# 基金名(可拆行) | 金额、昨日 | 持有收益、持有收益率
ALIPAY_PAGE_MARKERS = (
    "我的持有",
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
)
ALIPAY_HEADER_MARKERS = (
    "名称",
    "金额/昨日收益",
    "持有收益/率",
    "全部",
    "偏股",
    "偏债",
    "黄金",
    "全球",
    "基金市场",
    "机会",
    "自选",
)
ALIPAY_FOOTER_MARKERS = ("基金市场", "上证指数", "新增持有", "批量")
ALIPAY_NOISE_MARKERS = (
    "基金经理说",
    "市场解读",
    "财富号",
    "的重要性",
    "正在被",
    "厄尔尼诺",
)
PERCENT_LINE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])([+-]?\d[\d,]*(?:\.\d+)?)(?![\d.])")
NEGATIVE_LINE_RE = re.compile(r"^[-—－―+]$")
NAME_FRAGMENT_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·]{1,40}$")
COMPLETE_FUND_NAME_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9·]{4,40}"
    r"(?:混合[A-CEH]|联接[A-CEH]|ETF联接[A-CEH]|主题ETF联接[A-CEH])$",
    re.IGNORECASE,
)


def is_alipay_holdings_page(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    if "我的持有" in joined or "金额/昨日收益" in joined:
        return True
    percent_blocks = sum(1 for line in lines if _extract_percent(line) is not None)
    return percent_blocks >= 2 and "￥" not in joined


def parse_alipay_holdings_page(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not is_alipay_holdings_page(lines):
        return []

    blocks = _split_fund_blocks(lines)
    holdings: list[Holding] = []
    for block in blocks:
        holding = _parse_fund_block(block)
        if holding is not None:
            holdings.append(holding)
    return holdings


def _split_fund_blocks(lines: list[str]) -> list[list[str]]:
    percent_indexes = [
        index
        for index, line in enumerate(lines)
        if _extract_percent(line) is not None and not _is_header_line(line)
    ]
    if not percent_indexes:
        return []

    start = 0
    for index, line in enumerate(lines):
        if "持有收益/率" in line:
            start = index + 1
            break

    blocks: list[list[str]] = []
    for pct_index in percent_indexes:
        if pct_index < start:
            continue
        block = lines[start : pct_index + 1]
        if block:
            blocks.append(block)
        start = pct_index + 1
    return blocks


def _is_header_line(line: str) -> bool:
    return line in ALIPAY_HEADER_MARKERS or line.startswith("持有收益/率")


def _parse_fund_block(block_lines: list[str]) -> Holding | None:
    cleaned = [line for line in block_lines if line and not _is_footer_line(line)]
    if not cleaned:
        return None

    percent_line = cleaned[-1]
    percent_pending_negative = (
        len(cleaned) >= 2 and NEGATIVE_LINE_RE.match(cleaned[-2].strip()) is not None
    )
    holding_return_percent = _extract_percent(percent_line)
    if holding_return_percent is None:
        return None
    if percent_pending_negative and holding_return_percent > 0:
        holding_return_percent = -holding_return_percent

    inline_profit_numbers = _numbers_from_line(
        PERCENT_LINE_RE.sub("", percent_line),
        pending_negative=percent_pending_negative,
    )
    body = cleaned[:-1]
    if percent_pending_negative:
        body = cleaned[:-2]
    yesterday_profit: float | None = None
    if body and _is_near_zero_line(body[-1]):
        yesterday_profit = _first_number(body[-1])
        body = body[:-1]

    ordered_numbers: list[float] = []
    name_fragments: list[str] = []
    pending_negative = False
    for line in body:
        if _is_noise_line(line):
            continue
        if NEGATIVE_LINE_RE.match(line.strip()):
            pending_negative = True
            continue
        numbers = _numbers_from_line(line, pending_negative=pending_negative)
        pending_negative = False
        if numbers:
            ordered_numbers.extend(numbers)
            continue
        if _looks_like_name_fragment(line):
            name_fragments.append(line)

    holding_amount = None
    holding_profit = None
    yesterday_from_body: float | None = None
    for value in ordered_numbers:
        if holding_amount is None and _is_holding_amount(value, ""):
            holding_amount = value
            continue
        if holding_amount is None:
            continue
        if _is_near_zero(value):
            yesterday_from_body = value
            continue
        if holding_profit is None:
            holding_profit = value
            break

    if yesterday_profit is None:
        yesterday_profit = yesterday_from_body

    if holding_profit is None and inline_profit_numbers:
        for value in inline_profit_numbers:
            if not _is_near_zero(value) and value != holding_amount:
                holding_profit = value
                break

    holding_profit = align_profit_sign(holding_profit, holding_return_percent)
    if holding_profit is None and holding_amount and holding_return_percent is not None:
        holding_profit = round(
            holding_amount * holding_return_percent / (100 + holding_return_percent),
            2,
        )
        holding_profit = align_profit_sign(holding_profit, holding_return_percent)

    fund_name = _merge_name_fragments(name_fragments)
    if not fund_name or holding_amount is None:
        return None

    return Holding(
        fund_code="000000",
        fund_name=fund_name,
        holding_amount=holding_amount,
        return_percent=holding_return_percent or 0,
        holding_profit=holding_profit,
        holding_return_percent=holding_return_percent,
        yesterday_profit=yesterday_profit,
    )


def _merge_name_fragments(fragments: list[str]) -> str:
    if not fragments:
        return ""
    if len(fragments) == 1:
        return fragments[0]

    merged = fragments[0]
    for fragment in fragments[1:]:
        merged = _join_name_fragment(merged, fragment)
    return merged


def _join_name_fragment(left: str, right: str) -> str:
    if re.fullmatch(r"[A-CEH]", right) and left.endswith(("混合", "联接", "债券")):
        return left + right
    if right.startswith("接") and left.endswith("联"):
        return left + right[1:]
    if right.startswith("题") and left.endswith("主"):
        return left[:-1] + "主题" + right[1:]
    if right.startswith("题ETF") and left.endswith("主"):
        return left[:-1] + "主题" + right[1:]
    if COMPLETE_FUND_NAME_RE.match(right):
        return right
    if COMPLETE_FUND_NAME_RE.match(left):
        return left
    return left + right


def _looks_like_name_fragment(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned or _is_noise_line(cleaned):
        return False
    if PERCENT_LINE_RE.search(cleaned):
        return False
    if _numbers_from_line(cleaned) and _looks_like_holding_amount_line(cleaned):
        return False
    if not NAME_FRAGMENT_RE.match(cleaned):
        return False
    if is_alipay_tag_line(cleaned):
        return False
    if re.fullmatch(r"[A-CEH]", cleaned):
        return True
    if re.fullmatch(r"[A-Za-z+]{1,3}", cleaned):
        return False
    if cleaned.endswith("》"):
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in cleaned)


def _is_noise_line(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned:
        return True
    if cleaned in ALIPAY_HEADER_MARKERS:
        return True
    if is_alipay_tag_line(cleaned):
        return True
    if any(marker in cleaned for marker in ALIPAY_NOISE_MARKERS):
        return True
    if cleaned.endswith("》"):
        return True
    if re.fullmatch(r"[A-Za-z+]{1,3}", cleaned):
        return True
    if cleaned in {"m", "AA", "+", "13:20", "79", "基金"}:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
        return True
    if re.fullmatch(r"\d{1,3}", cleaned):
        return True
    return False


def is_alipay_tag_line(line: str) -> bool:
    cleaned = line.strip()
    tag_words = ("金选", "超额收益", "指数基金", "金选超额收益", "金选指数基金")
    if cleaned in tag_words:
        return True
    remainder = cleaned
    for tag in sorted(tag_words, key=len, reverse=True):
        remainder = remainder.replace(tag, "")
    remainder = re.sub(r"[\s·]+", "", remainder)
    return len(remainder) == 0


def _is_footer_line(line: str) -> bool:
    return any(marker in line for marker in ALIPAY_FOOTER_MARKERS)


def _looks_like_holding_amount_line(line: str) -> bool:
    numbers = _numbers_from_line(line)
    if not numbers:
        return False
    return any(_is_holding_amount(value, line) for value in numbers)


def _is_holding_amount(value: float, line: str) -> bool:
    if "," in line:
        return value >= 10
    if value >= 50:
        return True
    return value >= 10 and "." in line


def _is_near_zero_line(line: str) -> bool:
    numbers = _numbers_from_line(line)
    return len(numbers) == 1 and _is_near_zero(numbers[0]) and not PERCENT_LINE_RE.search(line)


def _is_near_zero(value: float) -> bool:
    return abs(value) < 0.0001


def _numbers_from_line(line: str, *, pending_negative: bool = False) -> list[float]:
    values: list[float] = []
    if NEGATIVE_LINE_RE.match(line.strip()):
        return values

    line_for_numbers = PERCENT_LINE_RE.sub(" ", line)
    for piece in re.split(r"[\s]+", line_for_numbers.strip()):
        if not piece:
            continue
        if NEGATIVE_LINE_RE.match(piece):
            pending_negative = True
            continue
        cleaned = piece.replace(",", "")
        match = NUMBER_TOKEN_RE.search(cleaned)
        if not match:
            continue
        value = float(match.group(1))
        if pending_negative and value > 0:
            value = -value
        pending_negative = False
        values.append(value)
    return values


def _first_number(line: str) -> float | None:
    numbers = _numbers_from_line(line)
    return numbers[0] if numbers else None


def _extract_percent(line: str) -> float | None:
    match = PERCENT_LINE_RE.search(line)
    if not match:
        return None
    return float(match.group(1))


def align_profit_sign(
    profit: float | None,
    return_percent: float | None,
) -> float | None:
    if profit is None or return_percent is None or profit == 0 or return_percent == 0:
        return profit
    if (profit > 0) > (return_percent > 0):
        return -abs(profit)
    return profit


# 兼容旧测试与 ocr_parser 检测
def is_alipay_fund_name(line: str) -> bool:
    return bool(COMPLETE_FUND_NAME_RE.match(line.strip()))
