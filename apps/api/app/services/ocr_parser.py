from __future__ import annotations

import re

from app.models import Holding


FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
AMOUNT_RE = re.compile(r"(?:持有金额|金额|资产)[^\d-]*([\d,]+(?:\.\d+)?)")
RETURN_RE = re.compile(r"(?:持有收益率|收益率|收益)[^\d+-]*([+-]?\d+(?:\.\d+)?)%")
YUAN_AMOUNT_RE = re.compile(r"￥\s*([\d,]+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
SIGNED_AMOUNT_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
UNSIGNED_AMOUNT_RE = re.compile(r"^\d+(?:\.\d+)?$")
DAILY_PLACEHOLDER_RE = re.compile(r"^[-—－―.]+$")
FUND_NAME_HINTS = (
    "...",
    "..",
    "ETF",
    "混合",
    "混",
    "基金",
    "联接",
    "债券",
    "指数",
    "主题",
    "发起",
    "军工",
    "成长",
)
BLOCK_FOOTER_MARKERS = ("上证指数", "新增持有", "批量加减仓", "批量")
FUND_NAME_BLOCKLIST = frozenset(
    {
        "基金",
        "场内",
        "持有",
        "自选",
        "行情",
        "资讯",
        "会员",
        "我的",
        "详情",
        "账户",
        "账户资产",
        "场内穿透",
        "当日收益",
        "关联板块",
        "持有收益",
        "养基宝",
        "养基宝App",
    }
)


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
        block_lines = _trim_block_footer(lines[index:next_index])
        amount = _extract_float("\n".join(block_lines), YUAN_AMOUNT_RE)
        if amount is None:
            continue

        metrics = _extract_yangjibao_metrics(block_lines, amount)
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
                return_percent=return_percent if holding_return_percent is not None else 0,
                daily_profit=daily_profit,
                daily_return_percent=daily_return_percent,
                holding_profit=holding_profit,
                holding_return_percent=holding_return_percent,
                sector_name=sector_name,
                sector_return_percent=sector_return_percent,
            )
        )

    return drafts


def _trim_block_footer(block_lines: list[str]) -> list[str]:
    for index, line in enumerate(block_lines):
        if any(marker in line for marker in BLOCK_FOOTER_MARKERS):
            return block_lines[:index]
    return block_lines


def _looks_like_alipay_fund_name(line: str) -> bool:
    if FUND_CODE_RE.search(line):
        return False
    cleaned = line.strip()
    if cleaned in FUND_NAME_BLOCKLIST:
        return False
    if any(noise in cleaned for noise in ("账户", "支付宝", "上证指数", "新增持有", "批量")):
        return False
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in cleaned)
    if not has_chinese:
        return False
    if len(cleaned) < 4 and not any(hint in cleaned for hint in ("..", "...", "ETF", "混")):
        return False
    return any(hint in cleaned for hint in FUND_NAME_HINTS)


def _extract_yangjibao_metrics(lines: list[str], amount: float) -> dict:
    amount_index = _find_amount_index(lines, amount)
    before_amount = lines[:amount_index] if amount_index is not None else lines
    after_amount = lines[amount_index + 1 :] if amount_index is not None else []

    before_numbers = _extract_amounts_before_principal(before_amount)
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

    daily_missing = _daily_data_missing(lines, amount_index)

    if daily_missing:
        daily_profit = None
        daily_return_percent = None
        holding_profit = before_numbers[-1] if before_numbers else None
        sector_return_percent = before_percents[0] if before_percents else None
        holding_return_percent = after_percents[-1] if after_percents else None
    elif len(before_numbers) >= 2:
        daily_profit = before_numbers[0]
        holding_profit = before_numbers[-1]
        sector_return_percent = before_percents[-1] if before_percents else None
        if len(after_percents) >= 2:
            daily_return_percent = after_percents[0]
            holding_return_percent = after_percents[-1]
        elif len(after_percents) == 1:
            daily_return_percent = after_percents[0]
            holding_return_percent = before_percents[-1] if len(before_percents) >= 2 else after_percents[0]
        else:
            daily_return_percent = None
            holding_return_percent = before_percents[-1] if before_percents else 0
    else:
        daily_profit = before_numbers[0] if before_numbers else None
        holding_profit = before_numbers[-1] if before_numbers else None
        daily_return_percent = after_percents[0] if after_percents else None
        holding_return_percent = (
            after_percents[-1] if after_percents else (before_percents[-1] if before_percents else 0)
        )
        sector_return_percent = before_percents[-1] if before_percents else None

    if holding_return_percent is None:
        holding_return_percent = 0

    holding_profit = _align_profit_sign_with_return(holding_profit, holding_return_percent)

    return {
        "percentages": before_percents + after_percents,
        "daily_return_percent": daily_return_percent,
        "daily_profit": daily_profit,
        "sector_return_percent": sector_return_percent,
        "holding_profit": holding_profit,
        "holding_return_percent": holding_return_percent,
    }


def _extract_amounts_before_principal(before_amount: list[str]) -> list[float]:
    values: list[float] = []
    for line in before_amount:
        parsed = _parse_amount_token(line)
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_amount_token(line: str) -> float | None:
    cleaned = line.replace(",", "").strip()
    if SIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    if UNSIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    return None


def _align_profit_sign_with_return(
    profit: float | None,
    return_percent: float | None,
) -> float | None:
    if profit is None or return_percent is None or profit == 0 or return_percent == 0:
        return profit
    if (profit > 0) > (return_percent > 0):
        return -abs(profit)
    return profit


def _daily_data_missing(lines: list[str], amount_index: int | None) -> bool:
    if amount_index is None:
        return False
    for line in lines[amount_index + 1 :]:
        cleaned = line.strip()
        if not cleaned or cleaned in ("PK", "0"):
            continue
        if _is_daily_placeholder_line(cleaned) or cleaned == "=":
            return True
        if PERCENT_RE.search(cleaned):
            return False
        if _looks_like_sector_name(cleaned):
            return True
    return False


def _is_daily_placeholder_line(line: str) -> bool:
    return bool(DAILY_PLACEHOLDER_RE.match(line.strip()))


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
                if _is_daily_placeholder_line(candidate) or candidate.strip() == "=":
                    continue
                if _looks_like_sector_name(candidate):
                    return candidate
    return None


def _looks_like_sector_name(line: str) -> bool:
    if _is_daily_placeholder_line(line):
        return False
    if not any("\u4e00" <= char <= "\u9fff" for char in line):
        return False
    if _looks_like_alipay_fund_name(line):
        return False
    if any(noise in line for noise in ("账户", "支付宝", "收益", "持有", "新增", "批量")):
        return False
    return not PERCENT_RE.search(line) and not YUAN_AMOUNT_RE.search(line)
