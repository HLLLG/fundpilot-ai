"""从一段 NAV 切片算因子原始值（动量/Calmar/回撤）。

模块2（持仓不在榜的净值兜底）与模块3（因子 IC 回测）共用，避免重复。
设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md 第 5 章。

窗口口径与排行榜一致：3 月≈60、6 月≈120、1 年≈250 交易日；
最大回撤复用模块1 `portfolio_risk_metrics._max_drawdown` 保口径一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TotalReturnSeries:
    points: list[tuple[str, float]]
    daily_return_points: int
    nav_ratio_points: int
    invalid_points: int

    @property
    def return_coverage(self) -> float:
        transitions = max(0, len(self.points) - 1)
        return self.daily_return_points / transitions if transitions else 0.0


def build_total_return_index(rows: list[dict[str, Any]]) -> TotalReturnSeries:
    """用日增长率优先重建总收益指数；缺失时才回落到单位净值比值。"""
    normalized: dict[str, tuple[float | None, float | None]] = {}
    for row in rows:
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        try:
            nav = float(row["nav"]) if row.get("nav") is not None else None
        except (TypeError, ValueError):
            nav = None
        growth_raw = row.get("daily_growth", row.get("daily_return_percent"))
        try:
            growth = float(growth_raw) if growth_raw is not None else None
        except (TypeError, ValueError):
            growth = None
        normalized[day] = (nav if nav and nav > 0 else None, growth)

    points: list[tuple[str, float]] = []
    index_value = 1.0
    previous_nav: float | None = None
    daily_count = 0
    nav_count = 0
    invalid_count = 0
    for day in sorted(normalized):
        nav, growth = normalized[day]
        if not points:
            if nav is None:
                invalid_count += 1
                continue
            points.append((day, index_value))
            previous_nav = nav
            continue

        period_return: float | None = None
        if growth is not None and -99.9 < growth < 1_000:
            period_return = growth / 100.0
            daily_count += 1
        elif nav is not None and previous_nav is not None and previous_nav > 0:
            period_return = nav / previous_nav - 1.0
            nav_count += 1

        if period_return is None or period_return <= -0.999 or period_return > 10:
            invalid_count += 1
            if nav is not None:
                previous_nav = nav
            continue
        index_value *= 1.0 + period_return
        if index_value <= 0:
            invalid_count += 1
            continue
        points.append((day, index_value))
        if nav is not None:
            previous_nav = nav

    return TotalReturnSeries(
        points=points,
        daily_return_points=daily_count,
        nav_ratio_points=nav_count,
        invalid_points=invalid_count,
    )


def total_return_navs_from_points(points: list[Any]) -> TotalReturnSeries:
    return build_total_return_index(
        [
            {
                "date": getattr(point, "date", None),
                "nav": getattr(point, "nav", None),
                "daily_return_percent": getattr(point, "daily_return_percent", None),
            }
            for point in points
        ]
    )


def window_return_percent(navs: list[float], window: int) -> float | None:
    """升序净值序列近 window 个交易日区间收益(%)；不足则尽力从最早点算。"""
    if len(navs) < 2:
        return None
    base = navs[max(0, len(navs) - 1 - window)]
    if base <= 0:
        return None
    return (navs[-1] / base - 1.0) * 100.0


def factor_input_from_navs(code: str, name: str, navs: list[float]):
    """从一段升序净值算 FundFactorInput（return_3m/6m/1y + 1年最大回撤；规模 None）。"""
    from app.services.fund_factors import FundFactorInput
    from app.services.portfolio_risk_metrics import _max_drawdown

    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1] > 0]
    mdd = _max_drawdown(rets) * 100.0 if rets else None
    return FundFactorInput(
        fund_code=code,
        fund_name=name,
        return_3m_percent=window_return_percent(navs, 60),
        return_6m_percent=window_return_percent(navs, 120),
        return_1y_percent=window_return_percent(navs, 250),
        max_drawdown_1y_percent=mdd,
        fund_scale_yi=None,
    )


def factor_input_from_points(
    code: str,
    name: str,
    points: list[Any],
    *,
    require_complete: bool = False,
    minimum_points: int = 250,
):
    series = total_return_navs_from_points(points)
    if require_complete and len(series.points) < minimum_points:
        from app.services.fund_factors import FundFactorInput

        return FundFactorInput(fund_code=code, fund_name=name)
    selected = (
        series.points[-minimum_points:]
        if require_complete
        else series.points
    )
    return factor_input_from_navs(code, name, [value for _, value in selected])
