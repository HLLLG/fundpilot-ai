"""组合风险度量：波动率、夏普、索提诺、最大回撤、Beta/Alpha、HHI。

数据来源：portfolio_daily_snapshots（组合日收益）+ 沪深300日线（基准）。
现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / 金融评估与路径风险」。

设计要点：
- 本模块只接收**纯数据**（收益数组、金额数组），不碰数据库，便于单元测试。
- 累计收益一律走复利累乘（见文档 Bug A：简单百分比不可直接相加）。
- 取数 / 日期对齐的脏活由调用方（dashboard 装配层）负责。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

TRADING_DAYS_PER_YEAR = 252
MIN_SAMPLE_DAYS = 20  # 少于此样本不计算
MIN_ANNUALIZATION_SAMPLE_DAYS = 60  # 年化指标在更短窗口下仅作低置信度参考
DEFAULT_RISK_FREE_RATE = 0.02  # 年化无风险利率，可被 config 覆盖
MIN_CORRELATION_SAMPLE_DAYS = 20  # 相关性矩阵：对齐后少于此交易日不计算


# ---------- 基础工具：收益序列处理 ----------


def _to_decimal_returns(daily_return_percents: list[float]) -> list[float]:
    """把百分比收益（如 1.5 表示 +1.5%）转成小数（0.015）。"""
    return [float(p) / 100.0 for p in daily_return_percents if p is not None]


def _equity_curve(returns: list[float]) -> list[float]:
    """复利累乘成净值曲线，起点 1.0。注意：用相乘，不是相加（见文档 Bug A）。"""
    # 必须显式保留初始高点。否则样本首日即下跌时，最大回撤会漏掉
    # 从 1.0 到首个观测点的损失。
    equity: list[float] = [1.0]
    value = 1.0
    for r in returns:
        value *= 1.0 + r
        equity.append(value)
    return equity


def _cumulative_return(returns: list[float]) -> float:
    """区间总收益（小数）。"""
    if not returns:
        return 0.0
    return _equity_curve(returns)[-1] - 1.0


def _annualized_return(returns: list[float]) -> float:
    """几何年化收益。"""
    n = len(returns)
    if n == 0:
        return 0.0
    total = _cumulative_return(returns)
    base = 1.0 + total
    # 净值跌到 <=0 在现实中不会发生，但样本极端时做个保护避免复数 / math domain error。
    if base <= 0:
        return -1.0
    return base ** (TRADING_DAYS_PER_YEAR / n) - 1.0


def _daily_volatility(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns)  # 样本标准差，分母 n-1


def _annualized_volatility(returns: list[float]) -> float:
    return _daily_volatility(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _daily_risk_free_rate(annual_rate: float) -> float:
    """把年化无风险利率按复利转换为日收益率。"""
    if annual_rate <= -1:
        return -1.0
    return (1.0 + annual_rate) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0


def _downside_volatility(returns: list[float], target: float = 0.0) -> float:
    """年化下行偏差（lower partial moment），分母使用全部观测日。"""
    if not returns:
        return 0.0
    lower_partial_moment = sum(min(r - target, 0.0) ** 2 for r in returns) / len(
        returns
    )
    return math.sqrt(lower_partial_moment) * math.sqrt(TRADING_DAYS_PER_YEAR)


# ---------- 核心指标 ----------


def _sharpe(returns: list[float], risk_free_rate: float) -> float | None:
    if len(returns) < 2:
        return None
    daily_rf = _daily_risk_free_rate(risk_free_rate)
    excess = [value - daily_rf for value in returns]
    daily_vol = _daily_volatility(excess)
    if daily_vol == 0:
        return None
    return statistics.mean(excess) / daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR)


def _sortino(returns: list[float], risk_free_rate: float) -> float | None:
    if not returns:
        return None
    daily_rf = _daily_risk_free_rate(risk_free_rate)
    dvol = _downside_volatility(returns, target=daily_rf)
    if dvol == 0:
        return None
    annualized_excess = (statistics.mean(returns) - daily_rf) * TRADING_DAYS_PER_YEAR
    return annualized_excess / dvol


def _max_drawdown(returns: list[float]) -> float:
    """返回最大回撤（负数小数，如 -0.15 表示 -15%）。"""
    equity = _equity_curve(returns)
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = (value - peak) / peak
            mdd = min(mdd, drawdown)
    return mdd


def _beta_alpha(
    portfolio_returns: list[float],
    index_returns: list[float],
    risk_free_rate: float,
) -> tuple[float | None, float | None]:
    """对齐后的两条日收益序列求 Beta 和 CAPM Alpha。

    要求调用方已按日期对齐（两序列等长、逐日配对）。这里再做一次尾部对齐兜底。
    """
    n = min(len(portfolio_returns), len(index_returns))
    if n < MIN_SAMPLE_DAYS:
        return None, None
    p = portfolio_returns[-n:]
    m = index_returns[-n:]
    var_m = statistics.pvariance(m)  # 总体方差，与下面协方差口径一致（均除以 n）
    if var_m == 0:
        return None, None
    mean_p, mean_m = statistics.mean(p), statistics.mean(m)
    cov = sum((p[i] - mean_p) * (m[i] - mean_m) for i in range(n)) / n
    beta = cov / var_m
    daily_rf = _daily_risk_free_rate(risk_free_rate)
    # Jensen alpha 由日频 CAPM 残差的算术均值年化。几何年化两条序列后再相减
    # 会混入波动拖累，尤其在短窗口中造成显著偏差。
    alpha = (mean_p - daily_rf - beta * (mean_m - daily_rf)) * TRADING_DAYS_PER_YEAR
    return beta, alpha


def _hhi(weights: list[float]) -> float:
    """权重平方和（weights 为小数，和为 1）。全压一只=1.0，越分散越接近 0。"""
    return sum(w * w for w in weights)


# ---------- 对外主函数 ----------


@dataclass
class PortfolioRiskMetrics:
    available: bool
    sample_days: int
    message: str | None = None
    sample_quality: str = "insufficient"
    annualization_reliable: bool = False
    annualized_return_percent: float | None = None
    annualized_volatility_percent: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown_percent: float | None = None
    beta: float | None = None
    alpha_percent: float | None = None
    hhi: float | None = None
    effective_holdings: float | None = None


def compute_portfolio_metrics(
    *,
    portfolio_daily_returns: list[float],  # 单位：百分比，如 [1.2, -0.5, ...]，按日期升序
    index_daily_returns: list[float],  # 同上，沪深300，已按日期对齐
    holding_amounts: list[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> PortfolioRiskMetrics:
    returns = _to_decimal_returns(portfolio_daily_returns)
    n = len(returns)
    if n < MIN_SAMPLE_DAYS:
        return PortfolioRiskMetrics(
            available=False,
            sample_days=n,
            message=f"历史快照不足 {MIN_SAMPLE_DAYS} 个交易日，暂无法计算风险指标。",
        )

    index_returns = _to_decimal_returns(index_daily_returns)
    beta, alpha = _beta_alpha(returns, index_returns, risk_free_rate)

    total_amount = sum(a for a in holding_amounts if a and a > 0)
    weights = (
        [a / total_amount for a in holding_amounts if a and a > 0]
        if total_amount > 0
        else []
    )
    hhi = _hhi(weights) if weights else None

    sharpe = _sharpe(returns, risk_free_rate)
    sortino = _sortino(returns, risk_free_rate)

    def pct(value: float | None) -> float | None:
        return round(value * 100, 2) if value is not None else None

    return PortfolioRiskMetrics(
        available=True,
        sample_days=n,
        message=(
            None
            if n >= MIN_ANNUALIZATION_SAMPLE_DAYS
            else (
                f"当前仅 {n} 个交易日；年化收益、夏普、索提诺和 Alpha "
                f"低于 {MIN_ANNUALIZATION_SAMPLE_DAYS} 日可靠性阈值，仅作低置信度参考。"
            )
        ),
        sample_quality=(
            "standard" if n >= MIN_ANNUALIZATION_SAMPLE_DAYS else "short_window"
        ),
        annualization_reliable=n >= MIN_ANNUALIZATION_SAMPLE_DAYS,
        annualized_return_percent=pct(_annualized_return(returns)),
        annualized_volatility_percent=pct(_annualized_volatility(returns)),
        sharpe_ratio=round(sharpe, 2) if sharpe is not None else None,
        sortino_ratio=round(sortino, 2) if sortino is not None else None,
        max_drawdown_percent=pct(_max_drawdown(returns)),
        beta=round(beta, 2) if beta is not None else None,
        alpha_percent=pct(alpha),
        hhi=round(hhi, 3) if hhi is not None else None,
        effective_holdings=round(1.0 / hhi, 1) if hhi else None,
    )


# ---------- 相关性矩阵（第二批） ----------


def _pearson(a: list[float], b: list[float]) -> float | None:
    """皮尔逊相关系数，取值 [-1, 1]；任一序列零方差返回 None。"""
    n = len(a)
    if n < 2:
        return None
    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    denom = math.sqrt(var_a * var_b)
    if denom == 0:
        return None
    value = cov / denom
    # 数值误差兜底，夹到 [-1, 1]
    return max(-1.0, min(1.0, value))


@dataclass
class CorrelationPair:
    code_a: str
    code_b: str
    name_a: str
    name_b: str
    corr: float


@dataclass
class PortfolioCorrelationMatrix:
    available: bool
    message: str | None = None
    sample_days: int = 0
    codes: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    matrix: list[list[float | None]] = field(default_factory=list)
    max_pair: CorrelationPair | None = None


def compute_correlation_matrix(
    *,
    returns_by_code: dict[str, dict[str, float]],  # code -> {snapshot_date: 日收益(%)}
    names_by_code: dict[str, str],
    min_sample_days: int = MIN_CORRELATION_SAMPLE_DAYS,
) -> PortfolioCorrelationMatrix:
    """两两计算持仓基金的日收益相关系数矩阵。

    所有序列对齐到"全体持仓都有数据"的公共交易日集合，保证矩阵内部口径一致
    （相关系数与量纲无关，传 % 或小数都可）。
    """
    codes = list(returns_by_code.keys())
    if len(codes) < 2:
        return PortfolioCorrelationMatrix(
            available=False,
            message="持仓不足 2 只，无法计算相关性。",
            codes=codes,
            names=[names_by_code.get(code, code) for code in codes],
        )

    # 全体公共交易日（每只基金都有净值的那些天）
    common_dates: set[str] | None = None
    for code in codes:
        dates = set(returns_by_code[code].keys())
        common_dates = dates if common_dates is None else (common_dates & dates)
    common_dates = common_dates or set()
    sorted_dates = sorted(common_dates)
    sample_days = len(sorted_dates)

    names = [names_by_code.get(code, code) for code in codes]

    if sample_days < min_sample_days:
        return PortfolioCorrelationMatrix(
            available=False,
            message=f"持仓净值对齐后不足 {min_sample_days} 个交易日，暂无法计算相关性。",
            sample_days=sample_days,
            codes=codes,
            names=names,
        )

    aligned = {
        code: [returns_by_code[code][day] for day in sorted_dates] for code in codes
    }

    size = len(codes)
    matrix: list[list[float | None]] = [[None] * size for _ in range(size)]
    max_pair: CorrelationPair | None = None
    for i in range(size):
        matrix[i][i] = 1.0
        for j in range(i + 1, size):
            corr = _pearson(aligned[codes[i]], aligned[codes[j]])
            matrix[i][j] = round(corr, 2) if corr is not None else None
            matrix[j][i] = matrix[i][j]
            if corr is not None and (max_pair is None or corr > max_pair.corr):
                max_pair = CorrelationPair(
                    code_a=codes[i],
                    code_b=codes[j],
                    name_a=names[i],
                    name_b=names[j],
                    corr=round(corr, 2),
                )

    return PortfolioCorrelationMatrix(
        available=True,
        sample_days=sample_days,
        codes=codes,
        names=names,
        matrix=matrix,
        max_pair=max_pair,
    )
