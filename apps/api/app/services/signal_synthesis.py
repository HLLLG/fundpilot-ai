"""信号合成（证据卡，模块4 竖切5）。

把每只持仓的三路量化置信（因子IC / 板块信号 / 组合风险样本）聚合成一个综合置信
+ 证据摘要。**不决策买卖动作**（动作仍由 LLM / tactical 决定），只做置信聚合。

纯函数，注入已组装好的 dict（factor_scores / signal_entry / risk_metrics）。
设计文档：docs/superpowers/specs/2026-06-24-signal-synthesis-design.md。
"""
from __future__ import annotations

_LEVEL_SCORE = {"高": 3, "中": 2, "低": 1}  # 不足 = 无数据，不计入
_OVERVIEW_LEVELS = ("高", "中", "低", "不足")
_FACTOR_KEYS = ("momentum", "risk_adjusted", "drawdown")  # size 未回测，不参与
_FACTOR_LABEL = {"momentum": "动量", "risk_adjusted": "风险调整", "drawdown": "回撤控制"}


def synthesize_confidence(component_levels: list[str]) -> dict:
    """把若干分量置信等级聚合成综合置信 {level, score}。

    只计入有数据的分量（高/中/低）；全为不足/空 → 综合不足。
    """
    scores = [_LEVEL_SCORE[lv] for lv in component_levels if lv in _LEVEL_SCORE]
    if not scores:
        return {"level": "不足", "score": 0}
    avg = sum(scores) / len(scores)
    if avg >= 2.5:
        level = "高"
    elif avg >= 1.5:
        level = "中"
    else:
        level = "低"
    return {"level": level, "score": round(avg, 2)}


def _factor_component(fund_code: str, factor_scores: dict | None) -> dict | None:
    if not factor_scores or not factor_scores.get("available"):
        return None
    reliability = factor_scores.get("factor_reliability") or {}
    row = None
    for item in factor_scores.get("holdings") or []:
        if item.get("fund_code") == fund_code:
            row = item
            break
    if not row:
        return None
    percentiles = row.get("factor_percentiles") or {}

    # 候选：IC 置信非「不足」的因子，按百分位降序取主因子
    candidates = []
    for key in _FACTOR_KEYS:
        rel = reliability.get(key)
        pct = percentiles.get(key)
        if not rel or rel.get("level") in (None, "不足"):
            continue
        if pct is None:
            continue
        candidates.append((pct, key, rel))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    pct, key, rel = candidates[0]
    label = _FACTOR_LABEL.get(key, key)
    return {
        "source": "factor",
        "level": rel["level"],
        "basis": f"主因子 {label}(百分位{pct})·IC{rel.get('basis', '')}",
    }


def _signal_component(signal_entry: dict | None) -> dict | None:
    if not signal_entry:
        return None
    best = None
    for rule in (signal_entry.get("by_rule") or {}).values():
        conf = rule.get("confidence")
        if not conf:
            continue
        if best is None or (conf.get("score") or 0) > (best[0].get("score") or 0):
            best = (conf, rule)
    if best is None:
        return None
    conf, rule = best
    label = rule.get("label") or "板块信号"
    return {
        "source": "signal",
        "level": conf.get("level", "不足"),
        "basis": f"板块信号 {label}·{conf.get('basis', '')}",
    }


def _risk_component(risk_metrics: dict | None) -> dict | None:
    if not risk_metrics or not risk_metrics.get("available"):
        return None
    conf = risk_metrics.get("confidence")
    if not conf:
        return None
    return {
        "source": "risk",
        "level": conf.get("level", "不足"),
        "basis": f"组合风险{conf.get('basis', '')}",
    }


def build_holding_evidence(
    *,
    fund_code: str,
    signal_entry: dict | None,
    factor_scores: dict | None,
    risk_metrics: dict | None,
) -> dict | None:
    """聚合单只持仓三路证据 → {composite, components, summary}；全无 → None。"""
    components = [
        comp
        for comp in (
            _factor_component(fund_code, factor_scores),
            _signal_component(signal_entry),
            _risk_component(risk_metrics),
        )
        if comp is not None
    ]
    if not components:
        return None
    composite = synthesize_confidence([c["level"] for c in components])
    return {
        "composite": composite,
        "components": components,
        "summary": "；".join(c["basis"] for c in components),
    }


def build_evidence_overview(rows: list[dict]) -> dict:
    """把每只持仓的 evidence 聚合成组合级背书分布（市值加权）。

    rows: build_analysis_facts 的 per_fund 行（含 holding_amount，可含 evidence）。
    分母为全部持仓市值（含未覆盖），各级之和=已覆盖市值占比。
    """
    total_amount = sum(float(r.get("holding_amount") or 0) for r in rows)
    covered = [r for r in rows if r.get("evidence")]
    if not covered or total_amount <= 0:
        return {"available": False}

    count_by_level = {lv: 0 for lv in _OVERVIEW_LEVELS}
    weight_by_level = {lv: 0.0 for lv in _OVERVIEW_LEVELS}
    for r in covered:
        lv = (r["evidence"].get("composite") or {}).get("level")
        if lv not in count_by_level:
            continue
        count_by_level[lv] += 1
        weight_by_level[lv] += float(r.get("holding_amount") or 0) / total_amount * 100

    weight_by_level = {k: round(v, 1) for k, v in weight_by_level.items()}
    backed = round(weight_by_level["高"] + weight_by_level["中"], 1)
    return {
        "available": True,
        "total_holdings": len(rows),
        "covered_holdings": len(covered),
        "count_by_level": count_by_level,
        "weight_by_level": weight_by_level,
        "backed_weight_percent": backed,
        "summary": (
            f"组合 {backed:.0f}% 市值有中/高量化背书，"
            f"{len(covered)}/{len(rows)} 只持仓有证据覆盖。"
        ),
    }
