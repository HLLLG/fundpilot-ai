from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Callable, Iterable


METRIC_CONTRACT_VERSION = "decision_outcome_metrics.v2"
METRIC_NAMES = (
    "gross_direction",
    "positive_net_return",
    "gross_excess",
    "net_excess",
)

BenchmarkFetcher = Callable[..., object]

_FORMAL_BENCHMARK_TIERS = {"fund_contract_exact"}
_REFERENCE_BENCHMARK_TIERS = {"tracked_index_exact", "category_proxy"}
_BULLISH_CLASSES = {"bullish", "buy"}
_DIRECTIONAL_CLASSES = {*_BULLISH_CLASSES, "bearish"}


def default_benchmark_fetcher(
    component: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> object:
    """Fetch a domestic numeric index component through the existing history client.

    Foreign, rate, FX and other unsupported components deliberately return ``None``.
    A formal composite benchmark is unavailable unless every frozen component can be
    valued; missing legs are never reweighted.
    """

    _ = start_date, end_date
    component_type = str(
        component.get("component_type") or component.get("kind") or "index"
    ).strip()
    if component_type not in {"index", "equity_index", "bond_index"}:
        return None
    symbol = str(
        component.get("source_symbol")
        or component.get("benchmark_code")
        or component.get("index_code")
        or component.get("code")
        or ""
    ).strip()
    if not symbol.isdigit():
        return None

    from app.services.index_daily_client import fetch_index_daily_history

    return fetch_index_daily_history(symbol, trading_days=800)


def evaluate_frozen_benchmark(
    benchmark: object,
    *,
    baseline_date: str,
    target_date: str,
    is_frozen: bool,
    fetch_component: BenchmarkFetcher | None = default_benchmark_fetcher,
) -> dict[str, Any]:
    """Evaluate a frozen point-in-time benchmark mapping without look-ahead.

    Only a complete ``fund_contract_exact`` mapping frozen into DecisionEvent v2 is
    eligible for formal excess-return metrics. Tracking/category mappings may still
    return a reference return, but are explicitly excluded from formal excess KPIs.
    """

    spec = dict(benchmark) if isinstance(benchmark, dict) else {}
    tier = _benchmark_tier(spec)
    base = {
        "tier": tier,
        "mapping_id": spec.get("mapping_id"),
        "is_frozen": bool(is_frozen),
        "formal_excess_eligible": False,
        "available": False,
        "return_percent": None,
        "reference_return_percent": None,
        "reason": None,
        "components": [],
    }
    if tier == "unavailable":
        return {**base, "reason": str(spec.get("reason") or "benchmark_unavailable")}
    if not is_frozen:
        return {**base, "reason": "benchmark_not_frozen_at_decision"}

    components = _benchmark_components(spec)
    if not components:
        return {**base, "reason": "benchmark_components_missing"}
    weights = _component_weights(components)
    if weights is None:
        return {**base, "reason": "benchmark_weights_invalid"}

    evaluated: list[dict[str, Any]] = []
    weighted_return = 0.0
    for component, weight in zip(components, weights, strict=True):
        rows = component.get("points") or component.get("data")
        if rows is None and fetch_component is not None:
            try:
                rows = fetch_component(
                    component,
                    start_date=baseline_date,
                    end_date=target_date,
                )
            except Exception as exc:  # Source failures are evidence states.
                return {
                    **base,
                    "reason": "benchmark_component_fetch_failed",
                    "provider_error": type(exc).__name__,
                    "components": evaluated,
                }
        points = _normalize_component_points(rows)
        aligned = _aligned_component_return(
            points,
            baseline_date=baseline_date,
            target_date=target_date,
            max_lag_calendar_days=_max_lag_days(component),
        )
        if aligned is None:
            return {
                **base,
                "reason": "benchmark_component_data_unavailable",
                "missing_component": _component_identity(component),
                "components": evaluated,
            }
        component_return, start_point, end_point = aligned
        evaluated.append(
            {
                "component": _component_identity(component),
                "weight_percent": weight,
                "baseline_date": start_point[0],
                "target_date": end_point[0],
                "return_percent": component_return,
            }
        )
        weighted_return += component_return * weight / 100.0

    calculated = round(weighted_return, 4)
    if tier in _FORMAL_BENCHMARK_TIERS:
        if not _formal_mapping_complete(spec):
            return {
                **base,
                "reason": "formal_benchmark_mapping_incomplete",
                "components": evaluated,
            }
        return {
            **base,
            "available": True,
            "formal_excess_eligible": True,
            "return_percent": calculated,
            "reason": None,
            "components": evaluated,
        }
    if tier in _REFERENCE_BENCHMARK_TIERS:
        return {
            **base,
            "available": True,
            "reference_return_percent": calculated,
            "reason": "reference_only_not_formal_benchmark",
            "components": evaluated,
        }
    return {**base, "reason": "benchmark_tier_unsupported", "components": evaluated}


def evaluate_decision_metrics(
    *,
    gross_return_percent: float | None,
    evaluation_class: str,
    fee_policy: object,
    benchmark_result: object,
) -> dict[str, dict[str, Any]]:
    """Build four non-overlapping metric contracts for one mature/pending outcome."""

    action_class = str(evaluation_class or "").strip()
    gross = _finite_float(gross_return_percent)
    fee = resolve_user_assumption_fee(fee_policy)
    benchmark = dict(benchmark_result) if isinstance(benchmark_result, dict) else {}
    formal_benchmark = (
        bool(benchmark.get("available"))
        and bool(benchmark.get("formal_excess_eligible"))
        and _finite_float(benchmark.get("return_percent")) is not None
    )

    gross_eligible = action_class in _DIRECTIONAL_CLASSES
    gross_mature = gross_eligible and gross is not None
    metrics = {
        "gross_direction": _metric(
            eligible=gross_eligible,
            mature=gross_mature,
            value=gross,
            hit=_direction_hit(action_class, gross) if gross_mature else None,
            unavailable_reason=None if gross_mature else "gross_return_unavailable",
        )
    }

    positive_net_eligible = action_class in _BULLISH_CLASSES
    net_return = round(gross - fee["rate_percent"], 4) if (
        positive_net_eligible and gross is not None and fee["available"]
    ) else None
    metrics["positive_net_return"] = _metric(
        eligible=positive_net_eligible,
        mature=net_return is not None,
        value=net_return,
        hit=net_return > 0 if net_return is not None else None,
        unavailable_reason=(
            None
            if net_return is not None
            else "gross_return_unavailable"
            if gross is None
            else "fee_assumption_not_frozen"
        ),
        metadata=fee,
    )

    gross_excess_eligible = gross_eligible
    benchmark_return = _finite_float(benchmark.get("return_percent"))
    gross_excess = round(gross - benchmark_return, 4) if (
        gross is not None and formal_benchmark and benchmark_return is not None
    ) else None
    metrics["gross_excess"] = _metric(
        eligible=gross_excess_eligible,
        mature=gross_excess is not None,
        value=gross_excess,
        hit=(
            _direction_hit(action_class, gross_excess)
            if gross_excess is not None
            else None
        ),
        unavailable_reason=(
            None
            if gross_excess is not None
            else "gross_return_unavailable"
            if gross is None
            else str(benchmark.get("reason") or "formal_benchmark_unavailable")
        ),
        metadata={"benchmark": benchmark},
    )

    net_excess = round(net_return - benchmark_return, 4) if (
        net_return is not None and formal_benchmark and benchmark_return is not None
    ) else None
    metrics["net_excess"] = _metric(
        eligible=positive_net_eligible,
        mature=net_excess is not None,
        value=net_excess,
        hit=net_excess > 0 if net_excess is not None else None,
        unavailable_reason=(
            None
            if net_excess is not None
            else "fee_adjusted_return_unavailable"
            if net_return is None
            else str(benchmark.get("reason") or "formal_benchmark_unavailable")
        ),
        metadata={"fee": fee, "benchmark": benchmark},
    )
    return metrics


def summarize_metrics(
    metric_sets: Iterable[object],
) -> dict[str, dict[str, Any]]:
    rows = [row for row in metric_sets if isinstance(row, dict)]
    result: dict[str, dict[str, Any]] = {}
    for name in METRIC_NAMES:
        values = [row.get(name) for row in rows if isinstance(row.get(name), dict)]
        eligible = sum(1 for value in values if value.get("eligible"))
        mature = sum(
            1 for value in values if value.get("eligible") and value.get("mature")
        )
        hits = sum(
            1
            for value in values
            if value.get("eligible")
            and value.get("mature")
            and value.get("hit") is True
        )
        result[name] = {
            "eligible_count": eligible,
            "mature_count": mature,
            "unavailable_count": max(eligible - mature, 0),
            "hit_count": hits,
            "miss_count": max(mature - hits, 0),
            "coverage_percent": round(mature / eligible * 100.0, 1) if eligible else None,
            "hit_rate_percent": round(hits / mature * 100.0, 1) if mature else None,
        }
    return result


def metric_aliases(metrics: object) -> dict[str, Any]:
    rows = dict(metrics) if isinstance(metrics, dict) else {}
    gross = rows.get("gross_direction") or {}
    net = rows.get("positive_net_return") or {}
    gross_excess = rows.get("gross_excess") or {}
    net_excess = rows.get("net_excess") or {}
    return {
        "gross_direction_return_percent": gross.get("value_percent"),
        "gross_direction_hit": gross.get("hit"),
        "positive_net_return_percent": net.get("value_percent"),
        "positive_net_return_hit": net.get("hit"),
        "gross_excess_return_percent": gross_excess.get("value_percent"),
        "gross_excess_hit": gross_excess.get("hit"),
        "net_excess_return_percent": net_excess.get("value_percent"),
        "net_excess_hit": net_excess.get("hit"),
    }


def resolve_user_assumption_fee(fee_policy: object) -> dict[str, Any]:
    policy = dict(fee_policy) if isinstance(fee_policy, dict) else {}
    rate = _finite_float(policy.get("round_trip_fee_percent"))
    source = str(policy.get("fee_source") or "").strip()
    calculation = str(policy.get("fee_calculation") or "").strip()
    status = str(policy.get("status") or "").strip()
    available = (
        rate is not None
        and rate >= 0
        and status in {"available", "frozen", "verified"}
        and source == "user_assumption"
        and calculation == "initial_principal_haircut"
    )
    return {
        "available": available,
        "fee_source": "user_assumption" if available else source or "unavailable",
        "rate_percent": rate if available else None,
        "fee_calculation": calculation or None,
        "is_actual_cost": False,
        "recurring_fund_expenses": "already_embedded_in_nav",
    }


def fee_policy_from_report(
    report: dict[str, Any],
    *,
    decision_kind: str,
) -> dict[str, Any]:
    facts_key = "analysis_facts" if decision_kind == "daily" else "discovery_facts"
    facts = report.get(facts_key) or {}
    source = (facts.get("portfolio") or {}) if decision_kind == "daily" else (
        facts.get("profile") or {}
    )
    rate = _finite_float(source.get("round_trip_fee_percent"))
    if rate is None or rate < 0:
        return {
            "status": "not_frozen",
            "fee_source": "unavailable",
            "round_trip_fee_percent": None,
            "fee_calculation": None,
            "is_actual_cost": False,
            "recurring_fund_expenses": "already_embedded_in_nav",
        }
    return {
        "status": "available",
        "fee_source": "user_assumption",
        "round_trip_fee_percent": rate,
        "fee_calculation": "initial_principal_haircut",
        "is_actual_cost": False,
        "recurring_fund_expenses": "already_embedded_in_nav",
    }


def find_frozen_decision_event(
    report: dict[str, Any],
    *,
    recommendation_index: int,
    fund_code: str | None,
) -> dict[str, Any] | None:
    events = report.get("decision_events") or []
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("schema_version") or "") != "decision_event.v2":
            continue
        try:
            index = int(event.get("recommendation_index"))
        except (TypeError, ValueError):
            continue
        if index != recommendation_index:
            continue
        event_code = str(event.get("fund_code") or "").strip()
        if fund_code and event_code and event_code != fund_code:
            continue
        return dict(event)
    return None


def is_formal_v2_metric_event(
    report: dict[str, Any],
    event: object,
) -> bool:
    contract = report.get("decision_contract") or {}
    if not isinstance(contract, dict):
        return False
    if contract.get("persistence") != "persisted":
        return False
    if contract.get("audit_eligible") is not True:
        return False
    if not isinstance(event, dict):
        return False
    return (
        str(event.get("schema_version") or "") == "decision_event.v2"
        and event.get("metric_eligible") is True
    )


def _metric(
    *,
    eligible: bool,
    mature: bool,
    value: float | None,
    hit: bool | None,
    unavailable_reason: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "eligible": bool(eligible),
        "mature": bool(eligible and mature),
        "value_percent": round(value, 4) if value is not None else None,
        "hit": hit if eligible and mature else None,
        "unavailable_reason": unavailable_reason if eligible and not mature else None,
        **({"metadata": metadata} if metadata else {}),
    }


def _direction_hit(evaluation_class: str, value: float | None) -> bool:
    if value is None:
        return False
    if evaluation_class in _BULLISH_CLASSES:
        return value > 0
    if evaluation_class == "bearish":
        return value < 0
    return False


def _benchmark_tier(spec: dict[str, Any]) -> str:
    raw = str(spec.get("tier") or spec.get("benchmark_kind") or "").strip()
    aliases = {
        "official_contract": "fund_contract_exact",
        "contract_exact": "fund_contract_exact",
        "tracking_index": "tracked_index_exact",
        "tracked_index": "tracked_index_exact",
        "proxy": "category_proxy",
    }
    tier = aliases.get(raw, raw)
    return tier if tier in {*_FORMAL_BENCHMARK_TIERS, *_REFERENCE_BENCHMARK_TIERS} else "unavailable"


def _formal_mapping_complete(spec: dict[str, Any]) -> bool:
    status = str(spec.get("status") or spec.get("completeness") or "").strip()
    if status not in {"available", "complete", "frozen", "verified"}:
        return False
    # Formal eligibility is opt-in evidence produced by the point-in-time
    # contract parser. A tier/status label alone must never promote an old or
    # malformed mapping into the official excess-return denominator.
    return (
        spec.get("formal_excess_eligible") is True
        and bool(str(spec.get("mapping_id") or "").strip())
    )


def _benchmark_components(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows = spec.get("components") or []
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _component_weights(components: list[dict[str, Any]]) -> list[float] | None:
    if len(components) == 1 and components[0].get("weight_percent") is None:
        return [100.0]
    weights: list[float] = []
    for component in components:
        value = _finite_float(component.get("weight_percent"))
        if value is None or value < 0:
            return None
        weights.append(value)
    if abs(sum(weights) - 100.0) > 0.1:
        return None
    return weights


def _normalize_component_points(payload: object) -> list[tuple[str, float]]:
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows") or payload.get("points")
    if not isinstance(rows, list):
        return []
    by_date: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_date(row.get("date") or row.get("day"))
        value = _positive_float(
            row.get("close")
            if row.get("close") is not None
            else row.get("value")
            if row.get("value") is not None
            else row.get("nav")
        )
        if day and value is not None:
            by_date[day] = value
    return sorted(by_date.items())


def _aligned_component_return(
    points: list[tuple[str, float]],
    *,
    baseline_date: str,
    target_date: str,
    max_lag_calendar_days: int,
) -> tuple[float, tuple[str, float], tuple[str, float]] | None:
    baseline = next((point for point in reversed(points) if point[0] <= baseline_date), None)
    target = next((point for point in reversed(points) if point[0] <= target_date), None)
    if baseline is None or target is None or target[0] <= baseline[0]:
        return None
    if _calendar_lag(baseline[0], baseline_date) > max_lag_calendar_days:
        return None
    if _calendar_lag(target[0], target_date) > max_lag_calendar_days:
        return None
    value = round((target[1] / baseline[1] - 1.0) * 100.0, 4)
    return value, baseline, target


def _max_lag_days(component: dict[str, Any]) -> int:
    try:
        value = int(component.get("max_lag_calendar_days", 7))
    except (TypeError, ValueError):
        value = 7
    return max(0, min(value, 31))


def _calendar_lag(available: str, requested: str) -> int:
    try:
        return max((date.fromisoformat(requested) - date.fromisoformat(available)).days, 0)
    except ValueError:
        return 10_000


def _component_identity(component: dict[str, Any]) -> str:
    return str(
        component.get("component_id")
        or component.get("benchmark_code")
        or component.get("index_code")
        or component.get("code")
        or component.get("name")
        or "unknown"
    )


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _positive_float(value: object) -> float | None:
    parsed = _finite_float(value)
    return parsed if parsed is not None and parsed > 0 else None
