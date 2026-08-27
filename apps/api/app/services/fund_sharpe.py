"""按天天基金 / 东方财富 Choice 零售口径，用净值自算近 1 年、近 3 年夏普。

支付宝基金页的标准差、夏普与天天基金「特色数据」同源（Choice），页面只解释含义、
不公布逐步公式。本模块实现国内基金零售页通行写法，而不是组合模块
``portfolio_risk_metrics._sharpe``（日超额均值 / 样本标准差 × √252，无风险利率 2%）。

现行口径 ``fund_sharpe.alipay_style.v1``：

- 窗口：截止日期往前 N 个自然年（对齐月日；2 月 29 日落到 2 月 28 日），含两端净值日
- 日收益：官方日增长率优先的复权序列（``build_total_return_index``）
- 年化收益：``(1 + 区间收益) ** (365 / 首末自然日) - 1``
- 年化波动：日收益总体标准差 × √250
- 无风险利率：一年期定期存款基准 1.50%
- 夏普：``(年化收益 - 1.50%) / 年化波动``
- 展示四舍五入到 2 位（与天天基金一致）

支付宝未公布逐行公式，个券公示值可能仍有约 0.05～0.10 偏差（已知：000001 近 1 年）。
只作研究描述，不得当买入或否决门；样本不足则该期限为空，不填 0。
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite, sqrt
import statistics
from typing import Any

from app.services.fund_factor_nav import build_total_return_index

SHARPE_SCHEMA_VERSION = "fund_sharpe.alipay_style.v1"
ALIPAY_STYLE_RISK_FREE_RATE = 0.015
STD_ANNUALIZATION_FACTOR = 250
RETURN_CALENDAR_DAYS = 365
MIN_DAILY_RETURNS_BY_YEARS = {1: 180, 3: 500}


def shift_calendar_years(day: date, years: int) -> date:
    """截止日期往前推 N 个自然年；2 月 29 日对齐到 2 月 28 日。"""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return date(day.year - years, 2, 28)


def _round_half_up(value: float, digits: int) -> float:
    quant = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10].replace("/", "-")
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _point_row(point: Any) -> dict[str, Any]:
    if isinstance(point, Mapping):
        return {
            "date": point.get("date"),
            "nav": point.get("nav"),
            "daily_return_percent": point.get(
                "daily_return_percent", point.get("daily_growth")
            ),
        }
    return {
        "date": getattr(point, "date", None),
        "nav": getattr(point, "nav", None),
        "daily_return_percent": getattr(point, "daily_return_percent", None),
    }


def _resolve_as_of(points: list[Any], as_of: object) -> date | None:
    last: date | None = None
    for point in points:
        day = _parse_date(_point_row(point)["date"])
        if day is not None and (last is None or day > last):
            last = day
    requested = _parse_date(as_of)
    if requested is None:
        return last
    if last is None:
        return requested
    return min(requested, last)


def compute_alipay_style_sharpe(
    points: list[Any],
    *,
    years: int,
    as_of: date | str | None = None,
    min_daily_returns: int | None = None,
    risk_free_rate: float = ALIPAY_STYLE_RISK_FREE_RATE,
) -> dict[str, Any] | None:
    """对单一期限复算夏普；样本或波动不足则返回 None。"""
    if years not in MIN_DAILY_RETURNS_BY_YEARS:
        raise ValueError(f"unsupported sharpe horizon years={years}")
    required = (
        MIN_DAILY_RETURNS_BY_YEARS[years]
        if min_daily_returns is None
        else min_daily_returns
    )
    end = _resolve_as_of(points, as_of)
    if end is None:
        return None
    start = shift_calendar_years(end, years)
    window_rows = []
    for point in points:
        row = _point_row(point)
        day = _parse_date(row["date"])
        if day is None or day < start or day > end:
            continue
        window_rows.append(row)
    series = build_total_return_index(window_rows)
    if len(series.points) < 2:
        return None
    first_day, first_value = series.points[0]
    last_day, last_value = series.points[-1]
    if first_value <= 0 or last_value <= 0:
        return None
    daily_returns = [
        current / previous - 1.0
        for (_, previous), (_, current) in zip(series.points, series.points[1:])
        if previous > 0
    ]
    if len(daily_returns) < required:
        return None
    calendar_days = (date.fromisoformat(last_day) - date.fromisoformat(first_day)).days
    if calendar_days <= 0:
        return None
    period_return = last_value / first_value - 1.0
    if period_return <= -1.0:
        return None
    annualized_return = (1.0 + period_return) ** (
        RETURN_CALENDAR_DAYS / calendar_days
    ) - 1.0
    annualized_vol = statistics.pstdev(daily_returns) * sqrt(STD_ANNUALIZATION_FACTOR)
    if (
        not isfinite(annualized_return)
        or not isfinite(annualized_vol)
        or annualized_vol <= 1e-12
    ):
        return None
    sharpe = (annualized_return - risk_free_rate) / annualized_vol
    if not isfinite(sharpe):
        return None
    return {
        "sharpe": _round_half_up(sharpe, 2),
        "annualized_return_percent": _round_half_up(annualized_return * 100.0, 4),
        "annualized_volatility_percent": _round_half_up(annualized_vol * 100.0, 4),
        "period_return_percent": _round_half_up(period_return * 100.0, 4),
        "sample_days": len(daily_returns),
        "calendar_days": calendar_days,
        "start_date": first_day,
        "end_date": last_day,
    }


def compute_window_max_drawdown_percent(
    points: list[Any],
    *,
    years: int = 1,
    as_of: date | str | None = None,
    min_daily_returns: int | None = None,
) -> float | None:
    """与夏普同一自然年窗口，用复权净值算最大回撤（负数百分比）。"""

    if years not in MIN_DAILY_RETURNS_BY_YEARS:
        raise ValueError(f"unsupported drawdown horizon years={years}")
    required = (
        MIN_DAILY_RETURNS_BY_YEARS[years]
        if min_daily_returns is None
        else min_daily_returns
    )
    end = _resolve_as_of(points, as_of)
    if end is None:
        return None
    start = shift_calendar_years(end, years)
    window_rows = []
    for point in points:
        row = _point_row(point)
        day = _parse_date(row["date"])
        if day is None or day < start or day > end:
            continue
        window_rows.append(row)
    series = build_total_return_index(window_rows)
    if len(series.points) < 2:
        return None
    values = [value for _, value in series.points if value > 0]
    # `required` 是日收益条数，与夏普同一门槛；净值点数要比收益数多 1。
    if len(values) < required + 1:
        return None
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    percent = _round_half_up(max_drawdown * 100.0, 2)
    if not isfinite(percent) or not -100.0 <= percent <= 0.0:
        return None
    return percent


def compute_alipay_style_sharpes(
    points: list[Any],
    *,
    as_of: date | str | None = None,
    available_at: str | None = None,
    min_daily_returns: dict[int, int] | None = None,
) -> dict[str, Any]:
    resolved_as_of = _resolve_as_of(points, as_of)
    horizons: dict[str, dict[str, Any] | None] = {}
    for years in (1, 3):
        required = None if min_daily_returns is None else min_daily_returns.get(years)
        horizons[f"{years}y"] = compute_alipay_style_sharpe(
            points,
            years=years,
            as_of=resolved_as_of,
            min_daily_returns=required,
        )
    return {
        "schema_version": SHARPE_SCHEMA_VERSION,
        "source_label": "天天基金/Choice 零售口径复算",
        "risk_free_rate": ALIPAY_STYLE_RISK_FREE_RATE,
        "std_annualization_factor": STD_ANNUALIZATION_FACTOR,
        "return_annualization": "geometric_365_calendar_days",
        "std_estimator": "population",
        "as_of": resolved_as_of.isoformat() if resolved_as_of else None,
        "available_at": available_at,
        "horizons": horizons,
    }


def attach_alipay_style_sharpes(
    row: dict[str, Any],
    points: list[Any] | None,
    *,
    as_of: date | str | None = None,
    available_at: str | None = None,
) -> dict[str, Any]:
    """把近 1 年 / 近 3 年夏普挂到候选行；不足则该期限为 None。"""
    payload = compute_alipay_style_sharpes(
        list(points or []),
        as_of=as_of,
        available_at=available_at,
    )
    one = payload["horizons"]["1y"]
    three = payload["horizons"]["3y"]
    row["sharpe_1y"] = None if one is None else one["sharpe"]
    row["sharpe_3y"] = None if three is None else three["sharpe"]
    row["sharpe_research"] = payload
    return payload
