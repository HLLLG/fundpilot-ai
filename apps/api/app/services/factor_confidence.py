"""因子 IC 置信映射（模块4 竖切3）。

把模块3A 离线 IC 回测产物（var/factor_ic/summary.json）映射成「每个因子可不可信」，
给模块2 的因子分挂可回测背书。纯映射 + best-effort 文件读，不改模块2/3A 算法。

设计文档：docs/superpowers/specs/2026-06-24-factor-confidence-llm-design.md。
"""
from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Any

from app.services.factor_ic_snapshot import DEFAULT_SUMMARY_PATH, load_factor_ic_context

SUMMARY_PATH = DEFAULT_SUMMARY_PATH
SUMMARY_TTL_SECONDS = 300

IC_STRONG = 0.03

# 模块2 因子键（fund_factors.FACTOR_KEYS）→ 3A IC 因子键；size 未回测 → None
FACTOR_IC_KEY: dict[str, str | None] = {
    "momentum": "momentum",
    "risk_adjusted": "risk_adjusted",
    "drawdown": "drawdown",
    "size": None,
}

_IC_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_IC_CONTEXT_CACHE_LOCK = Lock()
_IC_CONTEXT_CACHE_GENERATION = 0


def clear_ic_summary_cache() -> None:
    global _IC_CONTEXT_CACHE_GENERATION

    with _IC_CONTEXT_CACHE_LOCK:
        _IC_CONTEXT_CACHE_GENERATION += 1
        _IC_CONTEXT_CACHE.clear()


def load_ic_context() -> dict[str, Any]:
    """Cache the IC evidence state, status, and usable factor rows together."""
    while True:
        now = time.time()
        with _IC_CONTEXT_CACHE_LOCK:
            load_generation = _IC_CONTEXT_CACHE_GENERATION
            cached = _IC_CONTEXT_CACHE.get("default")
            if cached and now - cached[0] < SUMMARY_TTL_SECONDS:
                return cached[1]

        snapshot_context = load_factor_ic_context(local_path=Path(SUMMARY_PATH))
        state = snapshot_context.get("state", "unavailable")
        status = snapshot_context.get("status")
        if not isinstance(status, dict):
            status = {"available": False, "source": "unavailable"}

        factors: dict[str, dict] = {}
        summary = snapshot_context.get("summary")
        if state == "available" and isinstance(summary, dict):
            for stats in summary.get("factors") or []:
                if not isinstance(stats, dict):
                    continue
                key = stats.get("factor")
                if key:
                    factors[str(key)] = stats

        research_model = (
            summary.get("research_model")
            if isinstance(summary, dict) and isinstance(summary.get("research_model"), dict)
            else None
        )
        context = {
            "state": state,
            "status": status,
            "factors": factors,
            "research_model": research_model,
        }
        with _IC_CONTEXT_CACHE_LOCK:
            if _IC_CONTEXT_CACHE_GENERATION != load_generation:
                continue
            _IC_CONTEXT_CACHE["default"] = (now, context)
            return context


def load_ic_summary() -> dict[str, dict]:
    """Return only currently usable factor rows from the shared IC context cache."""
    return load_ic_context()["factors"]


def factor_confidence(
    ic_factors: dict[str, dict],
    factor_key: str,
    *,
    missing_basis: str = "无回测数据",
) -> dict:
    """单因子置信：{level, basis}。"""
    if factor_key == "size":
        return {"level": "不足", "basis": "规模因子未回测，仅供参考"}

    ic_key = FACTOR_IC_KEY.get(factor_key)
    if ic_key is None:
        return {"level": "不足", "basis": missing_basis}

    stats = (ic_factors or {}).get(ic_key)
    if not stats:
        return {"level": "不足", "basis": missing_basis}

    mean_ic = stats.get("mean_ic")
    significant = bool(stats.get("significant"))
    if mean_ic is None:
        return {"level": "不足", "basis": missing_basis}

    if not significant:
        return {"level": "低", "basis": f"回测不显著（IC {mean_ic:+.3f}），仅描述性"}
    if mean_ic < 0:
        return {"level": "低", "basis": f"回测显著反向（IC {mean_ic:+.3f}），慎用"}
    if mean_ic >= IC_STRONG:
        return {"level": "高", "basis": f"回测显著正向（IC {mean_ic:+.3f}），置信高"}
    return {"level": "中", "basis": f"回测显著但偏弱（IC {mean_ic:+.3f}），置信中"}


def factor_reliability(
    ic_factors: dict[str, dict] | None = None,
    *,
    missing_basis: str = "无回测数据",
    research_model: dict | None = None,
    segment: str | None = None,
) -> dict[str, dict]:
    """模块2 四因子各算一次置信，返回 {factor_key: {level, basis}}。"""
    if research_model and segment:
        return {
            key: _research_factor_confidence(research_model, segment, key)
            for key in FACTOR_IC_KEY
        }
    factors = ic_factors if ic_factors is not None else load_ic_summary()
    return {
        key: factor_confidence(factors, key, missing_basis=missing_basis)
        for key in FACTOR_IC_KEY
    }


def _research_factor_confidence(
    research_model: dict,
    segment: str,
    factor_key: str,
) -> dict:
    if factor_key == "size":
        return {"level": "不足", "basis": "规模因子无历史规模序列，未回测"}
    horizon = str(research_model.get("primary_horizon") or 20)
    segment_row = (research_model.get("segments") or {}).get(segment) or {}
    horizon_row = (segment_row.get("horizons") or {}).get(horizon) or {}
    stats = next(
        (
            row
            for row in horizon_row.get("factors") or []
            if row.get("factor") == factor_key
        ),
        None,
    )
    if not stats or not (horizon_row.get("qualified") or {}).get(factor_key):
        return {"level": "不足", "basis": "同类基金样本或样本外时期不足"}
    mean_ic = stats.get("mean_ic")
    oos_ic = stats.get("oos_mean_ic")
    if mean_ic is None or oos_ic is None:
        return {"level": "不足", "basis": "同类 IC 统计不完整"}
    label = str(segment_row.get("label") or segment)
    if mean_ic < 0 or oos_ic < 0:
        return {
            "level": "低",
            "basis": f"{label}未来{horizon}日呈反向/均值回归（IC {mean_ic:+.3f}，样本外 {oos_ic:+.3f}）",
        }
    stable = bool(stats.get("direction_stable"))
    ci_low = stats.get("ci_low")
    if stable and ci_low is not None and ci_low > 0:
        # 当前仍是 current-survivors cohort；在积累 point-in-time 历史前不授予“高”。
        return {
            "level": "中",
            "basis": f"{label}未来{horizon}日同类 IC 正向且样本外稳定（{mean_ic:+.3f}），仍受幸存者样本限制",
        }
    return {
        "level": "低",
        "basis": f"{label}未来{horizon}日 IC {mean_ic:+.3f}，样本外/区间稳定性不足",
    }
