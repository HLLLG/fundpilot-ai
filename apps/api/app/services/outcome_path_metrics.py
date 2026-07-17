"""Formal shadow metrics for decision paths and no-action counterfactuals."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from app.services.benchmark_fee_evaluation import resolve_user_assumption_fee


PATH_METRIC_CONTRACT_VERSION = "decision_path_metrics.v1"
COUNTERFACTUAL_CONTRACT_VERSION = "decision_counterfactual.v1"
STRATEGY_EVALUATION_POLICY_VERSION = "strategy_evaluation.2026-07.v1"
SHADOW_HORIZONS = (5, 20, 60)
MIN_CVAR_OBSERVATIONS = 20


def build_strategy_evaluation_policy(
    *,
    decision_kind: str,
    report: Mapping[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Register the current strategy and every pre-declared comparison arm."""

    report_row = dict(report or {})
    fact_row = dict(facts or {})
    analysis_mode = str(
        report_row.get("analysis_mode")
        or fact_row.get("analysis_mode")
        or ((fact_row.get("pipeline") or {}).get("analysis_mode") if isinstance(fact_row.get("pipeline"), Mapping) else "")
        or "unknown"
    )
    if decision_kind == "discovery":
        discovery_strategy = str(
            report_row.get("discovery_strategy")
            or fact_row.get("discovery_strategy")
            or "unknown"
        )
        selection_strategy = str(
            report_row.get("selection_strategy")
            or fact_row.get("selection_strategy")
            or "unknown"
        )
    else:
        discovery_strategy = "not_applicable"
        selection_strategy = "not_applicable"
    return {
        "schema_version": "strategy_evaluation_policy.v1",
        "policy_version": STRATEGY_EVALUATION_POLICY_VERSION,
        "mode": "shadow_record_only",
        "decision_kind": decision_kind,
        "strategy_context": {
            "analysis_mode": analysis_mode,
            "discovery_strategy": discovery_strategy,
            "selection_strategy": selection_strategy,
        },
        "objective": (
            "maximize_after_cost_risk_adjusted_expected_total_return_subject_to_"
            "suitability_liquidity_concentration_and_drawdown_constraints"
        ),
        "primary_metric": "net_excess_return_percent",
        "horizons_trading_days": list(SHADOW_HORIZONS),
        "return_basis": "total_return_daily_growth_first",
        "comparators": [
            {
                "id": "no_action",
                "scope": "recommendation_action",
                "status": "implemented_when_position_change_is_frozen",
            },
            {
                "id": "formal_fund_benchmark",
                "scope": "fund_contract_or_exact_type_benchmark",
                "status": "implemented_when_pit_mapping_is_complete",
            },
            {
                "id": "quality_only_peer",
                "scope": "same_sector_eligible_candidate_pool",
                "status": "implemented_when_candidate_snapshot_is_complete",
            },
            {
                "id": "low_fee_peer",
                "scope": "same_sector_eligible_candidate_pool",
                "status": "implemented_when_pit_cost_is_complete",
            },
            {
                "id": "seeded_random_peer",
                "scope": "same_sector_eligible_candidate_pool",
                "status": "implemented_with_frozen_deterministic_seed",
            },
        ],
    }


def build_path_metrics(
    points: list[tuple[str, float]],
    *,
    baseline_index: int,
    target_index: int,
) -> dict[str, Any]:
    """Compute MAE/MFE/path drawdown and daily historical CVaR on a frozen path."""

    if (
        baseline_index < 0
        or target_index <= baseline_index
        or target_index >= len(points)
    ):
        return unavailable_path_metrics("mature_total_return_path_unavailable")
    values = [value for _day, value in points[baseline_index : target_index + 1]]
    if len(values) < 2 or any(value <= 0 or not math.isfinite(value) for value in values):
        return unavailable_path_metrics("mature_total_return_path_invalid")

    baseline = values[0]
    excursions = [(value / baseline - 1.0) * 100.0 for value in values]
    peak = values[0]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    for index, value in enumerate(values):
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, (value / peak - 1.0) * 100.0)
        if index:
            daily_returns.append((value / values[index - 1] - 1.0) * 100.0)

    cvar = _daily_cvar_95(daily_returns)
    return {
        "schema_version": PATH_METRIC_CONTRACT_VERSION,
        "available": True,
        "basis": "total_return_daily_growth_first",
        "sample_days": len(daily_returns),
        "max_adverse_excursion_percent": round(min(excursions), 4),
        "max_favorable_excursion_percent": round(max(excursions), 4),
        "max_drawdown_percent": round(max_drawdown, 4),
        "daily_cvar_95": cvar,
        "unavailable_reason": None,
    }


def unavailable_path_metrics(reason: str) -> dict[str, Any]:
    return {
        "schema_version": PATH_METRIC_CONTRACT_VERSION,
        "available": False,
        "basis": "total_return_daily_growth_first",
        "sample_days": 0,
        "max_adverse_excursion_percent": None,
        "max_favorable_excursion_percent": None,
        "max_drawdown_percent": None,
        "daily_cvar_95": {
            "available": False,
            "confidence_level": 0.95,
            "value_percent": None,
            "tail_observation_count": 0,
            "unavailable_reason": reason,
        },
        "unavailable_reason": reason,
    }


def evaluate_no_action_counterfactual(
    *,
    gross_return_percent: float | None,
    evaluation_class: str,
    recommendation: Mapping[str, Any] | None,
    fee_policy: object,
) -> dict[str, Any]:
    """Compare the recommended exposure change with leaving that exposure unchanged.

    The result is expressed per changed notional. We deliberately do not label it a
    portfolio return because daily reductions are relative to a holding while discovery
    allocations can be relative to a cash budget.
    """

    recommendation_row = dict(recommendation or {})
    position_change = _finite_float(
        recommendation_row.get("suggested_position_change_percent")
    )
    result = {
        "schema_version": COUNTERFACTUAL_CONTRACT_VERSION,
        "comparator": "no_action",
        "available": False,
        "basis": "incremental_return_per_changed_notional",
        "position_change_percent": position_change,
        "position_change_basis": str(
            recommendation_row.get("suggested_position_change_basis") or ""
        )
        or None,
        "no_action_incremental_return_percent": 0.0,
        "recommended_action_incremental_return_percent": None,
        "incremental_value_add_percent": None,
        "hit": None,
        "unavailable_reason": None,
    }
    gross = _finite_float(gross_return_percent)
    if gross is None:
        result["unavailable_reason"] = "gross_total_return_unavailable"
        return result
    if position_change is None or position_change == 0:
        result["unavailable_reason"] = "frozen_position_change_unavailable"
        return result

    action_class = str(evaluation_class or "").strip()
    if action_class in {"bullish", "buy"} and position_change > 0:
        exposure_direction = 1.0
    elif action_class == "bearish" and position_change < 0:
        exposure_direction = -1.0
    else:
        result["unavailable_reason"] = "position_change_direction_inconsistent"
        return result

    fee = resolve_user_assumption_fee(fee_policy)
    if not fee["available"]:
        result["unavailable_reason"] = "fee_assumption_not_frozen"
        return result
    incremental = exposure_direction * gross - float(fee["rate_percent"])
    result.update(
        {
            "available": True,
            "recommended_action_incremental_return_percent": round(incremental, 4),
            "incremental_value_add_percent": round(incremental, 4),
            "hit": incremental > 0,
            "fee": fee,
            "unavailable_reason": None,
        }
    )
    return result


def summarize_path_metrics(rows: Iterable[object]) -> dict[str, Any]:
    metrics = [dict(row) for row in rows if isinstance(row, Mapping)]
    available = [row for row in metrics if row.get("available")]
    return {
        "contract_version": PATH_METRIC_CONTRACT_VERSION,
        "eligible_count": len(metrics),
        "available_count": len(available),
        "coverage_percent": (
            round(len(available) / len(metrics) * 100.0, 1) if metrics else None
        ),
        "average_max_adverse_excursion_percent": _average(
            row.get("max_adverse_excursion_percent") for row in available
        ),
        "average_max_favorable_excursion_percent": _average(
            row.get("max_favorable_excursion_percent") for row in available
        ),
        "average_max_drawdown_percent": _average(
            row.get("max_drawdown_percent") for row in available
        ),
        "cvar_available_count": sum(
            1 for row in available if (row.get("daily_cvar_95") or {}).get("available")
        ),
    }


def summarize_no_action_counterfactuals(rows: Iterable[object]) -> dict[str, Any]:
    values = [dict(row) for row in rows if isinstance(row, Mapping)]
    available = [row for row in values if row.get("available")]
    hits = sum(1 for row in available if row.get("hit") is True)
    return {
        "contract_version": COUNTERFACTUAL_CONTRACT_VERSION,
        "eligible_count": len(values),
        "available_count": len(available),
        "coverage_percent": (
            round(len(available) / len(values) * 100.0, 1) if values else None
        ),
        "value_add_count": hits,
        "value_add_rate_percent": (
            round(hits / len(available) * 100.0, 1) if available else None
        ),
        "average_incremental_value_add_percent": _average(
            row.get("incremental_value_add_percent") for row in available
        ),
    }


def _daily_cvar_95(daily_returns: list[float]) -> dict[str, Any]:
    if len(daily_returns) < MIN_CVAR_OBSERVATIONS:
        return {
            "available": False,
            "confidence_level": 0.95,
            "value_percent": None,
            "tail_observation_count": 0,
            "unavailable_reason": f"requires_{MIN_CVAR_OBSERVATIONS}_daily_observations",
        }
    tail_count = max(1, math.ceil(len(daily_returns) * 0.05))
    tail = sorted(daily_returns)[:tail_count]
    return {
        "available": True,
        "confidence_level": 0.95,
        "value_percent": round(sum(tail) / len(tail), 4),
        "tail_observation_count": tail_count,
        "unavailable_reason": None,
    }


def _average(values: Iterable[object]) -> float | None:
    normalized = [value for raw in values if (value := _finite_float(raw)) is not None]
    return round(sum(normalized) / len(normalized), 4) if normalized else None


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
