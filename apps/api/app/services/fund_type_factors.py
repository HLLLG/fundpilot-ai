"""基金类型专属 NAV 因子（同一套定义供离线研究与线上评分复用）。

所有数值因子均按“越大越好”定向。无法仅凭基金净值严谨计算的指标（例如
指数基金的跟踪误差）会显式返回 ``insufficient``，绝不使用同类中位数或基金
自身收益冒充基准。规模也只作为容量/风险门，不进入预期收益因子。
"""

from __future__ import annotations

import math
import statistics
from typing import Any

TYPE_FACTOR_SCHEMA_VERSION = "fund_type_factors.v1"
MIN_TYPE_FACTOR_POINTS = 60

_TYPE_FACTOR_KEYS: dict[str, tuple[str, ...]] = {
    "gp": (
        "medium_momentum",
        "momentum_acceleration",
        "return_consistency",
        "downside_resilience",
        "drawdown_recovery",
    ),
    "hh": (
        "medium_momentum",
        "momentum_acceleration",
        "return_consistency",
        "downside_resilience",
        "drawdown_recovery",
    ),
    "zq": (
        "stable_return",
        "downside_resilience",
        "negative_day_resilience",
        "tail_resilience",
        "drawdown_resilience",
    ),
    "zs": (
        "medium_momentum",
        "return_consistency",
        "downside_resilience",
        "drawdown_resilience",
    ),
    "qdii": (
        "medium_momentum",
        "relative_momentum",
        "downside_resilience",
        "drawdown_resilience",
    ),
    "fof": (
        "risk_adjusted_60",
        "risk_adjusted_120",
        "downside_resilience",
        "drawdown_recovery",
        "return_consistency",
    ),
}

TYPE_FACTOR_LABELS = {
    "medium_momentum": "中期动量",
    "momentum_acceleration": "动量加速度",
    "return_consistency": "收益一致性",
    "downside_resilience": "下行韧性",
    "drawdown_recovery": "回撤修复",
    "stable_return": "稳健收益",
    "negative_day_resilience": "负收益日控制",
    "tail_resilience": "尾部损失控制",
    "drawdown_resilience": "回撤控制",
    "relative_momentum": "相对基准动量",
    "risk_adjusted_60": "60日风险调整",
    "risk_adjusted_120": "120日风险调整",
    "tracking_difference": "跟踪偏离",
    "tracking_quality": "跟踪质量",
}


def normalize_fund_type(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    aliases = {
        "股票": "gp",
        "股票型": "gp",
        "混合": "hh",
        "混合型": "hh",
        "债券": "zq",
        "债券型": "zq",
        "指数": "zs",
        "指数型": "zs",
        "qdii型": "qdii",
        "fof型": "fof",
    }
    return aliases.get(text, text if text in _TYPE_FACTOR_KEYS else "unknown")


def type_factor_keys(
    fund_type: object,
    *,
    benchmark_available: bool = False,
) -> tuple[str, ...]:
    segment = normalize_fund_type(fund_type)
    keys = list(_TYPE_FACTOR_KEYS.get(segment, ()))
    if segment == "zs" and benchmark_available:
        keys.extend(("tracking_difference", "tracking_quality"))
    if segment == "qdii" and not benchmark_available:
        keys = [key for key in keys if key != "relative_momentum"]
    return tuple(keys)


def _finite_navs(navs: list[float]) -> list[float]:
    values: list[float] = []
    for value in navs:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and math.isfinite(parsed):
            values.append(parsed)
    return values


def _returns(navs: list[float], window: int | None = None) -> list[float]:
    selected = navs[-(window + 1) :] if window and len(navs) > window else navs
    return [
        selected[index] / selected[index - 1] - 1.0
        for index in range(1, len(selected))
        if selected[index - 1] > 0
    ]


def _window_return(navs: list[float], window: int) -> float | None:
    if len(navs) < 2:
        return None
    start = max(0, len(navs) - window - 1)
    base = navs[start]
    return navs[-1] / base - 1.0 if base > 0 else None


def _annualized_volatility(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(250)


def _downside_deviation(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    downside = [min(value, 0.0) for value in returns]
    return math.sqrt(sum(value * value for value in downside) / len(downside)) * math.sqrt(250)


def _max_drawdown(navs: list[float]) -> float | None:
    if len(navs) < 2:
        return None
    peak = navs[0]
    worst = 0.0
    for nav in navs:
        peak = max(peak, nav)
        if peak > 0:
            worst = min(worst, nav / peak - 1.0)
    return worst


def _rolling_positive_ratio(navs: list[float], window: int = 20) -> float | None:
    if len(navs) <= window:
        return None
    values = [
        navs[index] / navs[index - window] - 1.0
        for index in range(window, len(navs))
        if navs[index - window] > 0
    ]
    return sum(value > 0 for value in values) / len(values) if values else None


def _tail_mean(returns: list[float], fraction: float = 0.05) -> float | None:
    if not returns:
        return None
    count = max(1, math.ceil(len(returns) * fraction))
    return statistics.mean(sorted(returns)[:count])


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 1e-12:
        return None
    return numerator / denominator


def compute_nav_feature_library(navs: list[float]) -> dict[str, float | None]:
    """计算不依赖基金类型的同源 NAV 特征库。"""
    navs = _finite_navs(navs)
    ret_20 = _window_return(navs, 20)
    ret_60 = _window_return(navs, 60)
    ret_120 = _window_return(navs, 120)
    rets_60 = _returns(navs, 60)
    rets_120 = _returns(navs, 120)
    downside_120 = _downside_deviation(rets_120)
    vol_60 = _annualized_volatility(rets_60)
    vol_120 = _annualized_volatility(rets_120)
    mdd = _max_drawdown(navs[-251:])
    peak = max(navs[-251:]) if navs else None
    current_drawdown = navs[-1] / peak - 1.0 if peak and peak > 0 else None
    positive_daily_ratio = (
        sum(value > 0 for value in rets_120) / len(rets_120)
        if rets_120
        else None
    )
    tail = _tail_mean(rets_120)
    return {
        "medium_momentum": ret_120,
        # 短期收益减去按持有期折算的中期收益；只比较同类横截面。
        "momentum_acceleration": (
            ret_20 - ret_120 * (20 / 120)
            if ret_20 is not None and ret_120 is not None
            else None
        ),
        "return_consistency": _rolling_positive_ratio(navs, 20),
        "downside_resilience": -downside_120 if downside_120 is not None else None,
        "drawdown_recovery": current_drawdown,
        "stable_return": _safe_ratio(ret_120, vol_120),
        "negative_day_resilience": (
            positive_daily_ratio if positive_daily_ratio is not None else None
        ),
        "tail_resilience": tail,
        "drawdown_resilience": mdd,
        "risk_adjusted_60": _safe_ratio(ret_60, vol_60),
        "risk_adjusted_120": _safe_ratio(ret_120, vol_120),
    }


def _benchmark_library(
    navs: list[float],
    benchmark_navs: list[float] | None,
) -> dict[str, float | None]:
    benchmark = _finite_navs(benchmark_navs or [])
    # 仅接收调用方已按同一交易日轴严格对齐的序列；长度不同不能尾部硬拼。
    if len(benchmark) < MIN_TYPE_FACTOR_POINTS or len(benchmark) != len(navs):
        return {
            "tracking_difference": None,
            "tracking_quality": None,
            "relative_momentum": None,
        }
    length = min(len(navs), len(benchmark))
    fund = navs[-length:]
    bench = benchmark[-length:]
    fund_returns = _returns(fund, 120)
    benchmark_returns = _returns(bench, 120)
    active = [a - b for a, b in zip(fund_returns, benchmark_returns)]
    tracking_error = _annualized_volatility(active)
    active_return = (
        _window_return(fund, 120) - _window_return(bench, 120)
        if _window_return(fund, 120) is not None
        and _window_return(bench, 120) is not None
        else None
    )
    return {
        "tracking_difference": -abs(active_return) if active_return is not None else None,
        "tracking_quality": -tracking_error if tracking_error is not None else None,
        "relative_momentum": active_return,
    }


def compute_type_factor_values(
    fund_type: object,
    navs: list[float],
    *,
    benchmark_navs: list[float] | None = None,
) -> dict[str, float | None]:
    """返回该类型可严谨计算的、统一正向定向的因子值。"""
    segment = normalize_fund_type(fund_type)
    cleaned = _finite_navs(navs)
    if segment == "unknown" or len(cleaned) < MIN_TYPE_FACTOR_POINTS:
        return {key: None for key in type_factor_keys(segment)}
    benchmark = _finite_navs(benchmark_navs or [])
    has_benchmark = bool(
        len(benchmark) >= MIN_TYPE_FACTOR_POINTS and len(benchmark) == len(cleaned)
    )
    library = {
        **compute_nav_feature_library(cleaned),
        **_benchmark_library(cleaned, benchmark_navs),
    }
    return {
        key: library.get(key)
        for key in type_factor_keys(segment, benchmark_available=has_benchmark)
    }


def build_type_factor_evidence(
    fund_type: object,
    navs: list[float],
    *,
    benchmark_navs: list[float] | None = None,
    nav_age_days: int | None = None,
    fund_scale_yi: float | None = None,
) -> dict[str, Any]:
    """输出可审计证据；缺基准、净值陈旧、规模仅守卫均显式标注。"""
    segment = normalize_fund_type(fund_type)
    cleaned = _finite_navs(navs)
    benchmark = _finite_navs(benchmark_navs or [])
    benchmark_available = bool(
        len(benchmark) >= MIN_TYPE_FACTOR_POINTS and len(benchmark) == len(cleaned)
    )
    values = compute_type_factor_values(
        segment,
        cleaned,
        benchmark_navs=benchmark_navs,
    )
    tracking_required = segment == "zs"
    tracking = {
        "status": "available" if tracking_required and benchmark_available else "insufficient",
        "reason": (
            None
            if tracking_required and benchmark_available
            else "缺少该指数基金精确、时点可得且按同一交易日轴对齐的跟踪基准，未计算 tracking 因子"
            if tracking_required
            else "该基金类型不使用 tracking 因子"
        ),
    }
    stale_limit = 7 if segment == "qdii" else 3
    freshness = {
        "status": (
            "unknown"
            if nav_age_days is None
            else "fresh" if 0 <= nav_age_days <= stale_limit else "insufficient"
        ),
        "age_days": nav_age_days,
        "max_age_days": stale_limit,
    }
    capacity = {
        "role": "risk_guard",
        "status": "available" if fund_scale_yi is not None and fund_scale_yi > 0 else "insufficient",
        "scale_yi": fund_scale_yi,
        "used_as_return_factor": False,
    }
    return {
        "schema_version": TYPE_FACTOR_SCHEMA_VERSION,
        "fund_type": segment,
        "lookback_points": len(cleaned),
        "applicable": bool(
            segment != "unknown"
            and len(cleaned) >= MIN_TYPE_FACTOR_POINTS
            and freshness["status"] != "insufficient"
        ),
        "values": values,
        "tracking_evidence": tracking,
        "freshness": freshness,
        "capacity_gate": capacity,
    }
