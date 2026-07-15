from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models import FundNavHistory
from app.services.trading_session import build_trading_session


RISK_CONTEXT_SCHEMA_VERSION = "discovery_risk_context.v1"

DEFAULT_LOOKBACK_TRADING_DAYS = 260
DEFAULT_MIN_COMMON_RETURN_DAYS = 60
# Seven calendar days covers a normal weekend plus a short exchange holiday,
# while still failing closed on a genuinely stale public-fund NAV series.
DEFAULT_MAX_NAV_AGE_CALENDAR_DAYS = 7
DEFAULT_MIN_HOLDING_NAV_AMOUNT_COVERAGE_RATIO = 0.80
DEFAULT_MAX_FETCH_WORKERS = 8

_UNKNOWN_FUND_CODES = {"", "000000"}
_VARIANCE_EPSILON = 1e-18


@dataclass(frozen=True)
class _RequestedSeries:
    code: str
    name: str
    role: str
    amount_yuan: float | None = None


@dataclass(frozen=True)
class _ParsedSeries:
    request: _RequestedSeries
    source: str
    nav_by_date: dict[str, float]
    returns_by_date: dict[str, float]
    latest_date: str | None
    future_points_dropped: int
    status: str
    reason_code: str | None = None
    hard_data_error: bool = False


def build_discovery_risk_context(
    candidate_rows: Sequence[Mapping[str, Any]],
    holdings_slim: Sequence[Mapping[str, Any]],
    *,
    decision_at: datetime | str,
    fetch_nav: Callable[[str, str, int], Any] | None = None,
    lookback_trading_days: int = DEFAULT_LOOKBACK_TRADING_DAYS,
    min_common_return_days: int = DEFAULT_MIN_COMMON_RETURN_DAYS,
    max_nav_age_calendar_days: int = DEFAULT_MAX_NAV_AGE_CALENDAR_DAYS,
    min_holding_nav_amount_coverage_ratio: float = (
        DEFAULT_MIN_HOLDING_NAV_AMOUNT_COVERAGE_RATIO
    ),
    max_fetch_workers: int = DEFAULT_MAX_FETCH_WORKERS,
) -> dict[str, Any]:
    """Build point-in-time risk evidence for deterministic discovery allocation.

    The result is executable only when every candidate has a fresh, finite NAV
    series with at least ``min_common_return_days`` aligned daily returns and a
    positive variance. Current holdings may have unavailable NAV histories, but
    the usable amount coverage must be at least 80% by default. Malformed NAV
    data (invalid dates, duplicate dates, NaN/inf, or non-positive NAVs) always
    fails closed instead of being treated as ordinary provider unavailability.

    ``fetch_nav`` may return a ``FundNavHistory`` (or a mapping carrying
    ``points``) or a points sequence. The default fetch runs
    ``FundDataService.get_nav_history`` concurrently. All points after the
    decision's effective trade date are discarded before any statistic is
    calculated, which makes historical replays immune to future observations.
    """

    decision = _resolve_decision(decision_at)
    configuration = {
        "lookback_trading_days": lookback_trading_days,
        "min_common_return_days": min_common_return_days,
        "max_nav_age_calendar_days": max_nav_age_calendar_days,
        "min_holding_nav_amount_coverage_ratio": (
            min_holding_nav_amount_coverage_ratio
        ),
    }
    base = _base_payload(
        decision_at=decision[0] if decision else None,
        effective_trade_date=decision[1] if decision else None,
        configuration=configuration,
    )

    input_reasons = _validate_configuration(
        lookback_trading_days=lookback_trading_days,
        min_common_return_days=min_common_return_days,
        max_nav_age_calendar_days=max_nav_age_calendar_days,
        min_holding_nav_amount_coverage_ratio=(
            min_holding_nav_amount_coverage_ratio
        ),
        max_fetch_workers=max_fetch_workers,
    )
    if decision is None:
        input_reasons.append("decision_at_invalid")
    if not _is_mapping_sequence(candidate_rows):
        input_reasons.append("candidate_rows_invalid")
    if not _is_mapping_sequence(holdings_slim):
        input_reasons.append("holdings_slim_invalid")
    if input_reasons:
        return _finish(base, status="unqualified", reasons=input_reasons)

    candidates, candidate_reasons = _normalize_candidates(candidate_rows)
    holdings, holding_reasons = _normalize_holdings(holdings_slim)
    input_reasons.extend(candidate_reasons)
    input_reasons.extend(holding_reasons)
    if not candidates:
        input_reasons.append("candidate_rows_empty")

    candidate_codes = {item.code for item in candidates}
    holding_codes = {item.code for item in holdings}
    if candidate_codes & holding_codes:
        input_reasons.append("fund_code_duplicated_across_candidates_and_holdings")

    base["candidate_codes"] = sorted(candidate_codes)
    base["holding_codes"] = sorted(holding_codes)
    if input_reasons:
        return _finish(base, status="unqualified", reasons=input_reasons)

    assert decision is not None
    effective_trade_date = decision[1]
    fetcher = fetch_nav or _default_fetch_nav
    requests = sorted(candidates + holdings, key=lambda item: (item.code, item.role))
    fetched = _fetch_all(
        requests,
        fetcher=fetcher,
        trading_days=lookback_trading_days,
        max_workers=max_fetch_workers,
        effective_trade_date=effective_trade_date,
    )

    series_meta = {
        item.request.code: _series_metadata(
            item,
            effective_trade_date=effective_trade_date,
            max_nav_age_calendar_days=max_nav_age_calendar_days,
        )
        for item in fetched
    }
    base["series_by_code"] = dict(sorted(series_meta.items()))

    hard_errors = sorted(
        {
            item.reason_code or "nav_data_invalid"
            for item in fetched
            if item.hard_data_error
        }
    )
    if hard_errors:
        return _finish(base, status="unqualified", reasons=hard_errors)

    by_code = {item.request.code: item for item in fetched}
    candidate_series = [by_code[item.code] for item in candidates]
    holding_series = [by_code[item.code] for item in holdings]

    candidate_quality_reasons: list[str] = []
    for item in candidate_series:
        if item.status != "available":
            candidate_quality_reasons.append(
                item.reason_code or "candidate_nav_unavailable"
            )
            continue
        if not _is_fresh(
            item.latest_date,
            effective_trade_date=effective_trade_date,
            max_age_days=max_nav_age_calendar_days,
        ):
            candidate_quality_reasons.append("candidate_nav_stale")

    if candidate_quality_reasons:
        return _finish(
            base,
            status="unqualified",
            reasons=candidate_quality_reasons,
        )

    common_candidate_dates = _common_return_dates(candidate_series)
    base["candidate_common_return_sample_days"] = len(common_candidate_dates)
    if len(common_candidate_dates) < min_common_return_days:
        return _finish(
            base,
            status="unqualified",
            reasons=["candidate_common_return_sample_insufficient"],
        )

    candidate_vectors = {
        item.request.code: [
            item.returns_by_date[day] for day in common_candidate_dates
        ]
        for item in candidate_series
    }
    if any(
        _sample_variance(values) <= _VARIANCE_EPSILON
        for values in candidate_vectors.values()
    ):
        return _finish(
            base,
            status="unqualified",
            reasons=["candidate_return_variance_nonpositive"],
        )

    covariance = _covariance_matrix(candidate_vectors)
    if not _is_positive_semidefinite(covariance):
        return _finish(
            base,
            status="unqualified",
            reasons=["candidate_covariance_not_positive_semidefinite"],
        )
    correlation = _correlation_matrix(covariance)

    total_holding_amount = sum(
        item.request.amount_yuan or 0.0 for item in holding_series
    )
    valid_holdings: list[_ParsedSeries] = []
    holding_correlations: dict[str, dict[str, float]] = {
        item.request.code: {} for item in candidate_series
    }
    for holding in holding_series:
        if not _holding_series_is_usable(
            holding,
            candidates=candidate_series,
            effective_trade_date=effective_trade_date,
            max_nav_age_calendar_days=max_nav_age_calendar_days,
            min_common_return_days=min_common_return_days,
            correlation_output=holding_correlations,
        ):
            continue
        valid_holdings.append(holding)

    covered_amount = sum(item.request.amount_yuan or 0.0 for item in valid_holdings)
    coverage_ratio = (
        covered_amount / total_holding_amount if total_holding_amount > 0 else 1.0
    )
    coverage_ratio = _bounded(coverage_ratio, lower=0.0, upper=1.0)
    base["current_holdings_nav_amount_coverage_ratio"] = _round(coverage_ratio, 8)
    base["current_holdings_nav_amount_coverage_percent"] = _round(
        coverage_ratio * 100.0, 4
    )
    base["current_holdings_covered_amount_yuan"] = _round(covered_amount, 2)
    base["current_holdings_total_amount_yuan"] = _round(total_holding_amount, 2)

    if coverage_ratio + 1e-12 < min_holding_nav_amount_coverage_ratio:
        return _finish(
            base,
            status="unqualified",
            reasons=["current_holdings_nav_amount_coverage_insufficient"],
        )

    current_returns, current_sample_days = _current_portfolio_returns(
        valid_holdings,
        min_common_return_days=min_common_return_days,
    )
    if total_holding_amount > 0 and current_returns is None:
        return _finish(
            base,
            status="unqualified",
            reasons=["current_holdings_common_return_sample_insufficient"],
        )

    uncovered_ratio = 1.0 - coverage_ratio
    positive_penalties: dict[str, float] = {}
    for candidate in candidate_series:
        code = candidate.request.code
        weighted_positive_correlation = uncovered_ratio
        if covered_amount > 0:
            for holding in valid_holdings:
                holding_code = holding.request.code
                weight = (holding.request.amount_yuan or 0.0) / total_holding_amount
                corr = holding_correlations[code][holding_code]
                weighted_positive_correlation += weight * max(corr, 0.0)
        positive_penalties[code] = _round(
            _bounded(weighted_positive_correlation, lower=0.0, upper=1.0), 8
        )

    candidate_basket_returns = [
        statistics.fmean(candidate_vectors[code][index] for code in sorted(candidate_vectors))
        for index in range(len(common_candidate_dates))
    ]
    current_drawdown = (
        _max_drawdown_percent(current_returns) if current_returns is not None else 0.0
    )

    base.update(
        {
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "max_drawdown_percent_by_code": {
                item.request.code: _round(
                    _max_drawdown_percent(
                        [
                            item.returns_by_date[day]
                            for day in sorted(item.returns_by_date)
                        ]
                    ),
                    6,
                )
                for item in sorted(candidate_series, key=lambda row: row.request.code)
            },
            "covariance_by_code": covariance,
            "correlation_by_code": correlation,
            "candidate_to_current_holding_correlation_by_code": {
                code: dict(sorted(values.items()))
                for code, values in sorted(holding_correlations.items())
                if values
            },
            "positive_correlation_penalty_to_current_holdings_by_code": dict(
                sorted(positive_penalties.items())
            ),
            "scenario_drawdown": {
                "current_portfolio_max_drawdown_percent": _round(
                    current_drawdown, 6
                ),
                "current_portfolio_return_sample_days": current_sample_days,
                "equal_weight_candidate_basket_max_drawdown_percent": _round(
                    _max_drawdown_percent(candidate_basket_returns), 6
                ),
                "equal_weight_candidate_basket_return_sample_days": len(
                    common_candidate_dates
                ),
                "current_portfolio_basis": (
                    "covered_holdings_normalized_by_amount"
                    if total_holding_amount > 0
                    else "no_holdings_cash_baseline"
                ),
            },
        }
    )
    return _finish(base, status="qualified", reasons=[])


def _resolve_decision(value: datetime | str) -> tuple[str, str] | None:
    moment: datetime
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        # A bare date has ambiguous pre-open/after-close semantics and must not
        # silently select a different point-in-time boundary.
        if "T" not in text and " " not in text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        session = build_trading_session(moment)
        canonical = str(session["decision_at"])
        effective = date.fromisoformat(str(session["effective_trade_date"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return canonical, effective.isoformat()


def _validate_configuration(
    *,
    lookback_trading_days: Any,
    min_common_return_days: Any,
    max_nav_age_calendar_days: Any,
    min_holding_nav_amount_coverage_ratio: Any,
    max_fetch_workers: Any,
) -> list[str]:
    reasons: list[str] = []
    if not _positive_int(lookback_trading_days):
        reasons.append("lookback_trading_days_invalid")
    if not _positive_int(min_common_return_days):
        reasons.append("min_common_return_days_invalid")
    if (
        _positive_int(lookback_trading_days)
        and _positive_int(min_common_return_days)
        and min_common_return_days >= lookback_trading_days
    ):
        reasons.append("lookback_shorter_than_required_sample")
    if not isinstance(max_nav_age_calendar_days, int) or isinstance(
        max_nav_age_calendar_days, bool
    ) or max_nav_age_calendar_days < 0:
        reasons.append("max_nav_age_calendar_days_invalid")
    if (
        not _finite_number(min_holding_nav_amount_coverage_ratio)
        or not 0.0 <= float(min_holding_nav_amount_coverage_ratio) <= 1.0
    ):
        reasons.append("holding_nav_amount_coverage_threshold_invalid")
    if not _positive_int(max_fetch_workers):
        reasons.append("max_fetch_workers_invalid")
    return reasons


def _normalize_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[_RequestedSeries], list[str]]:
    requests: list[_RequestedSeries] = []
    raw_codes: list[str] = []
    reasons: list[str] = []
    for row in rows:
        code = _fund_code(row.get("fund_code"))
        if code is None:
            reasons.append("candidate_fund_code_missing_or_unknown")
            continue
        raw_codes.append(code)
        requests.append(
            _RequestedSeries(
                code=code,
                name=str(row.get("fund_name") or code).strip() or code,
                role="candidate",
            )
        )
    if any(count > 1 for count in Counter(raw_codes).values()):
        reasons.append("candidate_fund_code_duplicated")
    return requests, reasons


def _normalize_holdings(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[_RequestedSeries], list[str]]:
    requests: list[_RequestedSeries] = []
    raw_codes: list[str] = []
    reasons: list[str] = []
    for row in rows:
        code = _fund_code(row.get("fund_code"))
        if code is None:
            reasons.append("holding_fund_code_missing_or_unknown")
            continue
        amount = _finite_nonnegative(row.get("holding_amount"))
        if amount is None:
            reasons.append("holding_amount_invalid")
            continue
        raw_codes.append(code)
        if amount == 0:
            continue
        requests.append(
            _RequestedSeries(
                code=code,
                name=str(row.get("fund_name") or code).strip() or code,
                role="holding",
                amount_yuan=amount,
            )
        )
    if any(count > 1 for count in Counter(raw_codes).values()):
        reasons.append("holding_fund_code_duplicated")
    return requests, reasons


def _fetch_all(
    requests: list[_RequestedSeries],
    *,
    fetcher: Callable[[str, str, int], Any],
    trading_days: int,
    max_workers: int,
    effective_trade_date: str,
) -> list[_ParsedSeries]:
    def fetch_one(request: _RequestedSeries) -> _ParsedSeries:
        try:
            raw = fetcher(request.code, request.name, trading_days)
        except Exception:
            return _unavailable_series(request, "nav_fetch_failed")
        return _parse_fetched_series(
            request,
            raw,
            effective_trade_date=effective_trade_date,
        )

    if len(requests) == 1:
        return [fetch_one(requests[0])]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(requests))) as executor:
        return list(executor.map(fetch_one, requests))


def _default_fetch_nav(code: str, name: str, trading_days: int) -> FundNavHistory:
    from app.services.fund_data import FundDataService

    return FundDataService().get_nav_history(
        code,
        name,
        trading_days=trading_days,
    )


def _parse_fetched_series(
    request: _RequestedSeries,
    raw: Any,
    *,
    effective_trade_date: str,
) -> _ParsedSeries:
    source = "injected_points"
    history_latest_nav: Any = None
    history_latest_date: Any = None
    if isinstance(raw, FundNavHistory):
        points = raw.points
        source = (raw.source or "").strip()
        history_latest_nav = raw.latest_nav
        history_latest_date = raw.latest_date
    elif isinstance(raw, Mapping) and "points" in raw:
        points = raw.get("points")
        source = str(raw.get("source") or "injected_points").strip()
        history_latest_nav = raw.get("latest_nav")
        history_latest_date = raw.get("latest_date")
    else:
        points = raw

    if not source:
        return _invalid_series(request, "nav_source_invalid")
    if history_latest_nav is not None and (
        not _finite_number(history_latest_nav) or float(history_latest_nav) <= 0
    ):
        return _invalid_series(request, "nav_latest_value_invalid", source=source)
    if history_latest_date is not None and _parse_date(history_latest_date) is None:
        return _invalid_series(request, "nav_latest_date_invalid", source=source)
    if isinstance(points, (str, bytes, Mapping)) or not isinstance(points, Sequence):
        return _unavailable_series(request, "nav_points_unavailable", source=source)
    if not points:
        return _unavailable_series(request, "nav_points_unavailable", source=source)

    cutoff = date.fromisoformat(effective_trade_date)
    all_points: list[tuple[date, float]] = []
    for point in points:
        raw_date = _field(point, "date")
        raw_nav = _field(point, "nav")
        parsed_date = _parse_date(raw_date)
        if parsed_date is None:
            return _invalid_series(request, "nav_point_date_invalid", source=source)
        if not _finite_number(raw_nav) or float(raw_nav) <= 0:
            return _invalid_series(request, "nav_point_value_invalid", source=source)
        all_points.append((parsed_date, float(raw_nav)))

    dates = [day for day, _ in all_points]
    if len(set(dates)) != len(dates):
        return _invalid_series(request, "nav_point_date_duplicated", source=source)

    eligible = sorted((day, nav) for day, nav in all_points if day <= cutoff)
    future_count = len(all_points) - len(eligible)
    if len(eligible) < 2:
        return _unavailable_series(
            request,
            "nav_points_before_decision_insufficient",
            source=source,
            future_points_dropped=future_count,
        )

    nav_by_date = {day.isoformat(): nav for day, nav in eligible}
    returns: dict[str, float] = {}
    previous_nav: float | None = None
    for day, nav in eligible:
        if previous_nav is not None:
            value = nav / previous_nav - 1.0
            if not math.isfinite(value):
                return _invalid_series(request, "nav_return_invalid", source=source)
            returns[day.isoformat()] = value
        previous_nav = nav

    return _ParsedSeries(
        request=request,
        source=source,
        nav_by_date=nav_by_date,
        returns_by_date=returns,
        latest_date=eligible[-1][0].isoformat(),
        future_points_dropped=future_count,
        status="available",
    )


def _unavailable_series(
    request: _RequestedSeries,
    reason: str,
    *,
    source: str = "unavailable",
    future_points_dropped: int = 0,
) -> _ParsedSeries:
    return _ParsedSeries(
        request=request,
        source=source,
        nav_by_date={},
        returns_by_date={},
        latest_date=None,
        future_points_dropped=future_points_dropped,
        status="unavailable",
        reason_code=reason,
    )


def _invalid_series(
    request: _RequestedSeries,
    reason: str,
    *,
    source: str = "invalid",
) -> _ParsedSeries:
    return _ParsedSeries(
        request=request,
        source=source,
        nav_by_date={},
        returns_by_date={},
        latest_date=None,
        future_points_dropped=0,
        status="invalid",
        reason_code=reason,
        hard_data_error=True,
    )


def _series_metadata(
    item: _ParsedSeries,
    *,
    effective_trade_date: str,
    max_nav_age_calendar_days: int,
) -> dict[str, Any]:
    return {
        "role": item.request.role,
        "fund_name": item.request.name,
        "amount_yuan": item.request.amount_yuan,
        "source": item.source,
        "status": item.status,
        "reason_code": item.reason_code,
        "latest_nav_date": item.latest_date,
        "nav_point_count": len(item.nav_by_date),
        "return_sample_count": len(item.returns_by_date),
        "future_points_dropped": item.future_points_dropped,
        "freshness": (
            "fresh"
            if item.status == "available"
            and _is_fresh(
                item.latest_date,
                effective_trade_date=effective_trade_date,
                max_age_days=max_nav_age_calendar_days,
            )
            else "stale_or_unavailable"
        ),
    }


def _holding_series_is_usable(
    holding: _ParsedSeries,
    *,
    candidates: list[_ParsedSeries],
    effective_trade_date: str,
    max_nav_age_calendar_days: int,
    min_common_return_days: int,
    correlation_output: dict[str, dict[str, float]],
) -> bool:
    if holding.status != "available" or not _is_fresh(
        holding.latest_date,
        effective_trade_date=effective_trade_date,
        max_age_days=max_nav_age_calendar_days,
    ):
        return False
    pending: dict[str, float] = {}
    for candidate in candidates:
        dates = sorted(
            set(candidate.returns_by_date) & set(holding.returns_by_date)
        )
        if len(dates) < min_common_return_days:
            return False
        left = [candidate.returns_by_date[day] for day in dates]
        right = [holding.returns_by_date[day] for day in dates]
        if (
            _sample_variance(left) <= _VARIANCE_EPSILON
            or _sample_variance(right) <= _VARIANCE_EPSILON
        ):
            return False
        corr = _pearson(left, right)
        if corr is None:
            return False
        pending[candidate.request.code] = _round(corr, 10)
    for candidate_code, corr in pending.items():
        correlation_output[candidate_code][holding.request.code] = corr
    return True


def _current_portfolio_returns(
    holdings: list[_ParsedSeries],
    *,
    min_common_return_days: int,
) -> tuple[list[float] | None, int]:
    if not holdings:
        return [], 0
    dates = _common_return_dates(holdings)
    if len(dates) < min_common_return_days:
        return None, len(dates)
    total = sum(item.request.amount_yuan or 0.0 for item in holdings)
    if total <= 0:
        return None, len(dates)
    values = [
        sum(
            item.returns_by_date[day]
            * ((item.request.amount_yuan or 0.0) / total)
            for item in holdings
        )
        for day in dates
    ]
    return values, len(dates)


def _covariance_matrix(
    vectors: Mapping[str, list[float]],
) -> dict[str, dict[str, float]]:
    codes = sorted(vectors)
    means = {code: statistics.fmean(vectors[code]) for code in codes}
    sample_count = len(vectors[codes[0]])
    denominator = sample_count - 1
    output: dict[str, dict[str, float]] = {code: {} for code in codes}
    for left_index, left in enumerate(codes):
        for right in codes[left_index:]:
            covariance = sum(
                (vectors[left][index] - means[left])
                * (vectors[right][index] - means[right])
                for index in range(sample_count)
            ) / denominator
            covariance = 0.0 if abs(covariance) < 1e-30 else covariance
            output[left][right] = covariance
            output[right][left] = covariance
    return output


def _correlation_matrix(
    covariance: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    codes = sorted(covariance)
    output: dict[str, dict[str, float]] = {code: {} for code in codes}
    for left in codes:
        for right in codes:
            denominator = math.sqrt(covariance[left][left] * covariance[right][right])
            value = covariance[left][right] / denominator
            output[left][right] = _round(_bounded(value, lower=-1.0, upper=1.0), 10)
    return output


def _is_positive_semidefinite(
    covariance: Mapping[str, Mapping[str, float]],
) -> bool:
    codes = sorted(covariance)
    if not codes:
        return False
    scale = max(covariance[code][code] for code in codes)
    if not math.isfinite(scale) or scale <= _VARIANCE_EPSILON:
        return False
    tolerance = max(1e-18, scale * 1e-10)
    lower = [[0.0 for _ in codes] for _ in codes]
    for row_index, code in enumerate(codes):
        for column_index in range(row_index + 1):
            other = codes[column_index]
            value = covariance[code][other]
            if not math.isfinite(value):
                return False
            residual = value - sum(
                lower[row_index][offset] * lower[column_index][offset]
                for offset in range(column_index)
            )
            if row_index == column_index:
                if residual < -tolerance:
                    return False
                lower[row_index][column_index] = math.sqrt(max(residual, 0.0))
                continue
            diagonal = lower[column_index][column_index]
            if diagonal <= tolerance:
                if abs(residual) > tolerance:
                    return False
                lower[row_index][column_index] = 0.0
            else:
                lower[row_index][column_index] = residual / diagonal
    return True


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    covariance = sum(
        (left[index] - mean_left) * (right[index] - mean_right)
        for index in range(len(left))
    )
    left_sum = sum((value - mean_left) ** 2 for value in left)
    right_sum = sum((value - mean_right) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    if denominator <= 0 or not math.isfinite(denominator):
        return None
    return _bounded(covariance / denominator, lower=-1.0, upper=1.0)


def _sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    value = statistics.variance(values)
    return value if math.isfinite(value) else 0.0


def _max_drawdown_percent(decimal_returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for daily_return in decimal_returns:
        if not math.isfinite(daily_return) or daily_return <= -1.0:
            return 100.0
        equity *= 1.0 + daily_return
        if not math.isfinite(equity) or equity <= 0:
            return 100.0
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak)
    return _bounded(maximum * 100.0, lower=0.0, upper=100.0)


def _common_return_dates(series: list[_ParsedSeries]) -> list[str]:
    common: set[str] | None = None
    for item in series:
        dates = set(item.returns_by_date)
        common = dates if common is None else common & dates
    return sorted(common or set())


def _is_fresh(
    latest_date: str | None,
    *,
    effective_trade_date: str,
    max_age_days: int,
) -> bool:
    if latest_date is None:
        return False
    try:
        age = (
            date.fromisoformat(effective_trade_date) - date.fromisoformat(latest_date)
        ).days
    except ValueError:
        return False
    return 0 <= age <= max_age_days


def _base_payload(
    *,
    decision_at: str | None,
    effective_trade_date: str | None,
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RISK_CONTEXT_SCHEMA_VERSION,
        "status": "unqualified",
        "qualified": False,
        "decision_at": decision_at,
        "effective_trade_date": effective_trade_date,
        "configuration": dict(configuration),
        "candidate_codes": [],
        "holding_codes": [],
        "candidate_common_return_sample_days": 0,
        "current_holdings_nav_amount_coverage_ratio": 0.0,
        "current_holdings_nav_amount_coverage_percent": 0.0,
        "current_holdings_covered_amount_yuan": 0.0,
        "current_holdings_total_amount_yuan": 0.0,
        "max_drawdown_percent_by_code": {},
        "covariance_by_code": {},
        "correlation_by_code": {},
        "candidate_to_current_holding_correlation_by_code": {},
        "positive_correlation_penalty_to_current_holdings_by_code": {},
        "scenario_drawdown": {},
        "series_by_code": {},
        "reason_codes": [],
    }


def _finish(
    payload: dict[str, Any],
    *,
    status: str,
    reasons: Sequence[str],
) -> dict[str, Any]:
    payload["status"] = status
    payload["qualified"] = status == "qualified"
    payload["reason_codes"] = sorted(dict.fromkeys(str(reason) for reason in reasons))
    if status != "qualified":
        payload["max_drawdown_percent_by_code"] = {}
        payload["covariance_by_code"] = {}
        payload["correlation_by_code"] = {}
        payload["candidate_to_current_holding_correlation_by_code"] = {}
        payload["positive_correlation_penalty_to_current_holdings_by_code"] = {}
        payload["scenario_drawdown"] = {}
    payload.pop("snapshot_hash", None)
    _ensure_json_finite(payload)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _ensure_json_finite(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("risk context contains NaN or infinity")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _ensure_json_finite(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _ensure_json_finite(item)


def _field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) != 6 or text in _UNKNOWN_FUND_CODES:
        return None
    return text


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _finite_nonnegative(value: Any) -> float | None:
    if not _finite_number(value):
        return None
    parsed = float(value)
    return parsed if parsed >= 0 else None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_mapping_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, Mapping) for item in value)
    )


def _bounded(value: float, *, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round(value: float, digits: int) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded
