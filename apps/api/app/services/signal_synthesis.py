"""量化证据合成。

证据的“可靠”与“方向正确”是两件不同的事。本模块保留旧版 ``level`` /
``composite`` 字段，同时为每路证据显式输出 reliability、direction、effect_size、
coverage、freshness。组合风险只作为 risk_guard，永远不计入收益支持。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.services.fund_type_factors import TYPE_FACTOR_LABELS

_LEVEL_SCORE = {"高": 3, "中": 2, "低": 1}
_OVERVIEW_LEVELS = ("高", "中", "低", "不足")
_FACTOR_KEYS = ("momentum", "risk_adjusted", "drawdown")
_FACTOR_LABEL = {"momentum": "动量", "risk_adjusted": "风险调整", "drawdown": "回撤控制"}
def synthesize_confidence(component_levels: list[str]) -> dict:
    """兼容旧调用：聚合可靠性等级，返回 ``{level, score}``。"""
    scores = [_LEVEL_SCORE[lv] for lv in component_levels if lv in _LEVEL_SCORE]
    if not scores:
        return {"level": "不足", "score": 0}
    avg = sum(scores) / len(scores)
    level = "高" if avg >= 2.5 else ("中" if avg >= 1.5 else "低")
    return {"level": level, "score": round(avg, 2)}


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _level_from_percent(percent: float | None) -> str:
    if percent is None:
        return "不足"
    if percent >= 80:
        return "高"
    if percent >= 50:
        return "中"
    return "低"


def _effect(score: float | None, basis: str) -> dict[str, Any]:
    if score is None:
        return {"level": "不足", "score": None, "basis": basis}
    bounded = round(max(0.0, min(100.0, score)), 1)
    level = "高" if bounded >= 60 else ("中" if bounded >= 25 else "低")
    return {"level": level, "score": bounded, "basis": basis}


def _coverage(percent: float | None, basis: str) -> dict[str, Any]:
    bounded = None if percent is None else round(max(0.0, min(100.0, percent)), 1)
    return {"level": _level_from_percent(bounded), "percent": bounded, "basis": basis}


def _freshness(
    *,
    status: object = None,
    as_of: object = None,
    basis: str = "未提供证据时点",
) -> dict[str, Any]:
    normalized = str(status or "unknown").strip().lower()
    if normalized in {"available", "fresh", "current"}:
        normalized = "fresh"
    elif normalized in {"stale", "expired"}:
        normalized = "stale"
    elif normalized in {"unavailable", "missing"}:
        normalized = "unavailable"
    else:
        normalized = "unknown"
    return {"status": normalized, "as_of": str(as_of) if as_of else None, "basis": basis}


def _normalize_direction(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"positive", "up", "bullish", "inflow", "看多", "向上", "正向"}:
        return "positive"
    if text in {
        "negative",
        "down",
        "down_or_flat",
        "bearish",
        "outflow",
        "看空",
        "向下",
        "负向",
    }:
        return "negative"
    if text in {"neutral", "flat", "中性"}:
        return "neutral"
    return "unknown"


def _factor_component(fund_code: str, factor_scores: dict | None) -> dict | None:
    if not factor_scores or not factor_scores.get("available"):
        return None
    ic_status = factor_scores.get("ic_status") or {}
    state = str(ic_status.get("state") or "").strip().lower()
    if not state:
        state = "available" if ic_status.get("available") else "unavailable"
    if (
        state != "available"
        or ic_status.get("stale") is True
        or ic_status.get("available", True) is False
    ):
        return None
    global_reliability = factor_scores.get("factor_reliability") or {}
    row = next(
        (
            item
            for item in factor_scores.get("holdings") or []
            if item.get("fund_code") == fund_code
        ),
        None,
    )
    if not row or row.get("applicable") is False:
        return None
    reliability = row.get("factor_reliability") or global_reliability
    percentiles = row.get("factor_percentiles") or {}
    candidates: list[tuple[int, float, str, dict[str, Any], str]] = []
    for key in _FACTOR_KEYS:
        rel = reliability.get(key)
        pct = _number(percentiles.get(key))
        if not isinstance(rel, dict) or rel.get("level") in (None, "不足") or pct is None:
            continue
        candidates.append(
            (
                _LEVEL_SCORE.get(str(rel.get("level")), 0),
                abs(pct - 50),
                key,
                rel,
                "common",
            )
        )
    if row.get("typed_factor_applicable") is True:
        typed_reliability = row.get("typed_factor_reliability") or {}
        typed_percentiles = row.get("typed_factor_percentiles") or {}
        for key, rel in typed_reliability.items():
            pct = _number(typed_percentiles.get(key))
            if not isinstance(rel, dict) or pct is None:
                continue
            if (
                rel.get("qualified") is not True
                or rel.get("level") in (None, "不足")
                or rel.get("orientation") != "higher_is_better"
                or ((rel.get("economic_significance") or {}).get("qualified"))
                is not True
            ):
                continue
            candidates.append(
                (
                    _LEVEL_SCORE.get(str(rel.get("level")), 0),
                    abs(pct - 50),
                    str(key),
                    rel,
                    "fund_type_specific",
                )
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, key, rel, factor_family = candidates[0]
    pct_source = (
        row.get("typed_factor_percentiles") or {}
        if factor_family == "fund_type_specific"
        else percentiles
    )
    pct = float(pct_source[key])
    basis_text = str(rel.get("basis") or "")
    direction = "neutral" if 45 <= pct <= 55 else ("positive" if pct > 55 else "negative")
    if "反向" in basis_text or "均值回归" in basis_text:
        direction = {"positive": "negative", "negative": "positive"}.get(direction, direction)
    completeness = _number(
        row.get("typed_feature_completeness")
        if factor_family == "fund_type_specific"
        else row.get("feature_completeness")
    )
    coverage_percent = (
        completeness * 100
        if completeness is not None and completeness <= 1
        else completeness
    )
    label = (
        TYPE_FACTOR_LABELS.get(key, key)
        if factor_family == "fund_type_specific"
        else _FACTOR_LABEL.get(key, key)
    )
    factor_prefix = "类型因子" if factor_family == "fund_type_specific" else "主因子"
    basis = f"{factor_prefix} {label}(百分位{pct:g})·IC{basis_text}"
    return {
        "source": "factor",
        "factor_family": factor_family,
        "factor_key": key,
        "role": "return_signal",
        "level": str(rel.get("level") or "不足"),
        "reliability": {
            "level": str(rel.get("level") or "不足"),
            "score": _LEVEL_SCORE.get(str(rel.get("level")), 0),
            "basis": basis_text or "未提供 IC 可靠性依据",
        },
        "direction": direction,
        "effect_size": _effect(abs(pct - 50) * 2, f"因子百分位偏离中位 {abs(pct - 50):.1f} 点"),
        "coverage": _coverage(coverage_percent, "基金特征完整度"),
        "freshness": _freshness(
            status=state,
            as_of=ic_status.get("run_date") or ic_status.get("generated_at"),
            basis="因子 IC 快照状态",
        ),
        "basis": basis,
    }


def _rule_direction(rule: dict[str, Any], signal_entry: dict[str, Any]) -> str:
    """Read only an explicitly active/current prediction.

    ``signal_entry.by_rule`` is a historical backtest table.  A rule's semantic
    direction (for example ``sector_weak``) does not prove that the rule fired
    today, so the rule id itself must never be converted into a live direction.
    """
    current = signal_entry.get("current_signal")
    if isinstance(current, dict) and current.get("active", True) is not False:
        for key in ("direction", "prediction", "signal_direction"):
            direction = _normalize_direction(current.get(key))
            if direction != "unknown":
                return direction
    for key in ("current_direction", "current_prediction"):
        direction = _normalize_direction(signal_entry.get(key))
        if direction != "unknown":
            return direction
    if rule.get("active") is True or rule.get("triggered_now") is True:
        for key in ("direction", "prediction", "expected_direction"):
            direction = _normalize_direction(rule.get(key))
            if direction != "unknown":
                return direction
    return "unknown"


def _signal_component(signal_entry: dict | None) -> dict | None:
    if not signal_entry:
        return None
    best: tuple[dict[str, Any], dict[str, Any]] | None = None
    for rule_id, rule in (signal_entry.get("by_rule") or {}).items():
        if not isinstance(rule, dict):
            continue
        conf = rule.get("confidence")
        if not isinstance(conf, dict):
            continue
        if best is None or (_number(conf.get("score")) or 0) > (_number(best[0].get("score")) or 0):
            best = (conf, rule)
    if best is None:
        return None
    current = signal_entry.get("current_signal")
    if isinstance(current, dict):
        current_rule = (signal_entry.get("by_rule") or {}).get(str(current.get("rule_id") or ""))
        current_conf = current_rule.get("confidence") if isinstance(current_rule, dict) else None
        if isinstance(current_rule, dict) and isinstance(current_conf, dict):
            best = (current_conf, current_rule)
    conf, rule = best
    triggers = int(_number(rule.get("trigger_count")) or 0)
    edge = _number(rule.get("edge_percent"))
    label = rule.get("label") or "板块信号"
    direction = _rule_direction(rule, signal_entry)
    is_current_signal = direction != "unknown"
    basis = (
        f"当前板块信号 {label}·{conf.get('basis', '')}"
        if is_current_signal
        else f"板块规则历史回测 {label}·{conf.get('basis', '')}（未提供当日触发方向）"
    )
    return {
        "source": "signal",
        "role": "return_signal" if is_current_signal else "historical_validation",
        "level": str(conf.get("level") or "不足"),
        "reliability": {
            "level": str(conf.get("level") or "不足"),
            "score": _number(conf.get("score")),
            "basis": str(conf.get("basis") or "未提供回测可靠性依据"),
        },
        "direction": direction,
        "effect_size": _effect(abs(edge) * 5 if edge is not None else None, "相对自然基线的 edge"),
        "coverage": _coverage(
            min(triggers / 50 * 100, 100) if triggers else 0,
            f"历史触发 {triggers} 次",
        ),
        "freshness": _freshness(
            status=signal_entry.get("freshness_status"),
            as_of=signal_entry.get("as_of") or signal_entry.get("trade_date"),
            basis="板块回测未提供生成时点" if not signal_entry.get("as_of") else "板块回测时点",
        ),
        "basis": basis,
    }


def _risk_component(risk_metrics: dict | None) -> dict | None:
    if not risk_metrics or not risk_metrics.get("available"):
        return None
    conf = risk_metrics.get("confidence")
    if not isinstance(conf, dict):
        return None
    sample_days = int(_number(risk_metrics.get("sample_days")) or 0)
    max_drawdown = _number(risk_metrics.get("max_drawdown_percent"))
    hhi = _number(risk_metrics.get("hhi"))
    severity = max(
        abs(min(max_drawdown or 0, 0)) * 3,
        (hhi or 0) * 100,
    )
    basis = f"组合风险{conf.get('basis', '')}"
    return {
        "source": "risk",
        "role": "risk_guard",
        "level": str(conf.get("level") or "不足"),
        "reliability": {
            "level": str(conf.get("level") or "不足"),
            "score": _LEVEL_SCORE.get(str(conf.get("level")), 0),
            "basis": str(conf.get("basis") or "未提供风险样本依据"),
        },
        "direction": "risk",
        "effect_size": _effect(severity, "最大回撤/集中度风险强度"),
        "coverage": _coverage(min(sample_days / 120 * 100, 100), f"组合历史 {sample_days} 个交易日"),
        "freshness": _freshness(
            status=risk_metrics.get("freshness_status"),
            as_of=risk_metrics.get("as_of") or risk_metrics.get("trade_date"),
            basis="风险快照未提供生成时点" if not risk_metrics.get("as_of") else "风险快照时点",
        ),
        "basis": basis,
    }


def _aggregate_percent(components: Iterable[dict[str, Any]], key: str, field: str) -> float | None:
    values = [
        value
        for component in components
        if (value := _number((component.get(key) or {}).get(field))) is not None
    ]
    return round(sum(values) / len(values), 1) if values else None


def _composite(components: list[dict[str, Any]]) -> dict[str, Any]:
    declared_returns = [
        item for item in components if item.get("role") == "return_signal"
    ]
    returns = [
        item
        for item in declared_returns
        if (item.get("freshness") or {}).get("status") == "fresh"
    ]
    positive = [item for item in returns if item.get("direction") == "positive"]
    negative = [item for item in returns if item.get("direction") == "negative"]
    neutral = [item for item in returns if item.get("direction") in {"neutral", "unknown"}]
    reliability = synthesize_confidence(
        [str((item.get("reliability") or {}).get("level") or "不足") for item in returns]
    )
    positive_support = synthesize_confidence([str(item.get("level") or "不足") for item in positive])
    positive_max = max(
        (_LEVEL_SCORE.get(str(item.get("level")), 0) for item in positive),
        default=0,
    )
    negative_max = max(
        (_LEVEL_SCORE.get(str(item.get("level")), 0) for item in negative),
        default=0,
    )
    if positive and negative and negative_max >= positive_max:
        positive_support = {"level": "低", "score": 1.0}
    elif not positive:
        positive_support = {"level": "不足", "score": 0}
    if positive and not negative:
        direction = "positive"
    elif negative and not positive:
        direction = "negative"
    elif positive and negative:
        direction = "mixed"
    elif neutral:
        direction = "neutral"
    else:
        direction = "unknown"
    coverage_percent = _aggregate_percent(returns, "coverage", "percent")
    effect_score = _aggregate_percent(returns, "effect_size", "score")
    freshness_states = [
        str((item.get("freshness") or {}).get("status") or "unknown")
        for item in returns
    ]
    freshness = (
        "stale"
        if "stale" in freshness_states
        else "unavailable"
        if "unavailable" in freshness_states
        else "fresh"
        if freshness_states and all(value == "fresh" for value in freshness_states)
        else "unknown"
    )
    return {
        # 兼容旧消费方：level/score 现在严格表示“正向收益支持”，不再是无方向平均。
        **positive_support,
        "reliability": reliability,
        "direction": direction,
        "effect_size": _effect(effect_score, "参与收益证据的平均效应强度"),
        "coverage": _coverage(coverage_percent, "参与收益证据的平均覆盖率"),
        "freshness": {"status": freshness, "basis": "参与收益证据的最弱时效"},
        "positive_component_count": len(positive),
        "negative_component_count": len(negative),
        "neutral_component_count": len(neutral),
        "risk_guard_count": sum(item.get("role") == "risk_guard" for item in components),
        "stale_or_unknown_return_count": len(declared_returns) - len(returns),
    }


def build_holding_evidence(
    *,
    fund_code: str,
    signal_entry: dict | None,
    factor_scores: dict | None,
    risk_metrics: dict | None,
) -> dict | None:
    components = [
        component
        for component in (
            _factor_component(fund_code, factor_scores),
            _signal_component(signal_entry),
            _risk_component(risk_metrics),
        )
        if component is not None
    ]
    if not components:
        return None
    composite = _composite(components)
    return {
        "schema_version": "quant_evidence.v2",
        "composite": composite,
        "components": components,
        "risk_guards": [item for item in components if item.get("role") == "risk_guard"],
        "summary": "；".join(str(item["basis"]) for item in components),
    }


def build_evidence_overview(rows: list[dict]) -> dict:
    """按市值汇总正向背书；负向证据和风险守卫单独计数，绝不冒充背书。"""
    total_amount = sum(float(row.get("holding_amount") or 0) for row in rows)
    covered = [row for row in rows if row.get("evidence")]
    if not covered or total_amount <= 0:
        return {"available": False}
    count_by_level = {level: 0 for level in _OVERVIEW_LEVELS}
    weight_by_level = {level: 0.0 for level in _OVERVIEW_LEVELS}
    direction_counts = {key: 0 for key in ("positive", "negative", "mixed", "neutral", "unknown")}
    risk_guard_weight = 0.0
    for row in covered:
        evidence = row["evidence"]
        composite = evidence.get("composite") or {}
        level = composite.get("level")
        if level in count_by_level:
            count_by_level[level] += 1
            weight_by_level[level] += float(row.get("holding_amount") or 0) / total_amount * 100
        direction = str(composite.get("direction") or "unknown")
        direction_counts[direction if direction in direction_counts else "unknown"] += 1
        if composite.get("risk_guard_count"):
            risk_guard_weight += float(row.get("holding_amount") or 0) / total_amount * 100
    weight_by_level = {key: round(value, 1) for key, value in weight_by_level.items()}
    backed = round(weight_by_level["高"] + weight_by_level["中"], 1)
    return {
        "available": True,
        "schema_version": "quant_evidence_overview.v2",
        "total_holdings": len(rows),
        "covered_holdings": len(covered),
        "count_by_level": count_by_level,
        "weight_by_level": weight_by_level,
        "backed_weight_percent": backed,
        "direction_counts": direction_counts,
        "risk_guard_weight_percent": round(risk_guard_weight, 1),
        "summary": (
            f"组合 {backed:.0f}% 市值有中/高正向量化支持，"
            f"{len(covered)}/{len(rows)} 只持仓有证据覆盖；风险证据仅作守卫。"
        ),
    }
