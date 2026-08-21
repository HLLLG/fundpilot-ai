"""支付宝基金 Tab「我的持有」三列版式解析。

与「收益明细 → 全部持有」总览页（4 列：金额 / 日收益 / 持有收益 / 累计收益）不同，
这个版式是 3 列 × 2 行：

    名称                金额/昨日收益      持有收益/率
    南方黄金股指数C       1,592.98        +92.98
                          +76.54         +6.20%
    [产品周报] 通胀粘性仍存…
    招商基金财富号 ›
    招商医疗保健股票A     4,637.97        +137.97
                          +205.86        +3.07%

对解析而言有四个特征，通用锚点策略都踩不住：

1. **财富号可有可无**。有的基金只有产品周报、没有财富号；要求「至少两个财富号」
   会丢掉分组头之前的持仓，零财富号时整页走不通。
2. **长基金名常被 OCR 拆成两行**（`万家宏观择时多策略灵活` + `配置混合C`），
   首行既不含完整产品后缀，通用锚点认不到。真正的行终结符是「持有收益率」。
3. **产品周报 / 财富号 / 底栏**会插在基金行之间，必须跳过而不能并进基金名。
4. **数字列序不稳定**：qwen-vl-ocr 可能读成 [金额, 昨日收益, 持有收益, 率] 或
   [金额, 持有收益, 昨日收益, 率]。靠位置判断必然错一半，所以用收益率反算
   `amount × pct / (100 + pct)` 去认领「持有收益」，剩下的才是昨日收益。
"""
from __future__ import annotations

import re

from app.models import Holding
from app.services.fund_name_utils import sanitize_fund_name
from app.services.ocr_text_utils import align_profit_sign, extract_percent

# 「新华基金财富号」「博时基金财富号 ›」，也兼容旗舰店/直销店等变体
WEALTH_ACCOUNT_HEADER_RE = re.compile(
    r"^(?P<issuer>.{1,16}?)(?:基金)?(?:财富号|旗舰店|直销店)(?P<rest>.*)$"
)
_ARROW_CHARS = "›>》»〉→ 　\t"
# 纯数字行：允许千分位、正负号、货币符号；`2,229.22` / `-770.78` / `+4.41`
_NUMERIC_LINE_RE = re.compile(r"^[¥￥]?[+\-−–—]?\d[\d,，]*(?:\.\d+)?$")
_PERCENT_ONLY_LINE_RE = re.compile(r"^[+\-−–—]?\d+(?:\.\d+)?\s*%$")
_NUMBER_TOKEN_RE = re.compile(r"[+\-−–—]?\d[\d,，]*(?:\.\d+)?")
# 「占比 0.40%」属于全部持有版式，这里出现也不能当成持有收益率
_WEIGHT_LINE_RE = re.compile(r"^占比")
# 支付宝行内标签与页脚
_SKIP_EXACT = {
    "基金",
    "进阶理财",
    "金选",
    "指数基金",
    "超额收益",
    "定投",
    "全部",
    "偏股",
    "偏债",
    "指数",
    "黄金",
    "全球",
    "名称",
    "持有",
    "排行",
    "自选",
    "基金市场",
    "产品周报",
    "明细",
}
_SKIP_CONTAINS = (
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
    "持有收益排序",
    "我的持有",
    "本页面非任何法律文件",
    "该页面由蚂蚁财富",
    "产品周报",
    "更多产品",
    "去市场看看",
    "板块近",
    "近一年涨幅",
    "蚂蚁（杭州）",
    "蚂蚁(杭州)",
)
_PAGE_FOOTER_MARKERS = (
    "基金市场",
    "更多产品",
    "去市场看看",
    "本页面非任何法律文件",
    "该页面由蚂蚁财富",
    "以上按照持有收益排序",
)
_TIP_PUNCTUATION = "，。！？、"


def is_alipay_my_holdings_three_column_page(lines: list[str]) -> bool:
    """基金 Tab「我的持有」三列版式：名称 | 金额/昨日收益 | 持有收益/率。

    与「收益明细 → 全部持有」四列总览互斥。财富号可有可无——有的基金没有财富号、
    只有产品周报，不能再要求「至少两个财富号」才走这条解析。
    """
    from app.services.alipay_holdings_parser import is_alipay_overview_holdings_page

    if is_alipay_overview_holdings_page(lines):
        return False
    joined = "\n".join(lines)
    if "金额/昨日收益" in joined or "持有收益/率" in joined:
        return True
    return "我的持有" in joined and "名称/金额" not in joined


def is_alipay_wealth_account_grouped_page(lines: list[str]) -> bool:
    """兼容旧名：三列「我的持有」页，不再要求必须出现两个财富号。"""
    return is_alipay_my_holdings_three_column_page(lines)


def parse_wealth_account_header(line: str) -> tuple[str, str] | None:
    """命中分组头时返回 (基金公司, 同行残留内容)，否则 None。

    残留内容用于兜底 OCR 把分组头和基金名连成一行的情况。
    """
    cleaned = line.strip()
    if not cleaned or len(cleaned) > 40:
        return None
    if not any(marker in cleaned for marker in ("财富号", "旗舰店", "直销店")):
        return None
    match = WEALTH_ACCOUNT_HEADER_RE.match(cleaned)
    if match is None:
        return None
    issuer = match.group("issuer").strip()
    rest = match.group("rest").strip(_ARROW_CHARS).strip()
    if not issuer:
        return None
    return issuer, rest


def parse_alipay_grouped_holdings(lines: list[str]) -> list[Holding]:
    """三列「我的持有」：按持有收益率切行；财富号只是可跳过的分组头。

    财富号之前的基金（只有产品周报、没有财富号）也要解析，不能从第一个分组头才起算。
    """
    return _parse_group_block(lines[_content_start_index(lines) :])


def _content_start_index(lines: list[str]) -> int:
    start = 0
    for index, line in enumerate(lines):
        if "持有收益/率" in line or "金额/昨日收益" in line:
            start = index + 1
    return start


def _parse_group_block(block_lines: list[str]) -> list[Holding]:
    """以「持有收益率」行为终结符切分；财富号分组头本身丢掉，同行残留基金名保留。"""
    rows: list[list[str]] = []
    current: list[str] = []
    for line in block_lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if _is_page_footer(cleaned):
            break
        header = parse_wealth_account_header(cleaned)
        if header is not None:
            _issuer, inline_rest = header
            if inline_rest:
                current.append(inline_rest)
            continue
        if _should_skip(cleaned):
            continue
        current.append(cleaned)
        if _is_row_terminator(cleaned):
            rows.append(current)
            current = []
    if current:
        rows.append(current)

    holdings: list[Holding] = []
    for row in rows:
        holding = _parse_fund_row(row)
        if holding is not None:
            holdings.append(holding)
    return holdings


def _is_row_terminator(line: str) -> bool:
    """持有收益率行结束一只基金；占比行不算（那是全部持有版式）。"""
    if _WEIGHT_LINE_RE.match(line):
        return False
    return extract_percent(line) is not None


def _should_skip(line: str) -> bool:
    if line in _SKIP_EXACT:
        return True
    if any(marker in line for marker in _SKIP_CONTAINS):
        return True
    # 状态栏时间 `11:18`
    if re.fullmatch(r"\d{1,2}:\d{2}", line):
        return True
    if _is_promo_tip_line(line):
        return True
    return False


def _is_page_footer(line: str) -> bool:
    return any(marker in line for marker in _PAGE_FOOTER_MARKERS)


def _is_promo_tip_line(line: str) -> bool:
    """产品周报正文、运营文案：有中文标点且不像基金名。"""
    if not any(punct in line for punct in _TIP_PUNCTUATION):
        return False
    if extract_percent(line) is not None:
        return False
    from app.services.fund_name_utils import FUND_PRODUCT_SUFFIX_RE

    return FUND_PRODUCT_SUFFIX_RE.search(line) is None


def _parse_fund_row(row_lines: list[str]) -> Holding | None:
    name_parts: list[str] = []
    numbers: list[float] = []
    percents: list[float] = []

    for line in row_lines:
        if _WEIGHT_LINE_RE.match(line):
            continue
        if _PERCENT_ONLY_LINE_RE.match(line):
            percent = extract_percent(line)
            if percent is not None:
                percents.append(percent)
            continue
        if _NUMERIC_LINE_RE.match(line):
            value = _parse_number(line)
            if value is not None:
                numbers.append(value)
            continue
        # 数字与百分比混在同一行（OCR 把两列读成一行）
        percent = extract_percent(line)
        remainder = re.sub(r"[+\-−–—]?\d+(?:\.\d+)?\s*%", " ", line)
        inline_numbers = _metric_numbers_in_text(remainder)
        if percent is not None or inline_numbers:
            if percent is not None:
                percents.append(percent)
            numbers.extend(inline_numbers)
            if not _has_cjk(remainder):
                continue
        # 剩下的当基金名片段（`新华鑫科技3个月滚动` 这种带数字的也走这里）
        if _has_cjk(line) or _looks_like_latin_name_fragment(line):
            name_parts.append(line)

    fund_name = sanitize_fund_name("".join(name_parts))
    if not fund_name or not numbers:
        return None

    holding_amount = _pick_amount(numbers)
    if holding_amount is None:
        return None

    holding_return_percent = percents[-1] if percents else None
    rest = _drop_first(numbers, holding_amount)
    holding_profit, yesterday_profit = _assign_profit_columns(
        rest,
        holding_amount=holding_amount,
        holding_return_percent=holding_return_percent,
    )

    return Holding(
        fund_code="000000",
        fund_name=fund_name,
        holding_amount=holding_amount,
        return_percent=holding_return_percent or 0,
        holding_profit=align_profit_sign(holding_profit, holding_return_percent),
        holding_return_percent=holding_return_percent,
        yesterday_profit=yesterday_profit,
    )


def _parse_number(token: str) -> float | None:
    cleaned = (
        token.replace(",", "")
        .replace("，", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def _metric_numbers_in_text(text: str) -> list[float]:
    """只取独立成列的数字，忽略夹在汉字中间的数字。

    基金名里的数字（`新华鑫科技3个月滚动`、`招商中证白酒A` 这类）两侧都是汉字，
    不是金额/收益列；只有被空白或行首行尾界定的数字才算指标。
    """
    values: list[float] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if _has_cjk(before) and _has_cjk(after):
            continue
        value = _parse_number(match.group(0))
        if value is not None:
            values.append(value)
    return values


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _looks_like_latin_name_fragment(text: str) -> bool:
    """`ETF联接C` 被拆成纯拉丁片段时也要保留。"""
    stripped = text.strip()
    return len(stripped) >= 2 and bool(re.fullmatch(r"[A-Za-z0-9·()（）\-]+", stripped))


def _pick_amount(numbers: list[float]) -> float | None:
    """持有金额是本行最大的正数；行内只有一个数字时直接用它。"""
    if not numbers:
        return None
    positives = [value for value in numbers if value > 0]
    if not positives:
        return max(numbers, key=abs)
    # 金额通常远大于收益；取绝对值最大的正数比取首个更抗列序抖动
    return max(positives)


def _drop_first(numbers: list[float], value: float) -> list[float]:
    rest = list(numbers)
    for index, item in enumerate(rest):
        if abs(item - value) < 1e-9:
            del rest[index]
            break
    return rest


def _assign_profit_columns(
    candidates: list[float],
    *,
    holding_amount: float,
    holding_return_percent: float | None,
) -> tuple[float | None, float | None]:
    """用收益率反算认领「持有收益」，剩下的归「昨日收益」——与 OCR 列序无关。"""
    if not candidates:
        return None, None

    expected = _expected_profit(holding_amount, holding_return_percent)
    if expected is None:
        # 没有收益率可校验：保守地把第一个非零值当持有收益
        for index, value in enumerate(candidates):
            if value != 0:
                rest = candidates[:index] + candidates[index + 1 :]
                return value, rest[0] if rest else None
        return candidates[0], candidates[1] if len(candidates) > 1 else None

    best_index = min(
        range(len(candidates)),
        key=lambda index: abs(candidates[index] - expected),
    )
    holding_profit = candidates[best_index]
    remaining = candidates[:best_index] + candidates[best_index + 1 :]

    tolerance = max(0.5, abs(expected) * 0.05)
    if abs(holding_profit - expected) > tolerance:
        # 没有任何候选能对上收益率：说明持有收益列没被读到，用反算值兜底
        return round(expected, 2), holding_profit

    return holding_profit, remaining[0] if remaining else None


def _expected_profit(
    holding_amount: float,
    holding_return_percent: float | None,
) -> float | None:
    if holding_return_percent is None or holding_return_percent == 0:
        return None
    denominator = 100 + holding_return_percent
    if abs(denominator) < 1e-6:
        return None
    return holding_amount * holding_return_percent / denominator
