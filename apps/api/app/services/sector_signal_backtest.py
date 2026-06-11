from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.eastmoney_trends_client import (
    DailyKlineBar,
    fetch_eastmoney_daily_kline_series,
)
from app.services.sector_canonical import get_canonical_sector, list_canonical_sector_labels
from app.services.sector_signal_rules import (
    SIGNAL_RULE_IDS,
    prediction_matches,
    predict_for_rule,
    rule_label,
)
from app.services.trade_calendar_cache import get_trade_date_set

FetchSeriesFn = Callable[[str, str | None], list[DailyKlineBar]]

_DEFAULT_RULES = ("reversal_down", "sector_weak", "intraday_pullback", "baseline_momentum")


def build_sector_signal_backtest(
    sector_labels: list[str] | None = None,
    *,
    lookback_days: int = 120,
    rules: tuple[str, ...] | None = None,
    fetch_series: FetchSeriesFn | None = None,
) -> dict[str, Any]:
    """对 canonical 板块日线做 T→T+1 信号回测（离线诊断，不写入日报）。"""
    labels = _resolve_sector_labels(sector_labels)
    active_rules = rules or _DEFAULT_RULES
    loader = fetch_series or _default_fetch_series
    window = max(30, min(lookback_days, 400))

    if not labels:
        return {
            "has_data": False,
            "message": "未指定有效板块；请传入 canonical 板块名（如 半导体、商业航天）。",
            "lookback_days": window,
            "sectors": [],
            "by_rule": {},
            "summary_lines": [],
        }

    trade_dates = get_trade_date_set()
    sector_results: list[dict[str, Any]] = []
    aggregate: dict[str, dict[str, Any]] = {}

    for label in labels:
        canon = get_canonical_sector(label)
        if canon is None:
            sector_results.append(
                {
                    "sector_label": label,
                    "resolved": False,
                    "message": "无 canonical 映射，已跳过。",
                }
            )
            continue

        series = loader(canon.eastmoney_secid, canon.source_code)
        filtered = _filter_trading_days(series, trade_dates, window)
        if len(filtered) < 3:
            sector_results.append(
                {
                    "sector_label": label,
                    "resolved": True,
                    "secid": canon.eastmoney_secid,
                    "sample_days": len(filtered),
                    "message": "有效交易日不足，无法回测。",
                    "by_rule": {},
                }
            )
            continue

        by_rule = _evaluate_rules(filtered, active_rules)
        sector_results.append(
            {
                "sector_label": label,
                "resolved": True,
                "secid": canon.eastmoney_secid,
                "sample_days": len(filtered),
                "by_rule": by_rule,
            }
        )
        _merge_rule_stats(aggregate, by_rule)

    overall = _finalize_aggregate(aggregate, active_rules)
    return {
        "has_data": bool(overall.get("by_rule")),
        "lookback_days": window,
        "sector_count": len([item for item in sector_results if item.get("resolved")]),
        "sectors": sector_results,
        "by_rule": overall.get("by_rule", {}),
        "summary_lines": _summary_lines(overall.get("by_rule", {})),
    }


def _resolve_sector_labels(sector_labels: list[str] | None) -> list[str]:
    if sector_labels:
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in sector_labels:
            label = (raw or "").strip()
            if not label or label in seen:
                continue
            if get_canonical_sector(label) is None:
                continue
            seen.add(label)
            resolved.append(label)
        return resolved
    return list_canonical_sector_labels()


def _default_fetch_series(secid: str, source_code: str | None) -> list[DailyKlineBar]:
    return fetch_eastmoney_daily_kline_series(
        secid,
        source_code=source_code,
        max_days=400,
        timeout=10.0,
        max_retries=1,
    )


def _filter_trading_days(
    series: list[DailyKlineBar],
    trade_dates: frozenset[str] | None,
    window: int,
) -> list[DailyKlineBar]:
    if trade_dates:
        filtered = [
            bar
            for bar in series
            if str(bar.get("date", ""))[:10] in trade_dates
        ]
    else:
        filtered = list(series)
    if len(filtered) > window:
        filtered = filtered[-window:]
    return filtered


def _evaluate_rules(
    series: list[DailyKlineBar],
    rule_ids: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for rule_id in rule_ids:
        stats[rule_id] = {
            "rule_id": rule_id,
            "label": rule_label(rule_id),
            "trigger_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "hit_rate_percent": None,
        }

    for index in range(1, len(series) - 1):
        prev_bar = series[index - 1]
        cur_bar = series[index]
        next_bar = series[index + 1]

        prev_change = float(prev_bar["change_percent"])
        cur_change = float(cur_bar["change_percent"])
        next_change = float(next_bar["change_percent"])
        high_change = cur_bar.get("high_change_percent")
        high_value = float(high_change) if high_change is not None else None

        for rule_id in rule_ids:
            prediction = predict_for_rule(
                rule_id,
                prev_change=prev_change,
                cur_change=cur_change,
                high_change=high_value,
            )
            if prediction is None:
                continue
            bucket = stats[rule_id]
            bucket["trigger_count"] += 1
            if prediction_matches(prediction, next_change):
                bucket["hit_count"] += 1
            else:
                bucket["miss_count"] += 1

    for bucket in stats.values():
        triggers = int(bucket["trigger_count"])
        if triggers:
            bucket["hit_rate_percent"] = round(bucket["hit_count"] / triggers * 100, 1)
    return stats


def _merge_rule_stats(
    aggregate: dict[str, dict[str, Any]],
    by_rule: dict[str, dict[str, Any]],
) -> None:
    for rule_id, bucket in by_rule.items():
        target = aggregate.setdefault(
            rule_id,
            {
                "rule_id": rule_id,
                "label": bucket.get("label") or rule_label(rule_id),
                "trigger_count": 0,
                "hit_count": 0,
                "miss_count": 0,
            },
        )
        target["trigger_count"] += int(bucket.get("trigger_count") or 0)
        target["hit_count"] += int(bucket.get("hit_count") or 0)
        target["miss_count"] += int(bucket.get("miss_count") or 0)


def _finalize_aggregate(
    aggregate: dict[str, dict[str, Any]],
    rule_ids: tuple[str, ...],
) -> dict[str, Any]:
    by_rule: dict[str, dict[str, Any]] = {}
    for rule_id in rule_ids:
        bucket = aggregate.get(rule_id)
        if not bucket:
            continue
        triggers = int(bucket["trigger_count"])
        hit_rate = round(bucket["hit_count"] / triggers * 100, 1) if triggers else None
        by_rule[rule_id] = {
            **bucket,
            "hit_rate_percent": hit_rate,
            "beats_random": hit_rate is not None and hit_rate > 50.0,
        }
    return {"by_rule": by_rule}


def _summary_lines(by_rule: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for rule_id in SIGNAL_RULE_IDS:
        bucket = by_rule.get(rule_id)
        if not bucket or not bucket.get("trigger_count"):
            continue
        lines.append(
            f"{bucket['label']}：触发 {bucket['trigger_count']} 次，"
            f"T+1 命中率 {bucket['hit_rate_percent']}%"
            f"（{'高于' if bucket.get('beats_random') else '不高于'}随机基准 50%）。"
        )
    if not lines:
        lines.append("样本内无足够触发次数，请拉长 lookback 或换板块。")
    return lines
