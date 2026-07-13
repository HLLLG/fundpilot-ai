from __future__ import annotations

import re

from app.models import Holding
from app.services.fund_name_utils import looks_like_fund_product_name, sanitize_fund_name
from app.services.ocr_text_utils import (
    align_profit_sign,
    extract_percent,
    infer_holding_profit,
    is_near_zero,
)

# 支付宝「我的持有」三列排版，OCR 常按行交错读出：
# 基金名(可拆行) | 金额、昨日 | 持有收益、持有收益率
ALIPAY_PAGE_MARKERS = (
    "我的持有",
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
)
# 支付宝「全部持有」总览页：名称/金额 | 日收益 | 持有收益 | 累计收益（2025+ 版式）
ALIPAY_OVERVIEW_MARKERS = (
    "全部持有",
    "名称/金额",
    "日收益",
    "持有收益排序",
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
ALIPAY_FOOTER_MARKERS = (
    "基金市场",
    "上证指数",
    "新增持有",
    "批量",
    "本页面非任何法律文件",
    "该页面由蚂蚁财富",
    "以上按照持有收益排序",
)
ALIPAY_NOISE_MARKERS = (
    "余额宝",
    "余额",
    "灵活取用",
    "投资锦囊",
    "基金经理说",
    "市场解读",
    "财富号",
    "的重要性",
    "正在被",
    "厄尔尼诺",
    "北美云厂商",
    "持续加大资本支出",
    "当前行情下",
    "该如何操作",
    "拿不定主意",
    "不妨先看看收益分析",
    "去看看",
    "以上按照持有收益排序",
)
PERCENT_LINE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])([+-]?\d[\d,]*(?:\.\d+)?)(?![\d.])")
NEGATIVE_LINE_RE = re.compile(r"^[-—－―+]$")
NAME_FRAGMENT_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·（）()]{1,48}$")
INLINE_TWO_COLUMN_RE = re.compile(
    r"^\s*([+-]?\d[\d,]*(?:\.\d+)?)\s+([+-]?\d[\d,]*(?:\.\d+)?)\s*$"
)
# 允许 混合/股票/指数/联接 与份额字母间出现 (QDII)/（QDII）/(QDII-ETF) 等括注
_QDII_INFIX = r"(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?"
COMPLETE_FUND_NAME_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9·]{4,40}"
    r"(?:混合|联接|ETF联接|主题ETF联接|股票|指数)"
    + _QDII_INFIX
    + r"[A-CEH]$",
    re.IGNORECASE,
)


def is_alipay_overview_holdings_page(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    return all(marker in joined for marker in ("全部持有", "名称/金额", "日收益"))


def alipay_today_official_profit_published(lines: list[str]) -> bool:
    """支付宝持有页出现「今日收益更新」时，日收益列已是当日官方净值收益。"""
    joined = "\n".join(lines)
    return "今日收益更新" in joined or "今日收益已更新" in joined


def is_alipay_holdings_page(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    if is_alipay_overview_holdings_page(lines):
        return True
    if "我的持有" in joined or "金额/昨日收益" in joined:
        return True
    percent_blocks = sum(1 for line in lines if extract_percent(line) is not None)
    return percent_blocks >= 2 and "￥" not in joined


def _is_compact_alipay_overview_layout(lines: list[str]) -> bool:
    """VLM OCR 常省略「全部持有/名称/金额」页眉，但保留「占比 + 基金名」紧凑版式。"""
    weight_lines = sum(1 for line in lines if _is_portfolio_weight_line(line))
    name_lines = sum(
        1
        for line in lines
        if is_alipay_fund_name(line) or looks_like_fund_product_name(line)
    )
    return weight_lines >= 2 and name_lines >= 2


def parse_alipay_holdings_page(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    from app.services.alipay_block_parser import parse_alipay_holdings_multi_strategy

    if (
        is_alipay_holdings_page(lines)
        or _is_compact_alipay_overview_layout(lines)
        or _find_my_holdings_name_anchors(lines, 0)
    ):
        holdings = parse_alipay_holdings_multi_strategy(lines)
        if alipay_today_official_profit_published(lines):
            holdings = _promote_today_official_daily_profit(holdings)
        return holdings
    return []


def _promote_today_official_daily_profit(holdings: list[Holding]) -> list[Holding]:
    """「今日收益更新」页：各策略可能把日收益写入 yesterday_profit，统一提升为 daily_profit。"""
    promoted: list[Holding] = []
    for holding in holdings:
        if holding.daily_profit is not None:
            promoted.append(holding)
            continue
        if holding.yesterday_profit is None:
            promoted.append(holding)
            continue
        promoted.append(
            holding.model_copy(
                update={
                    "daily_profit": holding.yesterday_profit,
                    "yesterday_profit": None,
                    "daily_return_percent_source": "official_nav",
                    "amount_includes_today": True,
                    "settled_holding_amount": holding.holding_amount,
                }
            )
        )
    return promoted


def _parse_my_holdings_name_anchored(lines: list[str]) -> list[Holding]:
    start = 0
    for index, line in enumerate(lines):
        if "持有收益/率" in line or "金额/昨日收益" in line:
            start = index + 1
            break

    anchors = _find_my_holdings_name_anchors(lines, start)
    if not anchors:
        return []

    holdings: list[Holding] = []
    for position, (anchor_index, _) in enumerate(anchors):
        next_index = anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
        block_lines = lines[anchor_index:next_index]
        holding = _parse_my_holdings_block(block_lines)
        if holding is not None:
            holdings.append(holding)
    return holdings


def _find_my_holdings_name_anchors(
    lines: list[str],
    start: int,
) -> list[tuple[int, str]]:
    anchors: list[tuple[int, str]] = []
    for index in range(start, len(lines)):
        line = lines[index]
        if _is_footer_line(line):
            break
        if _is_noise_line(line):
            continue
        if _is_fund_name_anchor(line):
            anchors.append((index, line))
    return anchors


def _is_fund_name_anchor(line: str) -> bool:
    cleaned = line.strip()
    if cleaned.startswith(("题", "接")) and not any(
        issuer in cleaned for issuer in ("华夏", "易方达", "银河", "广发", "中欧", "招商")
    ):
        return False
    if re.fullmatch(r"[A-CEH]", cleaned):
        return False
    if COMPLETE_FUND_NAME_RE.match(cleaned):
        return True
    if looks_like_fund_product_name(cleaned):
        return True
    return False


def _parse_my_holdings_block(block_lines: list[str]) -> Holding | None:
    cleaned = [line for line in block_lines if line and not _is_footer_line(line)]
    if not cleaned:
        return None

    percent_index, holding_return_percent, percent_pending_negative = _find_holding_return_percent(cleaned)
    if holding_return_percent is None or percent_index is None:
        return None

    metric_lines = cleaned[:percent_index]
    if percent_pending_negative and metric_lines:
        metric_lines = metric_lines[:-1]

    name_fragments: list[str] = []
    metric_only_lines: list[str] = []
    for line in metric_lines:
        if _looks_like_name_fragment(line) and not _numbers_from_line(line):
            if not _is_promo_fragment(line):
                name_fragments.append(line)
            continue
        metric_only_lines.append(line)

    holding_amount, yesterday_profit, holding_profit = _extract_my_holdings_metrics(
        metric_only_lines,
        percent_line=cleaned[percent_index],
        percent_pending_negative=percent_pending_negative,
    )

    fund_name = sanitize_fund_name(_merge_name_fragments(name_fragments))
    if not fund_name or holding_amount is None:
        return None

    holding_profit = infer_holding_profit(
        holding_amount=holding_amount,
        holding_return_percent=holding_return_percent,
        holding_profit=holding_profit,
    )

    return Holding(
        fund_code="000000",
        fund_name=fund_name,
        holding_amount=holding_amount,
        return_percent=holding_return_percent or 0,
        holding_profit=holding_profit,
        holding_return_percent=holding_return_percent,
        yesterday_profit=yesterday_profit,
    )


def _find_holding_return_percent(
    lines: list[str],
) -> tuple[int | None, float | None, bool]:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if _is_portfolio_weight_line(line):
            continue
        percent = extract_percent(line)
        if percent is None:
            continue
        pending_negative = index >= 1 and NEGATIVE_LINE_RE.match(lines[index - 1].strip()) is not None
        if pending_negative and percent > 0:
            percent = -percent
        return index, percent, pending_negative
    return None, None, False


def _extract_my_holdings_metrics(
    metric_lines: list[str],
    *,
    percent_line: str,
    percent_pending_negative: bool,
) -> tuple[float | None, float | None, float | None]:
    """从持有收益率行向上解析：金额 | 昨日收益 | 持有收益（养基宝式 bottom-up）。"""
    inline_profit_numbers = _numbers_from_line(
        PERCENT_LINE_RE.sub("", percent_line),
        pending_negative=percent_pending_negative,
    )

    numbers_bottom_up: list[float] = []
    for line in reversed(metric_lines):
        if _is_noise_line(line):
            continue
        inline_match = INLINE_TWO_COLUMN_RE.match(line.replace(",", ""))
        if inline_match:
            numbers_bottom_up.extend(
                [float(inline_match.group(2)), float(inline_match.group(1))]
            )
            continue
        line_numbers = _numbers_from_line(line)
        numbers_bottom_up.extend(reversed(line_numbers))

    holding_amount: float | None = None
    for line in metric_lines:
        if _is_noise_line(line):
            continue
        for value in _numbers_from_line(line):
            if _is_holding_amount(value, line):
                if holding_amount is None or abs(value) > abs(holding_amount):
                    holding_amount = value

    yesterday_profit: float | None = None
    holding_profit: float | None = None

    metric_numbers = [value for value in numbers_bottom_up if value != holding_amount]
    if metric_numbers:
        if is_near_zero(metric_numbers[0]) and len(metric_numbers) >= 2:
            yesterday_profit = metric_numbers[0]
            holding_profit = metric_numbers[1]
        else:
            holding_profit = metric_numbers[0]
            if len(metric_numbers) >= 2:
                yesterday_profit = metric_numbers[1]

    if holding_profit is None and inline_profit_numbers:
        for value in inline_profit_numbers:
            if not is_near_zero(value) and value != holding_amount:
                holding_profit = value
                break
    elif holding_profit is not None and is_near_zero(holding_profit) and inline_profit_numbers:
        for value in inline_profit_numbers:
            if not is_near_zero(value) and value != holding_amount:
                holding_profit = value
                break

    return holding_amount, yesterday_profit, holding_profit


def _reconcile_alipay_profit_signs(holdings: list[Holding]) -> list[Holding]:
    """对齐持有收益/昨日收益符号与收益率（养基宝 account-level 思路的 per-fund 版）。"""
    reconciled: list[Holding] = []
    for holding in holdings:
        holding_profit = align_profit_sign(
            holding.holding_profit,
            holding.holding_return_percent,
        )
        yesterday_profit = holding.yesterday_profit
        reconciled.append(
            holding.model_copy(
                update={
                    "holding_profit": holding_profit,
                    "yesterday_profit": yesterday_profit,
                }
            )
        )
    return reconciled


def _parse_alipay_overview_holdings(lines: list[str]) -> list[Holding]:
    """解析「全部持有」四列版式：金额、日收益、持有收益、累计收益 + 占比 + 持有收益率。"""
    name_indexes = [
        index
        for index, line in enumerate(lines)
        if is_alipay_fund_name(line) or looks_like_fund_product_name(line)
    ]
    if not name_indexes:
        return []

    holdings: list[Holding] = []
    today_official = alipay_today_official_profit_published(lines)
    for position, name_index in enumerate(name_indexes):
        next_index = (
            name_indexes[position + 1] if position + 1 < len(name_indexes) else len(lines)
        )
        block_lines = [
            line
            for line in lines[name_index + 1 : next_index]
            if line and not _is_noise_line(line)
        ]
        holding = _parse_overview_fund_block(
            lines[name_index],
            block_lines,
            today_official_profit=today_official,
        )
        if holding is not None:
            holdings.append(holding)
    return holdings


def _parse_overview_fund_block(
    fund_name: str,
    block_lines: list[str],
    *,
    today_official_profit: bool = False,
) -> Holding | None:
    """四列版式：金额、日收益、持有收益、累计收益。

    默认「日收益」= 上一交易日官方净值收益 → ``yesterday_profit``。
    「今日收益更新」时「日收益」= 当日官方净值收益 → ``daily_profit``。
    """
    numbers: list[float] = []
    holding_return_percent: float | None = None

    for line in block_lines:
        if _is_portfolio_weight_line(line):
            continue
        percent = extract_percent(line)
        if percent is not None:
            holding_return_percent = percent
            continue
        numbers.extend(_numbers_from_line(line))

    if not numbers:
        return None

    holding_amount = numbers[0]
    daily_column = numbers[1] if len(numbers) >= 2 else None
    holding_profit = numbers[2] if len(numbers) >= 3 else None
    if holding_profit is None and len(numbers) >= 2 and not today_official_profit:
        holding_profit = numbers[1]

    holding_profit = infer_holding_profit(
        holding_amount=holding_amount,
        holding_return_percent=holding_return_percent,
        holding_profit=holding_profit,
    )

    if today_official_profit and daily_column is not None:
        return Holding(
            fund_code="000000",
            fund_name=sanitize_fund_name(fund_name),
            holding_amount=holding_amount,
            settled_holding_amount=holding_amount,
            return_percent=holding_return_percent or 0,
            holding_profit=holding_profit,
            holding_return_percent=holding_return_percent,
            daily_profit=daily_column,
            daily_return_percent_source="official_nav",
            amount_includes_today=True,
        )

    return Holding(
        fund_code="000000",
        fund_name=sanitize_fund_name(fund_name),
        holding_amount=holding_amount,
        return_percent=holding_return_percent or 0,
        holding_profit=holding_profit,
        holding_return_percent=holding_return_percent,
        yesterday_profit=daily_column,
    )


def _is_portfolio_weight_line(line: str) -> bool:
    cleaned = line.strip().replace(" ", "")
    return cleaned.startswith("占比") and PERCENT_LINE_RE.search(cleaned) is not None


def _split_fund_blocks(lines: list[str]) -> list[list[str]]:
    percent_indexes = [
        index
        for index, line in enumerate(lines)
        if extract_percent(line) is not None and not _is_header_line(line)
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

    percent_index, holding_return_percent, percent_pending_negative = _find_holding_return_percent(cleaned)
    if holding_return_percent is None or percent_index is None:
        return None

    metric_lines = cleaned[:percent_index]
    if percent_pending_negative and metric_lines:
        metric_lines = metric_lines[:-1]

    name_fragments: list[str] = []
    metric_only_lines: list[str] = []
    for line in metric_lines:
        if _looks_like_name_fragment(line) and not _numbers_from_line(line):
            if not _is_promo_fragment(line):
                name_fragments.append(line)
            continue
        metric_only_lines.append(line)

    holding_amount, yesterday_profit, holding_profit = _extract_my_holdings_metrics(
        metric_only_lines,
        percent_line=cleaned[percent_index],
        percent_pending_negative=percent_pending_negative,
    )

    fund_name = sanitize_fund_name(_merge_name_fragments(name_fragments))
    if not fund_name or holding_amount is None:
        return None

    holding_profit = infer_holding_profit(
        holding_amount=holding_amount,
        holding_return_percent=holding_return_percent,
        holding_profit=holding_profit,
    )

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
    if re.fullmatch(r"[A-CEH]", right) and left.endswith(("混合", "联接", "债券", "股票")):
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


def _is_promo_fragment(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned:
        return True
    if COMPLETE_FUND_NAME_RE.match(cleaned):
        return False
    if looks_like_fund_product_name(cleaned):
        return False
    if any(marker in cleaned for marker in ALIPAY_NOISE_MARKERS):
        return True
    if len(cleaned) > 18 and not _has_fund_product_suffix(cleaned):
        return any(
            keyword in cleaned
            for keyword in ("持续", "加大", "机会", "来袭", "重要", "资本支出", "云厂商")
        )
    return False


def _has_fund_product_suffix(line: str) -> bool:
    return bool(COMPLETE_FUND_NAME_RE.search(line.strip()))


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
    tag_words = (
        "金选",
        "超额收益",
        "指数基金",
        "金选超额收益",
        "金选指数基金",
        "进阶理财",
        "基金",
        "定投",
    )
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


# 兼容旧测试与 ocr_parser 检测
def is_alipay_fund_name(line: str) -> bool:
    return bool(COMPLETE_FUND_NAME_RE.match(line.strip()))
