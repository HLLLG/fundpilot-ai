"""Strict point-in-time fund-versus-benchmark research metrics.

The calculation boundary is pure and intentionally descriptive.  A verified
fund contract may authorize formal excess metrics; a tracking index may only
produce reference/tracking metrics.  Missing components, future snapshots,
duplicate dates, stale endpoints, or insufficient aligned observations remain
explicitly unavailable and never get reweighted or imputed.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.fund_peer_ranking import resolve_benchmark_comparison
from app.services.trading_session import build_trading_session


BENCHMARK_RESEARCH_SCHEMA_VERSION = "fund_benchmark_research.v1"
DEFAULT_MIN_ALIGNED_RETURN_DAYS = 60
DEFAULT_MAX_ENDPOINT_LAG_CALENDAR_DAYS = 7
DEFAULT_ROLLING_WINDOW_DAYS = 20
DEFAULT_HORIZONS: tuple[tuple[str, int], ...] = (
    ("3m", 63),
    ("6m", 126),
    ("1y", 252),
)

_TRACKING_STYLES = {"passive", "passive_index", "enhanced", "enhanced_index"}
DEFAULT_SOURCE_LOOKBACK_DAYS = 320
DEFAULT_SOURCE_FETCH_WORKERS = 6
DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS = 30 * 60


def build_fund_benchmark_research(
    fund_series: object,
    benchmark_spec: Mapping[str, Any] | None,
    component_series_by_id: Mapping[str, object] | None,
    *,
    decision_at: str | datetime,
    management_style: str | None = None,
    horizons: Sequence[tuple[str, int]] = DEFAULT_HORIZONS,
    minimum_aligned_return_days: int = DEFAULT_MIN_ALIGNED_RETURN_DAYS,
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    max_endpoint_lag_calendar_days: int = DEFAULT_MAX_ENDPOINT_LAG_CALENDAR_DAYS,
) -> dict[str, Any]:
    """Calculate aligned descriptive comparison metrics from frozen series.

    ``fund_series`` and every component payload must be a snapshot envelope:
    ``{"points": [...], "source": "...", "available_at": "..."}``.
    Snapshot availability must be timezone-aware and no later than
    ``decision_at``.  The function performs no I/O and cannot promote a
    reference index into a formal benchmark.
    """

    decision = _decision_context(decision_at)
    comparison = (
        resolve_benchmark_comparison(benchmark_spec, decision_at=decision_at)
        if decision is not None
        else _unavailable_comparison("decision_at_invalid")
    )
    if (
        isinstance(benchmark_spec, Mapping)
        and benchmark_spec.get("tier") == "unavailable"
        and comparison.get("comparison_role") == "unavailable"
        and benchmark_spec.get("reason")
    ):
        comparison = {**comparison, "reason": str(benchmark_spec.get("reason"))}
    base = _base_payload(
        comparison=comparison,
        decision=decision,
        horizons=horizons,
        minimum_aligned_return_days=minimum_aligned_return_days,
        rolling_window_days=rolling_window_days,
        max_endpoint_lag_calendar_days=max_endpoint_lag_calendar_days,
    )

    configuration_reasons = _validate_configuration(
        horizons=horizons,
        minimum_aligned_return_days=minimum_aligned_return_days,
        rolling_window_days=rolling_window_days,
        max_endpoint_lag_calendar_days=max_endpoint_lag_calendar_days,
    )
    if decision is None:
        configuration_reasons.append("decision_at_invalid")
    role = str(comparison.get("comparison_role") or "unavailable")
    if role == "unavailable":
        configuration_reasons.append(
            str(comparison.get("reason") or "benchmark_identity_unavailable")
        )
    if configuration_reasons:
        return _finish(base, status="unavailable", reasons=configuration_reasons)

    assert decision is not None
    cutoff = decision[1]
    fund, fund_reason = _normalize_snapshot(
        fund_series,
        decision=decision[0],
        cutoff=cutoff,
        value_keys=("nav", "value", "close"),
        series_id="fund",
    )
    base["fund_series"] = _snapshot_audit(
        fund_series,
        point_count=len(fund or {}),
        status="available" if fund_reason is None else "unavailable",
        reason=fund_reason,
    )
    if fund_reason:
        return _finish(base, status="unavailable", reasons=[fund_reason])

    spec = dict(benchmark_spec or {})
    components = [
        dict(item)
        for item in spec.get("components") or []
        if isinstance(item, Mapping)
    ]
    if not components:
        # A tracking mapping may expose only one code.  Preserve its reference
        # role while constructing an explicit, deterministic single component.
        code = comparison.get("benchmark_code") or spec.get("benchmark_code")
        name = comparison.get("benchmark_name") or spec.get("benchmark_name")
        if code or name:
            components = [
                {
                    "component_id": str(code or name),
                    "benchmark_code": code,
                    "name": name,
                    "weight_percent": 100.0,
                }
            ]
    weights, weight_reason = _component_weights(components)
    if weight_reason:
        return _finish(base, status="unavailable", reasons=[weight_reason])

    payloads = component_series_by_id or {}
    normalized_components: list[tuple[dict[str, Any], float, dict[date, float]]] = []
    component_audit: list[dict[str, Any]] = []
    for component, weight in zip(components, weights or [], strict=True):
        identity = _component_identity(component)
        payload = _component_payload(payloads, component)
        series, reason = _normalize_snapshot(
            payload,
            decision=decision[0],
            cutoff=cutoff,
            value_keys=("close", "value", "nav"),
            series_id=identity,
        )
        component_audit.append(
            {
                "component_id": identity,
                "benchmark_code": component.get("benchmark_code"),
                "name": component.get("name"),
                "weight_percent": weight,
                "status": "available" if reason is None else "unavailable",
                "reason": reason,
                "point_count": len(series or {}),
                **_snapshot_audit_fields(payload),
            }
        )
        if reason:
            base["components"] = component_audit
            return _finish(
                base,
                status="unavailable",
                reasons=[f"benchmark_component_{reason}"],
            )
        assert series is not None
        normalized_components.append((component, weight, series))

    assert fund is not None
    common_dates = set(fund)
    for _component, _weight, series in normalized_components:
        common_dates &= set(series)
    dates = sorted(common_dates)
    base["components"] = component_audit
    base["alignment"] = {
        "fund_point_count": len(fund),
        "benchmark_component_count": len(normalized_components),
        "common_point_count": len(dates),
        "common_return_sample_days": max(len(dates) - 1, 0),
        "first_common_date": dates[0].isoformat() if dates else None,
        "last_common_date": dates[-1].isoformat() if dates else None,
        "future_point_policy": "drop_after_effective_trade_date",
        "missing_value_policy": "never_impute_or_reweight",
    }
    if len(dates) - 1 < minimum_aligned_return_days:
        return _finish(
            base,
            status="insufficient",
            reasons=["aligned_return_sample_insufficient"],
        )
    if (cutoff - dates[-1]).days > max_endpoint_lag_calendar_days:
        return _finish(base, status="unavailable", reasons=["aligned_endpoint_stale"])

    fund_values = [fund[day] for day in dates]
    component_values = [
        [series[day] for day in dates]
        for _component, _weight, series in normalized_components
    ]
    fund_returns = _period_returns(fund_values)
    benchmark_returns = _composite_returns(
        component_values,
        weights or [],
    )
    if fund_returns is None or benchmark_returns is None:
        return _finish(base, status="unavailable", reasons=["aligned_return_invalid"])

    horizon_rows: dict[str, dict[str, Any]] = {}
    available_horizons = 0
    for label, required_days in horizons:
        if len(fund_returns) < required_days:
            horizon_rows[label] = {
                "status": "unavailable",
                "reason": "horizon_sample_insufficient",
                "required_return_days": required_days,
                "available_return_days": len(fund_returns),
            }
            continue
        available_horizons += 1
        start_index = len(fund_returns) - required_days
        horizon_rows[label] = _horizon_metrics(
            label=label,
            role=role,
            required_days=required_days,
            dates=dates[start_index:],
            fund_returns=fund_returns[start_index:],
            benchmark_returns=benchmark_returns[start_index:],
        )

    comparison_window = min(len(fund_returns), max(required for _, required in horizons))
    rolling = _rolling_metrics(
        role=role,
        fund_returns=fund_returns[-comparison_window:],
        benchmark_returns=benchmark_returns[-comparison_window:],
        window_days=rolling_window_days,
    )
    tracking_applicable = bool(
        role == "tracking_reference"
        or str(management_style or "").strip().casefold() in _TRACKING_STYLES
    )
    tracking = _tracking_metrics(
        applicable=tracking_applicable,
        role=role,
        fund_returns=fund_returns[-comparison_window:],
        benchmark_returns=benchmark_returns[-comparison_window:],
    )

    base.update(
        {
            "status": "qualified" if available_horizons else "insufficient",
            "qualified": bool(available_horizons),
            "descriptive_only": True,
            "execution_tilt_eligible": False,
            "horizons": horizon_rows,
            "available_horizon_count": available_horizons,
            "rolling_comparison": rolling,
            "tracking_metrics": tracking,
            "comparison_policy": {
                "formal_excess_requires_verified_contract": True,
                "tracking_reference_never_formal_excess": True,
                "execution_semantics": "descriptive_only_not_amount_signal",
            },
        }
    )
    reasons = [] if available_horizons else ["no_horizon_has_sufficient_sample"]
    return _finish(base, status=str(base["status"]), reasons=reasons)


def build_fund_benchmark_research_batch(
    funds: Sequence[Mapping[str, Any]],
    *,
    decision_at: str | datetime,
    fetch_fund: Any | None = None,
    fetch_component: Any | None = None,
    trading_days: int = DEFAULT_SOURCE_LOOKBACK_DAYS,
    max_workers: int = DEFAULT_SOURCE_FETCH_WORKERS,
    live_fetch_decision_skew_seconds: int = DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Fetch shared series once, then calculate research for each fund.

    The batch is best-effort and fail-closed.  Benchmark identities that are
    unavailable do not trigger provider I/O.  Repeated benchmark components
    are fetched once per batch and reused across funds.
    """

    decision = _decision_context(decision_at)
    normalized: dict[str, dict[str, Any]] = {}
    for raw in funds:
        if not isinstance(raw, Mapping):
            continue
        code = _fund_code(raw.get("fund_code"))
        if code is not None and code not in normalized:
            normalized[code] = dict(raw)
    if not normalized:
        return {}
    if (
        decision is None
        or not isinstance(trading_days, int)
        or isinstance(trading_days, bool)
        or trading_days < 64
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or max_workers < 1
        or not isinstance(live_fetch_decision_skew_seconds, int)
        or isinstance(live_fetch_decision_skew_seconds, bool)
        or live_fetch_decision_skew_seconds < 0
    ):
        return {
            code: build_fund_benchmark_research(
                None,
                row.get("benchmark_spec") if isinstance(row.get("benchmark_spec"), Mapping) else {},
                {},
                decision_at=decision_at,
                management_style=_management_style(row),
            )
            for code, row in sorted(normalized.items())
        }

    eligible: dict[str, dict[str, Any]] = {}
    immediate: dict[str, dict[str, Any]] = {}
    unique_components: dict[str, dict[str, Any]] = {}
    for code, row in sorted(normalized.items()):
        spec = (
            dict(row.get("benchmark_spec"))
            if isinstance(row.get("benchmark_spec"), Mapping)
            else {}
        )
        comparison = resolve_benchmark_comparison(spec, decision_at=decision_at)
        if comparison.get("comparison_role") == "unavailable":
            immediate[code] = build_fund_benchmark_research(
                None,
                spec,
                {},
                decision_at=decision_at,
                management_style=_management_style(row),
            )
            continue
        eligible[code] = row
        components = [
            dict(item)
            for item in spec.get("components") or []
            if isinstance(item, Mapping)
        ]
        if not components:
            identity = comparison.get("benchmark_code") or comparison.get("benchmark_name")
            if identity:
                components = [
                    {
                        "component_id": str(identity),
                        "benchmark_code": comparison.get("benchmark_code"),
                        "name": comparison.get("benchmark_name"),
                        "weight_percent": 100.0,
                    }
                ]
        for component in components:
            unique_components.setdefault(_component_identity(component), component)
    if not eligible:
        return dict(sorted(immediate.items()))

    uses_live_default_provider = fetch_fund is None or fetch_component is None
    now_utc = datetime.now(timezone.utc)
    decision_utc = decision[0].astimezone(timezone.utc)
    clock_skew_seconds = (now_utc - decision_utc).total_seconds()
    if uses_live_default_provider and not (
        -300 <= clock_skew_seconds <= live_fetch_decision_skew_seconds
    ):
        for code, row in sorted(eligible.items()):
            spec = (
                dict(row.get("benchmark_spec"))
                if isinstance(row.get("benchmark_spec"), Mapping)
                else {}
            )
            unavailable = build_fund_benchmark_research(
                None,
                spec,
                {},
                decision_at=decision_at,
                management_style=_management_style(row),
            )
            immediate[code] = _finish(
                unavailable,
                status="unavailable",
                reasons=["historical_live_fetch_disallowed"],
            )
        return dict(sorted(immediate.items()))

    fund_fetcher = fetch_fund or _default_fund_fetcher
    component_fetcher = fetch_component or _default_component_fetcher
    available_at = decision[0].isoformat()
    start_date = (decision[1] - timedelta(days=max(trading_days * 2, 400))).isoformat()
    end_date = decision[1].isoformat()

    def fetch_one_fund(item: tuple[str, dict[str, Any]]) -> tuple[str, object]:
        code, row = item
        try:
            raw = fund_fetcher(
                code,
                str(row.get("fund_name") or code),
                trading_days,
            )
        except Exception:
            raw = None
        return code, _snapshot_envelope(
            raw,
            available_at=available_at,
            fallback_source="fund_nav_provider",
        )

    def fetch_one_component(item: tuple[str, dict[str, Any]]) -> tuple[str, object]:
        identity, component = item
        try:
            raw = component_fetcher(
                component,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            raw = None
        return identity, _snapshot_envelope(
            raw,
            available_at=available_at,
            fallback_source="benchmark_component_provider",
        )

    jobs = len(eligible) + len(unique_components)
    with ThreadPoolExecutor(max_workers=min(max_workers, max(jobs, 1))) as executor:
        fund_payloads = dict(executor.map(fetch_one_fund, sorted(eligible.items())))
        component_payloads = dict(
            executor.map(fetch_one_component, sorted(unique_components.items()))
        )

    output = dict(immediate)
    for code, row in sorted(eligible.items()):
        spec = (
            dict(row.get("benchmark_spec"))
            if isinstance(row.get("benchmark_spec"), Mapping)
            else {}
        )
        output[code] = build_fund_benchmark_research(
            fund_payloads.get(code),
            spec,
            component_payloads,
            decision_at=decision_at,
            management_style=_management_style(row),
        )
    return dict(sorted(output.items()))


def attach_fund_benchmark_metrics(
    funds: Sequence[Mapping[str, Any]],
    metrics_by_code: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach precomputed metrics without changing candidate order."""

    output: list[dict[str, Any]] = []
    for raw in funds:
        row = dict(raw)
        code = _fund_code(row.get("fund_code"))
        metrics = metrics_by_code.get(code or "")
        row["benchmark_metrics"] = dict(metrics) if isinstance(metrics, Mapping) else {}
        output.append(row)
    return output


def summarize_benchmark_research(
    metrics_by_code: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in metrics_by_code.values() if isinstance(row, Mapping)]
    return {
        "schema_version": BENCHMARK_RESEARCH_SCHEMA_VERSION,
        "calculation_policy": "strict_pit_aligned_before_generation",
        "formal_excess_policy": "verified_fund_contract_only",
        "reference_policy": "tracking_reference_never_formal",
        "execution_policy": "descriptive_only_not_amount_signal",
        "fund_count": len(rows),
        "qualified_count": sum(row.get("status") == "qualified" for row in rows),
        "formal_excess_count": sum(
            row.get("status") == "qualified"
            and row.get("comparison_role") == "formal_excess"
            for row in rows
        ),
        "tracking_reference_count": sum(
            row.get("status") == "qualified"
            and row.get("comparison_role") == "tracking_reference"
            for row in rows
        ),
        "unavailable_count": sum(row.get("status") != "qualified" for row in rows),
    }


def _decision_context(value: str | datetime) -> tuple[datetime, date] | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    try:
        session = build_trading_session(moment)
        canonical = datetime.fromisoformat(
            str(session["decision_at"]).replace("Z", "+00:00")
        )
        effective = date.fromisoformat(str(session["effective_trade_date"]))
    except (KeyError, TypeError, ValueError):
        return None
    return canonical, effective


def _default_fund_fetcher(code: str, name: str, trading_days: int) -> object:
    from app.services.fund_data import FundDataService

    return FundDataService().get_nav_history(
        code,
        name,
        trading_days=trading_days,
    )


def _default_component_fetcher(
    component: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> object:
    from app.services.benchmark_fee_evaluation import default_benchmark_fetcher

    return default_benchmark_fetcher(
        dict(component),
        start_date=start_date,
        end_date=end_date,
    )


def _snapshot_envelope(
    raw: object,
    *,
    available_at: str,
    fallback_source: str,
) -> object:
    if raw is None:
        return None
    source = fallback_source
    points: object = None
    if isinstance(raw, Mapping):
        source = str(raw.get("source") or fallback_source).strip() or fallback_source
        points = raw.get("points")
        if points is None:
            points = raw.get("data") or raw.get("rows")
    elif hasattr(raw, "points"):
        source = str(getattr(raw, "source", None) or fallback_source).strip()
        points = getattr(raw, "points", None)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        points = raw
    if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
        normalized_points: list[object] = []
        for item in points:
            if hasattr(item, "model_dump"):
                normalized_points.append(item.model_dump(mode="json"))
            elif isinstance(item, Mapping):
                normalized_points.append(dict(item))
            else:
                normalized_points.append(item)
        points = normalized_points
    return {
        "points": points,
        "source": source,
        "available_at": available_at,
    }


def _management_style(row: Mapping[str, Any]) -> str | None:
    group = row.get("peer_group")
    if isinstance(group, Mapping) and group.get("management_style"):
        return str(group.get("management_style"))
    exposure = row.get("risk_exposure")
    if isinstance(exposure, Mapping) and exposure.get("management_style"):
        return str(exposure.get("management_style"))
    text = " ".join(
        str(row.get(key) or "")
        for key in ("fund_type", "fund_name")
    ).casefold()
    if "指数增强" in text or "增强指数" in text:
        return "enhanced_index"
    if "指数" in text or "etf" in text:
        return "passive_index"
    return "active" if text.strip() else None


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        return None
    code = text.zfill(6)
    return code if code != "000000" else None


def _base_payload(
    *,
    comparison: Mapping[str, Any],
    decision: tuple[datetime, date] | None,
    horizons: Sequence[tuple[str, int]],
    minimum_aligned_return_days: int,
    rolling_window_days: int,
    max_endpoint_lag_calendar_days: int,
) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_RESEARCH_SCHEMA_VERSION,
        "decision_at": decision[0].isoformat() if decision else None,
        "effective_trade_date": decision[1].isoformat() if decision else None,
        "status": "unavailable",
        "qualified": False,
        "descriptive_only": True,
        "execution_tilt_eligible": False,
        "comparison_role": comparison.get("comparison_role"),
        "formal_excess_eligible": comparison.get("formal_excess_eligible") is True,
        "mapping_id": comparison.get("mapping_id"),
        "benchmark_code": comparison.get("benchmark_code"),
        "benchmark_name": comparison.get("benchmark_name"),
        "contract_verification_kind": comparison.get("contract_verification_kind"),
        "benchmark_identity_reason": comparison.get("reason"),
        "configuration": {
            "horizons": [
                {"label": str(label), "required_return_days": days}
                for label, days in horizons
            ],
            "minimum_aligned_return_days": minimum_aligned_return_days,
            "rolling_window_days": rolling_window_days,
            "max_endpoint_lag_calendar_days": max_endpoint_lag_calendar_days,
        },
        "alignment": {},
        "fund_series": {},
        "components": [],
        "horizons": {},
        "rolling_comparison": {},
        "tracking_metrics": {},
        "reason_codes": [],
    }


def _validate_configuration(
    *,
    horizons: Sequence[tuple[str, int]],
    minimum_aligned_return_days: int,
    rolling_window_days: int,
    max_endpoint_lag_calendar_days: int,
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(minimum_aligned_return_days, int) or isinstance(
        minimum_aligned_return_days, bool
    ) or minimum_aligned_return_days < 2:
        reasons.append("minimum_aligned_return_days_invalid")
    if not isinstance(rolling_window_days, int) or isinstance(
        rolling_window_days, bool
    ) or rolling_window_days < 2:
        reasons.append("rolling_window_days_invalid")
    if not isinstance(max_endpoint_lag_calendar_days, int) or isinstance(
        max_endpoint_lag_calendar_days, bool
    ) or max_endpoint_lag_calendar_days < 0:
        reasons.append("max_endpoint_lag_calendar_days_invalid")
    labels: set[str] = set()
    if isinstance(horizons, (str, bytes)) or not isinstance(horizons, Sequence):
        reasons.append("horizons_invalid")
        return reasons
    for row in horizons:
        if (
            not isinstance(row, Sequence)
            or len(row) != 2
            or not str(row[0]).strip()
            or not isinstance(row[1], int)
            or isinstance(row[1], bool)
            or row[1] < 2
        ):
            reasons.append("horizon_definition_invalid")
            continue
        label = str(row[0]).strip()
        if label in labels:
            reasons.append("horizon_label_duplicated")
        labels.add(label)
    if not labels:
        reasons.append("horizons_empty")
    return _unique(reasons)


def _normalize_snapshot(
    payload: object,
    *,
    decision: datetime,
    cutoff: date,
    value_keys: Sequence[str],
    series_id: str,
) -> tuple[dict[date, float] | None, str | None]:
    if not isinstance(payload, Mapping):
        return None, f"{series_id}_snapshot_envelope_missing"
    available = _aware_datetime(payload.get("available_at"))
    if available is None:
        return None, f"{series_id}_snapshot_available_at_missing_or_invalid"
    if available > decision.astimezone(timezone.utc):
        return None, f"{series_id}_snapshot_available_after_decision"
    source = str(payload.get("source") or "").strip()
    if not source:
        return None, f"{series_id}_source_missing"
    rows = payload.get("points")
    if rows is None:
        rows = payload.get("data") or payload.get("rows")
    if isinstance(rows, (str, bytes, Mapping)) or not isinstance(rows, Sequence):
        return None, f"{series_id}_points_missing"
    result: dict[date, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return None, f"{series_id}_point_invalid"
        day = _day(row.get("date") or row.get("day"))
        if day is None:
            return None, f"{series_id}_point_date_invalid"
        value = next((row.get(key) for key in value_keys if row.get(key) is not None), None)
        parsed = _positive_finite(value)
        if parsed is None:
            return None, f"{series_id}_point_value_invalid"
        if day in result:
            return None, f"{series_id}_point_date_duplicated"
        if day <= cutoff:
            result[day] = parsed
    if len(result) < 2:
        return None, f"{series_id}_points_before_decision_insufficient"
    return dict(sorted(result.items())), None


def _snapshot_audit(
    payload: object,
    *,
    point_count: int,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "point_count": point_count,
        **_snapshot_audit_fields(payload),
    }


def _snapshot_audit_fields(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"source": None, "available_at": None}
    return {
        "source": str(payload.get("source") or "").strip() or None,
        "available_at": (
            _aware_datetime(payload.get("available_at")).isoformat()
            if _aware_datetime(payload.get("available_at")) is not None
            else None
        ),
    }


def _component_weights(
    components: Sequence[Mapping[str, Any]],
) -> tuple[list[float] | None, str | None]:
    if not components:
        return None, "benchmark_components_missing"
    if len(components) == 1 and components[0].get("weight_percent") is None:
        return [100.0], None
    weights: list[float] = []
    for component in components:
        value = _nonnegative_finite(component.get("weight_percent"))
        if value is None:
            return None, "benchmark_component_weight_invalid"
        weights.append(value)
    if abs(sum(weights) - 100.0) > 0.1:
        return None, "benchmark_component_weights_do_not_sum_to_100"
    return weights, None


def _component_payload(
    payloads: Mapping[str, object],
    component: Mapping[str, Any],
) -> object:
    keys = [
        _component_identity(component),
        str(component.get("benchmark_code") or "").strip(),
        str(component.get("source_symbol") or "").strip(),
        str(component.get("name") or "").strip(),
    ]
    for key in keys:
        if key and key in payloads:
            return payloads[key]
    return None


def _component_identity(component: Mapping[str, Any]) -> str:
    return str(
        component.get("component_id")
        or component.get("benchmark_code")
        or component.get("source_symbol")
        or component.get("name")
        or "unknown"
    ).strip()


def _period_returns(values: Sequence[float]) -> list[float] | None:
    output: list[float] = []
    for previous, current in zip(values, values[1:], strict=False):
        if previous <= 0 or current <= 0:
            return None
        value = current / previous - 1.0
        if not math.isfinite(value):
            return None
        output.append(value)
    return output


def _composite_returns(
    component_values: Sequence[Sequence[float]],
    weights: Sequence[float],
) -> list[float] | None:
    returns = [_period_returns(values) for values in component_values]
    if any(values is None for values in returns):
        return None
    assert all(values is not None for values in returns)
    sample = len(returns[0]) if returns else 0
    if any(len(values or []) != sample for values in returns):
        return None
    output: list[float] = []
    for index in range(sample):
        value = sum(
            (values or [])[index] * weight / 100.0
            for values, weight in zip(returns, weights, strict=True)
        )
        if not math.isfinite(value) or value <= -1.0:
            return None
        output.append(value)
    return output


def _horizon_metrics(
    *,
    label: str,
    role: str,
    required_days: int,
    dates: Sequence[date],
    fund_returns: Sequence[float],
    benchmark_returns: Sequence[float],
) -> dict[str, Any]:
    fund_return = _cumulative_return_percent(fund_returns)
    benchmark_return = _cumulative_return_percent(benchmark_returns)
    fund_drawdown = _max_drawdown_percent(fund_returns)
    benchmark_drawdown = _max_drawdown_percent(benchmark_returns)
    difference = round(fund_return - benchmark_return, 6)
    return {
        "status": "available",
        "label": label,
        "required_return_days": required_days,
        "aligned_return_days": len(fund_returns),
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "fund_return_percent": fund_return,
        "benchmark_return_percent": benchmark_return,
        "formal_excess_return_percent": difference if role == "formal_excess" else None,
        "reference_difference_percent": (
            difference if role == "tracking_reference" else None
        ),
        "fund_max_drawdown_percent": fund_drawdown,
        "benchmark_max_drawdown_percent": benchmark_drawdown,
        # Positive means the fund lost less than the benchmark at its worst.
        "drawdown_advantage_percent": round(fund_drawdown - benchmark_drawdown, 6),
        "comparison_role": role,
    }


def _rolling_metrics(
    *,
    role: str,
    fund_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    window_days: int,
) -> dict[str, Any]:
    count = len(fund_returns) - window_days + 1
    if count <= 0:
        return {
            "status": "unavailable",
            "reason": "rolling_window_sample_insufficient",
            "window_days": window_days,
            "window_count": 0,
        }
    differences: list[float] = []
    wins = 0
    for index in range(count):
        fund_value = _cumulative_return_percent(
            fund_returns[index : index + window_days]
        )
        benchmark_value = _cumulative_return_percent(
            benchmark_returns[index : index + window_days]
        )
        difference = fund_value - benchmark_value
        differences.append(difference)
        if difference > 0:
            wins += 1
    return {
        "status": "available",
        "comparison_role": role,
        "window_days": window_days,
        "window_count": count,
        "formal_excess_win_rate_percent": (
            round(wins / count * 100.0, 4) if role == "formal_excess" else None
        ),
        "reference_outperformance_rate_percent": (
            round(wins / count * 100.0, 4)
            if role == "tracking_reference"
            else None
        ),
        "active_return_mean_percent": round(statistics.fmean(differences), 6),
        "active_return_stdev_percent": (
            round(statistics.stdev(differences), 6) if len(differences) > 1 else 0.0
        ),
        "positive_window_count": wins,
    }


def _tracking_metrics(
    *,
    applicable: bool,
    role: str,
    fund_returns: Sequence[float],
    benchmark_returns: Sequence[float],
) -> dict[str, Any]:
    if not applicable:
        return {
            "applicable": False,
            "available": False,
            "reason": "fund_strategy_not_tracking_or_enhanced_index",
        }
    active = [
        left - right
        for left, right in zip(fund_returns, benchmark_returns, strict=True)
    ]
    if len(active) < 2:
        return {
            "applicable": True,
            "available": False,
            "reason": "tracking_sample_insufficient",
        }
    fund_return = _cumulative_return_percent(fund_returns)
    benchmark_return = _cumulative_return_percent(benchmark_returns)
    tracking_error = statistics.stdev(active) * math.sqrt(252.0) * 100.0
    return {
        "applicable": True,
        "available": True,
        "comparison_role": role,
        "sample_days": len(active),
        "tracking_difference_percent": round(fund_return - benchmark_return, 6),
        "tracking_error_annualized_percent": round(tracking_error, 6),
        "mean_daily_active_return_percent": round(
            statistics.fmean(active) * 100.0,
            8,
        ),
        "formal_excess_eligible": role == "formal_excess",
    }


def _cumulative_return_percent(returns: Sequence[float]) -> float:
    value = 1.0
    for item in returns:
        value *= 1.0 + item
    return round((value - 1.0) * 100.0, 6)


def _max_drawdown_percent(returns: Sequence[float]) -> float:
    wealth = 1.0
    peak = 1.0
    minimum = 0.0
    for item in returns:
        wealth *= 1.0 + item
        peak = max(peak, wealth)
        minimum = min(minimum, wealth / peak - 1.0)
    return round(minimum * 100.0, 6)


def _finish(
    payload: dict[str, Any],
    *,
    status: str,
    reasons: Sequence[str],
) -> dict[str, Any]:
    payload["status"] = status
    payload["qualified"] = status == "qualified"
    payload["reason_codes"] = _unique(str(value) for value in reasons if value)
    material = dict(payload)
    material.pop("snapshot_hash", None)
    payload["snapshot_hash"] = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def _unavailable_comparison(reason: str) -> dict[str, Any]:
    return {
        "comparison_role": "unavailable",
        "formal_excess_eligible": False,
        "reason": reason,
    }


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(timezone.utc)


def _day(value: object) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _positive_finite(value: object) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_finite(value: object) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _unique(values: Sequence[str] | Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "BENCHMARK_RESEARCH_SCHEMA_VERSION",
    "attach_fund_benchmark_metrics",
    "build_fund_benchmark_research",
    "build_fund_benchmark_research_batch",
    "summarize_benchmark_research",
]
