from __future__ import annotations

import re

from app.models import Holding


FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
AMOUNT_RE = re.compile(r"(?:持有金额|金额|资产)[^\d-]*([\d,]+(?:\.\d+)?)")
RETURN_RE = re.compile(r"(?:持有收益率|收益率|收益)[^\d+-]*([+-]?\d+(?:\.\d+)?)%")
YUAN_AMOUNT_RE = re.compile(r"￥\s*([\d,]+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
SIGNED_AMOUNT_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
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

        metrics = _extract_yangjibao_metrics(block_lines, amount)
        percentages = metrics["percentages"]
        return_percent = metrics["holding_return_percent"]
        sector_return_percent = metrics["sector_return_percent"]
        daily_return_percent = metrics["daily_return_percent"]
        holding_return_percent = metrics["holding_return_percent"]
        daily_profit = metrics["daily_profit"]
        holding_profit = metrics["holding_profit"]
        sector_name = _extract_sector_name(block_lines, amount)
        drafts.append(
            Holding(
                fund_code="000000",
                fund_name=lines[index],
                holding_amount=amount,
                return_percent=return_percent,
                daily_profit=daily_profit,
                daily_return_percent=daily_return_percent,
                holding_profit=holding_profit,
                holding_return_percent=holding_return_percent,
                sector_name=sector_name,
                sector_return_percent=sector_return_percent,
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


def _extract_signed_amount(lines: list[str]) -> float | None:
    for line in lines:
        cleaned = line.replace(",", "").strip()
        if SIGNED_AMOUNT_RE.match(cleaned):
            return float(cleaned)
    return None


def _extract_yangjibao_metrics(lines: list[str], amount: float) -> dict:
    amount_index = _find_amount_index(lines, amount)
    before_amount = lines[:amount_index] if amount_index is not None else lines
    after_amount = lines[amount_index + 1 :] if amount_index is not None else []

    before_numbers = [
        float(line.replace(",", ""))
        for line in before_amount
        if SIGNED_AMOUNT_RE.match(line.replace(",", "").strip())
    ]
    before_percents = [
        float(match.group(1))
        for line in before_amount
        for match in PERCENT_RE.finditer(line)
    ]
    after_percents = [
        float(match.group(1))
        for line in after_amount
        for match in PERCENT_RE.finditer(line)
    ]

    daily_profit = before_numbers[0] if before_numbers else None
    holding_profit = before_numbers[-1] if before_numbers else None
    daily_return_percent = after_percents[0] if after_percents else None
    holding_return_percent = after_percents[-1] if after_percents else (before_percents[-1] if before_percents else 0)
    sector_return_percent = before_percents[-1] if before_percents else None
    holding_profit = before_numbers[-1] if before_numbers else None

    return {
        "percentages": before_percents + after_percents,
        "daily_return_percent": daily_return_percent,
        "daily_profit": daily_profit,
        "sector_return_percent": sector_return_percent,
        "holding_profit": holding_profit,
        "holding_return_percent": holding_return_percent,
    }


def _find_amount_index(lines: list[str], amount: float) -> int | None:
    amount_text = f"{amount:,.2f}"
    for index, line in enumerate(lines):
        if amount_text in line:
            return index
    return None


def _extract_sector_name(lines: list[str], amount: float) -> str | None:
    amount_text = f"{amount:,.2f}"
    for index, line in enumerate(lines):
        if amount_text in line or f"￥{amount_text}" in line:
            for candidate in lines[index + 1 :]:
                if _looks_like_sector_name(candidate):
                    return candidate
    return None


def _looks_like_sector_name(line: str) -> bool:
    if not any("\u4e00" <= char <= "\u9fff" for char in line):
        return False
    if any(hint in line for hint in FUND_NAME_HINTS):
        return False
    if any(noise in line for noise in ("账户", "支付宝", "收益", "持有", "新增", "批量")):
        return False
    return not PERCENT_RE.search(line) and not YUAN_AMOUNT_RE.search(line)
