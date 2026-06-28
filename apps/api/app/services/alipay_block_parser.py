"""支付宝持有页 OCR：块锚 + 列无关解析，及多策略择优编排。"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

from app.models import Holding
from app.services.fund_name_utils import looks_like_fund_product_name, sanitize_fund_name
from app.services.ocr_text_utils import (
    align_profit_sign,
    extract_percent,
    infer_holding_profit,
    is_near_zero,
)

# 延迟导入避免循环依赖；编排层在运行时引用 legacy 解析函数
LegacyParseFn = Callable[[list[str]], list[Holding]]


class _StrategyResult(NamedTuple):
    name: str
    holdings: list[Holding]
    score: float


def parse_block_anchored_holdings(lines: list[str]) -> list[Holding]:
    """以基金名为锚切块，块内列顺序无关地提取金额与收益。"""
    from app.services.alipay_holdings_parser import (
        _find_my_holdings_name_anchors,
        _is_footer_line,
        _is_noise_line,
        _is_portfolio_weight_line,
        _merge_name_fragments,
        _reconcile_alipay_profit_signs,
    )

    anchors = _find_my_holdings_name_anchors(lines, 0)
    if not anchors:
        return []

    holdings: list[Holding] = []
    for position, (anchor_index, anchor_name) in enumerate(anchors):
        next_index = anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
        block_lines: list[str] = []
        for line in lines[anchor_index:next_index]:
            if _is_footer_line(line):
                break
            block_lines.append(line)
        if not block_lines:
            continue
        if _is_footer_line(block_lines[0]):
            break
        holding = _parse_block_column_agnostic(
            anchor_name,
            block_lines[1:],
            is_noise_line=_is_noise_line,
            is_portfolio_weight_line=_is_portfolio_weight_line,
            merge_name_fragments=_merge_name_fragments,
        )
        if holding is not None:
            holdings.append(holding)
    return _reconcile_alipay_profit_signs(holdings)


def _parse_block_column_agnostic(
    anchor_name: str,
    body_lines: list[str],
    *,
    is_noise_line: Callable[[str], bool],
    is_portfolio_weight_line: Callable[[str], bool],
    merge_name_fragments: Callable[[list[str]], str],
) -> Holding | None:
    from app.services.alipay_holdings_parser import (
        _looks_like_name_fragment,
        _numbers_from_line,
        _is_promo_fragment,
    )

    name_fragments = [anchor_name.strip()]
    metric_lines: list[str] = []
    for line in body_lines:
        if not line or is_noise_line(line):
            continue
        from app.services.alipay_holdings_parser import _is_footer_line

        if _is_footer_line(line):
            break
        if _looks_like_name_fragment(line) and not _numbers_from_line(line):
            if not _is_promo_fragment(line):
                name_fragments.append(line)
            continue
        metric_lines.append(line)

    fund_name = sanitize_fund_name(merge_name_fragments(name_fragments))
    if not fund_name or not looks_like_fund_product_name(fund_name):
        if not re.search(r"(混合|联接|股票|指数)", fund_name):
            return None

    numbers: list[float] = []
    return_percents: list[float] = []
    for line in metric_lines:
        if is_portfolio_weight_line(line):
            continue
        percent = extract_percent(line)
        if percent is not None:
            return_percents.append(percent)
            remainder = re.sub(r"([+-]?\d+(?:\.\d+)?)\s*%", " ", line)
            numbers.extend(_numbers_from_line(remainder))
            continue
        numbers.extend(_numbers_from_line(line))

    holding_amount = _pick_holding_amount(numbers)
    if holding_amount is None:
        return None

    profit_numbers = _profit_numbers_after_amount(numbers, holding_amount)
    yesterday_profit, holding_profit = _map_profit_columns(profit_numbers)
    holding_return_percent = return_percents[-1] if return_percents else None

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


def _pick_holding_amount(numbers: list[float]) -> float | None:
    if not numbers:
        return None
    large = [value for value in numbers if abs(value) >= 10]
    pool = large or [value for value in numbers if value > 0] or numbers
    return max(pool, key=abs)


def _profit_numbers_after_amount(numbers: list[float], holding_amount: float) -> list[float]:
    profits: list[float] = []
    skipped_amount = False
    for value in numbers:
        if not skipped_amount and abs(value - holding_amount) < 0.001:
            skipped_amount = True
            continue
        profits.append(value)
    return profits


def _map_profit_columns(profits: list[float]) -> tuple[float | None, float | None]:
    if len(profits) >= 3:
        return profits[0], profits[1]
    if len(profits) == 2:
        if is_near_zero(profits[0]):
            return profits[0], profits[1]
        return profits[1], profits[0]
    if len(profits) == 1:
        return None, profits[0]
    return None, None


def _normalize_fund_key(name: str) -> str:
    return sanitize_fund_name(name).replace("（", "(").replace("）", ")")


def _score_holdings(holdings: list[Holding], lines: list[str]) -> float:
    from app.services.alipay_holdings_parser import (
        ALIPAY_NOISE_MARKERS,
        _find_my_holdings_name_anchors,
    )

    if not holdings:
        return 0.0

    anchor_count = len(_find_my_holdings_name_anchors(lines, 0))
    score = float(len(holdings) * 100)
    if anchor_count > len(holdings):
        score -= float(anchor_count - len(holdings)) * 40.0

    for holding in holdings:
        name = holding.fund_name or ""
        if looks_like_fund_product_name(name):
            score += 15.0
        if holding.holding_amount is not None and holding.holding_amount >= 1:
            score += 10.0
        if holding.holding_profit is not None:
            score += 5.0
        if holding.yesterday_profit is not None:
            score += 3.0
        if any(marker in name for marker in ALIPAY_NOISE_MARKERS):
            score -= 200.0
        if holding.holding_return_percent is not None and holding.holding_return_percent > 50:
            # 占比误当收益率时通常 >10
            score -= 25.0

    return score


def _merge_holdings_by_name(*groups: list[Holding]) -> list[Holding]:
    merged: dict[str, Holding] = {}
    order: list[str] = []
    for group in groups:
        for holding in group:
            key = _normalize_fund_key(holding.fund_name or "")
            if not key:
                continue
            if key not in merged:
                order.append(key)
                merged[key] = holding
                continue
            existing = merged[key]
            # 保留字段更完整的一版
            existing_fields = sum(
                1
                for field in (
                    existing.holding_profit,
                    existing.yesterday_profit,
                    existing.holding_return_percent,
                )
                if field is not None
            )
            incoming_fields = sum(
                1
                for field in (
                    holding.holding_profit,
                    holding.yesterday_profit,
                    holding.holding_return_percent,
                )
                if field is not None
            )
            if incoming_fields >= existing_fields:
                merged[key] = holding
    return [merged[key] for key in order]


def parse_alipay_holdings_multi_strategy(lines: list[str]) -> list[Holding]:
    """并行多种解析策略，取得分最高结果；不足时按基金名并集补漏。"""
    from app.services.alipay_holdings_parser import (
        _find_my_holdings_name_anchors,
        _parse_alipay_overview_holdings,
        _parse_fund_block,
        _parse_my_holdings_name_anchored,
        _reconcile_alipay_profit_signs,
        _split_fund_blocks,
    )

    def parse_percent_blocks() -> list[Holding]:
        holdings: list[Holding] = []
        for block in _split_fund_blocks(lines):
            holding = _parse_fund_block(block)
            if holding is not None:
                holdings.append(holding)
        return holdings

    strategies: list[tuple[str, LegacyParseFn]] = [
        ("block_anchored", lambda ls: parse_block_anchored_holdings(ls)),
        ("overview", _parse_alipay_overview_holdings),
        ("name_anchored", _parse_my_holdings_name_anchored),
        ("percent_blocks", parse_percent_blocks),
    ]

    scored: list[_StrategyResult] = []
    by_name: dict[str, list[Holding]] = {}
    for name, fn in strategies:
        try:
            holdings = fn(lines)
        except Exception:  # noqa: BLE001 — 单策略失败不拖垮整体
            holdings = []
        scored.append(_StrategyResult(name, holdings, _score_holdings(holdings, lines)))
        by_name[name] = holdings

    scored.sort(key=lambda item: (item.score, len(item.holdings)), reverse=True)
    best = scored[0]
    if not best.holdings:
        return []

    anchor_count = len(_find_my_holdings_name_anchors(lines, 0))
    chosen = best.holdings
    if anchor_count > len(chosen):
        union = _merge_holdings_by_name(
            by_name.get("block_anchored", []),
            by_name.get("overview", []),
            chosen,
        )
        if len(union) > len(chosen):
            chosen = union

    return _reconcile_alipay_profit_signs(chosen)
