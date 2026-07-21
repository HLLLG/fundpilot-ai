"""从一段 NAV 切片算因子原始值（动量/Calmar/回撤）。

模块2（持仓不在榜的净值兜底）与模块3（因子 IC 回测）共用，避免重复。
现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / Factor IC、PIT 与量化证据」。

窗口口径与排行榜一致：3 月≈60、6 月≈120、1 年≈250 交易日；
最大回撤复用模块1 `portfolio_risk_metrics._max_drawdown` 保口径一致。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
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


def factor_input_from_navs(
    code: str,
    name: str,
    navs: list[float],
    *,
    feature_as_of: str | None = None,
    feature_observed_at: str | None = None,
    feature_source: str | None = None,
    return_coverage: float | None = None,
    nav_age_trading_days: int | None = None,
    feature_freshness: str = "unknown",
    feature_max_age_trading_days: int | None = None,
):
    """从一段升序净值算 FundFactorInput（return_3m/6m/1y + 1年最大回撤；规模 None）。"""
    from app.services.fund_factors import FundFactorInput
    from app.services.portfolio_risk_metrics import _max_drawdown

    from app.services.fund_type_factors import compute_nav_feature_library

    if len(navs) < 2:
        return FundFactorInput(
            fund_code=code,
            fund_name=name,
            feature_as_of=feature_as_of,
            feature_observed_at=feature_observed_at,
            feature_source=feature_source,
            return_coverage=return_coverage,
            nav_age_trading_days=nav_age_trading_days,
            feature_freshness=feature_freshness,
            feature_max_age_trading_days=feature_max_age_trading_days,
        )
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
        feature_as_of=feature_as_of,
        feature_observed_at=feature_observed_at,
        feature_source=feature_source,
        return_coverage=return_coverage,
        nav_age_trading_days=nav_age_trading_days,
        feature_freshness=feature_freshness,
        feature_max_age_trading_days=feature_max_age_trading_days,
        # 先计算与类型无关的 NAV 特征库；真正允许使用哪些键由研究模型中的
        # peer_group + qualification 决定，避免在取数层猜基金类型。
        typed_feature_values=compute_nav_feature_library(
            [float(value) for value in navs]
        ),
        typed_feature_meta={
            "schema_version": "fund_type_factors.v1",
            "source": "point_in_time_nav",
            "lookback_points": len(navs),
            "feature_as_of": feature_as_of,
            "observed_at": feature_observed_at,
            "observation_source": feature_source,
            "return_coverage": return_coverage,
            "nav_age_trading_days": nav_age_trading_days,
            "freshness_status": feature_freshness,
            "max_age_trading_days": feature_max_age_trading_days,
        },
    )


def _trading_day_age(feature_as_of: str, effective_trade_date: str) -> int | None:
    """Count trading dates after the feature NAV through the decision date."""

    try:
        feature_day = date.fromisoformat(feature_as_of[:10])
        effective_day = date.fromisoformat(effective_trade_date[:10])
    except (TypeError, ValueError):
        return None
    if feature_day > effective_day:
        return -1

    from app.services.trade_calendar_cache import get_trade_date_set

    trade_dates = get_trade_date_set()
    calendar_min = min(trade_dates) if trade_dates else None
    calendar_max = max(trade_dates) if trade_dates else None

    def is_trading_day(day: date) -> bool:
        encoded = day.isoformat()
        if (
            trade_dates
            and calendar_min is not None
            and calendar_max is not None
            and calendar_min <= encoded <= calendar_max
        ):
            return encoded in trade_dates
        # A stale/unavailable exchange calendar must not turn all newer weekdays
        # into age zero. Weekdays are a conservative fallback (holidays may make
        # age slightly larger, causing fail-closed rather than false freshness).
        return day.weekday() < 5

    count = 0
    cursor = feature_day
    # A corrupt multi-year stale series should fail cheaply without an unbounded loop.
    for _ in range(3700):
        if cursor >= effective_day:
            return count
        cursor += timedelta(days=1)
        if is_trading_day(cursor):
            count += 1
    return None


def factor_input_from_points(
    code: str,
    name: str,
    points: list[Any],
    *,
    require_complete: bool = False,
    minimum_points: int = 250,
    effective_trade_date: str | None = None,
    fund_type: str | None = None,
    observed_at: str | None = None,
    source: str = "fund_nav_history",
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
    feature_as_of = selected[-1][0] if selected else None
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    valid_transitions = series.daily_return_points + series.nav_ratio_points
    transition_attempts = valid_transitions + series.invalid_points
    valid_return_coverage = (
        valid_transitions / transition_attempts if transition_attempts else 0.0
    )
    normalized_type = str(fund_type or "").strip().lower()
    if not normalized_type and "qdii" in str(name or "").lower():
        normalized_type = "qdii"
    max_age = 2 if normalized_type == "qdii" else 1
    nav_age = (
        _trading_day_age(feature_as_of, effective_trade_date)
        if feature_as_of and effective_trade_date
        else None
    )
    freshness = "unknown"
    if effective_trade_date:
        freshness = (
            "fresh"
            if nav_age is not None
            and 0 <= nav_age <= max_age
            and valid_return_coverage >= 0.90
            else "insufficient"
        )
    result = factor_input_from_navs(
        code,
        name,
        [value for _, value in selected],
        feature_as_of=feature_as_of,
        feature_observed_at=observed,
        feature_source=source,
        return_coverage=round(valid_return_coverage, 4),
        nav_age_trading_days=nav_age,
        feature_freshness=freshness,
        feature_max_age_trading_days=max_age,
    )
    if freshness != "insufficient":
        return result

    # Preserve the audit trail but fail closed: stale or low-coverage target NAV
    # must not retain common or type-specific factor values.
    return replace(
        result,
        return_3m_percent=None,
        return_6m_percent=None,
        return_1y_percent=None,
        max_drawdown_1y_percent=None,
        typed_feature_values={},
    )
