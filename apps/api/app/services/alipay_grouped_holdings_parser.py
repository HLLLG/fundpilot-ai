"""支付宝「我的持有」按基金公司财富号分组的版式解析。

与「全部持有」总览页（4 列：金额 / 日收益 / 持有收益 / 累计收益）不同，这个版式是
3 列 × 2 行：

    名称                金额/昨日收益      持有收益/率
    新新华基金财富号 ›
    新华鑫科技3个月滚动    2,229.22        -770.78
    持有灵活配置混合A      0.00            -25.69%

对解析而言它有三个特征，通用锚点策略都踩不住：

1. **真正的块边界是「XX基金财富号」分组头**，不是基金名。基金名可能被 OCR 拆成两行
   （`新华鑫科技3个月滚动` + `持有灵活配置混合A`），而首行既不含完整产品后缀、也不以
   已知后缀结尾，通用锚点只会认到第二行，于是基金名被截断。
2. **同一个财富号下可以有多只基金**，所以分组头不能一对一映射成一条持仓；行内的
   「持有收益率」百分比才是每行的终结符。
3. **数字列序不稳定**：qwen-vl-ocr 对真实截图按列读出 [金额, 昨日收益, 持有收益, 率]，
   而某些截图会按视觉行读成 [金额, 持有收益, 昨日收益, 率]。靠位置判断必然错一半，
   所以这里用收益率反算 `amount × pct / (100 + pct)` 去认领「持有收益」，剩下的才是
   昨日收益——列序换了结果也不变。
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
}
_SKIP_CONTAINS = (
    "金额/昨日收益",
    "持有收益/率",
    "更新时间排序",
    "持有收益排序",
    "我的持有",
    "本页面非任何法律文件",
    "该页面由蚂蚁财富",
)


def is_alipay_wealth_account_grouped_page(lines: list[str]) -> bool:
    """至少两个财富号分组头 + 「我的持有」三列页眉，才认定是这个版式。"""
    if _count_group_headers(lines) < 2:
        return False
    joined = "\n".join(lines)
    return "金额/昨日收益" in joined or "持有收益/率" in joined or "我的持有" in joined


def _count_group_headers(lines: list[str]) -> int:
    return sum(1 for line in lines if parse_wealth_account_header(line) is not None)


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
    """按财富号分组切块，块内再按「持有收益率」拆行，逐行解析成 Holding。"""
    header_indexes = [
        index for index, line in enumerate(lines) if parse_wealth_account_header(line) is not None
    ]
    if not header_indexes:
        return []

    holdings: list[Holding] = []
    for position, start in enumerate(header_indexes):
        end = (
            header_indexes[position + 1]
            if position + 1 < len(header_indexes)
            else len(lines)
        )
        header = parse_wealth_account_header(lines[start])
        assert header is not None  # header_indexes 由同一判定产生
        _issuer, inline_rest = header

        block: list[str] = [inline_rest] if inline_rest else []
        block.extend(lines[start + 1 : end])
        holdings.extend(_parse_group_block(block))
    return holdings


def _parse_group_block(block_lines: list[str]) -> list[Holding]:
    """一个财富号分组下可能有多只基金：以「持有收益率」行为终结符切分。"""
    rows: list[list[str]] = []
    current: list[str] = []
    for line in block_lines:
        cleaned = line.strip()
        if not cleaned or _should_skip(cleaned):
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
    return False


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
