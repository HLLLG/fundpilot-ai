from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.eastmoney_trends_client import (
    DailyKlineBar,
    fetch_eastmoney_daily_kline_series,
)
from app.services.sector_canonical import CanonicalSector, get_canonical_sector, list_canonical_sector_labels
from app.services.sector_signal_rules import (
    SIGNAL_RULE_IDS,
    prediction_matches,
    predict_for_rule,
    rule_label,
)
from app.services.trade_calendar_cache import get_trade_date_set

FetchSeriesFn = Callable[[str, str | None], list[DailyKlineBar]]

_DEFAULT_RULES = ("reversal_down", "sector_weak", "intraday_pullback", "baseline_momentum")
_BACKTEST_RESPONSE_TTL_SECONDS = 86400
_BACKTEST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Bug B 修复：命中率基准不是固定 50%，而是「方向感知的自然发生率」+ 统计显著性。
MIN_TRIGGERS_FOR_SIGNIFICANCE = 30  # 触发次数太少时不下「有效」结论（可能是运气）
EDGE_MIN_PERCENT = 5.0  # 命中率需超过基准至少这么多个百分点才算真有超额
_FLAT_THRESHOLD = 0.3  # 与 sector_signal_rules.FLAT_THRESHOLD 对齐


def _direction_fractions(changes: list[float]) -> tuple[float, float, float]:
    """一组日涨跌的方向分布（up, down, flat 占比）。空集返回全 0。"""
    if not changes:
        return 0.0, 0.0, 0.0
    up = down = flat = 0
    for ch in changes:
        if abs(ch) < _FLAT_THRESHOLD:
            flat += 1
        elif ch > 0:
            up += 1
        else:
            down += 1
    total = len(changes)
    return up / total, down / total, flat / total


def _baseline_prob(prediction: str, fracs: tuple[float, float, float]) -> float:
    """某预测方向在随机时点上「自然命中」的概率（方向感知的 base rate）。"""
    up, down, flat = fracs
    if prediction == "up":
        return up
    if prediction == "down":
        return down
    if prediction == "down_or_flat":
        return down + flat
    return 0.0


def _finalize_bucket(bucket: dict[str, Any]) -> None:
    """就地补全命中率/基准/超额/显著性。

    显著 = 触发次数 >= 门槛 且 命中率超过基准 > EDGE_MIN_PERCENT。
    `beats_random` 保留为 `beats_baseline` 的向后兼容别名。
    """
    triggers = int(bucket.get("trigger_count") or 0)
    if triggers <= 0:
        bucket["hit_rate_percent"] = None
        bucket["baseline_rate_percent"] = None
        bucket["edge_percent"] = None
        bucket["significant"] = False
        bucket["beats_baseline"] = False
        bucket["beats_random"] = False
        return
    hit_rate = bucket["hit_count"] / triggers * 100
    baseline = float(bucket.get("expected_random_hits") or 0.0) / triggers * 100
    edge = hit_rate - baseline
    significant = triggers >= MIN_TRIGGERS_FOR_SIGNIFICANCE and edge > EDGE_MIN_PERCENT
    bucket["hit_rate_percent"] = round(hit_rate, 1)
    bucket["baseline_rate_percent"] = round(baseline, 1)
    bucket["edge_percent"] = round(edge, 1)
    bucket["significant"] = significant
    bucket["beats_baseline"] = significant
    bucket["beats_random"] = significant


def _backtest_cache_key(
    sector_labels: list[str] | None,
    lookback_days: int,
    rules: tuple[str, ...] | None,
) -> str:
    labels = sorted(sector_labels or [])
    active_rules = rules or _DEFAULT_RULES
    payload = json.dumps(
        {"labels": labels, "lookback_days": lookback_days, "rules": list(active_rules)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_sector_signal_backtest(
    sector_labels: list[str] | None = None,
    *,
    lookback_days: int = 120,
    rules: tuple[str, ...] | None = None,
    fetch_series: FetchSeriesFn | None = None,
) -> dict[str, Any]:
    if fetch_series is None:
        cache_key = _backtest_cache_key(sector_labels, lookback_days, rules)
        now = time.time()
        cached = _BACKTEST_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _BACKTEST_RESPONSE_TTL_SECONDS:
            return cached[1]
    else:
        cache_key = None

    if fetch_series is None:
        result = _build_sector_signal_backtest_impl(
            sector_labels,
            lookback_days=lookback_days,
            rules=rules,
            fetch_series=None,
        )
        if result.get("has_data"):
            _BACKTEST_CACHE[cache_key] = (time.time(), result)
        return result

    return _build_sector_signal_backtest_impl(
        sector_labels,
        lookback_days=lookback_days,
        rules=rules,
        fetch_series=fetch_series,
    )


def _build_sector_signal_backtest_impl(
    sector_labels: list[str] | None = None,
    *,
    lookback_days: int = 120,
    rules: tuple[str, ...] | None = None,
    fetch_series: FetchSeriesFn | None = None,
) -> dict[str, Any]:
    """对 canonical 板块日线做 T→T+1 信号回测（离线诊断，不写入日报）。"""
    labels = _resolve_sector_labels(sector_labels)
    active_rules = rules or _DEFAULT_RULES
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

        if fetch_series is None:
            series = _default_fetch_series_for_canon(canon)
        else:
            series = fetch_series(canon.eastmoney_secid, canon.source_code)
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


def _default_fetch_series_for_canon(canon: CanonicalSector) -> list[DailyKlineBar]:
    return fetch_canonical_daily_kline_series(canon, max_days=400, timeout=10.0)


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
            "expected_random_hits": 0.0,
            "hit_rate_percent": None,
        }

    # 基准用的「未来日」方向分布：回测里被当作 outcome 的那批 bar（series[2:]）。
    next_changes = [float(series[i + 1]["change_percent"]) for i in range(1, len(series) - 1)]
    fracs = _direction_fractions(next_changes)

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
            bucket["expected_random_hits"] += _baseline_prob(prediction, fracs)
            if prediction_matches(prediction, next_change):
                bucket["hit_count"] += 1
            else:
                bucket["miss_count"] += 1

    for bucket in stats.values():
        _finalize_bucket(bucket)
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
                "expected_random_hits": 0.0,
            },
        )
        target["trigger_count"] += int(bucket.get("trigger_count") or 0)
        target["hit_count"] += int(bucket.get("hit_count") or 0)
        target["miss_count"] += int(bucket.get("miss_count") or 0)
        target["expected_random_hits"] += float(bucket.get("expected_random_hits") or 0.0)


def _finalize_aggregate(
    aggregate: dict[str, dict[str, Any]],
    rule_ids: tuple[str, ...],
) -> dict[str, Any]:
    by_rule: dict[str, dict[str, Any]] = {}
    for rule_id in rule_ids:
        bucket = aggregate.get(rule_id)
        if not bucket:
            continue
        merged = {**bucket}
        _finalize_bucket(merged)
        by_rule[rule_id] = merged
    return {"by_rule": by_rule}


def _summary_lines(by_rule: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for rule_id in SIGNAL_RULE_IDS:
        bucket = by_rule.get(rule_id)
        if not bucket or not bucket.get("trigger_count"):
            continue
        baseline = bucket.get("baseline_rate_percent")
        edge = bucket.get("edge_percent")
        verdict = "显著跑赢基准 ✓" if bucket.get("significant") else "未显著跑赢基准"
        lines.append(
            f"{bucket['label']}：触发 {bucket['trigger_count']} 次，"
            f"T+1 命中率 {bucket['hit_rate_percent']}%"
            f"（自然基准 {baseline}%，超额 {edge}pp，{verdict}）。"
        )
    if not lines:
        lines.append("样本内无足够触发次数，请拉长 lookback 或换板块。")
    return lines
