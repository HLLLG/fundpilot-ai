from __future__ import annotations

import re

from app.models import Holding
from app.services.alipay_holdings_parser import is_alipay_holdings_page, parse_alipay_holdings_page
from app.services.fund_name_utils import sanitize_fund_name


FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
AMOUNT_RE = re.compile(r"(?:持有金额|金额|资产)[^\d-]*([\d,]+(?:\.\d+)?)")
RETURN_RE = re.compile(r"(?:持有收益率|收益率|收益)[^\d+-]*([+-]?\d+(?:\.\d+)?)%")
YUAN_AMOUNT_RE = re.compile(r"￥\s*([\d,]+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
SIGNED_AMOUNT_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
UNSIGNED_AMOUNT_RE = re.compile(r"^\d+(?:\.\d+)?$")
DAILY_PLACEHOLDER_RE = re.compile(r"^[-—－―.]+$")
NEGATIVE_MARKER_RE = re.compile(r"^[-—－―]$")
ACCOUNT_DAILY_RE = re.compile(
    r"(?:当日收益|账户资产)[^\d+-]*([+-]?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
FUND_NAME_HINTS = (
    "...",
    "..",
    "ETF",
    "混合",
    "混",
    "联接",
    "债券",
    "指数",
    "主题",
    "发起",
    "军工",
    "成长",
)
FUND_SHARE_CLASS_SUFFIX_RE = re.compile(
    r"(混合|联接|债券|ETF|指数|主题|发起|货币|理财)[A-CEH]?\s*$",
    re.IGNORECASE,
)
ALIPAY_PROMO_MARKERS = (
    "投资锦囊",
    "基金经理说",
    "市场解读",
    "的重要性",
    "正在被",
    "资讯",
    "报道",
    "点评",
    "北美云厂商",
    "持续加大资本支出",
)
BLOCK_FOOTER_MARKERS = ("上证指数", "新增持有", "批量加减仓", "批量")
ALIPAY_HOLDINGS_MARKERS = (
    "我的持有",
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
    "全部持有",
    "名称/金额",
    "持有收益排序",
)
ALIPAY_TAG_NOISE = (
    "金选",
    "超额收益",
    "指数基金",
    "基金经理说",
    "市场解读",
    "偏股",
    "偏债",
    "指数",
    "黄金",
    "全球",
    "全部",
    "基金市场",
    "机会",
    "自选",
    "持有",
)
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

    if is_alipay_holdings_page(lines):
        alipay_holdings = parse_alipay_holdings_page(text)
        if alipay_holdings:
            return alipay_holdings

    holdings: list[Holding] = []

    for index, line in enumerate(lines):
        code_match = FUND_CODE_RE.search(line)
        if not code_match:
            continue

        fund_code = code_match.group(1)
        block = _holding_block(lines, index)
        fund_name = sanitize_fund_name(_guess_fund_name(lines, index, fund_code))
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

    drafts = _parse_alipay_drafts_without_codes(lines)
    account_daily = _parse_account_daily_profit(lines)
    return _reconcile_daily_profit_signs(drafts, account_daily)


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


def detect_ocr_source(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if is_yangjibao_detail_page(lines):
        return "yangjibao_detail"
    if any(marker in line for line in lines for marker in ALIPAY_HOLDINGS_MARKERS):
        return "alipay_holdings"
    if any("养基宝" in line for line in lines):
        return "yangjibao_overview"
    if any(marker in line for line in lines for marker in ("账户汇总", "账户资产")):
        return "yangjibao_overview"
    if is_alipay_holdings_page(lines):
        return "alipay_holdings"
    return "unknown"


def is_yangjibao_detail_page(lines: list[str]) -> bool:
    """养基宝单基金详情页：含 6 位代码 + 持有份额/关联板块等字段。"""
    joined = "\n".join(lines)
    if not FUND_CODE_RE.search(joined):
        return False
    if "持有份额" in joined and "持有金额" in joined:
        return True
    if "关联板块" in joined and ("业绩走势" in joined or "我的收益" in joined):
        return True
    if "持仓占比" in joined and "持有收益" in joined and "持有金额" in joined:
        return True
    return False


def _looks_like_alipay_holdings_list(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    if any(marker in joined for marker in ALIPAY_HOLDINGS_MARKERS):
        return True
    name_indexes = [
        index for index, line in enumerate(lines) if _looks_like_alipay_fund_name(line)
    ]
    if len(name_indexes) < 2:
        return False
    plain_amount_blocks = 0
    for position, index in enumerate(name_indexes):
        next_index = name_indexes[position + 1] if position + 1 < len(name_indexes) else len(lines)
        block = _trim_alipay_noise_lines(lines[index + 1 : next_index])
        if _extract_alipay_holdings_metrics(block)["holding_amount"] is not None:
            plain_amount_blocks += 1
    return plain_amount_blocks >= 2 and "￥" not in joined


def _parse_alipay_holdings_list(lines: list[str]) -> list[Holding]:
    name_indexes = [
        index for index, line in enumerate(lines) if _looks_like_alipay_fund_name(line)
    ]
    holdings: list[Holding] = []

    for position, index in enumerate(name_indexes):
        next_index = name_indexes[position + 1] if position + 1 < len(name_indexes) else len(lines)
        block_lines = _trim_alipay_noise_lines(lines[index + 1 : next_index])
        metrics = _extract_alipay_holdings_metrics(block_lines)
        amount = metrics["holding_amount"]
        if amount is None:
            continue

        holding_profit = metrics["holding_profit"]
        holding_return_percent = metrics["holding_return_percent"]
        yesterday_profit = metrics["yesterday_profit"]

        holdings.append(
            Holding(
                fund_code="000000",
                fund_name=lines[index],
                holding_amount=amount,
                return_percent=holding_return_percent if holding_return_percent is not None else 0,
                holding_profit=holding_profit,
                holding_return_percent=holding_return_percent,
                yesterday_profit=yesterday_profit,
            )
        )

    return holdings


def _trim_alipay_noise_lines(block_lines: list[str]) -> list[str]:
    trimmed: list[str] = []
    for line in block_lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if any(marker in cleaned for marker in BLOCK_FOOTER_MARKERS):
            break
        if cleaned in FUND_NAME_BLOCKLIST:
            continue
        if any(noise in cleaned for noise in ALIPAY_TAG_NOISE) and not PERCENT_RE.search(cleaned):
            if not _parse_amount_token(cleaned.replace(",", "")):
                continue
        trimmed.append(cleaned)
    return trimmed


def _extract_alipay_holdings_metrics(block_lines: list[str]) -> dict:
    numbers = _extract_signed_numbers(block_lines)
    percents = _extract_signed_percents(block_lines)

    holding_amount = numbers[0] if numbers else None
    holding_return_percent = percents[-1] if percents else None
    tail_numbers = numbers[1:] if numbers else []

    yesterday_profit: float | None = None
    holding_profit: float | None = None

    if len(tail_numbers) >= 2:
        yesterday_profit = tail_numbers[0]
        holding_profit = tail_numbers[1]
    elif len(tail_numbers) == 1:
        lone = tail_numbers[0]
        if lone == 0 and holding_return_percent is not None and holding_return_percent != 0:
            yesterday_profit = lone
        else:
            holding_profit = lone

    holding_profit = _align_profit_sign_with_return(holding_profit, holding_return_percent)
    if holding_profit is None and holding_return_percent is not None and holding_amount:
        holding_profit = _round2(
            holding_amount * holding_return_percent / (100 + holding_return_percent)
        )
        holding_profit = _align_profit_sign_with_return(holding_profit, holding_return_percent)

    return {
        "holding_amount": holding_amount,
        "yesterday_profit": yesterday_profit,
        "holding_profit": holding_profit,
        "holding_return_percent": holding_return_percent,
    }


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
    if _looks_like_alipay_promo_text(cleaned):
        return False
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in cleaned)
    if not has_chinese:
        return False
    if len(cleaned) < 4 and not any(hint in cleaned for hint in ("..", "...", "ETF", "混")):
        return False
    return _has_fund_product_name_shape(cleaned)


def _looks_like_alipay_promo_text(line: str) -> bool:
    if any(marker in line for marker in ALIPAY_PROMO_MARKERS):
        return True
    if ("，" in line or "。" in line or "？" in line) and not _has_fund_share_class_suffix(line):
        return True
    if len(line) > 22 and not _has_fund_share_class_suffix(line):
        return any(marker in line for marker in ("时代", "重要", "认为", "表示", "观点"))
    return False


def _has_fund_share_class_suffix(line: str) -> bool:
    if FUND_SHARE_CLASS_SUFFIX_RE.search(line.rstrip(".…")):
        return True
    if line.endswith(("...", "..", ".")):
        return any(hint in line for hint in ("混合", "ETF", "联接", "债券", "指数", "主题", "军工", "成长"))
    return False


def _has_fund_product_name_shape(line: str) -> bool:
    if _has_fund_share_class_suffix(line):
        return True
    return any(hint in line for hint in FUND_NAME_HINTS)


def _round2(value: float) -> float:
    return round(value, 2)


def _extract_yangjibao_metrics(lines: list[str], amount: float) -> dict:
    amount_index = _find_amount_index(lines, amount)
    before_amount = lines[:amount_index] if amount_index is not None else lines
    after_amount = lines[amount_index + 1 :] if amount_index is not None else []

    before_numbers = _extract_signed_numbers(before_amount)
    after_numbers = _extract_signed_numbers(after_amount)
    before_percents = _extract_signed_percents(before_amount)
    after_percents = _extract_signed_percents(after_amount)

    daily_missing = _daily_data_missing(lines, amount_index)

    if daily_missing:
        daily_profit = None
        daily_return_percent = None
        holding_profit = before_numbers[-1] if before_numbers else None
        sector_return_percent = before_percents[0] if before_percents else None
        holding_return_percent = after_percents[-1] if after_percents else None
    elif len(before_numbers) >= 2 and before_percents:
        # 版式 A：￥ 前有当日收益 + 持有收益两组金额，板块涨跌通常在第一个百分比
        daily_profit = before_numbers[0]
        holding_profit = before_numbers[-1]
        sector_return_percent = before_percents[0]
        daily_return_percent = after_percents[0] if after_percents else None
        holding_return_percent = after_percents[-1] if after_percents else None
    elif (
        len(before_numbers) == 1
        and len(before_percents) == 1
        and len(after_percents) >= 2
    ):
        # 版式 B：￥ 前仅当日收益额+当日收益率，￥ 后为板块与持有收益
        daily_profit = before_numbers[0]
        daily_return_percent = before_percents[0]
        sector_return_percent = after_percents[0]
        holding_return_percent = after_percents[-1]
        holding_profit = after_numbers[-1] if after_numbers else None
    elif len(before_numbers) == 1 and len(before_percents) == 1 and after_percents:
        if _daily_data_missing(lines, amount_index):
            daily_profit = None
            daily_return_percent = None
            holding_profit = before_numbers[0]
            sector_return_percent = before_percents[0]
            holding_return_percent = after_percents[-1]
        else:
            first_after = _first_meaningful_line(after_amount)
            if first_after and _looks_like_sector_name(first_after) and len(after_percents) >= 2:
                daily_profit = before_numbers[0]
                daily_return_percent = before_percents[0]
                sector_return_percent = after_percents[0]
                holding_return_percent = after_percents[-1]
                holding_profit = after_numbers[-1] if after_numbers else None
            elif len(after_percents) == 1:
                if _block_has_negative_markers(before_amount):
                    daily_profit = before_numbers[0]
                    daily_return_percent = before_percents[0]
                    sector_return_percent = after_percents[0]
                    holding_return_percent = after_percents[0]
                    holding_profit = after_numbers[-1] if after_numbers else None
                else:
                    daily_profit = None
                    daily_return_percent = None
                    holding_profit = before_numbers[0]
                    holding_return_percent = after_percents[0]
                    sector_return_percent = before_percents[0]
            else:
                daily_profit = before_numbers[0]
                daily_return_percent = before_percents[0]
                sector_return_percent = after_percents[0]
                holding_return_percent = after_percents[-1]
                holding_profit = after_numbers[-1] if after_numbers else None
    elif before_percents and after_percents:
        daily_profit = before_numbers[0] if before_numbers else None
        daily_return_percent = before_percents[0]
        sector_return_percent = after_percents[0]
        holding_return_percent = after_percents[-1]
        holding_profit = after_numbers[-1] if after_numbers else None
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

    daily_profit = _align_profit_sign_with_return(daily_profit, daily_return_percent)
    holding_profit = _align_profit_sign_with_return(holding_profit, holding_return_percent)
    sector_return_percent = _align_sector_sign(sector_return_percent, daily_return_percent)

    return {
        "percentages": before_percents + after_percents,
        "daily_return_percent": daily_return_percent,
        "daily_profit": daily_profit,
        "sector_return_percent": sector_return_percent,
        "holding_profit": holding_profit,
        "holding_return_percent": holding_return_percent,
    }


def _extract_signed_numbers(lines: list[str]) -> list[float]:
    values: list[float] = []
    pending_negative = False
    for line in lines:
        cleaned = line.replace(",", "").strip()
        if _is_negative_marker_line(cleaned):
            pending_negative = True
            continue
        parsed = _parse_amount_token(cleaned)
        if parsed is None:
            if not cleaned or cleaned in ("PK", "0", "="):
                continue
            pending_negative = False
            continue
        if pending_negative and parsed > 0:
            parsed = -parsed
        pending_negative = False
        values.append(parsed)
    return values


def _extract_signed_percents(lines: list[str]) -> list[float]:
    values: list[float] = []
    pending_negative = False
    for line in lines:
        cleaned = line.strip()
        if _is_negative_marker_line(cleaned):
            pending_negative = True
            continue
        matched = False
        for match in PERCENT_RE.finditer(cleaned):
            value = float(match.group(1))
            if pending_negative and value > 0:
                value = -value
            pending_negative = False
            values.append(value)
            matched = True
        if not matched and cleaned and not _parse_amount_token(cleaned.replace(",", "")):
            pending_negative = False
    return values


def _parse_amount_token(line: str) -> float | None:
    cleaned = line.replace(",", "").strip()
    if SIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    if UNSIGNED_AMOUNT_RE.match(cleaned):
        return float(cleaned)
    return None


def _is_negative_marker_line(line: str) -> bool:
    return bool(NEGATIVE_MARKER_RE.match(line.strip())) or bool(DAILY_PLACEHOLDER_RE.match(line.strip()))


def _block_has_negative_markers(lines: list[str]) -> bool:
    return any(_is_negative_marker_line(line.strip()) for line in lines if line.strip())


def _first_meaningful_line(lines: list[str]) -> str | None:
    for line in lines:
        cleaned = line.strip()
        if not cleaned or cleaned in ("PK", "0", "="):
            continue
        if _is_negative_marker_line(cleaned):
            continue
        return cleaned
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


def _align_sector_sign(
    sector_return_percent: float | None,
    daily_return_percent: float | None,
) -> float | None:
    if sector_return_percent is None or daily_return_percent is None:
        return sector_return_percent
    if sector_return_percent == 0 or daily_return_percent == 0:
        return sector_return_percent
    if (sector_return_percent > 0) > (daily_return_percent > 0):
        return -abs(sector_return_percent)
    return sector_return_percent


def _parse_account_daily_profit(lines: list[str]) -> float | None:
    joined = "\n".join(lines)
    match = ACCOUNT_DAILY_RE.search(joined)
    if match:
        return float(match.group(1).replace(",", ""))

    for index, line in enumerate(lines):
        if "账户资产" not in line and "账户汇总" not in line:
            continue
        for candidate in lines[index + 1 : index + 6]:
            cleaned = candidate.replace(",", "").strip()
            if SIGNED_AMOUNT_RE.match(cleaned) or UNSIGNED_AMOUNT_RE.match(cleaned):
                value = float(cleaned)
                if "当日收益" in "\n".join(lines[max(0, index - 2) : index + 6]):
                    return value
        break
    return None


def _reconcile_daily_profit_signs(
    holdings: list[Holding],
    account_daily: float | None,
) -> list[Holding]:
    if account_daily is None or account_daily >= 0:
        return holdings

    corrected: list[Holding] = []
    for holding in holdings:
        daily_profit = holding.daily_profit
        daily_return = holding.daily_return_percent
        sector_return = holding.sector_return_percent

        if daily_profit is not None and daily_profit > 0 and daily_return is not None and daily_return < 0:
            daily_profit = -abs(daily_profit)
        if (
            sector_return is not None
            and sector_return > 0
            and daily_return is not None
            and daily_return < 0
        ):
            sector_return = -abs(sector_return)

        corrected.append(
            holding.model_copy(
                update={
                    "daily_profit": daily_profit,
                    "sector_return_percent": sector_return,
                }
            )
        )

    total_daily = sum(item.daily_profit or 0 for item in corrected)
    if account_daily < 0 < total_daily:
        corrected = [
            item.model_copy(
                update={
                    "daily_profit": -abs(item.daily_profit)
                    if item.daily_profit is not None
                    and item.daily_profit > 0
                    and item.daily_return_percent is not None
                    and item.daily_return_percent < 0
                    else item.daily_profit,
                    "sector_return_percent": -abs(item.sector_return_percent)
                    if item.sector_return_percent is not None
                    and item.sector_return_percent > 0
                    and item.daily_return_percent is not None
                    and item.daily_return_percent < 0
                    else item.sector_return_percent,
                }
            )
            for item in corrected
        ]
    return corrected


def _daily_data_missing(lines: list[str], amount_index: int | None) -> bool:
    if amount_index is None:
        return False

    before_numbers = _extract_signed_numbers(lines[:amount_index])
    before_percents = _extract_signed_percents(lines[:amount_index])

    for line in lines[amount_index + 1 :]:
        cleaned = line.strip()
        if not cleaned or cleaned in ("PK", "0"):
            continue
        if _is_daily_placeholder_line(cleaned) or cleaned == "=":
            return True
        if PERCENT_RE.search(cleaned):
            return False
        if _looks_like_sector_name(cleaned):
            # 当日收益在 ￥ 前已出现时，后面会先出现板块名称
            return not (before_numbers and before_percents)
        break
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
