from __future__ import annotations

import re

from app.models import Holding


FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
AMOUNT_RE = re.compile(r"(?:持有金额|金额|资产)[^\d-]*([\d,]+(?:\.\d+)?)")
RETURN_RE = re.compile(r"(?:持有收益率|收益率|收益)[^\d+-]*([+-]?\d+(?:\.\d+)?)%")
YUAN_AMOUNT_RE = re.compile(r"￥\s*([\d,]+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
FUND_NAME_HINTS = ("...", "ETF", "混合", "基金", "联接", "债券", "指数")


def parse_holdings_from_text(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    holdings: list[Holding] = []

    for index, line in enumerate(lines):
        code_match = FUND_CODE_RE.search(line)
        if not code_match:
            continue

        fund_code = code_match.group(1)
        block = _holding_block(lines, index)
        fund_name = _guess_fund_name(lines, index, fund_code)
        amount = _extract_float(block, AMOUNT_RE)
        return_percent = _extract_float(block, RETURN_RE)

        if amount is None:
            amount = 0
        if return_percent is None:
            return_percent = 0

        holdings.append(
            Holding(
                fund_code=fund_code,
                fund_name=fund_name or f"基金 {fund_code}",
                holding_amount=amount,
                return_percent=return_percent,
            )
        )

    if holdings:
        return holdings

    return _parse_alipay_drafts_without_codes(lines)


def _holding_block(lines: list[str], index: int) -> str:
    end = len(lines)
    for next_index in range(index + 1, len(lines)):
        if FUND_CODE_RE.search(lines[next_index]):
            end = next_index
            break
    return "\n".join(lines[index:end])


def _guess_fund_name(lines: list[str], index: int, fund_code: str) -> str:
    candidates = []
    for offset in range(1, 4):
        candidate_index = index - offset
        if candidate_index < 0:
            break
        candidate = lines[candidate_index]
        if fund_code not in candidate and not FUND_CODE_RE.search(candidate):
            candidates.append(candidate)
    return candidates[0] if candidates else ""


def _extract_float(block: str, pattern: re.Pattern[str]) -> float | None:
    match = pattern.search(block)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_alipay_drafts_without_codes(lines: list[str]) -> list[Holding]:
    name_indexes = [
        index for index, line in enumerate(lines) if _looks_like_alipay_fund_name(line)
    ]
    drafts: list[Holding] = []

    for position, index in enumerate(name_indexes):
        next_index = name_indexes[position + 1] if position + 1 < len(name_indexes) else len(lines)
        block_lines = lines[index:next_index]
        amount = _extract_float("\n".join(block_lines), YUAN_AMOUNT_RE)
        if amount is None:
            continue

        percentages = [
            float(match.group(1))
            for line in block_lines
            for match in PERCENT_RE.finditer(line)
        ]
        return_percent = percentages[-1] if percentages else 0
        drafts.append(
            Holding(
                fund_code="000000",
                fund_name=lines[index],
                holding_amount=amount,
                return_percent=return_percent,
                user_note="OCR 未识别到基金代码，请手动补全。",
            )
        )

    return drafts


def _looks_like_alipay_fund_name(line: str) -> bool:
    if FUND_CODE_RE.search(line):
        return False
    if any(noise in line for noise in ("账户", "支付宝", "上证指数", "新增持有", "批量")):
        return False
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in line)
    return has_chinese and any(hint in line for hint in FUND_NAME_HINTS)
