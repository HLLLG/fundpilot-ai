from __future__ import annotations

"""Bounded full-market prefilter for discovery sector evidence.

The previous pipeline fetched expensive flow/position evidence almost entirely
for the hottest sectors.  That made an early setup structurally invisible until
after it had already rallied.  This module keeps the request bounded while
reserving evidence slots for same-day flow inflections plus four price views:
momentum, high elasticity, quiet setup and pullback acceptance.
"""

from math import isfinite, sqrt
from typing import Any, Iterable, Mapping


DEFAULT_EVIDENCE_LABEL_LIMIT = 32
DEFAULT_FLOW_INFLECTION_SLOTS = 8
DEFAULT_ELASTICITY_SLOTS = 10
DEFAULT_CACHED_ELASTICITY_EXPANSION_SLOTS = 6
DEFAULT_TARGET_MOMENTUM_SLOTS = 2
DEFAULT_TARGET_FLOW_SLOTS = 2
DEFAULT_TARGET_ELASTICITY_SLOTS = 2
DEFAULT_TARGET_QUIET_SLOTS = 1
DEFAULT_TARGET_PULLBACK_SLOTS = 1


def select_opportunity_evidence_labels(
    sector_heat: list[dict],
    target_sectors: list[str],
    focus_sectors: list[str],
    *,
    flow_inflection_labels: list[str] | None = None,
    max_labels: int = DEFAULT_EVIDENCE_LABEL_LIMIT,
) -> list[str]:
    limit = max(1, int(max_labels))
    result: list[str] = []
    seen: set[str] = set()

    def append_labels(values: Iterable[str]) -> None:
        for raw in values:
            label = str(raw or "").strip()
            if not label or label in seen or len(result) >= limit:
                continue
            seen.add(label)
            result.append(label)

    append_labels([*target_sectors, *focus_sectors])
    # 同日资金拐点必须在价格热度分桶之前保留，否则一个尚未明显上涨的早期方向
    # 会在资金证据计算前被热度榜挤掉。
    append_labels((flow_inflection_labels or [])[:DEFAULT_FLOW_INFLECTION_SLOTS])
    rows = [row for row in sector_heat if _label(row)]

    momentum = sorted(
        rows,
        key=lambda row: (
            _num(row.get("heat_score")) or -999.0,
            _num(row.get("change_5d_percent")) or -999.0,
        ),
        reverse=True,
    )
    append_labels(_label(row) for row in momentum[:8])

    # 给高弹性但尚未进入热度榜前列的方向留证据位。这里只做召回，不授予交易资格；
    # 完整的 20 日真实波动、趋势、资金和结构仍在后续 V3 方向模型中复核。
    elasticity = sorted(rows, key=_price_elasticity_recall_score, reverse=True)
    append_labels(_label(row) for row in elasticity[:DEFAULT_ELASTICITY_SLOTS])

    quiet_setups = sorted(rows, key=_quiet_setup_score, reverse=True)
    append_labels(_label(row) for row in quiet_setups[:10])

    pullbacks = sorted(
        [row for row in rows if _is_pullback_candidate(row)],
        key=_pullback_score,
        reverse=True,
    )
    append_labels(_label(row) for row in pullbacks[:8])

    # Fill any remaining budget deterministically.  This also covers providers
    # that temporarily omit one of the change windows.
    append_labels(_label(row) for row in momentum)
    return result[:limit]


def select_balanced_target_labels(
    sector_heat: list[dict],
    focus_sectors: list[str],
    *,
    flow_inflection_labels: list[str] | None = None,
    max_labels: int = 8,
) -> list[str]:
    """全市场目标方向：关注优先，其余按资金拐点 / 热度 / 弹性 / 蓄势轮转，不单吃短热度。

    1/5 日热度只是其中一路。资金拐点、价格弹性与安静蓄势已经在证据预筛里算过，
    这里把它们提升为与热度并列的目标席，避免 8 个自动方向全被短热度占满。
    """

    limit = max(1, int(max_labels))
    result: list[str] = []
    seen: set[str] = set()

    def append_one(raw: str) -> bool:
        label = str(raw or "").strip()
        if not label or label in seen or len(result) >= limit:
            return False
        seen.add(label)
        result.append(label)
        return True

    for raw in focus_sectors:
        append_one(raw)

    rows = [row for row in sector_heat if _label(row)]
    momentum = [
        _label(row)
        for row in sorted(
            rows,
            key=lambda row: (
                _num(row.get("heat_score")) or -999.0,
                _num(row.get("change_5d_percent")) or -999.0,
            ),
            reverse=True,
        )
    ]
    elasticity = [
        _label(row)
        for row in sorted(rows, key=_price_elasticity_recall_score, reverse=True)
    ]
    quiet = [
        _label(row) for row in sorted(rows, key=_quiet_setup_score, reverse=True)
    ]
    pullbacks = [
        _label(row)
        for row in sorted(
            [row for row in rows if _is_pullback_candidate(row)],
            key=_pullback_score,
            reverse=True,
        )
    ]
    buckets = [
        list((flow_inflection_labels or [])[:DEFAULT_TARGET_FLOW_SLOTS]),
        momentum[:DEFAULT_TARGET_MOMENTUM_SLOTS],
        elasticity[:DEFAULT_TARGET_ELASTICITY_SLOTS],
        quiet[:DEFAULT_TARGET_QUIET_SLOTS],
        pullbacks[:DEFAULT_TARGET_PULLBACK_SLOTS],
        list(flow_inflection_labels or []),
        momentum,
        elasticity,
        quiet,
        pullbacks,
    ]
    progressed = True
    while len(result) < limit and progressed:
        progressed = False
        for bucket in buckets:
            while bucket and (not bucket[0] or bucket[0] in seen):
                bucket.pop(0)
            if not bucket or len(result) >= limit:
                continue
            if append_one(bucket.pop(0)):
                progressed = True
    return result[:limit]


def select_snapshot_flow_inflection_labels(
    sector_heat: list[dict],
    snapshot: dict[str, Any] | None,
    *,
    max_labels: int = DEFAULT_FLOW_INFLECTION_SLOTS,
) -> list[str]:
    """从同日批量快照召回资金拐点，不额外发起逐板块网络请求。"""

    if not isinstance(snapshot, dict):
        return []
    snapshot_trade_date = str(snapshot.get("trade_date") or "")[:10]
    heat_by_label = {_label(row): row for row in sector_heat if _label(row)}
    ranked: list[tuple[tuple[float, float, float, float], str]] = []
    for raw in snapshot.get("items") or []:
        if not isinstance(raw, dict):
            continue
        label = _label(raw)
        if not label or label not in heat_by_label:
            continue
        flow_data_date = str(raw.get("flow_data_date") or "")[:10]
        today_flow = _num(raw.get("main_force_net_yi"))
        if today_flow is None or today_flow <= 0.05:
            continue
        # 主题榜的当日主力流入与涨跌幅来自同一次实时快照；五日累计的日期字段
        # 盘中却可能仍停在上一交易日。此时只弃用五日累计，不能把真实的当日回流
        # 一并删除，否则最早发生的资金拐点仍会在预筛阶段消失。
        five_day_aligned = bool(
            not snapshot_trade_date
            or (flow_data_date and flow_data_date == snapshot_trade_date)
        )
        five_day_flow = (
            _num(raw.get("cumulative_5d_net_yi")) if five_day_aligned else None
        )
        heat = heat_by_label[label]
        change_1d = _num(heat.get("change_1d_percent"))
        breadth = _num(heat.get("advancing_ratio_percent"))
        # 优先级：多日流出后回流 > 下跌中承接 > 量价同向温和上涨。
        pattern_priority = (
            3.0
            if five_day_flow is not None and five_day_flow < 0
            else 2.0
            if change_1d is not None and change_1d < 0
            else 1.0
        )
        ranked.append(
            (
                (
                    pattern_priority,
                    breadth if breadth is not None else 50.0,
                    -abs(change_1d or 0.0),
                    today_flow,
                ),
                label,
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [label for _score, label in ranked[: max(0, int(max_labels))]]


def select_cached_high_elasticity_labels(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    exclude_labels: Iterable[str] = (),
    as_of_trade_date: str | None = None,
    max_labels: int = DEFAULT_CACHED_ELASTICITY_EXPANSION_SLOTS,
) -> list[str]:
    """用全市场缓存中的真实 20 日波动补召回，不因短期热度不高而漏掉弹性方向。"""

    excluded = {str(label).strip() for label in exclude_labels if str(label).strip()}
    candidates: list[tuple[str, Mapping[str, Any], float]] = []
    for raw_label, row in positions.items():
        label = str(raw_label or "").strip()
        if not label or label in excluded or not isinstance(row, Mapping):
            continue
        if row.get("available") is not True:
            continue
        if as_of_trade_date and str(row.get("data_end_date") or "")[:10] != as_of_trade_date:
            # 分位分母允许用较旧缓存缩小样本，但一旦把板块提升为可执行候选，价格证据
            # 必须与本次有效交易日严格一致，不能把昨日高波动冒充今日机会。
            continue
        volatility = _num(row.get("annualized_volatility_20d_percent"))
        if volatility is None:
            continue
        position_label = str(row.get("position_label") or "")
        distance_ma20 = _num(row.get("distance_from_ma20_percent"))
        if position_label == "weak_breakdown" or (
            distance_ma20 is not None and distance_ma20 < -6.0
        ):
            continue
        return_5d = _num(row.get("return_5d_percent"))
        return_20d = _num(row.get("return_20d_percent"))
        return_60d = _num(row.get("return_60d_percent"))
        recovery = _num(row.get("drawdown_recovery_20d_percent"))
        trend_or_repair_intact = bool(
            (return_20d is not None and return_20d > 0)
            or (return_60d is not None and return_60d > 0)
            or (
                return_5d is not None
                and return_5d > 0
                and recovery is not None
                and recovery >= 55.0
            )
        )
        if not trend_or_repair_intact:
            continue
        candidates.append((label, row, volatility))

    if not candidates:
        return []
    ordered_volatility = sorted(value for _label, _row, value in candidates)
    cutoff_index = int((len(ordered_volatility) - 1) * 0.65)
    volatility_cutoff = max(18.0, ordered_volatility[cutoff_index])

    ranked: list[tuple[float, str]] = []
    for label, row, volatility in candidates:
        if volatility < volatility_cutoff:
            continue
        return_5d = _num(row.get("return_5d_percent")) or 0.0
        return_20d = _num(row.get("return_20d_percent")) or 0.0
        return_60d = _num(row.get("return_60d_percent")) or 0.0
        recovery = _num(row.get("drawdown_recovery_20d_percent")) or 0.0
        priority = (
            volatility * 1.2
            + max(return_5d, 0.0) * 1.5
            + max(return_20d, 0.0) * 0.8
            + max(return_60d, 0.0) * 0.25
            + recovery * 0.08
        )
        ranked.append((priority, label))
    ranked.sort(reverse=True)
    return [label for _score, label in ranked[: max(0, int(max_labels))]]


def _quiet_setup_score(row: dict[str, Any]) -> float:
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    breadth = _num(row.get("advancing_ratio_percent"))
    if change_1d is None and change_5d is None:
        return -999.0
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    score = 0.0
    if -2.5 <= c1 <= 1.5:
        score += 45.0
    else:
        score -= abs(c1) * 4.0
    if -4.0 <= c5 <= 3.0:
        score += 35.0
    else:
        score -= abs(c5) * 2.0
    if breadth is not None:
        score += max(0.0, 20.0 - abs(breadth - 55.0) * 0.4)
    return score


def _is_pullback_candidate(row: dict[str, Any]) -> bool:
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    return bool(
        change_1d is not None
        and change_5d is not None
        and -3.0 <= change_1d <= 1.5
        and 2.0 <= change_5d <= 12.0
    )


def _pullback_score(row: dict[str, Any]) -> float:
    change_1d = _num(row.get("change_1d_percent")) or 0.0
    change_5d = _num(row.get("change_5d_percent")) or 0.0
    return change_5d * 3.0 - abs(change_1d) * 2.0


def _price_elasticity_recall_score(row: dict[str, Any]) -> float:
    """用 1/5 日价格振幅近似召回弹性；真实波动率在后续证据层计算。"""

    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    if change_1d is None and change_5d is None:
        return -999.0
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    daily_equivalent = abs(c5) / sqrt(5.0)
    trend_bonus = max(c5, 0.0) * 0.8
    repair_bonus = 4.0 if c1 < 0 < c5 else 0.0
    breakdown_penalty = max(-c5 - 5.0, 0.0) * 3.0
    return max(abs(c1), daily_equivalent) * 10.0 + trend_bonus + repair_bonus - breakdown_penalty


def _label(row: dict[str, Any]) -> str:
    return str(row.get("sector_label") or "").strip()


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


__all__ = [
    "DEFAULT_EVIDENCE_LABEL_LIMIT",
    "select_balanced_target_labels",
    "select_cached_high_elasticity_labels",
    "select_opportunity_evidence_labels",
    "select_snapshot_flow_inflection_labels",
]
