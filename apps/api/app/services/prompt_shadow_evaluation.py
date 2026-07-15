"""Pure D5.2 paired evaluation for preregistered prompt-shadow evidence.

The module has no storage, provider, configuration, or wall-clock reads.  It
derives one source-addressed paired case from a complete D5.1 evidence chain,
then evaluates a single policy/transport stratum with deterministic day-cluster
statistics.  Missing evidence remains visible in denominators and can never be
promoted automatically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
import hashlib
import math
import random
from typing import Any

from app.services.decision_repository import canonical_hash
from app.services.fund_factor_nav import build_total_return_index
from app.services.prompt_shadow_contracts import (
    PromptShadowContractError,
    build_decision_projection,
    normalize_prompt_gate_policy,
    normalize_prompt_shadow_attempt,
    normalize_prompt_shadow_output,
    normalize_prompt_shadow_registration,
    validate_prompt_shadow_time_chain,
)


PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION = (
    "decision_quality_prompt_shadow_paired_case.v2"
)
PROMPT_SHADOW_GATE_SCHEMA_VERSION = "decision_quality_prompt_shadow_gate.v2"
PROMPT_SHADOW_EVALUATOR_VERSION = "prompt_shadow_evaluator.2026-07.d5-v2"

_VALID_PARSE_STATUSES = frozenset({"valid", "interrupted_salvaged"})
_INVALID_PARSE_STATUSES = frozenset({"empty", "invalid", "truncated", "oversize"})
_CLAIM_STATUSES = frozenset({"clean", "sanitized", "violation"})
_CASE_FIELDS = {
    "schema_version",
    "evaluator_version",
    "case_id",
    "run_id",
    "policy_id",
    "policy_hash",
    "stratum_hash",
    "decision_at",
    "live_cohort_date_local",
    "label_knowledge_boundary",
    "champion_decision_projection_hash",
    "challenger_decision_projection_hash",
    "differing",
    "champion_parse_status",
    "challenger_parse_status",
    "champion_claim_status",
    "challenger_claim_status",
    "champion_utility_percent",
    "challenger_utility_percent",
    "utility_delta_pp",
    "champion_max_drawdown_percent",
    "challenger_max_drawdown_percent",
    "drawdown_delta_pp",
    "formal",
    "reason_codes",
    "source_refs",
    "automatic_promotion_allowed",
    "content_hash",
}


class PromptShadowEvaluationError(ValueError):
    """Prompt-shadow evidence cannot be evaluated without ambiguity."""


def prompt_shadow_stratum_hash(registration: Mapping[str, Any]) -> str:
    """Hash only preregistered, non-secret execution dimensions."""

    normalized = normalize_prompt_shadow_registration(registration)
    pair = normalized["prompt_pair"]
    payload = pair["champion_provider_payload"]
    return canonical_hash(
        {
            "provider": normalized["scope"]["provider"],
            "model": payload["model"],
            "transport": pair["transport"],
            "temperature": payload["temperature"],
            "max_tokens": payload["max_tokens"],
            "response_format": payload["response_format"],
            "versions": normalized["versions"],
        }
    )


def build_prompt_shadow_paired_case(
    *,
    policy: Mapping[str, Any],
    policy_receipt: Mapping[str, Any] | None,
    registration: Mapping[str, Any],
    registration_receipt: Mapping[str, Any] | None,
    champion_attempt: Mapping[str, Any],
    champion_attempt_receipt: Mapping[str, Any] | None,
    champion_output: Mapping[str, Any],
    champion_output_receipt: Mapping[str, Any] | None,
    challenger_attempt: Mapping[str, Any],
    challenger_attempt_receipt: Mapping[str, Any] | None,
    challenger_output: Mapping[str, Any],
    challenger_output_receipt: Mapping[str, Any] | None,
    candidate_case: Mapping[str, Any] | None,
    evaluation_as_of: str | datetime,
    expected_user_id: int,
) -> dict[str, Any]:
    """Derive one paired case without retaining prompt or raw model content."""

    try:
        normalized_policy = normalize_prompt_gate_policy(policy)
        normalized_registration = normalize_prompt_shadow_registration(
            registration,
            policy=normalized_policy,
            expected_user_id=expected_user_id,
        )
        normalized_champion_attempt = normalize_prompt_shadow_attempt(
            champion_attempt,
            registration=normalized_registration,
            expected_user_id=expected_user_id,
        )
        normalized_challenger_attempt = normalize_prompt_shadow_attempt(
            challenger_attempt,
            registration=normalized_registration,
            expected_user_id=expected_user_id,
        )
        normalized_champion_output = normalize_prompt_shadow_output(
            champion_output,
            registration=normalized_registration,
            attempt=normalized_champion_attempt,
            expected_user_id=expected_user_id,
        )
        normalized_challenger_output = normalize_prompt_shadow_output(
            challenger_output,
            registration=normalized_registration,
            attempt=normalized_challenger_attempt,
            expected_user_id=expected_user_id,
        )
    except PromptShadowContractError as exc:
        raise PromptShadowEvaluationError(
            "prompt-shadow paired evidence failed its immutable contract"
        ) from exc

    if normalized_champion_attempt["role"] != "champion" or (
        normalized_challenger_attempt["role"] != "challenger"
    ):
        raise PromptShadowEvaluationError("prompt-shadow attempt roles are reversed")
    if normalized_champion_output["role"] != "champion" or (
        normalized_challenger_output["role"] != "challenger"
    ):
        raise PromptShadowEvaluationError("prompt-shadow output roles are reversed")

    cutoff = _aware_timestamp(evaluation_as_of, "evaluation_as_of")
    label_boundary = _candidate_label_boundary(candidate_case)
    validation_boundary = label_boundary or cutoff.isoformat()
    receipt_refs = {
        "policy": policy_receipt,
        "registration": registration_receipt,
        "champion_attempt": champion_attempt_receipt,
        "champion_output": champion_output_receipt,
        "challenger_attempt": challenger_attempt_receipt,
        "challenger_output": challenger_output_receipt,
    }
    try:
        chain = validate_prompt_shadow_time_chain(
            policy=normalized_policy,
            policy_receipt=policy_receipt,
            registration=normalized_registration,
            registration_receipt=registration_receipt,
            champion_attempt=normalized_champion_attempt,
            champion_attempt_receipt=champion_attempt_receipt,
            champion_output=normalized_champion_output,
            champion_output_receipt=champion_output_receipt,
            challenger_attempt=normalized_challenger_attempt,
            challenger_attempt_receipt=challenger_attempt_receipt,
            challenger_output=normalized_challenger_output,
            challenger_output_receipt=challenger_output_receipt,
            label_knowledge_boundary=validation_boundary,
            evaluation_as_of=cutoff,
            expected_user_id=expected_user_id,
        )
    except PromptShadowContractError as exc:
        raise PromptShadowEvaluationError(
            "prompt-shadow time chain failed its immutable binding"
        ) from exc

    reasons = list(chain["reason_codes"])
    champion_status = normalized_champion_output["response"]["parse_status"]
    challenger_status = normalized_challenger_output["response"]["parse_status"]
    if champion_status not in _VALID_PARSE_STATUSES:
        reasons.append(f"champion_{champion_status}")
    if challenger_status not in _VALID_PARSE_STATUSES:
        reasons.append(f"challenger_{challenger_status}")
    if candidate_case is None or label_boundary is None:
        reasons.append("prompt_shadow_outcome_labels_incomplete")

    champion_projection = normalized_champion_output["final_projection"]
    challenger_projection = normalized_challenger_output["final_projection"]
    champion_metrics: dict[str, float] | None = None
    challenger_metrics: dict[str, float] | None = None
    if (
        candidate_case is not None
        and label_boundary is not None
        and champion_projection is not None
        and challenger_projection is not None
    ):
        _validate_candidate_binding(
            candidate_case,
            normalized_champion_output,
            normalized_challenger_output,
            evaluation_as_of=cutoff,
        )
        champion_metrics = _portfolio_metrics(champion_projection, candidate_case)
        challenger_metrics = _portfolio_metrics(challenger_projection, candidate_case)
        if champion_metrics is None or challenger_metrics is None:
            reasons.append("prompt_shadow_allocation_labels_incomplete")

    champion_claim = _claim_status(champion_projection)
    challenger_claim = _claim_status(challenger_projection)
    if champion_claim == "violation":
        reasons.append("champion_guard_violation")
    if challenger_claim == "violation":
        reasons.append("challenger_guard_violation")

    champion_decision_hash = normalized_champion_output["decision_projection_hash"]
    challenger_decision_hash = normalized_challenger_output["decision_projection_hash"]
    metrics_ready = champion_metrics is not None and challenger_metrics is not None
    utility_delta = (
        round(
            challenger_metrics["utility_percent"]
            - champion_metrics["utility_percent"],
            8,
        )
        if metrics_ready
        else None
    )
    drawdown_delta = (
        round(
            challenger_metrics["max_drawdown_percent"]
            - champion_metrics["max_drawdown_percent"],
            8,
        )
        if metrics_ready
        else None
    )
    reasons = sorted(set(reasons))
    formal = not reasons and chain["formal"] is True and metrics_ready
    case: dict[str, Any] = {
        "schema_version": PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION,
        "evaluator_version": PROMPT_SHADOW_EVALUATOR_VERSION,
        "case_id": "dqpsc_"
        + canonical_hash(
            {
                "run_id": normalized_registration["run_id"],
                "policy_hash": normalized_policy["policy_hash"],
            }
        ),
        "run_id": normalized_registration["run_id"],
        "policy_id": normalized_policy["policy_id"],
        "policy_hash": normalized_policy["policy_hash"],
        "stratum_hash": prompt_shadow_stratum_hash(normalized_registration),
        "decision_at": normalized_registration["decision_at"],
        "live_cohort_date_local": (
            str(candidate_case.get("live_cohort_date_local") or "")
            if candidate_case is not None and label_boundary is not None
            else None
        ),
        "label_knowledge_boundary": label_boundary,
        "champion_decision_projection_hash": champion_decision_hash,
        "challenger_decision_projection_hash": challenger_decision_hash,
        "differing": bool(
            champion_decision_hash
            and challenger_decision_hash
            and champion_decision_hash != challenger_decision_hash
        ),
        "champion_parse_status": champion_status,
        "challenger_parse_status": challenger_status,
        "champion_claim_status": champion_claim,
        "challenger_claim_status": challenger_claim,
        "champion_utility_percent": (
            champion_metrics["utility_percent"] if champion_metrics else None
        ),
        "challenger_utility_percent": (
            challenger_metrics["utility_percent"] if challenger_metrics else None
        ),
        "utility_delta_pp": utility_delta,
        "champion_max_drawdown_percent": (
            champion_metrics["max_drawdown_percent"] if champion_metrics else None
        ),
        "challenger_max_drawdown_percent": (
            challenger_metrics["max_drawdown_percent"] if challenger_metrics else None
        ),
        "drawdown_delta_pp": drawdown_delta,
        "formal": formal,
        "reason_codes": reasons,
        "source_refs": _source_refs(receipt_refs, candidate_case),
        "automatic_promotion_allowed": False,
    }
    case["content_hash"] = canonical_hash(case)
    return normalize_prompt_shadow_paired_case(case)


def normalize_prompt_shadow_paired_case(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a derived case before it is included in a gate."""

    if set(value) != _CASE_FIELDS:
        raise PromptShadowEvaluationError("prompt-shadow paired case fields are invalid")
    result = deepcopy(dict(value))
    if (
        result.get("schema_version") != PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION
        or result.get("evaluator_version") != PROMPT_SHADOW_EVALUATOR_VERSION
        or not _content_id(result.get("case_id"), "dqpsc_")
        or not _content_id(result.get("run_id"), "dqsr_")
        or not isinstance(result.get("policy_id"), str)
        or not result["policy_id"]
        or not _sha256(result.get("policy_hash"))
        or not _sha256(result.get("stratum_hash"))
        or result.get("automatic_promotion_allowed") is not False
    ):
        raise PromptShadowEvaluationError("prompt-shadow paired case identity is invalid")
    expected_case_id = "dqpsc_" + canonical_hash(
        {"run_id": result["run_id"], "policy_hash": result["policy_hash"]}
    )
    if result["case_id"] != expected_case_id:
        raise PromptShadowEvaluationError("prompt-shadow paired case id is invalid")
    _aware_timestamp(result.get("decision_at"), "decision_at")
    for key in (
        "champion_decision_projection_hash",
        "challenger_decision_projection_hash",
    ):
        if result[key] is not None and not _sha256(result[key]):
            raise PromptShadowEvaluationError(f"{key} is invalid")
    if result.get("champion_parse_status") not in (
        _VALID_PARSE_STATUSES | _INVALID_PARSE_STATUSES | {
            "provider_error",
            "http_error",
            "timeout",
        }
    ) or result.get("challenger_parse_status") not in (
        _VALID_PARSE_STATUSES | _INVALID_PARSE_STATUSES | {
            "provider_error",
            "http_error",
            "timeout",
        }
    ):
        raise PromptShadowEvaluationError("prompt-shadow parse status is invalid")
    for key in ("champion_claim_status", "challenger_claim_status"):
        if result[key] is not None and result[key] not in _CLAIM_STATUSES:
            raise PromptShadowEvaluationError("prompt-shadow claim status is invalid")
    reasons = result.get("reason_codes")
    if (
        not isinstance(reasons, list)
        or not all(isinstance(item, str) and item for item in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise PromptShadowEvaluationError("prompt-shadow reason codes are invalid")
    formal = result.get("formal")
    metric_fields = (
        "champion_utility_percent",
        "challenger_utility_percent",
        "utility_delta_pp",
        "champion_max_drawdown_percent",
        "challenger_max_drawdown_percent",
        "drawdown_delta_pp",
    )
    for key in metric_fields:
        item = result.get(key)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        ):
            raise PromptShadowEvaluationError("prompt-shadow paired metric is invalid")
    if not isinstance(formal, bool) or (formal and reasons):
        raise PromptShadowEvaluationError("prompt-shadow formal status is invalid")
    if formal and (
        any(result[key] is None for key in metric_fields)
        or result.get("live_cohort_date_local") is None
        or result.get("label_knowledge_boundary") is None
    ):
        raise PromptShadowEvaluationError("formal prompt-shadow case lacks labels")
    if formal:
        try:
            date.fromisoformat(str(result["live_cohort_date_local"]))
        except ValueError as exc:
            raise PromptShadowEvaluationError(
                "prompt-shadow live cohort date is invalid"
            ) from exc
        boundary = _aware_timestamp(
            result["label_knowledge_boundary"], "label_knowledge_boundary"
        )
        decision = _aware_timestamp(result["decision_at"], "decision_at")
        if boundary <= decision:
            raise PromptShadowEvaluationError(
                "prompt-shadow label boundary must follow the decision"
            )
        expected_utility_delta = round(
            float(result["challenger_utility_percent"])
            - float(result["champion_utility_percent"]),
            8,
        )
        expected_drawdown_delta = round(
            float(result["challenger_max_drawdown_percent"])
            - float(result["champion_max_drawdown_percent"]),
            8,
        )
        if (
            result["utility_delta_pp"] != expected_utility_delta
            or result["drawdown_delta_pp"] != expected_drawdown_delta
        ):
            raise PromptShadowEvaluationError(
                "prompt-shadow paired metric delta is invalid"
            )
    expected_differing = bool(
        result.get("champion_decision_projection_hash")
        and result.get("challenger_decision_projection_hash")
        and result.get("champion_decision_projection_hash")
        != result.get("challenger_decision_projection_hash")
    )
    if result.get("differing") is not expected_differing:
        raise PromptShadowEvaluationError("prompt-shadow differing flag is invalid")
    refs = result.get("source_refs")
    if not isinstance(refs, list) or refs != sorted(
        refs, key=lambda item: (str(item.get("artifact_id") or ""), str(item.get("receipt_id") or ""))
    ):
        raise PromptShadowEvaluationError("prompt-shadow source refs are invalid")
    for ref in refs:
        if not isinstance(ref, Mapping) or set(ref) != {
            "artifact_id",
            "artifact_content_hash",
            "receipt_id",
            "receipt_content_hash",
        } or not all(
            isinstance(ref.get(key), str) and ref.get(key)
            for key in ("artifact_id", "receipt_id")
        ) or not _sha256(ref.get("artifact_content_hash")) or not _sha256(
            ref.get("receipt_content_hash")
        ):
            raise PromptShadowEvaluationError(
                "prompt-shadow source ref binding is invalid"
            )
    if formal and len(refs) < 8:
        raise PromptShadowEvaluationError(
            "formal prompt-shadow case lacks complete source refs"
        )
    expected_hash = canonical_hash(
        {key: item for key, item in result.items() if key != "content_hash"}
    )
    if result.get("content_hash") != expected_hash:
        raise PromptShadowEvaluationError("prompt-shadow paired case hash mismatch")
    return result


def evaluate_prompt_shadow_gate(
    *,
    policy: Mapping[str, Any],
    registrations: Sequence[Mapping[str, Any]],
    paired_cases: Sequence[Mapping[str, Any]],
    evaluation_as_of: str | datetime,
    integrity_failure_count: int = 0,
    tenant_failure_count: int = 0,
    budget_violation_count: int = 0,
) -> dict[str, Any]:
    """Evaluate one preregistered policy/stratum; never enable it."""

    try:
        normalized_policy = normalize_prompt_gate_policy(policy)
        normalized_registrations = [
            normalize_prompt_shadow_registration(item, policy=normalized_policy)
            for item in registrations
        ]
    except PromptShadowContractError as exc:
        raise PromptShadowEvaluationError("prompt-shadow gate inputs are invalid") from exc
    cutoff = _aware_timestamp(evaluation_as_of, "evaluation_as_of")
    failures = {
        "integrity_failure_count": _nonnegative_int(integrity_failure_count),
        "tenant_failure_count": _nonnegative_int(tenant_failure_count),
        "budget_violation_count": _nonnegative_int(budget_violation_count),
    }
    registrations_by_run = {
        item["run_id"]: item for item in normalized_registrations
    }
    if len(registrations_by_run) != len(normalized_registrations):
        raise PromptShadowEvaluationError("prompt-shadow registration identity is duplicated")
    if any(
        _aware_timestamp(item["decision_at"], "decision_at") > cutoff
        for item in normalized_registrations
    ):
        raise PromptShadowEvaluationError("future prompt-shadow registration is forbidden")

    strata = {prompt_shadow_stratum_hash(item) for item in normalized_registrations}
    if len(strata) > 1:
        raise PromptShadowEvaluationError(
            "one prompt-shadow gate cannot mix execution strata"
        )
    stratum_hash = next(iter(strata), canonical_hash({"empty": True}))
    cases = [normalize_prompt_shadow_paired_case(item) for item in paired_cases]
    cases.sort(key=lambda item: item["case_id"])
    cases_by_run = {item["run_id"]: item for item in cases}
    if len(cases_by_run) != len(cases):
        raise PromptShadowEvaluationError("prompt-shadow paired case identity is duplicated")
    for case in cases:
        registration = registrations_by_run.get(case["run_id"])
        if (
            registration is None
            or case["policy_hash"] != normalized_policy["policy_hash"]
            or case["policy_id"] != normalized_policy["policy_id"]
            or case["stratum_hash"] != stratum_hash
        ):
            raise PromptShadowEvaluationError(
                "prompt-shadow paired case is outside the registered stratum"
            )

    assigned = len(normalized_registrations)
    valid = sum(
        item["challenger_parse_status"] in _VALID_PARSE_STATUSES for item in cases
    )
    timeouts = sum(item["challenger_parse_status"] == "timeout" for item in cases)
    invalid = sum(
        item["challenger_parse_status"] in _INVALID_PARSE_STATUSES for item in cases
    )
    formal_cases = [item for item in cases if item["formal"] is True]
    differing_cases = [item for item in formal_cases if item["differing"] is True]
    mature_dates = sorted(
        {str(item["live_cohort_date_local"]) for item in formal_cases}
    )
    valid_rate = _rate(valid, assigned)
    timeout_rate = _rate(timeouts, assigned)
    invalid_rate = _rate(invalid, assigned)
    coverage = _rate(len(formal_cases), assigned)
    valid_cases = [
        item
        for item in cases
        if item["challenger_parse_status"] in _VALID_PARSE_STATUSES
    ]
    champion_sanitized_rate = _rate(
        sum(item["champion_claim_status"] == "sanitized" for item in valid_cases),
        valid,
    )
    challenger_sanitized_rate = _rate(
        sum(item["challenger_claim_status"] == "sanitized" for item in valid_cases),
        valid,
    )
    sanitized_delta = (
        round(challenger_sanitized_rate - champion_sanitized_rate, 8)
        if champion_sanitized_rate is not None
        and challenger_sanitized_rate is not None
        else None
    )
    guard_failures = sum(
        item["challenger_claim_status"] == "violation" for item in cases
    )

    day_rows = _day_cluster_rows(formal_cases)
    utility_values = [row["utility_delta_pp"] for row in day_rows]
    drawdown_values = [row["drawdown_delta_pp"] for row in day_rows]
    utility_mean = _mean(utility_values)
    drawdown_mean = _mean(drawdown_values)
    seed_material = (
        "prompt-paired-gate-v2|"
        f"{normalized_policy['policy_hash']}|{stratum_hash}"
    )
    seed_hex = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    bootstrap_iterations = normalized_policy["statistics"]["bootstrap_iterations"]
    permutation_iterations = normalized_policy["statistics"]["permutation_iterations"]
    utility_ci = _cluster_bootstrap_ci(
        utility_values,
        iterations=bootstrap_iterations,
        seed_hex=seed_hex,
        stream="utility",
    )
    drawdown_ci = _cluster_bootstrap_ci(
        drawdown_values,
        iterations=bootstrap_iterations,
        seed_hex=seed_hex,
        stream="drawdown",
    )
    utility_p = _sign_flip_p_value(
        utility_values,
        iterations=permutation_iterations,
        seed_hex=seed_hex,
        stream="utility",
        alternative="greater",
    )
    drawdown_p = _sign_flip_p_value(
        drawdown_values,
        iterations=permutation_iterations,
        seed_hex=seed_hex,
        stream="drawdown",
        alternative="less",
    )

    thresholds = normalized_policy["gate_thresholds"]
    threshold_results = {
        "minimum_mature_decision_days": len(mature_dates)
        >= thresholds["minimum_mature_decision_days"],
        "minimum_paired_label_coverage": coverage is not None
        and coverage >= thresholds["minimum_paired_label_coverage"],
        "minimum_differing_case_count": len(differing_cases)
        >= thresholds["minimum_differing_case_count"],
        "minimum_challenger_valid_completion_rate": valid_rate is not None
        and valid_rate >= thresholds["minimum_challenger_valid_completion_rate"],
        "maximum_challenger_timeout_rate": timeout_rate is not None
        and timeout_rate <= thresholds["maximum_challenger_timeout_rate"],
        "maximum_challenger_invalid_rate": invalid_rate is not None
        and invalid_rate <= thresholds["maximum_challenger_invalid_rate"],
        "maximum_integrity_failure_count": failures["integrity_failure_count"]
        <= thresholds["maximum_integrity_failure_count"],
        "maximum_tenant_failure_count": failures["tenant_failure_count"]
        <= thresholds["maximum_tenant_failure_count"],
        "maximum_guard_failure_count": guard_failures
        <= thresholds["maximum_guard_failure_count"],
        "minimum_mean_utility_delta_pp": utility_mean is not None
        and utility_mean >= thresholds["minimum_mean_utility_delta_pp"],
        "minimum_utility_ci95_lower_pp": utility_ci is not None
        and utility_ci[0] >= thresholds["minimum_utility_ci95_lower_pp"],
        "maximum_mean_drawdown_delta_pp": drawdown_mean is not None
        and drawdown_mean <= thresholds["maximum_mean_drawdown_delta_pp"],
        "maximum_drawdown_ci95_upper_pp": drawdown_ci is not None
        and drawdown_ci[1] <= thresholds["maximum_drawdown_ci95_upper_pp"],
        "maximum_sanitized_rate_delta": sanitized_delta is not None
        and sanitized_delta <= thresholds["maximum_sanitized_rate_delta"],
        "maximum_budget_violation_count": failures["budget_violation_count"]
        <= thresholds["maximum_budget_violation_count"],
    }
    reasons = sorted(
        f"threshold_failed:{name}"
        for name, passed in threshold_results.items()
        if not passed
    )
    status = (
        "not_evaluated"
        if assigned == 0
        else "ready_for_manual_review"
        if not reasons
        else "shadow_evaluation"
    )
    gate: dict[str, Any] = {
        "schema_version": PROMPT_SHADOW_GATE_SCHEMA_VERSION,
        "evaluator_version": PROMPT_SHADOW_EVALUATOR_VERSION,
        "evaluation_as_of": cutoff.isoformat(),
        "status": status,
        "reason_codes": reasons,
        "policy_id": normalized_policy["policy_id"],
        "policy_hash": normalized_policy["policy_hash"],
        "stratum_hash": stratum_hash,
        "assigned_registration_count": assigned,
        "paired_case_count": len(cases),
        "formal_paired_case_count": len(formal_cases),
        "differing_case_count": len(differing_cases),
        "mature_decision_dates": mature_dates,
        "mature_decision_day_count": len(mature_dates),
        "paired_label_coverage": coverage,
        "challenger_valid_completion_count": valid,
        "challenger_timeout_count": timeouts,
        "challenger_invalid_count": invalid,
        "challenger_valid_completion_rate": valid_rate,
        "challenger_timeout_rate": timeout_rate,
        "challenger_invalid_rate": invalid_rate,
        "integrity_failure_count": failures["integrity_failure_count"],
        "tenant_failure_count": failures["tenant_failure_count"],
        "guard_failure_count": guard_failures,
        "budget_violation_count": failures["budget_violation_count"],
        "champion_sanitized_rate": champion_sanitized_rate,
        "challenger_sanitized_rate": challenger_sanitized_rate,
        "sanitized_rate_delta": sanitized_delta,
        "mean_utility_delta_pp": utility_mean,
        "utility_ci95_pp": _ci_object(utility_ci),
        "utility_sign_flip_p_value": utility_p,
        "mean_drawdown_delta_pp": drawdown_mean,
        "drawdown_ci95_pp": _ci_object(drawdown_ci),
        "drawdown_sign_flip_p_value": drawdown_p,
        "day_cluster_count": len(day_rows),
        "statistics": {
            **deepcopy(normalized_policy["statistics"]),
            "seed_sha256": seed_hex,
            "quantile_method": "hyndman_fan_type_7",
        },
        "thresholds": deepcopy(thresholds),
        "threshold_results": threshold_results,
        "paired_case_refs": [
            {"case_id": item["case_id"], "content_hash": item["content_hash"]}
            for item in sorted(cases, key=lambda item: item["case_id"])
        ],
        "automatic_promotion_allowed": False,
    }
    gate["gate_hash"] = canonical_hash(gate)
    return gate


def _validate_candidate_binding(
    candidate_case: Mapping[str, Any],
    champion_output: Mapping[str, Any],
    challenger_output: Mapping[str, Any],
    *,
    evaluation_as_of: datetime,
) -> None:
    champion_ref = champion_output["candidate_audit_ref"]
    challenger_ref = challenger_output["candidate_audit_ref"]
    if champion_ref != challenger_ref:
        raise PromptShadowEvaluationError("prompt pair references different candidate audits")
    if (
        candidate_case.get("audit_artifact_id") != champion_ref["artifact_id"]
        or candidate_case.get("audit_content_hash")
        != champion_ref["artifact_content_hash"]
        or candidate_case.get("audit_commit_receipt_id") != champion_ref["receipt_id"]
        or candidate_case.get("audit_commit_receipt_content_hash")
        != champion_ref["receipt_content_hash"]
        or candidate_case.get("outcome_commit_receipt_status") != "verified"
        or candidate_case.get("horizon_trading_days") != 20
        or not isinstance(candidate_case.get("outcome_labels"), Mapping)
    ):
        raise PromptShadowEvaluationError(
            "prompt-shadow output is detached from formal candidate labels"
        )
    boundary = _aware_timestamp(
        candidate_case.get("label_storage_created_at"),
        "candidate label boundary",
    )
    if boundary > evaluation_as_of:
        raise PromptShadowEvaluationError("future candidate labels are forbidden")


def _candidate_label_boundary(candidate_case: Mapping[str, Any] | None) -> str | None:
    if candidate_case is None:
        return None
    value = candidate_case.get("label_storage_created_at")
    if value is None or candidate_case.get("outcome_commit_receipt_status") != "verified":
        return None
    return _aware_timestamp(value, "candidate label boundary").isoformat()


def _portfolio_metrics(
    projection: Mapping[str, Any], candidate_case: Mapping[str, Any]
) -> dict[str, float] | None:
    decision = build_decision_projection(projection)
    budget = float(projection["requested_budget_yuan"])
    if not math.isfinite(budget) or budget <= 0:
        raise PromptShadowEvaluationError("prompt-shadow budget is invalid")
    labels = candidate_case.get("outcome_labels")
    if not isinstance(labels, Mapping):
        return None
    paths: dict[str, list[tuple[str, float]]] = {}
    for allocation in decision["allocations"]:
        amount = float(allocation["suggested_amount_yuan"])
        if amount <= 0:
            continue
        code = allocation["fund_code"]
        label = labels.get(code)
        evidence = label.get("evidence") if isinstance(label, Mapping) else None
        observations = evidence.get("observations") if isinstance(evidence, Mapping) else None
        if (
            not isinstance(label, Mapping)
            or label.get("mature") is not True
            or label.get("eligible") is not True
            or not isinstance(observations, list)
        ):
            return None
        series = build_total_return_index(deepcopy(observations))
        if len(series.points) != 21 or series.invalid_points > 0 or series.return_coverage < 1.0:
            return None
        base = float(series.points[0][1])
        if not math.isfinite(base) or base <= 0:
            return None
        paths[code] = [(day, float(index) / base) for day, index in series.points]
    if not paths:
        return {"utility_percent": 0.0, "max_drawdown_percent": 0.0}
    dates = [day for day, _value in next(iter(paths.values()))]
    if any([day for day, _value in path] != dates for path in paths.values()):
        raise PromptShadowEvaluationError("candidate allocation paths use different dates")
    allocation_by_code = {
        item["fund_code"]: float(item["suggested_amount_yuan"])
        for item in decision["allocations"]
        if float(item["suggested_amount_yuan"]) > 0
    }
    values: list[float] = []
    allocated = sum(allocation_by_code.values())
    cash = budget - allocated
    if cash < -1e-6:
        raise PromptShadowEvaluationError("prompt-shadow allocation exceeds budget")
    for index in range(len(dates)):
        value = max(cash, 0.0) / budget
        value += sum(
            allocation_by_code[code] / budget * path[index][1]
            for code, path in paths.items()
        )
        values.append(value)
    utility = (values[-1] / values[0] - 1.0) * 100.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100.0)
    if not math.isfinite(utility) or not math.isfinite(max_drawdown):
        raise PromptShadowEvaluationError("prompt-shadow portfolio metric is non-finite")
    return {
        "utility_percent": round(utility, 8),
        "max_drawdown_percent": round(max_drawdown, 8),
    }


def _claim_status(projection: Mapping[str, Any] | None) -> str | None:
    if projection is None:
        return None
    audit = projection.get("claim_audit")
    status = audit.get("status") if isinstance(audit, Mapping) else None
    if status not in _CLAIM_STATUSES:
        raise PromptShadowEvaluationError("prompt-shadow claim audit is invalid")
    return str(status)


def _source_refs(
    receipts: Mapping[str, Mapping[str, Any] | None],
    candidate_case: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for receipt in receipts.values():
        if receipt is None:
            continue
        ref = {
            "artifact_id": str(receipt.get("artifact_id") or ""),
            "artifact_content_hash": str(
                receipt.get("artifact_content_hash") or ""
            ),
            "receipt_id": str(receipt.get("receipt_id") or ""),
            "receipt_content_hash": str(
                receipt.get("receipt_content_hash")
                or receipt.get("content_hash")
                or ""
            ),
        }
        if not all(ref.values()) or not _sha256(ref["artifact_content_hash"]) or not _sha256(
            ref["receipt_content_hash"]
        ):
            raise PromptShadowEvaluationError("prompt-shadow source receipt ref is invalid")
        refs.append(ref)
    if candidate_case is not None:
        for prefix in ("audit", "outcome"):
            artifact_id = candidate_case.get(f"{prefix}_artifact_id")
            artifact_hash = candidate_case.get(f"{prefix}_content_hash")
            receipt_id = candidate_case.get(f"{prefix}_commit_receipt_id")
            receipt_hash = candidate_case.get(
                f"{prefix}_commit_receipt_content_hash"
            )
            if all(isinstance(item, str) and item for item in (
                artifact_id,
                artifact_hash,
                receipt_id,
                receipt_hash,
            )):
                refs.append(
                    {
                        "artifact_id": artifact_id,
                        "artifact_content_hash": artifact_hash,
                        "receipt_id": receipt_id,
                        "receipt_content_hash": receipt_hash,
                    }
                )
    unique = {
        (item["artifact_id"], item["receipt_id"]): item for item in refs
    }
    if len(unique) != len(refs):
        raise PromptShadowEvaluationError("prompt-shadow source refs are duplicated")
    return sorted(
        refs, key=lambda item: (item["artifact_id"], item["receipt_id"])
    )


def _day_cluster_rows(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, float | str]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case["live_cohort_date_local"]), []).append(case)
    return [
        {
            "live_cohort_date_local": day,
            "utility_delta_pp": round(
                sum(float(item["utility_delta_pp"]) for item in rows) / len(rows), 8
            ),
            "drawdown_delta_pp": round(
                sum(float(item["drawdown_delta_pp"]) for item in rows) / len(rows), 8
            ),
        }
        for day, rows in sorted(grouped.items())
    ]


def _cluster_bootstrap_ci(
    values: Sequence[float],
    *,
    iterations: int,
    seed_hex: str,
    stream: str,
) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(_stream_seed(seed_hex, f"bootstrap:{stream}"))
    count = len(values)
    samples = [
        sum(values[rng.randrange(count)] for _index in range(count)) / count
        for _iteration in range(iterations)
    ]
    samples.sort()
    return (
        round(_type7_quantile(samples, 0.025), 8),
        round(_type7_quantile(samples, 0.975), 8),
    )


def _sign_flip_p_value(
    values: Sequence[float],
    *,
    iterations: int,
    seed_hex: str,
    stream: str,
    alternative: str,
) -> float | None:
    if not values:
        return None
    rng = random.Random(_stream_seed(seed_hex, f"sign_flip:{stream}"))
    observed = sum(values) / len(values)
    extreme = 0
    for _iteration in range(iterations):
        candidate = sum(value if rng.getrandbits(1) else -value for value in values) / len(
            values
        )
        if (alternative == "greater" and candidate >= observed) or (
            alternative == "less" and candidate <= observed
        ):
            extreme += 1
    return round((extreme + 1) / (iterations + 1), 8)


def _type7_quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise PromptShadowEvaluationError("quantile input is empty")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return float(sorted_values[lower]) + fraction * (
        float(sorted_values[upper]) - float(sorted_values[lower])
    )


def _stream_seed(seed_hex: str, stream: str) -> int:
    return int(hashlib.sha256(f"{seed_hex}|{stream}".encode("utf-8")).hexdigest(), 16)


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator > 0 else None


def _ci_object(value: tuple[float, float] | None) -> dict[str, float] | None:
    return {"lower": value[0], "upper": value[1]} if value is not None else None


def _aware_timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PromptShadowEvaluationError(f"{name} is invalid") from exc
    else:
        raise PromptShadowEvaluationError(f"{name} is invalid")
    if result.tzinfo is None or result.utcoffset() is None:
        raise PromptShadowEvaluationError(f"{name} must include a timezone")
    return result


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _content_id(value: object, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix) and _sha256(
        value[len(prefix) :]
    )


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PromptShadowEvaluationError("failure count must be a nonnegative integer")
    return value


__all__ = [
    "PROMPT_SHADOW_EVALUATOR_VERSION",
    "PROMPT_SHADOW_GATE_SCHEMA_VERSION",
    "PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION",
    "PromptShadowEvaluationError",
    "build_prompt_shadow_paired_case",
    "evaluate_prompt_shadow_gate",
    "normalize_prompt_shadow_paired_case",
    "prompt_shadow_stratum_hash",
]
