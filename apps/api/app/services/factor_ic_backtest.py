"""因子有效性回测：walk-forward Rank IC（信息系数）。

在一个基金池上回测「某日的因子排序，能不能预测之后 N 日的收益排序」，
攒成 IC 时序后求均值 / ICIR / t 统计量 / 显著性，检验模块2 的因子有没有预测力。

纯函数 + 依赖注入：只接收已对齐的 NAV 面板，不碰 DB/网络，便于单测。
设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

MIN_CROSS_SECTION = 10  # 单期横截面有效基金数下限
MIN_PERIODS = 12  # 有效期数下限（少于此不下显著性结论）
T_SIGNIF = 2.0  # |t| 显著阈值
DEFAULT_REBALANCE_STEP = 21
DEFAULT_FORWARD_DAYS = 20
DEFAULT_FACTOR_LOOKBACK = 250

# 被检因子（规模因子排除：历史规模拿不到）
SINGLE_FACTORS = ("momentum", "risk_adjusted", "drawdown")
FACTOR_ORDER = ("momentum", "risk_adjusted", "drawdown", "composite")


@dataclass
class NavPoint:
    date: str
    nav: float


@dataclass
class FactorICStats:
    factor: str
    n_periods: int
    mean_ic: float | None
    ic_std: float | None
    icir: float | None
    t_stat: float | None
    positive_ratio: float | None
    significant: bool
    ic_series: list[float] = field(default_factory=list)


@dataclass
class FactorICResult:
    available: bool
    universe_size: int
    rebalance_count: int
    forward_days: int
    message: str | None = None
    factors: list[FactorICStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 秩相关工具
# ---------------------------------------------------------------------------


def _rankdata(values: list[float]) -> list[float]:
    """平均秩：并列取名次均值（名次从 1 开始）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return cov / (vx**0.5 * vy**0.5)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    corr = _pearson(_rankdata(xs), _rankdata(ys))
    if corr is None:
        return None
    return round(corr, 10)  # 抹掉浮点尘，±1 干净落地


def _rank_ic_for_period(
    factor_vals: dict[str, float | None],
    forward_rets: dict[str, float | None],
    *,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> float | None:
    """对齐到两者都有值的基金，算 Rank IC；有效基金数不足返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    for code, fv in factor_vals.items():
        rv = forward_rets.get(code)
        if fv is None or rv is None:
            continue
        xs.append(fv)
        ys.append(rv)
    if len(xs) < min_cross_section:
        return None
    return _spearman(xs, ys)


# ---------------------------------------------------------------------------
# 面板取数（point-in-time，严格不偷看未来）
# ---------------------------------------------------------------------------


def _nav_asof(dates: list[str], navs: list[float], as_of: str) -> float | None:
    """date <= as_of 的最后一个净值（>0）。"""
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return None
    nav = navs[idx]
    return nav if nav > 0 else None


def _navs_upto(dates: list[str], navs: list[float], as_of: str, lookback: int) -> list[float]:
    """date <= as_of 的净值尾部 lookback 段。"""
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return []
    start = max(0, idx + 1 - lookback)
    return navs[start : idx + 1]


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def _aggregate(factor: str, raw_ics: list[float | None]) -> FactorICStats:
    ics = [v for v in raw_ics if v is not None]
    n = len(ics)
    if n == 0:
        return FactorICStats(factor, 0, None, None, None, None, None, False, [])
    mean = sum(ics) / n
    if n >= 2:
        var = sum((v - mean) ** 2 for v in ics) / (n - 1)
        std = var**0.5
    else:
        std = 0.0
    icir = mean / std if std > 1e-12 else None
    t_stat = mean / (std / n**0.5) if std > 1e-12 else None
    pos = sum(1 for v in ics if v > 0) / n
    significant = n >= MIN_PERIODS and t_stat is not None and abs(t_stat) > T_SIGNIF
    return FactorICStats(
        factor=factor,
        n_periods=n,
        mean_ic=round(mean, 4),
        ic_std=round(std, 4),
        icir=round(icir, 3) if icir is not None else None,
        t_stat=round(t_stat, 2) if t_stat is not None else None,
        positive_ratio=round(pos, 3),
        significant=significant,
        ic_series=[round(v, 4) for v in ics],
    )


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


def _raw_factors_at(navs: list[float]) -> dict[str, float | None]:
    """从一段升序净值（<=t）算单因子原始值。"""
    from app.services.fund_factor_nav import factor_input_from_navs
    from app.services.fund_factors import _blend_momentum, _calmar

    fi = factor_input_from_navs("", "", navs)
    return {
        "momentum": _blend_momentum(fi),
        "risk_adjusted": _calmar(fi),
        "drawdown": fi.max_drawdown_1y_percent,
    }


def compute_factor_ic(
    *,
    nav_panel: dict[str, list[NavPoint]],
    calendar: list[str],
    rebalance_step: int = DEFAULT_REBALANCE_STEP,
    forward_days: int = DEFAULT_FORWARD_DAYS,
    factor_lookback: int = DEFAULT_FACTOR_LOOKBACK,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> FactorICResult:
    """walk-forward Rank IC 回测。

    nav_panel: {code: [NavPoint(date, nav)...]}（升序）；calendar: 锚定交易日轴（升序）。
    """
    from app.services.fund_factors import (
        FACTOR_WEIGHTS,
        _composite_z,
        _factor_stats,
        _zscore,
    )

    universe_size = len(nav_panel)
    if universe_size < min_cross_section:
        return FactorICResult(
            available=False,
            universe_size=universe_size,
            rebalance_count=0,
            forward_days=forward_days,
            message=f"基金池有效数 {universe_size} < {min_cross_section}，无法回测。",
        )

    # 预拆每只基金的 (dates, navs) 便于二分
    indexed: dict[str, tuple[list[str], list[float]]] = {}
    for code, points in nav_panel.items():
        pts = sorted(points, key=lambda p: p.date)
        indexed[code] = ([p.date for p in pts], [p.nav for p in pts])

    # 锚定再平衡日
    anchors = [
        i
        for i in range(0, len(calendar), rebalance_step)
        if i + forward_days < len(calendar)
    ]

    ic_series: dict[str, list[float | None]] = {f: [] for f in FACTOR_ORDER}

    for i in anchors:
        t_date = calendar[i]
        fwd_date = calendar[i + forward_days]

        raws_by_factor: dict[str, dict[str, float | None]] = {
            f: {} for f in SINGLE_FACTORS
        }
        forward_rets: dict[str, float | None] = {}

        for code, (dates, navs) in indexed.items():
            slice_navs = _navs_upto(dates, navs, t_date, factor_lookback)
            raws = _raw_factors_at(slice_navs)
            for f in SINGLE_FACTORS:
                raws_by_factor[f][code] = raws[f]

            nav_t = _nav_asof(dates, navs, t_date)
            nav_fwd = _nav_asof(dates, navs, fwd_date)
            if nav_t and nav_fwd and nav_t > 0:
                forward_rets[code] = nav_fwd / nav_t - 1.0
            else:
                forward_rets[code] = None

        # 单因子 IC
        for f in SINGLE_FACTORS:
            ic_series[f].append(
                _rank_ic_for_period(
                    raws_by_factor[f], forward_rets, min_cross_section=min_cross_section
                )
            )

        # composite IC：横截面 z 合成（复用模块2 引擎）
        stats = {
            f: _factor_stats(list(raws_by_factor[f].values())) for f in SINGLE_FACTORS
        }
        composite_vals: dict[str, float | None] = {}
        for code in indexed:
            factor_z: dict[str, float | None] = {}
            for f in SINGLE_FACTORS:
                factor_z[f] = _zscore(raws_by_factor[f][code], stats[f])
            # 规模因子缺省 None：_composite_z 按剩余权重归一
            factor_z["size"] = None
            # 仅保留 FACTOR_WEIGHTS 已知键
            factor_z = {k: factor_z.get(k) for k in FACTOR_WEIGHTS}
            composite_vals[code] = _composite_z(factor_z)
        ic_series["composite"].append(
            _rank_ic_for_period(
                composite_vals, forward_rets, min_cross_section=min_cross_section
            )
        )

    factors = [_aggregate(f, ic_series[f]) for f in FACTOR_ORDER]
    return FactorICResult(
        available=True,
        universe_size=universe_size,
        rebalance_count=len(anchors),
        forward_days=forward_days,
        factors=factors,
    )
