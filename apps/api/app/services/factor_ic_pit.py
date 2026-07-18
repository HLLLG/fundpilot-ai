"""Point-in-time fund cohorts and robust Factor IC research helpers.

This module is deliberately storage agnostic.  A caller supplies immutable
universe snapshots and a NAV panel; every anchor is matched to the newest
snapshot that was *available* on that date.  A snapshot dated in the past but
published later is therefore never allowed to leak into an earlier cohort.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from app.services.factor_ic_backtest import (
    FACTOR_ORDER,
    SINGLE_FACTORS,
    NavPoint,
    _aggregate,
    _nav_asof,
    _navs_upto,
    _newey_west_standard_error,
    _rank_ic_for_period,
    _raw_factors_at,
)
from app.services.fund_type_factors import (
    TYPE_FACTOR_SCHEMA_VERSION,
    compute_type_factor_values,
    type_factor_keys,
)
from app.services.fund_factors import (
    FACTOR_WEIGHTS,
    _composite_z,
    _factor_stats,
    _zscore,
)

DEFAULT_MAX_SNAPSHOT_AGE_DAYS = 7
DEFAULT_WALK_FORWARD_FOLDS = 5
DEFAULT_EMBARGO_DAYS = 20
MIN_POINT_IN_TIME_ANCHORS = 24
MIN_POINT_IN_TIME_COVERAGE = 0.90
MIN_QUALIFIED_PERIODS = 30
MIN_OOS_IC = 0.02
MIN_ICIR = 0.20
MIN_SAME_DIRECTION_FOLDS = 4
MAX_FDR_Q_VALUE = 0.10
MIN_ECONOMIC_PERIODS = 36
MIN_ECONOMIC_COVERAGE = 0.80
MIN_TOP_NET_POSITIVE_RATIO = 0.55
DEFAULT_COST_RATE = 0.005
DEFAULT_NAV_PUBLICATION_LAG_TRADING_DAYS = 1
QDII_NAV_PUBLICATION_LAG_TRADING_DAYS = 2
EXECUTION_ENTRY_OFFSET_TRADING_DAYS = 1

_SEGMENT_LABELS = {
    "gp": "主动股票",
    "hh": "混合基金",
    "zq": "债券基金",
    "zs": "指数基金",
    "qdii": "QDII",
    "fof": "FOF",
    "unknown": "未分类",
}


def nav_publication_lag_trading_days(fund_type: object) -> int:
    """没有 NAV observation PIT 时采用的保守信息可得滞后。"""
    return (
        QDII_NAV_PUBLICATION_LAG_TRADING_DAYS
        if str(fund_type or "").strip().lower() == "qdii"
        else DEFAULT_NAV_PUBLICATION_LAG_TRADING_DAYS
    )


def nav_information_window(
    calendar: list[str],
    anchor_index: int,
    *,
    fund_type: object,
    horizon: int,
    nav_observation_pit: bool = False,
) -> dict[str, Any] | None:
    """给出信号可见 NAV 截止日与下一可执行 NAV 的目标日期。"""
    lag = 0 if nav_observation_pit else nav_publication_lag_trading_days(fund_type)
    factor_index = anchor_index - lag
    entry_index = anchor_index + EXECUTION_ENTRY_OFFSET_TRADING_DAYS
    exit_index = entry_index + horizon
    if factor_index < 0 or entry_index >= len(calendar) or exit_index >= len(calendar):
        return None
    return {
        "publication_lag_trading_days": lag,
        "factor_as_of": calendar[factor_index],
        "entry_target_date": calendar[entry_index],
        "exit_target_date": calendar[exit_index],
        "entry_offset_trading_days": EXECUTION_ENTRY_OFFSET_TRADING_DAYS,
        "holding_horizon_trading_days": horizon,
    }


def _nav_on_or_after(
    dates: list[str],
    navs: list[float],
    target_date: str,
    *,
    max_delay_days: int,
) -> float | None:
    """返回目标日之后第一笔可执行 NAV，不允许拿目标日前旧净值顶替。"""
    index = bisect.bisect_left(dates, target_date)
    if index >= len(dates):
        return None
    try:
        if (
            _parse_date(dates[index], field="nav date")
            - _parse_date(target_date, field="target date")
        ).days > max_delay_days:
            return None
    except ValueError:
        return None
    nav = navs[index]
    return nav if nav > 0 else None


def _observation_date(value: str | None, *, nav_date: str) -> str | None:
    """Return the real collector date; a missing timestamp stays unavailable."""

    if not value:
        return None
    fallback = _parse_date(nav_date, field="nav date")
    return _parse_datetime(
        value,
        fallback=fallback,
        field="NAV observed_at",
    ).date().isoformat()


def _observed_navs_upto(
    dates: list[str],
    navs: list[float],
    observed_dates: list[str | None],
    target_date: str,
    lookback: int,
) -> list[float]:
    eligible = [
        nav
        for day, nav, observed in zip(dates, navs, observed_dates)
        if day <= target_date and observed is not None and observed <= target_date
    ]
    return eligible[-lookback:]


def _observed_nav_asof(
    dates: list[str],
    navs: list[float],
    observed_dates: list[str | None],
    target_date: str,
    *,
    max_stale_days: int,
) -> float | None:
    eligible = [
        (day, nav)
        for day, nav, observed in zip(dates, navs, observed_dates)
        if day <= target_date and observed is not None and observed <= target_date
    ]
    if not eligible:
        return None
    filtered_dates = [day for day, _ in eligible]
    filtered_navs = [nav for _, nav in eligible]
    return _nav_asof(
        filtered_dates,
        filtered_navs,
        target_date,
        max_stale_days=max_stale_days,
    )


@dataclass(frozen=True)
class PointInTimeMember:
    fund_code: str
    fund_type: str = "unknown"
    available_at: datetime | None = None


@dataclass(frozen=True)
class PointInTimeUniverseSnapshot:
    snapshot_id: str
    snapshot_date: date
    available_at: datetime
    members: tuple[PointInTimeMember, ...]


def _parse_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 ISO 日期") from exc


def _parse_datetime(value: Any, *, fallback: date, field: str) -> datetime:
    if value in (None, ""):
        return datetime.combine(fallback, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是 ISO 时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_members(raw: Any, *, snapshot_date: date) -> tuple[PointInTimeMember, ...]:
    if isinstance(raw, dict):
        rows: Iterable[Any] = (
            {
                "fund_code": code,
                **(value if isinstance(value, dict) else {"fund_type": value}),
            }
            for code, value in raw.items()
        )
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = ()
    members: dict[str, PointInTimeMember] = {}
    for row in rows:
        if isinstance(row, str):
            row = {"fund_code": row}
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or row.get("code") or "").strip()
        if not code:
            continue
        member_available = row.get("available_at")
        available_at = (
            _parse_datetime(
                member_available,
                fallback=snapshot_date,
                field="member.available_at",
            )
            if member_available not in (None, "")
            else None
        )
        members[code] = PointInTimeMember(
            fund_code=code,
            fund_type=str(
                row.get("fund_type") or row.get("segment") or "unknown"
            ).lower(),
            available_at=available_at,
        )
    return tuple(members[code] for code in sorted(members))


def normalize_universe_snapshots(
    snapshots: Iterable[dict[str, Any] | PointInTimeUniverseSnapshot],
) -> list[PointInTimeUniverseSnapshot]:
    """Normalize storage rows and reject internally time-travelling snapshots."""
    normalized: list[PointInTimeUniverseSnapshot] = []
    for index, raw in enumerate(snapshots):
        if isinstance(raw, PointInTimeUniverseSnapshot):
            snapshot = raw
        elif isinstance(raw, dict):
            snapshot_date = _parse_date(
                raw.get("snapshot_date") or raw.get("as_of_date"),
                field="snapshot_date",
            )
            available_at = _parse_datetime(
                raw.get("available_at")
                or raw.get("captured_at")
                or raw.get("created_at"),
                fallback=snapshot_date,
                field="available_at",
            )
            snapshot = PointInTimeUniverseSnapshot(
                snapshot_id=str(raw.get("snapshot_id") or f"snapshot-{index}"),
                snapshot_date=snapshot_date,
                available_at=available_at,
                members=_normalise_members(raw.get("members"), snapshot_date=snapshot_date),
            )
        else:
            continue
        # A member learned after the snapshot was available cannot belong to it.
        members = tuple(
            member
            for member in snapshot.members
            if member.available_at is None or member.available_at <= snapshot.available_at
        )
        normalized.append(
            PointInTimeUniverseSnapshot(
                snapshot_id=snapshot.snapshot_id,
                snapshot_date=snapshot.snapshot_date,
                available_at=snapshot.available_at,
                members=members,
            )
        )
    return sorted(
        normalized,
        key=lambda row: (row.snapshot_date, row.available_at, row.snapshot_id),
    )


def select_asof_snapshot(
    snapshots: Iterable[dict[str, Any] | PointInTimeUniverseSnapshot],
    anchor_date: str | date,
    *,
    max_age_days: int = DEFAULT_MAX_SNAPSHOT_AGE_DAYS,
) -> PointInTimeUniverseSnapshot | None:
    """Return the latest snapshot observable at ``anchor_date`` (never future)."""
    anchor = _parse_date(anchor_date, field="anchor_date")
    if max_age_days < 0:
        raise ValueError("max_age_days 不可为负")
    snapshot_rows = list(snapshots)
    normalized = (
        sorted(
            snapshot_rows,
            key=lambda row: (row.snapshot_date, row.available_at, row.snapshot_id),
        )
        if all(isinstance(row, PointInTimeUniverseSnapshot) for row in snapshot_rows)
        else normalize_universe_snapshots(snapshot_rows)
    )
    candidates = [
        row
        for row in normalized
        if row.snapshot_date <= anchor
        and row.available_at.date() <= anchor
        and 0 <= (anchor - row.snapshot_date).days <= max_age_days
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row.snapshot_date, row.available_at, row.snapshot_id))


def build_anchor_cohorts(
    *,
    anchors: Iterable[str],
    snapshots: Iterable[dict[str, Any] | PointInTimeUniverseSnapshot],
    max_age_days: int = DEFAULT_MAX_SNAPSHOT_AGE_DAYS,
) -> tuple[dict[str, PointInTimeUniverseSnapshot], dict[str, Any]]:
    """Freeze one observable membership cohort for every research anchor."""
    normalized = normalize_universe_snapshots(snapshots)
    anchor_list = list(dict.fromkeys(str(value) for value in anchors))
    selected: dict[str, PointInTimeUniverseSnapshot] = {}
    ages: list[int] = []
    for anchor in anchor_list:
        row = select_asof_snapshot(normalized, anchor, max_age_days=max_age_days)
        if row is None:
            continue
        selected[anchor] = row
        ages.append((_parse_date(anchor, field="anchor") - row.snapshot_date).days)
    total = len(anchor_list)
    valid = len(selected)
    return selected, {
        "anchor_count": total,
        "effective_anchor_count": valid,
        "anchor_coverage_rate": round(valid / total, 4) if total else 0.0,
        "missing_anchor_count": total - valid,
        "max_snapshot_age_days": max_age_days,
        "observed_max_snapshot_age_days": max(ages) if ages else None,
        "future_snapshot_violations": 0,
    }


def benjamini_hochberg(p_values: dict[str, float | None]) -> dict[str, float | None]:
    """Benjamini-Hochberg adjusted q-values, monotone and capped at one."""
    valid = sorted(
        (max(0.0, min(1.0, float(value))), key)
        for key, value in p_values.items()
        if value is not None and math.isfinite(float(value))
    )
    result: dict[str, float | None] = {key: None for key in p_values}
    m = len(valid)
    running = 1.0
    for reverse_index in range(m - 1, -1, -1):
        p_value, key = valid[reverse_index]
        rank = reverse_index + 1
        running = min(running, p_value * m / rank)
        result[key] = round(min(1.0, running), 6)
    return result


def _two_sided_normal_p_value(t_stat: float | None) -> float | None:
    if t_stat is None or not math.isfinite(t_stat):
        return None
    return math.erfc(abs(t_stat) / math.sqrt(2.0))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _economic_period(
    *,
    anchor: str,
    factor_values: dict[str, float | None],
    forward_returns: dict[str, float | None],
    eligible_count: int,
    min_cross_section: int,
    entry_target_date: str,
    exit_target_date: str,
) -> dict[str, Any] | None:
    """构造单锚点同类相对收益组合，因子值统一按越高越好。"""
    returns = [
        float(value)
        for value in forward_returns.values()
        if value is not None and math.isfinite(float(value))
    ]
    peer_median = _percentile(returns, 0.5)
    if peer_median is None:
        return None
    aligned = sorted(
        (
            str(code),
            float(factor_value),
            float(forward_returns[code]) - peer_median,
        )
        for code, factor_value in factor_values.items()
        if factor_value is not None
        and code in forward_returns
        and forward_returns[code] is not None
        and math.isfinite(float(factor_value))
        and math.isfinite(float(forward_returns[code]))
    )
    if len(aligned) < min_cross_section:
        return None
    aligned.sort(key=lambda row: (row[1], row[0]))
    quantile_size = max(1, len(aligned) // 5)
    bottom = aligned[:quantile_size]
    top = aligned[-quantile_size:]
    top_mean = _mean([row[2] for row in top])
    bottom_mean = _mean([row[2] for row in bottom])
    if top_mean is None or bottom_mean is None:
        return None
    quintiles: list[float | None] = []
    for quintile in range(5):
        group = [
            row[2]
            for index, row in enumerate(aligned)
            if min(4, index * 5 // len(aligned)) == quintile
        ]
        quintiles.append(_mean(group))
    return {
        "anchor": anchor,
        "entry_target_date": entry_target_date,
        "exit_target_date": exit_target_date,
        "valid_count": len(aligned),
        "eligible_count": max(eligible_count, len(aligned)),
        "top_relative_return": top_mean,
        "bottom_relative_return": bottom_mean,
        "spread": top_mean - bottom_mean,
        "quintile_relative_returns": quintiles,
        "top_codes": [row[0] for row in top],
    }


def _economic_walk_forward(
    observations: list[dict[str, Any]],
    *,
    folds: int,
    embargo_days: int,
    trading_calendar: list[str],
) -> dict[str, Any]:
    raw = expanding_walk_forward(
        [
            (str(row["anchor"]), float(row["spread"]))
            for row in observations
            if row.get("spread") is not None
        ],
        folds=folds,
        embargo_days=embargo_days,
        trading_calendar=trading_calendar,
    )
    fold_rows: list[dict[str, Any]] = []
    for row in raw.get("folds") or []:
        normalized = dict(row)
        if "mean_ic" in normalized:
            normalized["mean_spread"] = normalized.pop("mean_ic")
        fold_rows.append(normalized)
    return {
        "method": "expanding_walk_forward_economic_spread",
        "fold_count": raw.get("fold_count"),
        "valid_fold_count": raw.get("valid_fold_count"),
        "embargo_trading_days": raw.get("embargo_trading_days"),
        "oos_mean_spread": raw.get("oos_mean_ic"),
        "same_direction_folds": raw.get("same_direction_folds"),
        "folds": fold_rows,
    }


def aggregate_economic_significance(
    observations: list[dict[str, Any]],
    *,
    hac_lags: int,
    walk_forward_folds: int,
    embargo_days: int,
    trading_calendar: list[str],
    point_in_time_scope: str = "membership_only",
    nav_revision_pit: bool = False,
    availability_basis: str | None = None,
    revision_policy: str | None = None,
) -> dict[str, Any]:
    """聚合同类相对收益的经济显著性；输入为空时严格 fail closed。"""
    ordered = sorted(observations, key=lambda row: str(row.get("anchor") or ""))
    spreads = [float(row["spread"]) for row in ordered if row.get("spread") is not None]
    tops = [
        float(row["top_relative_return"])
        for row in ordered
        if row.get("top_relative_return") is not None
    ]
    bottoms = [
        float(row["bottom_relative_return"])
        for row in ordered
        if row.get("bottom_relative_return") is not None
    ]
    spread = _mean(spreads)
    standard_error = _newey_west_standard_error(spreads, hac_lags)
    t_stat = (
        spread / standard_error
        if spread is not None and standard_error is not None and standard_error > 1e-12
        else None
    )
    ci_low = spread - 1.96 * standard_error if spread is not None and standard_error is not None else None
    ci_high = spread + 1.96 * standard_error if spread is not None and standard_error is not None else None
    quintiles: list[float | None] = []
    for index in range(5):
        values = [
            float(row["quintile_relative_returns"][index])
            for row in ordered
            if isinstance(row.get("quintile_relative_returns"), list)
            and len(row["quintile_relative_returns"]) == 5
            and row["quintile_relative_returns"][index] is not None
        ]
        quintiles.append(_mean(values))
    comparable_pairs = [
        (left, right)
        for left, right in zip(quintiles, quintiles[1:])
        if left is not None and right is not None
    ]
    monotonicity = (
        sum(right >= left for left, right in comparable_pairs) / len(comparable_pairs)
        if comparable_pairs
        else None
    )
    turnover_values: list[float] = []
    previous: set[str] | None = None
    for row in ordered:
        current = {str(code) for code in row.get("top_codes") or []}
        if previous and current:
            turnover_values.append(1.0 - len(previous & current) / min(len(previous), len(current)))
        previous = current
    top_mean = _mean(tops)
    bottom_mean = _mean(bottoms)
    total_valid = sum(int(row.get("valid_count") or 0) for row in ordered)
    total_eligible = sum(int(row.get("eligible_count") or 0) for row in ordered)
    coverage_rate = total_valid / total_eligible if total_eligible else 0.0
    top_net_positive_ratio = (
        sum(value - DEFAULT_COST_RATE > 0 for value in tops) / len(tops)
        if tops
        else None
    )
    walk = _economic_walk_forward(
        ordered,
        folds=walk_forward_folds,
        embargo_days=embargo_days,
        trading_calendar=trading_calendar,
    )
    cost_scenarios = [
        {
            "fee_rate": fee,
            "top_net_relative_return": (
                round(top_mean - fee, 6) if top_mean is not None else None
            ),
            # 多空两端各计一次换手成本；实际产品使用 top-only 指标。
            "spread_net_return": (
                round(spread - 2 * fee, 6) if spread is not None else None
            ),
        }
        for fee in (0.0, 0.005, 0.01)
    ]
    result = {
        "schema_version": "factor_economic_significance.v1",
        "label_type": "peer_group_relative_total_return",
        "benchmark": "same_segment_cross_section_median",
        "point_in_time_scope": point_in_time_scope,
        "nav_revision_pit": nav_revision_pit,
        "entry_rule": "next_trading_day_first_available_nav",
        "entry_offset_trading_days": EXECUTION_ENTRY_OFFSET_TRADING_DAYS,
        "quantile_count": 5,
        "period_count": len(spreads),
        "valid_observation_count": total_valid,
        "peer_relative_coverage_rate": round(coverage_rate, 4),
        "top_quantile_relative_return": round(top_mean, 6) if top_mean is not None else None,
        "bottom_quantile_relative_return": round(bottom_mean, 6) if bottom_mean is not None else None,
        "top_bottom_spread": round(spread, 6) if spread is not None else None,
        "hac_lags": hac_lags,
        "standard_error": round(standard_error, 6) if standard_error is not None else None,
        "t_stat": round(t_stat, 3) if t_stat is not None else None,
        "ci_low": round(ci_low, 6) if ci_low is not None else None,
        "ci_high": round(ci_high, 6) if ci_high is not None else None,
        "top_net_positive_ratio": (
            round(top_net_positive_ratio, 4) if top_net_positive_ratio is not None else None
        ),
        "top_net_positive_cost_rate": DEFAULT_COST_RATE,
        "quintile_mean_relative_returns": [
            round(value, 6) if value is not None else None for value in quintiles
        ],
        "quintile_monotonicity": round(monotonicity, 4) if monotonicity is not None else None,
        "turnover": round(_mean(turnover_values), 4) if turnover_values else None,
        "break_even_fee_rate": round(max(0.0, top_mean), 6) if top_mean is not None else None,
        "cost_scenarios": cost_scenarios,
        "top_relative_return_p10": round(_percentile(tops, 0.10), 6) if tops else None,
        "top_relative_return_worst": round(min(tops), 6) if tops else None,
        "downside_distribution_unit": "anchor_top_quantile_mean",
        "walk_forward": walk,
    }
    if nav_revision_pit:
        result["availability_basis"] = availability_basis
        result["revision_policy"] = revision_policy
    result["qualified"] = economic_significance_qualified(result)
    return result


def economic_significance_qualified(row: dict[str, Any]) -> bool:
    walk = row.get("walk_forward") or {}
    scenario = next(
        (
            item
            for item in row.get("cost_scenarios") or []
            if isinstance(item, dict) and float(item.get("fee_rate") or -1) == DEFAULT_COST_RATE
        ),
        {},
    )
    return bool(
        int(row.get("period_count") or 0) >= MIN_ECONOMIC_PERIODS
        and float(row.get("peer_relative_coverage_rate") or 0) >= MIN_ECONOMIC_COVERAGE
        and row.get("top_bottom_spread") is not None
        and float(row["top_bottom_spread"]) > 0
        and row.get("ci_low") is not None
        and float(row["ci_low"]) > 0
        and scenario.get("top_net_relative_return") is not None
        and float(scenario["top_net_relative_return"]) > 0
        and row.get("top_net_positive_ratio") is not None
        and float(row["top_net_positive_ratio"]) >= MIN_TOP_NET_POSITIVE_RATIO
        and row.get("quintile_monotonicity") is not None
        and float(row["quintile_monotonicity"]) >= 0.50
        and int(walk.get("valid_fold_count") or 0) == DEFAULT_WALK_FORWARD_FOLDS
        and int(walk.get("same_direction_folds") or 0) >= MIN_SAME_DIRECTION_FOLDS
        and walk.get("oos_mean_spread") is not None
        and float(walk["oos_mean_spread"]) > 0
    )


def expanding_walk_forward(
    observations: list[tuple[str, float]],
    *,
    folds: int = DEFAULT_WALK_FORWARD_FOLDS,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    trading_calendar: list[str] | None = None,
) -> dict[str, Any]:
    """Expanding-window validation with a trading-day embargo before each fold.

    When the complete NAV calendar is available it must be supplied.  The
    fallback treats observation dates themselves as the trading calendar,
    which is conservative for sparsely sampled IC observations.
    """
    if folds <= 0:
        raise ValueError("folds 必须为正数")
    if embargo_days < 0:
        raise ValueError("embargo_days 不可为负")
    ordered = sorted(
        (
            (_parse_date(day, field="observation date"), float(value))
            for day, value in observations
        ),
        key=lambda item: item[0],
    )
    calendar = sorted(
        {
            _parse_date(day, field="trading calendar date")
            for day in (trading_calendar or [day.isoformat() for day, _ in ordered])
        }
    )
    n = len(ordered)
    initial = max(5, n // 3)
    remainder = max(0, n - initial)
    rows: list[dict[str, Any]] = []
    oos_values: list[float] = []
    for fold_index in range(folds):
        start = initial + (remainder * fold_index) // folds
        end = initial + (remainder * (fold_index + 1)) // folds
        test = ordered[start:end]
        if not test:
            rows.append({"fold": fold_index + 1, "valid": False, "reason": "empty_test"})
            continue
        test_start = test[0][0]
        try:
            test_position = calendar.index(test_start)
        except ValueError:
            test_position = sum(day < test_start for day in calendar)
        cutoff_position = test_position - embargo_days - 1
        cutoff = calendar[cutoff_position] if cutoff_position >= 0 else None
        train = [
            item
            for item in ordered[:start]
            if cutoff is not None and item[0] <= cutoff
        ]
        if len(train) < 5:
            rows.append(
                {
                    "fold": fold_index + 1,
                    "valid": False,
                    "reason": "insufficient_train_after_embargo",
                    "train_count": len(train),
                    "test_count": len(test),
                    "test_start": test_start.isoformat(),
                    "embargo_cutoff": cutoff.isoformat() if cutoff is not None else None,
                }
            )
            continue
        test_mean = sum(value for _, value in test) / len(test)
        oos_values.extend(value for _, value in test)
        rows.append(
            {
                "fold": fold_index + 1,
                "valid": True,
                "train_count": len(train),
                "train_end": train[-1][0].isoformat(),
                "test_count": len(test),
                "test_start": test_start.isoformat(),
                "test_end": test[-1][0].isoformat(),
                "embargo_trading_days": embargo_days,
                "mean_ic": round(test_mean, 4),
                "direction": (
                    "positive"
                    if test_mean > 0
                    else "negative" if test_mean < 0 else "flat"
                ),
            }
        )
    oos_mean = sum(oos_values) / len(oos_values) if oos_values else None
    same_direction = sum(
        row.get("valid") and row.get("mean_ic") is not None and row["mean_ic"] > 0
        for row in rows
    )
    return {
        "method": "expanding_walk_forward",
        "fold_count": folds,
        "valid_fold_count": sum(bool(row.get("valid")) for row in rows),
        "embargo_trading_days": embargo_days,
        "oos_mean_ic": round(oos_mean, 4) if oos_mean is not None else None,
        "same_direction_folds": same_direction,
        "folds": rows,
    }


def _qualifies(row: dict[str, Any], *, minimum_embargo_days: int) -> bool:
    walk = row.get("walk_forward") or {}
    economic = row.get("economic_significance") or {}
    return bool(
        int(row.get("n_periods") or 0) >= MIN_QUALIFIED_PERIODS
        and float(walk.get("oos_mean_ic") or -99) >= MIN_OOS_IC
        and float(row.get("icir") or -99) >= MIN_ICIR
        and int(walk.get("valid_fold_count") or 0) == DEFAULT_WALK_FORWARD_FOLDS
        and int(walk.get("embargo_trading_days") or 0) >= minimum_embargo_days
        and int(walk.get("same_direction_folds") or 0) >= MIN_SAME_DIRECTION_FOLDS
        and row.get("ci_low") is not None
        and float(row["ci_low"]) > 0
        and row.get("q_value") is not None
        and float(row["q_value"]) <= MAX_FDR_Q_VALUE
        and economic.get("qualified") is True
        and economic_significance_qualified(economic)
    )


def compute_point_in_time_segmented_ic(
    *,
    nav_panel: dict[str, list[NavPoint]],
    snapshots: Iterable[dict[str, Any] | PointInTimeUniverseSnapshot],
    rebalance_step: int,
    forward_horizons: tuple[int, ...],
    factor_lookback: int,
    min_cross_section: int = 20,
    max_snapshot_age_days: int = DEFAULT_MAX_SNAPSHOT_AGE_DAYS,
    walk_forward_folds: int = DEFAULT_WALK_FORWARD_FOLDS,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    nav_observation_pit: bool = False,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Compute segmented IC using membership frozen independently at each anchor."""
    calendar = sorted({point.date for points in nav_panel.values() for point in points})
    horizons = tuple(sorted({int(value) for value in forward_horizons if int(value) > 0}))
    if not horizons or rebalance_step <= 0 or factor_lookback <= 0:
        raise ValueError("PIT IC 参数非法")
    normalized_snapshots = normalize_universe_snapshots(snapshots)
    first_snapshot_date = (
        normalized_snapshots[0].snapshot_date.isoformat()
        if normalized_snapshots
        else None
    )
    anchor_indexes = [
        index
        for index in range(
            max(
                0,
                factor_lookback
                - 1
                + (0 if nav_observation_pit else QDII_NAV_PUBLICATION_LAG_TRADING_DAYS),
            ),
            len(calendar),
            rebalance_step,
        )
        if index + EXECUTION_ENTRY_OFFSET_TRADING_DAYS < len(calendar)
        and (first_snapshot_date is None or calendar[index] >= first_snapshot_date)
    ]
    anchors = [calendar[index] for index in anchor_indexes]
    cohorts, coverage = build_anchor_cohorts(
        anchors=anchors,
        snapshots=normalized_snapshots,
        max_age_days=max_snapshot_age_days,
    )
    indexed: dict[str, tuple[list[str], list[float], list[str | None]]] = {}
    observation_point_count = 0
    timestamped_observation_point_count = 0
    for code, points in nav_panel.items():
        ordered = sorted(points, key=lambda point: point.date)
        dates = [point.date for point in ordered]
        navs = [point.nav for point in ordered]
        observed_dates = [
            _observation_date(point.observed_at, nav_date=point.date)
            for point in ordered
        ]
        indexed[code] = (dates, navs, observed_dates)
        observation_point_count += len(ordered)
        timestamped_observation_point_count += sum(
            value is not None for value in observed_dates
        )

    series: dict[tuple[str, int, str], list[tuple[str, float]]] = {}
    economic_series: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    segment_members: dict[str, set[str]] = {}
    cohort_memberships = 0
    nav_memberships = 0
    for anchor_index, anchor in zip(anchor_indexes, anchors):
        snapshot = cohorts.get(anchor)
        if snapshot is None:
            continue
        by_segment: dict[str, list[str]] = {}
        for member in snapshot.members:
            cohort_memberships += 1
            if member.fund_code not in indexed:
                continue
            segment = member.fund_type or "unknown"
            stale_days = 7 if segment == "qdii" else 3
            window = nav_information_window(
                calendar,
                anchor_index,
                fund_type=segment,
                horizon=0,
                nav_observation_pit=nav_observation_pit,
            )
            if window is None:
                continue
            dates, navs, observed_dates = indexed[member.fund_code]
            history = (
                _observed_navs_upto(
                    dates,
                    navs,
                    observed_dates,
                    str(window["factor_as_of"]),
                    factor_lookback,
                )
                if nav_observation_pit
                else _navs_upto(
                    dates,
                    navs,
                    str(window["factor_as_of"]),
                    factor_lookback,
                )
            )
            nav_factor_asof = (
                _observed_nav_asof(
                    dates,
                    navs,
                    observed_dates,
                    str(window["factor_as_of"]),
                    max_stale_days=stale_days,
                )
                if nav_observation_pit
                else _nav_asof(
                    dates,
                    navs,
                    str(window["factor_as_of"]),
                    max_stale_days=stale_days,
                )
            )
            nav_entry = _nav_on_or_after(
                dates,
                navs,
                str(window["entry_target_date"]),
                max_delay_days=stale_days,
            )
            if (
                len(history) < factor_lookback
                or nav_factor_asof is None
                or nav_entry is None
            ):
                continue
            nav_memberships += 1
            by_segment.setdefault(segment, []).append(member.fund_code)
            segment_members.setdefault(segment, set()).add(member.fund_code)
        for segment, codes in by_segment.items():
            stale_days = 7 if segment == "qdii" else 3
            maximum_window = nav_information_window(
                calendar,
                anchor_index,
                fund_type=segment,
                horizon=0,
                nav_observation_pit=nav_observation_pit,
            )
            if maximum_window is None:
                continue
            typed_keys = type_factor_keys(segment, benchmark_available=False)
            factor_order = tuple(dict.fromkeys((*FACTOR_ORDER, *typed_keys)))
            raws_by_factor: dict[str, dict[str, float | None]] = {
                factor: {} for factor in (*SINGLE_FACTORS, *typed_keys)
            }
            for code in codes:
                dates, navs, observed_dates = indexed[code]
                history = (
                    _observed_navs_upto(
                        dates,
                        navs,
                        observed_dates,
                        str(maximum_window["factor_as_of"]),
                        factor_lookback,
                    )
                    if nav_observation_pit
                    else _navs_upto(
                        dates,
                        navs,
                        str(maximum_window["factor_as_of"]),
                        factor_lookback,
                    )
                )
                if len(history) >= factor_lookback:
                    raws = {
                        **_raw_factors_at(history),
                        **compute_type_factor_values(segment, history),
                    }
                else:
                    raws = {
                        factor: None for factor in (*SINGLE_FACTORS, *typed_keys)
                    }
                for factor in (*SINGLE_FACTORS, *typed_keys):
                    raws_by_factor[factor][code] = raws.get(factor)
            stats = {
                factor: _factor_stats(list(raws_by_factor[factor].values()))
                for factor in SINGLE_FACTORS
            }
            composite: dict[str, float | None] = {}
            for code in codes:
                factor_z = {
                    factor: _zscore(raws_by_factor[factor].get(code), stats[factor])
                    for factor in SINGLE_FACTORS
                }
                weighted = {key: factor_z.get(key) for key in FACTOR_WEIGHTS}
                composite[code] = _composite_z(weighted)
            values_by_factor = {**raws_by_factor, "composite": composite}
            for horizon in horizons:
                window = nav_information_window(
                    calendar,
                    anchor_index,
                    fund_type=segment,
                    horizon=horizon,
                    nav_observation_pit=nav_observation_pit,
                )
                if window is None:
                    continue
                forward_returns: dict[str, float | None] = {}
                for code in codes:
                    dates, navs, _observed_dates = indexed[code]
                    nav_t = _nav_on_or_after(
                        dates,
                        navs,
                        str(window["entry_target_date"]),
                        max_delay_days=stale_days,
                    )
                    nav_fwd = _nav_on_or_after(
                        dates,
                        navs,
                        str(window["exit_target_date"]),
                        max_delay_days=stale_days,
                    )
                    forward_returns[code] = (
                        nav_fwd / nav_t - 1.0 if nav_t and nav_fwd and nav_t > 0 else None
                    )
                for factor in factor_order:
                    ic = _rank_ic_for_period(
                        values_by_factor[factor],
                        forward_returns,
                        min_cross_section=min_cross_section,
                    )
                    if ic is not None:
                        series.setdefault((segment, horizon, factor), []).append((anchor, ic))
                    economic = _economic_period(
                        anchor=anchor,
                        factor_values=values_by_factor[factor],
                        forward_returns=forward_returns,
                        eligible_count=len(codes),
                        min_cross_section=min_cross_section,
                        entry_target_date=str(window["entry_target_date"]),
                        exit_target_date=str(window["exit_target_date"]),
                    )
                    if economic is not None:
                        economic_series.setdefault(
                            (segment, horizon, factor), []
                        ).append(economic)

    coverage["cohort_membership_count"] = cohort_memberships
    coverage["nav_covered_membership_count"] = nav_memberships
    coverage["cohort_nav_coverage_rate"] = (
        round(nav_memberships / cohort_memberships, 4) if cohort_memberships else 0.0
    )
    point_in_time_scope = (
        "nav_observation_pit" if nav_observation_pit else "membership_only"
    )
    coverage["point_in_time_scope"] = point_in_time_scope
    coverage["nav_revision_pit"] = nav_observation_pit
    coverage["nav_publication_lag_trading_days"] = {
        "default": 0 if nav_observation_pit else DEFAULT_NAV_PUBLICATION_LAG_TRADING_DAYS,
        "qdii": 0 if nav_observation_pit else QDII_NAV_PUBLICATION_LAG_TRADING_DAYS,
    }
    coverage["observation_timestamp_coverage_rate"] = (
        round(timestamped_observation_point_count / observation_point_count, 4)
        if observation_point_count
        else 0.0
    )
    if nav_observation_pit:
        coverage["availability_basis"] = "collector_first_observed_at"
        coverage["revision_policy"] = "first_observed_value"
    coverage["execution_entry_offset_trading_days"] = (
        EXECUTION_ENTRY_OFFSET_TRADING_DAYS
    )
    mature_anchor_count_by_segment_horizon = {
        segment: {
            str(horizon): sum(
                anchor in cohorts
                and nav_information_window(
                    calendar,
                    anchor_index,
                    fund_type=segment,
                    horizon=horizon,
                    nav_observation_pit=nav_observation_pit,
                )
                is not None
                for anchor_index, anchor in zip(anchor_indexes, anchors)
            )
            for horizon in horizons
        }
        for segment in segment_members
    }
    # The model-wide maturity gate is deliberately conservative: when QDII or
    # another segment needs a longer publication lag, the shared PIT contract
    # must not become ready from a shorter-lag segment alone.
    coverage["mature_anchor_count_by_horizon"] = {
        str(horizon): min(
            (
                counts[str(horizon)]
                for counts in mature_anchor_count_by_segment_horizon.values()
            ),
            default=0,
        )
        for horizon in horizons
    }
    effective_anchor_count = int(coverage["effective_anchor_count"] or 0)
    coverage["mature_anchor_coverage_rate_by_horizon"] = {
        key: (
            round(count / effective_anchor_count, 4)
            if effective_anchor_count
            else 0.0
        )
        for key, count in coverage["mature_anchor_count_by_horizon"].items()
    }
    coverage["horizon_ready"] = {
        key: count >= MIN_POINT_IN_TIME_ANCHORS
        for key, count in coverage["mature_anchor_count_by_horizon"].items()
    }
    primary_maturity_horizon = "20" if 20 in horizons else str(min(horizons))
    coverage["primary_maturity_horizon"] = int(primary_maturity_horizon)
    coverage["ready"] = bool(
        coverage["effective_anchor_count"] >= MIN_POINT_IN_TIME_ANCHORS
        and coverage["anchor_coverage_rate"] >= MIN_POINT_IN_TIME_COVERAGE
        and coverage["cohort_nav_coverage_rate"] >= MIN_POINT_IN_TIME_COVERAGE
        and coverage["horizon_ready"].get(primary_maturity_horizon) is True
        and (
            not nav_observation_pit
            or coverage["observation_timestamp_coverage_rate"] == 1.0
        )
    )

    output: dict[str, dict[str, Any]] = {}
    all_rows: list[tuple[str, dict[str, Any]]] = []
    for segment in sorted(segment_members):
        horizon_rows: dict[str, Any] = {}
        typed_keys = type_factor_keys(segment, benchmark_available=False)
        factor_order = tuple(dict.fromkeys((*FACTOR_ORDER, *typed_keys)))
        for horizon in horizons:
            mature_anchor_count = mature_anchor_count_by_segment_horizon.get(
                segment, {}
            ).get(str(horizon), 0)
            rows: list[dict[str, Any]] = []
            hac_lags = max(0, (horizon - 1) // max(1, rebalance_step))
            for factor in factor_order:
                observations = series.get((segment, horizon, factor), [])
                stats = _aggregate(
                    factor,
                    [value for _, value in observations],
                    hac_lags=hac_lags,
                )
                row = {
                    key: value
                    for key, value in stats.__dict__.items()
                    if key != "ic_series"
                }
                row["p_value"] = _two_sided_normal_p_value(stats.t_stat)
                row["walk_forward"] = expanding_walk_forward(
                    observations,
                    folds=walk_forward_folds,
                    embargo_days=max(embargo_days, horizon),
                    trading_calendar=calendar,
                )
                row["economic_significance"] = aggregate_economic_significance(
                    economic_series.get((segment, horizon, factor), []),
                    hac_lags=hac_lags,
                    walk_forward_folds=walk_forward_folds,
                    embargo_days=max(embargo_days, horizon),
                    trading_calendar=calendar,
                    point_in_time_scope=point_in_time_scope,
                    nav_revision_pit=nav_observation_pit,
                    availability_basis=(
                        "collector_first_observed_at"
                        if nav_observation_pit
                        else None
                    ),
                    revision_policy=(
                        "first_observed_value" if nav_observation_pit else None
                    ),
                )
                row["factor_family"] = (
                    "fund_type_specific" if factor in typed_keys else "common"
                )
                rows.append(row)
                all_rows.append((f"{segment}:{horizon}:{factor}", row))
            horizon_rows[str(horizon)] = {
                "available": any(int(row.get("n_periods") or 0) > 0 for row in rows),
                "universe_size": len(segment_members[segment]),
                "rebalance_count": max(
                    (int(row.get("n_periods") or 0) for row in rows),
                    default=0,
                ),
                "maturity": {
                    "mature_anchor_count": mature_anchor_count,
                    "mature_anchor_coverage_rate": (
                        round(mature_anchor_count / effective_anchor_count, 4)
                        if effective_anchor_count
                        else 0.0
                    ),
                    "ready": mature_anchor_count >= MIN_POINT_IN_TIME_ANCHORS,
                },
                "qualified": {},
                "factors": rows,
            }
        output[segment] = {
            "label": _SEGMENT_LABELS.get(segment, segment),
            "sampled_portfolios": len(segment_members[segment]),
            "type_factor_model": {
                "schema_version": TYPE_FACTOR_SCHEMA_VERSION,
                "candidate_factors": list(typed_keys),
                "orientation": "higher_is_better",
                "tracking_evidence": {
                    "status": "insufficient" if segment == "zs" else "not_applicable",
                    "reason": (
                        "缺少逐只指数基金的精确、时点可得跟踪基准，tracking 因子未进入研究"
                        if segment == "zs"
                        else None
                    ),
                },
                "size_role": "capacity_risk_guard_only",
                "nav_information_lag_trading_days": (
                    0
                    if nav_observation_pit
                    else nav_publication_lag_trading_days(segment)
                ),
                "nav_revision_pit": nav_observation_pit,
                **(
                    {
                        "availability_basis": "collector_first_observed_at",
                        "revision_policy": "first_observed_value",
                    }
                    if nav_observation_pit
                    else {}
                ),
            },
            "horizons": horizon_rows,
        }

    q_values = benjamini_hochberg({key: row.get("p_value") for key, row in all_rows})
    for key, row in all_rows:
        row["p_value"] = round(row["p_value"], 6) if row.get("p_value") is not None else None
        row["q_value"] = q_values[key]
        segment, horizon, factor = key.split(":", 2)
        row["qualified"] = _qualifies(
            row,
            minimum_embargo_days=max(embargo_days, int(horizon)),
        )
        output[segment]["horizons"][horizon]["qualified"][factor] = row["qualified"]
    return output, coverage
