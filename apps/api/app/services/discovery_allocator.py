from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import floor, isfinite, sqrt
from typing import Any

from app.services.fund_tradeability import TRADEABILITY_GATE_SCHEMA_VERSION


ALLOCATION_PLAN_SCHEMA_VERSION = "discovery_allocation_plan.v1"
RISK_CONTEXT_SCHEMA_VERSION = "discovery_risk_context.v1"
PRIORITY_INPUT_SCHEMA_VERSION = "discovery_priority.v1"
PEER_RANK_SCHEMA_VERSION = "peer_rank.v1"

CURRENT_AMOUNT_SEMANTICS = "current_verified_initial_tranche"
RISK_AWARE_MODE = "qualified_risk_context"
QUALIFIED_RISK_ONLY_MODE = "qualified_equal_risk_only"
BLOCKED_MODE = "blocked_fail_closed"

_STYLE_TRANCHE_RATIOS: dict[str, tuple[float, float]] = {
    # (prefer_dca=True, prefer_dca=False)
    "conservative": (0.25, 0.35),
    "tactical": (0.30, 0.40),
    "aggressive": (0.35, 0.50),
}


@dataclass(frozen=True)
class _Candidate:
    code: str
    sector: str
    minimum_yuan: float
    cap_yuan: float
    weight: float
    priority_score: float | None
    peer_score: float | None
    peer_tilt_status: str
    risk_multiplier: float
    current_portfolio_correlation_penalty: float


@dataclass(frozen=True)
class _RiskResolution:
    available: bool
    status: str
    reason_codes: tuple[str, ...]
    drawdown_by_code: dict[str, float]
    covariance_by_code: dict[str, dict[str, float]]
    current_portfolio_correlation_penalty_by_code: dict[str, float]


def allocate_discovery_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    requested_budget_yuan: float | int | None,
    confirmed_cash_yuan: float | int | None,
    existing_sector_exposure_yuan: Mapping[str, float | int] | None,
    concentration_denominator_yuan: float | int | None,
    concentration_limit_percent: float | int | None,
    prefer_dca: bool,
    decision_style: str,
    risk_context: Mapping[str, Any] | None = None,
    priority_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    amount_step_yuan: float | int = 100,
) -> dict[str, Any]:
    """Allocate one verified initial tranche across all candidates at once.

    This boundary intentionally consumes only deterministic facts. In
    particular, candidate ``suggested_amount_yuan``, prose, LLM action, and
    input order are ignored. Each executable candidate must carry an eligible
    ``fund_tradeability_gate.v1`` either at ``tradeability_gate`` or under
    ``tradeability.tradeability_gate``.

    ``risk_context`` is optional and injectable. It is usable only when it is
    a qualified ``discovery_risk_context.v1`` containing complete per-code
    maximum drawdown and a finite symmetric covariance matrix. Missing or
    unqualified risk evidence blocks allocation completely. ``risk_only`` here
    means inverse-risk allocation backed by that qualified context when no
    qualified priority or peer tilt exists; it never means allocating without
    risk data. A descriptive peer percentile is ignored unless its contract
    separately carries an explicit execution qualification.
    """

    budget = _finite_nonnegative(requested_budget_yuan)
    cash = _finite_nonnegative(confirmed_cash_yuan)
    denominator = _finite_positive(concentration_denominator_yuan)
    concentration = _finite_positive(concentration_limit_percent)
    step = _finite_positive(amount_step_yuan)
    exposures, exposure_errors = _normalize_exposures(existing_sector_exposure_yuan)

    input_errors: list[str] = list(exposure_errors)
    if budget is None or budget <= 0:
        input_errors.append("requested_budget_invalid")
    if cash is None:
        input_errors.append("confirmed_cash_unavailable")
    if denominator is None:
        input_errors.append("concentration_denominator_invalid")
    if concentration is None or concentration > 100:
        input_errors.append("concentration_limit_invalid")
    if not isinstance(prefer_dca, bool):
        input_errors.append("prefer_dca_invalid")
    if decision_style not in _STYLE_TRANCHE_RATIOS:
        input_errors.append("decision_style_invalid")
    if step is None:
        input_errors.append("amount_step_invalid")
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        input_errors.append("candidates_invalid")

    if input_errors:
        return _blocked_plan(
            requested_budget_yuan=budget,
            confirmed_cash_yuan=cash,
            reason_codes=input_errors,
        )

    assert budget is not None
    assert cash is not None
    assert denominator is not None
    assert concentration is not None
    assert step is not None
    assert exposures is not None

    normalized_rows = [dict(row) for row in candidates if isinstance(row, Mapping)]
    malformed_row_count = len(candidates) - len(normalized_rows)
    codes = [_candidate_code(row) for row in normalized_rows]
    duplicate_codes = {
        code for code, count in Counter(code for code in codes if code).items() if count > 1
    }

    preliminary: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in normalized_rows:
        normalized, reasons = _normalize_candidate(
            row,
            duplicate_codes=duplicate_codes,
            current_budget_ceiling_yuan=min(budget, cash),
            amount_step_yuan=step,
            priority_inputs=priority_inputs,
        )
        if normalized is None:
            excluded.append(_excluded_candidate(row, reasons))
        else:
            preliminary.append(normalized)

    for index in range(malformed_row_count):
        excluded.append(
            {
                "fund_code": f"invalid-row-{index + 1}",
                "sector_name": None,
                "reason_codes": ["candidate_not_mapping"],
            }
        )

    if not preliminary:
        return _preallocation_blocked_plan(
            requested_budget_yuan=budget,
            confirmed_cash_yuan=cash,
            excluded_candidates=excluded,
            risk_status="not_evaluated_no_eligible_candidates",
            reason_codes=["no_gate_eligible_candidates"],
        )

    risk = _resolve_risk_context(risk_context, [row["code"] for row in preliminary])
    if not risk.available:
        return _preallocation_blocked_plan(
            requested_budget_yuan=budget,
            confirmed_cash_yuan=cash,
            excluded_candidates=excluded,
            risk_status=risk.status,
            reason_codes=list(risk.reason_codes),
        )

    qualified_tilt_available = any(
        row["priority_score"] is not None or row["peer_score"] is not None
        for row in preliminary
    )
    allocation_mode = (
        RISK_AWARE_MODE if qualified_tilt_available else QUALIFIED_RISK_ONLY_MODE
    )
    nominal_ratio = _STYLE_TRANCHE_RATIOS[decision_style][0 if prefer_dca else 1]
    tranche_ratio = nominal_ratio
    spendable_total = min(budget, cash)
    current_tranche_cap = _floor_step(spendable_total * tranche_ratio, step)

    eligible: list[_Candidate] = []
    for row in preliminary:
        risk_multiplier = _risk_multiplier(row["code"], risk)
        portfolio_penalty = risk.current_portfolio_correlation_penalty_by_code[
            row["code"]
        ]
        priority_score = row["priority_score"]
        peer_score = row["peer_score"]
        # Priority and peer evidence are deliberately bounded tilts. Neither
        # can override a hard transaction, cash, or concentration gate.
        priority_tilt = 1.0 + (priority_score or 0.0) / 400.0
        peer_tilt = 1.0 + (peer_score or 0.0) / 1000.0
        weight = max(0.05, priority_tilt * peer_tilt * risk_multiplier)
        eligible.append(
            _Candidate(
                code=row["code"],
                sector=row["sector"],
                minimum_yuan=row["minimum_yuan"],
                cap_yuan=min(row["cap_yuan"], current_tranche_cap),
                weight=weight,
                priority_score=priority_score,
                peer_score=peer_score,
                peer_tilt_status=row["peer_tilt_status"],
                risk_multiplier=risk_multiplier,
                current_portfolio_correlation_penalty=portfolio_penalty,
            )
        )

    ranked = sorted(eligible, key=lambda row: (-row.weight, row.code))
    sector_caps = _sector_caps(
        ranked,
        requested_budget_yuan=budget,
        denominator_yuan=denominator,
        concentration_limit_percent=concentration,
        exposures=exposures,
        amount_step_yuan=step,
    )

    selected: list[_Candidate] = []
    selected_minimums: dict[str, float] = {}
    selected_sector_minimums: dict[str, float] = {}
    committed_minimum = 0.0
    for candidate in ranked:
        minimum = _ceil_step(candidate.minimum_yuan, step)
        cap = _floor_step(candidate.cap_yuan, step)
        if cap < minimum:
            excluded.append(
                _excluded_normalized(
                    candidate,
                    ["daily_limit_below_rounded_initial_minimum"],
                )
            )
            continue
        sector_remaining = sector_caps.get(candidate.sector, 0.0) - (
            selected_sector_minimums.get(candidate.sector, 0.0)
        )
        if minimum > sector_remaining:
            excluded.append(
                _excluded_normalized(candidate, ["sector_cap_below_initial_minimum"])
            )
            continue
        if minimum > current_tranche_cap - committed_minimum:
            excluded.append(
                _excluded_normalized(
                    candidate,
                    ["current_tranche_cannot_fund_initial_minimum"],
                )
            )
            continue
        selected.append(candidate)
        selected_minimums[candidate.code] = minimum
        selected_sector_minimums[candidate.sector] = (
            selected_sector_minimums.get(candidate.sector, 0.0) + minimum
        )
        committed_minimum += minimum

    allocations: dict[str, float] = {}
    if selected and current_tranche_cap > 0:
        sector_rows: dict[str, list[_Candidate]] = {}
        for candidate in selected:
            sector_rows.setdefault(candidate.sector, []).append(candidate)
        effective_sector_caps = {
            sector: min(
                sector_caps[sector],
                sum(_floor_step(row.cap_yuan, step) for row in rows),
            )
            for sector, rows in sector_rows.items()
        }

        sector_allocations = _weighted_fill(
            keys=sorted(sector_rows),
            bases={
                sector: sum(selected_minimums[row.code] for row in rows)
                for sector, rows in sector_rows.items()
            },
            caps=effective_sector_caps,
            weights={
                sector: sum(row.weight for row in rows)
                for sector, rows in sector_rows.items()
            },
            target_total_yuan=min(
                current_tranche_cap,
                sum(effective_sector_caps.values()),
            ),
            amount_step_yuan=step,
        )

        for sector in sorted(sector_rows):
            rows = sector_rows[sector]
            sector_result = _weighted_fill(
                keys=[row.code for row in sorted(rows, key=lambda item: item.code)],
                bases={row.code: selected_minimums[row.code] for row in rows},
                caps={row.code: _floor_step(row.cap_yuan, step) for row in rows},
                weights={row.code: row.weight for row in rows},
                target_total_yuan=sector_allocations[sector],
                amount_step_yuan=step,
            )
            allocations.update(sector_result)

    allocation_rows = [
        _allocation_row(candidate, allocations[candidate.code], step)
        for candidate in sorted(selected, key=lambda row: (-row.weight, row.code))
        if allocations.get(candidate.code, 0.0) > 0
    ]
    allocated = round(sum(row["suggested_amount_yuan"] for row in allocation_rows), 2)
    current_unallocated = round(max(current_tranche_cap - allocated, 0.0), 2)
    deferred = round(max(spendable_total - current_tranche_cap, 0.0), 2)
    unavailable_cash = round(max(budget - spendable_total, 0.0), 2)
    total_unallocated = round(max(budget - allocated, 0.0), 2)

    current_unallocated_reasons: list[str] = []
    if cash <= 0:
        current_unallocated_reasons.append("confirmed_cash_insufficient")
    if not preliminary:
        current_unallocated_reasons.append("no_gate_eligible_candidates")
    if preliminary and not selected:
        current_unallocated_reasons.append("all_candidates_below_hard_constraints")
    if selected and current_unallocated > 0:
        current_unallocated_reasons.append("candidate_or_sector_caps_exhausted")

    status = "blocked" if allocated <= 0 else ("allocated" if current_unallocated <= 0 else "partial")
    return {
        "schema_version": ALLOCATION_PLAN_SCHEMA_VERSION,
        "status": status,
        "allocation_mode": allocation_mode,
        "amount_semantics": CURRENT_AMOUNT_SEMANTICS,
        "policy": {
            "decision_style": decision_style,
            "prefer_dca": prefer_dca,
            "nominal_current_tranche_ratio": nominal_ratio,
            "applied_current_tranche_ratio": tranche_ratio,
            "amount_step_yuan": _money(step),
            "concentration_denominator_yuan": _money(denominator),
            "concentration_limit_percent": concentration,
            "stable_tie_break": "fund_code_ascending",
            "candidate_order_ignored": True,
            "llm_amount_and_prose_ignored": True,
            "risk_weight_method": (
                "inverse_volatility_adjusted_by_drawdown_candidate_correlation_"
                "and_current_portfolio_correlation"
            ),
        },
        "risk_context": {
            "schema_version": RISK_CONTEXT_SCHEMA_VERSION,
            "status": risk.status,
            "reason_codes": list(risk.reason_codes),
            "fallback_rule": None,
        },
        "budget": {
            "requested_yuan": _money(budget),
            "confirmed_cash_yuan": _money(cash),
            "spendable_yuan": _money(spendable_total),
            "current_tranche_cap_yuan": _money(current_tranche_cap),
            "allocated_current_tranche_yuan": _money(allocated),
        },
        "sector_constraints": [
            {
                "sector_name": sector,
                "existing_exposure_yuan": _money(exposures.get(sector, 0.0)),
                "max_new_allocation_yuan": _money(cap),
            }
            for sector, cap in sorted(sector_caps.items())
        ],
        "allocations": allocation_rows,
        "excluded_candidates": sorted(
            excluded,
            key=lambda row: (
                str(row.get("fund_code") or ""),
                str(row.get("sector_name") or ""),
                tuple(row.get("reason_codes") or []),
            ),
        ),
        "unallocated_budget": {
            "amount_yuan": _money(total_unallocated),
            "current_tranche_unallocated_yuan": _money(current_unallocated),
            "deferred_future_tranches_yuan": _money(deferred),
            "unavailable_due_to_cash_yuan": _money(unavailable_cash),
            "reason_codes": current_unallocated_reasons,
        },
        "revalidation_required": True,
    }


def _normalize_candidate(
    row: Mapping[str, Any],
    *,
    duplicate_codes: set[str],
    current_budget_ceiling_yuan: float,
    amount_step_yuan: float,
    priority_inputs: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    code = _candidate_code(row)
    sector = _sector_key(row.get("sector_name") or row.get("sector"))
    reasons: list[str] = []
    if not code:
        reasons.append("fund_code_invalid")
    elif code in duplicate_codes:
        reasons.append("duplicate_fund_code")
    if not sector:
        reasons.append("sector_unknown")

    quality_gate = row.get("quality_gate")
    quality_action = row.get("quality_action")
    if quality_action is None and isinstance(quality_gate, Mapping):
        quality_action = quality_gate.get("status")
    if quality_action != "eligible":
        reasons.append("quality_action_not_eligible")
    if isinstance(quality_gate, Mapping) and quality_gate.get("eligible") is False:
        reasons.append("quality_gate_not_eligible")

    tradeability_gate = row.get("tradeability_gate")
    tradeability = row.get("tradeability")
    if not isinstance(tradeability_gate, Mapping) and isinstance(tradeability, Mapping):
        tradeability_gate = tradeability.get("tradeability_gate")
    if not isinstance(tradeability_gate, Mapping):
        reasons.append("tradeability_gate_missing")
        return None, _unique(reasons)
    if tradeability_gate.get("schema_version") != TRADEABILITY_GATE_SCHEMA_VERSION:
        reasons.append("tradeability_gate_schema_invalid")
    if tradeability_gate.get("status") != "eligible":
        reasons.append("tradeability_gate_not_eligible")
    if list(tradeability_gate.get("reason_codes") or []):
        reasons.append("tradeability_gate_eligible_with_reasons")
    if tradeability_gate.get("max_period") != "day":
        reasons.append("tradeability_gate_period_invalid")
    if tradeability_gate.get("revalidation_required") is not True:
        reasons.append("tradeability_revalidation_contract_missing")

    minimum = _finite_positive(
        tradeability_gate.get("effective_initial_min_purchase_yuan")
    )
    if minimum is None:
        reasons.append("effective_initial_minimum_invalid")

    unlimited = tradeability_gate.get("max_purchase_unlimited") is True
    maximum = _finite_positive(tradeability_gate.get("max_purchase_yuan"))
    if maximum is None and not unlimited:
        reasons.append("maximum_purchase_unknown")
    if maximum is not None and minimum is not None and maximum < minimum:
        reasons.append("maximum_purchase_below_initial_minimum")

    if reasons:
        return None, _unique(reasons)

    assert code
    assert sector
    assert minimum is not None
    cap = min(maximum or current_budget_ceiling_yuan, current_budget_ceiling_yuan)
    if _floor_step(cap, amount_step_yuan) < _ceil_step(minimum, amount_step_yuan):
        return None, ["rounded_purchase_capacity_below_initial_minimum"]

    priority_score = _qualified_priority_score(code, priority_inputs)
    peer_score, peer_status = _qualified_peer_score(row.get("peer_rank"))
    return (
        {
            "code": code,
            "sector": sector,
            "minimum_yuan": minimum,
            "cap_yuan": cap,
            "priority_score": priority_score,
            "peer_score": peer_score,
            "peer_tilt_status": peer_status,
        },
        [],
    )


def _qualified_priority_score(
    code: str,
    priority_inputs: Mapping[str, Mapping[str, Any]] | None,
) -> float | None:
    if not isinstance(priority_inputs, Mapping):
        return None
    value = priority_inputs.get(code)
    if not isinstance(value, Mapping):
        return None
    if (
        value.get("schema_version") != PRIORITY_INPUT_SCHEMA_VERSION
        or value.get("status") != "qualified"
    ):
        return None
    score = _finite_nonnegative(value.get("score"))
    return score if score is not None and score <= 100 else None


def _qualified_peer_score(value: Any) -> tuple[float | None, str]:
    if not isinstance(value, Mapping):
        return None, "unavailable"
    execution_gate = value.get("execution_tilt_gate")
    if (
        value.get("schema_version") != PEER_RANK_SCHEMA_VERSION
        or value.get("execution_tilt_eligible") is not True
        or not isinstance(execution_gate, Mapping)
        or execution_gate.get("status") != "qualified"
        or execution_gate.get("eligible") is not True
    ):
        return None, "ignored_not_execution_qualified"
    score = _finite_nonnegative(value.get("execution_score_percentile"))
    if score is None or score > 100:
        return None, "ignored_invalid_score"
    return score, "applied_execution_qualified_peer_rank_v1"


def _resolve_risk_context(
    value: Mapping[str, Any] | None,
    codes: list[str],
) -> _RiskResolution:
    codes = sorted(dict.fromkeys(codes))
    unavailable = _RiskResolution(
        available=False,
        status="risk_context_unavailable",
        reason_codes=("risk_context_unavailable",),
        drawdown_by_code={},
        covariance_by_code={},
        current_portfolio_correlation_penalty_by_code={},
    )
    if not codes or not isinstance(value, Mapping):
        return unavailable
    if (
        value.get("schema_version") != RISK_CONTEXT_SCHEMA_VERSION
        or value.get("status") != "qualified"
    ):
        return _RiskResolution(
            available=False,
            status="risk_context_unavailable",
            reason_codes=("risk_context_not_qualified",),
            drawdown_by_code={},
            covariance_by_code={},
            current_portfolio_correlation_penalty_by_code={},
        )

    raw_drawdowns = value.get("max_drawdown_percent_by_code")
    raw_covariance = value.get("covariance_by_code")
    raw_portfolio_penalties = value.get(
        "positive_correlation_penalty_to_current_holdings_by_code"
    )
    if (
        not isinstance(raw_drawdowns, Mapping)
        or not isinstance(raw_covariance, Mapping)
        or not isinstance(raw_portfolio_penalties, Mapping)
    ):
        return _RiskResolution(
            available=False,
            status="risk_context_unavailable",
            reason_codes=("risk_context_incomplete",),
            drawdown_by_code={},
            covariance_by_code={},
            current_portfolio_correlation_penalty_by_code={},
        )

    drawdowns: dict[str, float] = {}
    covariance: dict[str, dict[str, float]] = {}
    portfolio_penalties: dict[str, float] = {}
    for code in codes:
        drawdown = _finite_nonnegative(raw_drawdowns.get(code))
        portfolio_penalty = _finite_nonnegative(raw_portfolio_penalties.get(code))
        covariance_row = raw_covariance.get(code)
        if (
            drawdown is None
            or drawdown > 100
            or portfolio_penalty is None
            or portfolio_penalty > 1
            or not isinstance(covariance_row, Mapping)
        ):
            return _invalid_risk_resolution("risk_context_incomplete")
        drawdowns[code] = drawdown
        portfolio_penalties[code] = portfolio_penalty
        covariance[code] = {}
        for other in codes:
            parsed = _finite_number(covariance_row.get(other))
            if parsed is None:
                return _invalid_risk_resolution("risk_covariance_incomplete")
            covariance[code][other] = parsed
        if covariance[code][code] <= 0:
            return _invalid_risk_resolution("risk_covariance_nonpositive_variance")

    for code in codes:
        for other in codes:
            left = covariance[code][other]
            right = covariance[other][code]
            if abs(left - right) > max(1e-9, abs(left) * 1e-6, abs(right) * 1e-6):
                return _invalid_risk_resolution("risk_covariance_not_symmetric")
    if not _covariance_is_positive_semidefinite(codes, covariance):
        return _invalid_risk_resolution("risk_covariance_not_positive_semidefinite")

    return _RiskResolution(
        available=True,
        status="qualified",
        reason_codes=(),
        drawdown_by_code=drawdowns,
        covariance_by_code=covariance,
        current_portfolio_correlation_penalty_by_code=portfolio_penalties,
    )


def _invalid_risk_resolution(reason: str) -> _RiskResolution:
    return _RiskResolution(
        available=False,
        status="risk_context_unavailable",
        reason_codes=(reason,),
        drawdown_by_code={},
        covariance_by_code={},
        current_portfolio_correlation_penalty_by_code={},
    )


def _covariance_is_positive_semidefinite(
    codes: list[str],
    covariance: Mapping[str, Mapping[str, float]],
) -> bool:
    """Validate PSD with a tolerance-aware Cholesky decomposition."""

    scale = max(covariance[code][code] for code in codes)
    tolerance = max(1e-12, scale * 1e-10)
    lower = [[0.0 for _ in codes] for _ in codes]
    for row_index, code in enumerate(codes):
        for column_index in range(row_index + 1):
            other = codes[column_index]
            residual = covariance[code][other] - sum(
                lower[row_index][offset] * lower[column_index][offset]
                for offset in range(column_index)
            )
            if row_index == column_index:
                if residual < -tolerance:
                    return False
                lower[row_index][column_index] = sqrt(max(residual, 0.0))
                continue
            diagonal = lower[column_index][column_index]
            if diagonal <= tolerance:
                if abs(residual) > tolerance:
                    return False
                lower[row_index][column_index] = 0.0
            else:
                lower[row_index][column_index] = residual / diagonal
    return True


def _risk_multiplier(code: str, risk: _RiskResolution) -> float:
    if not risk.available:
        # This path is defensive only: the public allocator returns blocked
        # before reaching amount allocation when risk evidence is unavailable.
        return 0.0
    drawdown = risk.drawdown_by_code[code]
    drawdown_factor = max(0.25, 1.0 - min(drawdown, 75.0) / 100.0)
    variance = risk.covariance_by_code[code][code]
    inverse_volatility = 1.0 / sqrt(variance)
    correlations: list[float] = []
    for other, covariance in risk.covariance_by_code[code].items():
        if other == code:
            continue
        other_variance = risk.covariance_by_code[other][other]
        denominator = sqrt(max(variance, 0.0) * max(other_variance, 0.0))
        if denominator <= 0:
            continue
        correlations.append(max(-1.0, min(covariance / denominator, 1.0)))
    positive_average_correlation = (
        sum(max(value, 0.0) for value in correlations) / len(correlations)
        if correlations
        else 0.0
    )
    diversification_factor = 1.0 - 0.25 * positive_average_correlation
    current_portfolio_factor = (
        1.0
        - 0.35 * risk.current_portfolio_correlation_penalty_by_code[code]
    )
    return round(
        max(
            0.000001,
            inverse_volatility
            * drawdown_factor
            * diversification_factor
            * current_portfolio_factor,
        ),
        8,
    )


def _sector_caps(
    candidates: list[_Candidate],
    *,
    requested_budget_yuan: float,
    denominator_yuan: float,
    concentration_limit_percent: float,
    exposures: Mapping[str, float],
    amount_step_yuan: float,
) -> dict[str, float]:
    ratio = concentration_limit_percent / 100.0
    request_sector_cap = requested_budget_yuan * ratio
    result: dict[str, float] = {}
    for sector in sorted({row.sector for row in candidates}):
        existing = exposures.get(sector, 0.0)
        portfolio_remaining = max(denominator_yuan * ratio - existing, 0.0)
        result[sector] = _floor_step(
            min(portfolio_remaining, request_sector_cap),
            amount_step_yuan,
        )
    return result


def _weighted_fill(
    *,
    keys: list[str],
    bases: Mapping[str, float],
    caps: Mapping[str, float],
    weights: Mapping[str, float],
    target_total_yuan: float,
    amount_step_yuan: float,
) -> dict[str, float]:
    allocations = {key: _floor_step(bases.get(key, 0.0), amount_step_yuan) for key in keys}
    target = _floor_step(target_total_yuan, amount_step_yuan)
    target = min(target, sum(_floor_step(caps[key], amount_step_yuan) for key in keys))

    while target - sum(allocations.values()) >= amount_step_yuan - 1e-9:
        remaining = _floor_step(target - sum(allocations.values()), amount_step_yuan)
        active = [
            key
            for key in keys
            if _floor_step(caps[key] - allocations[key], amount_step_yuan)
            >= amount_step_yuan
        ]
        if not active:
            break
        total_weight = sum(max(weights.get(key, 0.0), 0.000001) for key in active)
        raw_shares = {
            key: remaining * max(weights.get(key, 0.0), 0.000001) / total_weight
            for key in active
        }
        increments = {
            key: min(
                _floor_step(raw_shares[key], amount_step_yuan),
                _floor_step(caps[key] - allocations[key], amount_step_yuan),
            )
            for key in active
        }
        increment_total = sum(increments.values())
        if increment_total > remaining:
            # This should be unreachable because individually floored weighted
            # shares cannot exceed the remaining total, but retaining the
            # guard keeps this helper fail-safe under unusual float inputs.
            increment_total = 0.0
            increments = {key: 0.0 for key in active}
        if increment_total <= 0:
            ranked = sorted(
                active,
                key=lambda key: (
                    -(raw_shares[key] / amount_step_yuan % 1),
                    -weights.get(key, 0.0),
                    key,
                ),
            )
            available_quanta = int(floor(remaining / amount_step_yuan + 1e-9))
            for key in ranked[:available_quanta]:
                allocations[key] += amount_step_yuan
            continue
        for key, increment in increments.items():
            allocations[key] += increment

    return {key: _money(value) for key, value in allocations.items()}


def _allocation_row(
    candidate: _Candidate,
    amount_yuan: float,
    amount_step_yuan: float,
) -> dict[str, Any]:
    return {
        "fund_code": candidate.code,
        "sector_name": candidate.sector,
        "suggested_amount_yuan": _money(amount_yuan),
        "amount_semantics": CURRENT_AMOUNT_SEMANTICS,
        "constraint_snapshot": {
            "effective_initial_min_purchase_yuan": _money(candidate.minimum_yuan),
            "candidate_purchase_cap_yuan": _money(candidate.cap_yuan),
            "amount_step_yuan": _money(amount_step_yuan),
        },
        "priority": {
            "qualified_priority_score": candidate.priority_score,
            "qualified_peer_score_percentile": candidate.peer_score,
            "peer_tilt_status": candidate.peer_tilt_status,
            "risk_multiplier": candidate.risk_multiplier,
            "current_portfolio_correlation_penalty": (
                candidate.current_portfolio_correlation_penalty
            ),
            "combined_weight": round(candidate.weight, 8),
        },
        "future_tranches": [
            {
                "sequence": 2,
                "amount_yuan": None,
                "revalidation_required": True,
                "preconditions": [
                    "tradeability_gate_recheck",
                    "confirmed_cash_recheck",
                    "sector_exposure_recheck",
                    "risk_context_recheck",
                ],
            }
        ],
        "revalidation_required": True,
    }


def _blocked_plan(
    *,
    requested_budget_yuan: float | None,
    confirmed_cash_yuan: float | None,
    reason_codes: list[str],
) -> dict[str, Any]:
    budget = requested_budget_yuan or 0.0
    return {
        "schema_version": ALLOCATION_PLAN_SCHEMA_VERSION,
        "status": "blocked",
        "allocation_mode": BLOCKED_MODE,
        "amount_semantics": CURRENT_AMOUNT_SEMANTICS,
        "policy": {
            "candidate_order_ignored": True,
            "llm_amount_and_prose_ignored": True,
        },
        "risk_context": {
            "schema_version": RISK_CONTEXT_SCHEMA_VERSION,
            "status": "risk_context_unavailable",
            "reason_codes": ["allocation_inputs_invalid"],
        },
        "budget": {
            "requested_yuan": _money(budget),
            "confirmed_cash_yuan": _money(confirmed_cash_yuan),
            "spendable_yuan": None,
            "current_tranche_cap_yuan": 0.0,
            "allocated_current_tranche_yuan": 0.0,
        },
        "sector_constraints": [],
        "allocations": [],
        "excluded_candidates": [],
        "unallocated_budget": {
            "amount_yuan": _money(budget),
            "current_tranche_unallocated_yuan": 0.0,
            "deferred_future_tranches_yuan": 0.0,
            "unavailable_due_to_cash_yuan": None,
            "reason_codes": _unique(reason_codes),
        },
        "revalidation_required": True,
    }


def _preallocation_blocked_plan(
    *,
    requested_budget_yuan: float,
    confirmed_cash_yuan: float,
    excluded_candidates: list[dict[str, Any]],
    risk_status: str,
    reason_codes: list[str],
) -> dict[str, Any]:
    """Return a complete zero-amount plan after input and gate validation."""

    return {
        "schema_version": ALLOCATION_PLAN_SCHEMA_VERSION,
        "status": "blocked",
        "allocation_mode": BLOCKED_MODE,
        "amount_semantics": CURRENT_AMOUNT_SEMANTICS,
        "policy": {
            "candidate_order_ignored": True,
            "llm_amount_and_prose_ignored": True,
            "risk_context_fail_closed": True,
        },
        "risk_context": {
            "schema_version": RISK_CONTEXT_SCHEMA_VERSION,
            "status": risk_status,
            "reason_codes": _unique(reason_codes),
            "fallback_rule": "no_executable_amount_without_qualified_risk_context",
        },
        "budget": {
            "requested_yuan": _money(requested_budget_yuan),
            "confirmed_cash_yuan": _money(confirmed_cash_yuan),
            "spendable_yuan": _money(min(requested_budget_yuan, confirmed_cash_yuan)),
            "current_tranche_cap_yuan": 0.0,
            "allocated_current_tranche_yuan": 0.0,
        },
        "sector_constraints": [],
        "allocations": [],
        "excluded_candidates": sorted(
            excluded_candidates,
            key=lambda row: (
                str(row.get("fund_code") or ""),
                str(row.get("sector_name") or ""),
                tuple(row.get("reason_codes") or []),
            ),
        ),
        "unallocated_budget": {
            "amount_yuan": _money(requested_budget_yuan),
            "current_tranche_unallocated_yuan": 0.0,
            "deferred_future_tranches_yuan": 0.0,
            "unavailable_due_to_cash_yuan": _money(
                max(requested_budget_yuan - confirmed_cash_yuan, 0.0)
            ),
            "reason_codes": _unique(reason_codes),
        },
        "revalidation_required": True,
    }


def _normalize_exposures(
    value: Mapping[str, float | int] | None,
) -> tuple[dict[str, float] | None, list[str]]:
    if not isinstance(value, Mapping):
        return None, ["sector_exposure_unavailable"]
    result: dict[str, float] = {}
    for raw_sector, raw_amount in value.items():
        sector = _sector_key(raw_sector)
        amount = _finite_nonnegative(raw_amount)
        if not sector or amount is None:
            return None, ["sector_exposure_invalid"]
        result[sector] = result.get(sector, 0.0) + amount
    return result, []


def _candidate_code(row: Mapping[str, Any]) -> str:
    raw = str(row.get("fund_code") or row.get("code") or "").strip()
    return raw if len(raw) == 6 and raw.isdigit() else ""


def _sector_key(value: Any) -> str:
    label = " ".join(str(value or "").strip().split()).casefold()
    if label in {"", "unknown", "unclassified", "未知", "未分类"}:
        return ""
    return label


def _excluded_candidate(row: Mapping[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "fund_code": _candidate_code(row) or str(row.get("fund_code") or row.get("code") or ""),
        "sector_name": _sector_key(row.get("sector_name") or row.get("sector")) or None,
        "reason_codes": _unique(reasons),
    }


def _excluded_normalized(candidate: _Candidate, reasons: list[str]) -> dict[str, Any]:
    return {
        "fund_code": candidate.code,
        "sector_name": candidate.sector,
        "reason_codes": _unique(reasons),
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None


def _finite_nonnegative(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _finite_positive(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _floor_step(value: float, step: float) -> float:
    return _money(floor(max(value, 0.0) / step + 1e-12) * step)


def _ceil_step(value: float, step: float) -> float:
    floored = _floor_step(value, step)
    if floored + 1e-9 >= value:
        return floored
    return _money(floored + step)


def _money(value: Any) -> float | None:
    parsed = _finite_number(value)
    return round(parsed, 2) if parsed is not None else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
