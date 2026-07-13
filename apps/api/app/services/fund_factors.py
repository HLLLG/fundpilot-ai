"""基金横截面因子打分：动量、风险调整(Calmar)、回撤控制、规模。

数据来源：开放式基金排行榜横截面（fetch_open_fund_rank）+ 持仓净值（不在榜时）。
设计文档：docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md

设计要点：
- 只接收纯数据（横截面行 + 目标行），不碰 DB/网络，便于单元测试。
- 横截面统计：去极值 → z-score → 合成 → 百分位（见文档第 4 章）。
- 缺失因子按"剩余权重归一"合成，不把 None 当 0。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

MIN_UNIVERSE_SIZE = 30  # 横截面有效样本少于此，不打分
WINSOR_LOWER_PCT = 5.0  # 去极值下分位
WINSOR_UPPER_PCT = 95.0  # 去极值上分位
Z_CLIP = 3.0  # z-score 裁剪边界

FACTOR_KEYS = ("momentum", "risk_adjusted", "drawdown", "size")
FACTOR_WEIGHTS = {
    "momentum": 0.40,
    "risk_adjusted": 0.35,
    "drawdown": 0.15,
    "size": 0.10,
}
FACTOR_LABELS = {
    "momentum": "动量",
    "risk_adjusted": "风险调整收益",
    "drawdown": "回撤控制",
    "size": "规模",
}


# ---------- 输入/输出数据结构 ----------


@dataclass
class FundFactorInput:
    """一只基金的因子原始输入（横截面行 / 目标行通用）。"""

    fund_code: str
    fund_name: str = ""
    return_3m_percent: float | None = None
    return_6m_percent: float | None = None
    return_1y_percent: float | None = None
    max_drawdown_1y_percent: float | None = None  # 负数，如 -22.0
    fund_scale_yi: float | None = None
    # 目标基金线上特征的真实时点链。研究模型的 run_date 不能替代这些字段：
    # 前者回答“横截面/IC 模型何时生成”，这里回答“该基金收益特征算到哪一天”。
    feature_as_of: str | None = None
    feature_observed_at: str | None = None
    feature_source: str | None = None
    return_coverage: float | None = None
    nav_age_trading_days: int | None = None
    feature_freshness: str = "unknown"
    feature_max_age_trading_days: int | None = None
    # 由 fund_type_factors 的同源 NAV 定义生成。旧调用不提供时保持空映射，
    # 因而完全向后兼容；线上只消费已通过 v3 同类 qualification 的键。
    typed_feature_values: dict[str, float | None] = field(default_factory=dict)
    typed_feature_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorDetail:
    raw: float | None
    z: float | None
    percentile: float | None  # 0-100
    hint: str | None = None


@dataclass
class FundFactorScore:
    fund_code: str
    fund_name: str
    in_universe: bool
    composite_score: float | None  # 0-100
    composite_grade: str | None  # A/B/C/D
    factors: dict[str, FactorDetail] = field(default_factory=dict)


@dataclass
class FactorScoreResult:
    available: bool
    universe_size: int
    message: str | None = None
    funds: list[FundFactorScore] = field(default_factory=list)


# ---------- 基础工具：原始值提取 ----------


def _blend_momentum(row: FundFactorInput) -> float | None:
    """多窗口动量加权；缺某窗口时按剩余窗口权重归一。"""
    parts = [
        (0.5, row.return_6m_percent),
        (0.3, row.return_3m_percent),
        (0.2, row.return_1y_percent),
    ]
    avail = [(w, v) for w, v in parts if v is not None]
    if not avail:
        return None
    total_w = sum(w for w, _ in avail)
    return sum(w * v for w, v in avail) / total_w


def _calmar(row: FundFactorInput) -> float | None:
    ret = row.return_1y_percent
    mdd = row.max_drawdown_1y_percent
    if ret is None or mdd is None:
        return None
    denom = abs(mdd)
    if denom < 1e-9:
        return None
    return ret / denom


def _size_raw(row: FundFactorInput) -> float | None:
    scale = row.fund_scale_yi
    if scale is None or scale <= 0:
        return None
    return math.log10(scale)


def _raw_factor(row: FundFactorInput, key: str) -> float | None:
    if key == "momentum":
        return _blend_momentum(row)
    if key == "risk_adjusted":
        return _calmar(row)
    if key == "drawdown":
        return row.max_drawdown_1y_percent
    if key == "size":
        return _size_raw(row)
    return None


# ---------- 横截面统计 ----------


def _percentile_value(sorted_vals: list[float], pct: float) -> float:
    """线性插值求分位值（pct 为 0-100）。sorted_vals 已升序、非空。"""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[int(rank)]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _winsorize(values: list[float]) -> list[float]:
    if len(values) < 2:
        return list(values)
    sorted_vals = sorted(values)
    lo = _percentile_value(sorted_vals, WINSOR_LOWER_PCT)
    hi = _percentile_value(sorted_vals, WINSOR_UPPER_PCT)
    return [min(max(v, lo), hi) for v in values]


@dataclass
class _FactorStats:
    mean: float
    std: float


def _factor_stats(universe_raw: list[float | None]) -> _FactorStats | None:
    """对一列因子原始值去极值后求均值/标准差。"""
    clean = [v for v in universe_raw if v is not None]
    if len(clean) < 2:
        return None
    wins = _winsorize(clean)
    mean = statistics.mean(wins)
    std = statistics.stdev(wins)  # 样本标准差 n-1
    return _FactorStats(mean=mean, std=std)


def _zscore(raw: float | None, stats: _FactorStats | None) -> float | None:
    if raw is None or stats is None:
        return None
    if stats.std < 1e-9:
        return 0.0  # 零方差：所有基金该因子相同，统一给中性 0
    z = (raw - stats.mean) / stats.std
    return max(-Z_CLIP, min(Z_CLIP, z))


def _percentile_rank(value: float | None, population: list[float]) -> float | None:
    """value 在 population 中的百分位（≤ 计数法），0-100。"""
    if value is None or not population:
        return None
    count = sum(1 for v in population if v <= value)
    return round(count / len(population) * 100, 1)


def _composite_z(factor_z: dict[str, float | None]) -> float | None:
    """按剩余权重归一合成综合 z。"""
    avail = [(FACTOR_WEIGHTS[k], z) for k, z in factor_z.items() if z is not None]
    if not avail:
        return None
    total_w = sum(w for w, _ in avail)
    if total_w < 1e-9:
        return None
    return sum(w * z for w, z in avail) / total_w


def _grade(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile >= 75:
        return "A"
    if percentile >= 50:
        return "B"
    if percentile >= 25:
        return "C"
    return "D"


# ---------- 对外主函数 ----------


def compute_factor_scores(
    *,
    universe: list[FundFactorInput],
    targets: list[FundFactorInput],
    min_universe_size: int = MIN_UNIVERSE_SIZE,
) -> FactorScoreResult:
    """对 targets（你的持仓）在 universe（排行榜横截面）里做因子打分。

    universe 与 targets 可以重叠（持仓在榜时直接用榜单行也行）；
    本函数只做统计，不去重、不取数。
    """
    # 1. 每个因子的横截面统计（去极值后的 mean/std）
    stats_by_factor: dict[str, _FactorStats | None] = {}
    momentum_valid_count = 0
    for key in FACTOR_KEYS:
        raws = [_raw_factor(row, key) for row in universe]
        stats_by_factor[key] = _factor_stats(raws)
        if key == "momentum":
            momentum_valid_count = sum(1 for r in raws if r is not None)

    if momentum_valid_count < min_universe_size:
        return FactorScoreResult(
            available=False,
            universe_size=momentum_valid_count,
            message=f"可比基金池不足 {min_universe_size} 只，暂无法计算因子评分。",
        )

    # 2. 池内每只基金的"综合 z"与各因子 z 分布，作为百分位底座
    universe_composite_pop: list[float] = []
    factor_z_pop: dict[str, list[float]] = {k: [] for k in FACTOR_KEYS}
    for row in universe:
        fz: dict[str, float | None] = {}
        for k in FACTOR_KEYS:
            z = _zscore(_raw_factor(row, k), stats_by_factor[k])
            fz[k] = z
            if z is not None:
                factor_z_pop[k].append(z)
        cz = _composite_z(fz)
        if cz is not None:
            universe_composite_pop.append(cz)

    # 3. 给每个 target 打分
    universe_codes = {row.fund_code for row in universe}
    funds: list[FundFactorScore] = []
    for tgt in targets:
        factor_z: dict[str, float | None] = {}
        details: dict[str, FactorDetail] = {}
        for k in FACTOR_KEYS:
            raw = _raw_factor(tgt, k)
            z = _zscore(raw, stats_by_factor[k])
            factor_z[k] = z
            pct = _percentile_rank(z, factor_z_pop[k])
            details[k] = FactorDetail(raw=raw, z=z, percentile=pct, hint=None)
        cz = _composite_z(factor_z)
        comp_pct = _percentile_rank(cz, universe_composite_pop)
        funds.append(
            FundFactorScore(
                fund_code=tgt.fund_code,
                fund_name=tgt.fund_name,
                in_universe=tgt.fund_code in universe_codes,
                composite_score=comp_pct,
                composite_grade=_grade(comp_pct),
                factors=details,
            )
        )

    return FactorScoreResult(
        available=True,
        universe_size=momentum_valid_count,
        funds=funds,
    )
