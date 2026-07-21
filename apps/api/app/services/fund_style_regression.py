"""收益型风格分析：把基金日收益对价值/成长指数日收益做二元回归。

现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / Factor IC、PIT 与量化证据」。

纯函数引擎，无 I/O。回归系数 = 基金对两种风格的暴露：
- beta_value 高 → 偏价值；beta_growth 高 → 偏成长；
- style_tilt = beta_value - beta_growth（>0 偏价值，<0 偏成长）。

诚实边界：这是「风格暴露」（基金长得像价值/成长），不是基本面便宜/质量。
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_STYLE_SAMPLE_DAYS = 60
TILT_LABEL_THRESHOLD = 0.15
_DET_EPS = 1e-12


@dataclass
class StyleExposure:
    available: bool
    beta_value: float | None
    beta_growth: float | None
    style_tilt: float | None
    r_squared: float | None
    sample_days: int
    label: str | None
    message: str | None = None


def _unavailable(sample_days: int, message: str) -> StyleExposure:
    return StyleExposure(
        available=False,
        beta_value=None,
        beta_growth=None,
        style_tilt=None,
        r_squared=None,
        sample_days=sample_days,
        label=None,
        message=message,
    )


def align_returns(
    fund_by_date: dict[str, float],
    value_by_date: dict[str, float],
    growth_by_date: dict[str, float],
) -> tuple[list[float], list[float], list[float]]:
    """按公共日期升序对齐三条收益序列（取交集）。"""
    common = sorted(set(fund_by_date) & set(value_by_date) & set(growth_by_date))
    fr = [float(fund_by_date[d]) for d in common]
    vr = [float(value_by_date[d]) for d in common]
    gr = [float(growth_by_date[d]) for d in common]
    return fr, vr, gr


def _label(tilt: float) -> str:
    if tilt > TILT_LABEL_THRESHOLD:
        return "偏价值"
    if tilt < -TILT_LABEL_THRESHOLD:
        return "偏成长"
    return "中性"


def compute_style_exposure(
    fund_returns: list[float],
    value_returns: list[float],
    growth_returns: list[float],
) -> StyleExposure:
    """二元 OLS（中心化、闭式解）求基金对价值/成长的风格暴露。"""
    n = min(len(fund_returns), len(value_returns), len(growth_returns))
    if n < MIN_STYLE_SAMPLE_DAYS:
        return _unavailable(n, f"样本不足（{n} < {MIN_STYLE_SAMPLE_DAYS} 天）")

    y = fund_returns[:n]
    x1 = value_returns[:n]
    x2 = growth_returns[:n]

    my = sum(y) / n
    m1 = sum(x1) / n
    m2 = sum(x2) / n

    dy = [v - my for v in y]
    d1 = [v - m1 for v in x1]
    d2 = [v - m2 for v in x2]

    s11 = sum(a * a for a in d1)
    s22 = sum(a * a for a in d2)
    s12 = sum(a * b for a, b in zip(d1, d2))
    s1y = sum(a * b for a, b in zip(d1, dy))
    s2y = sum(a * b for a, b in zip(d2, dy))
    sst = sum(a * a for a in dy)

    det = s11 * s22 - s12 * s12
    if abs(det) < _DET_EPS:
        return _unavailable(n, "价值/成长指数共线或零方差，无法分离风格暴露")

    bv = (s22 * s1y - s12 * s2y) / det
    bg = (s11 * s2y - s12 * s1y) / det

    if sst <= _DET_EPS:
        r2 = None
    else:
        ss_res = sum(
            (dy[i] - bv * d1[i] - bg * d2[i]) ** 2 for i in range(n)
        )
        r2 = 1.0 - ss_res / sst

    tilt = bv - bg
    return StyleExposure(
        available=True,
        beta_value=bv,
        beta_growth=bg,
        style_tilt=tilt,
        r_squared=r2,
        sample_days=n,
        label=_label(tilt),
        message=None,
    )
