"""因子 IC 置信映射（模块4 竖切3）。

把模块3A 离线 IC 回测产物（var/factor_ic/summary.json）映射成「每个因子可不可信」，
给模块2 的因子分挂可回测背书。纯映射 + best-effort 文件读，不改模块2/3A 算法。

设计文档：docs/superpowers/specs/2026-06-24-factor-confidence-llm-design.md。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_PATH = API_ROOT / "var" / "factor_ic" / "summary.json"
SUMMARY_TTL_SECONDS = 1800

IC_STRONG = 0.03

# 模块2 因子键（fund_factors.FACTOR_KEYS）→ 3A IC 因子键；size 未回测 → None
FACTOR_IC_KEY: dict[str, str | None] = {
    "momentum": "momentum",
    "risk_adjusted": "risk_adjusted",
    "drawdown": "drawdown",
    "size": None,
}

_SUMMARY_CACHE: dict[str, tuple[float, dict[str, dict]]] = {}


def load_ic_summary() -> dict[str, dict]:
    """best-effort 读 3A summary.json 的 factors → {factor_key: stats}；缺失/损坏→{}。"""
    now = time.time()
    cached = _SUMMARY_CACHE.get("default")
    if cached and now - cached[0] < SUMMARY_TTL_SECONDS:
        return cached[1]

    result: dict[str, dict] = {}
    try:
        raw = json.loads(Path(SUMMARY_PATH).read_text(encoding="utf-8"))
        for stats in raw.get("factors") or []:
            key = stats.get("factor")
            if key:
                result[key] = stats
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        result = {}

    _SUMMARY_CACHE["default"] = (now, result)
    return result


def factor_confidence(ic_factors: dict[str, dict], factor_key: str) -> dict:
    """单因子置信：{level, basis}。"""
    ic_key = FACTOR_IC_KEY.get(factor_key, None)
    if ic_key is None:
        return {"level": "不足", "basis": "规模因子未回测，仅供参考"}

    stats = (ic_factors or {}).get(ic_key)
    if not stats:
        return {"level": "不足", "basis": "无回测数据"}

    mean_ic = stats.get("mean_ic")
    significant = bool(stats.get("significant"))
    if mean_ic is None:
        return {"level": "不足", "basis": "无回测数据"}

    if not significant:
        return {"level": "低", "basis": f"回测不显著（IC {mean_ic:+.3f}），仅描述性"}
    if mean_ic < 0:
        return {"level": "低", "basis": f"回测显著反向（IC {mean_ic:+.3f}），慎用"}
    if mean_ic >= IC_STRONG:
        return {"level": "高", "basis": f"回测显著正向（IC {mean_ic:+.3f}），置信高"}
    return {"level": "中", "basis": f"回测显著但偏弱（IC {mean_ic:+.3f}），置信中"}


def factor_reliability(ic_factors: dict[str, dict] | None = None) -> dict[str, dict]:
    """模块2 四因子各算一次置信，返回 {factor_key: {level, basis}}。"""
    factors = ic_factors if ic_factors is not None else load_ic_summary()
    return {key: factor_confidence(factors, key) for key in FACTOR_IC_KEY}
