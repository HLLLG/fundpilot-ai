"""Pure offline quality evaluation for frozen decision evidence.

The evaluator has no provider or persistence dependencies.  It accepts only
immutable evidence, retains missing labels as unavailable coverage, and never
promotes a variant.  Candidate-selection formal metrics additionally require
provider-origin receipts and post-commit artifact-visibility receipts.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.services.benchmark_fee_evaluation import (
    METRIC_CONTRACT_VERSION,
    METRIC_NAMES,
    evaluate_decision_metrics,
    summarize_metrics,
)
from app.services.candidate_selection_audit import (
    evaluate_candidate_selection_audit,
    validate_candidate_selection_audit,
)
from app.services.decision_contract import (
    DECISION_QUALITY_CONTRACT_VERSION,
    DECISION_REPLAY_BUNDLE_SCHEMA_VERSION,
    DECISION_VARIANT_MANIFEST_SCHEMA_VERSION,
    decision_replay_bundle_error,
    payload_hash,
)
from app.services.decision_repository import decision_event_content_hash
from app.services.decision_quality_provider_policy import (
    CandidateProviderAdapterPolicyError,
    candidate_adapter_policy_is_registered,
    candidate_provider_adapter_stratum,
    candidate_provider_adapter_stratum_hash,
)


DECISION_QUALITY_EVALUATION_SCHEMA_VERSION = "decision_quality_evaluation.v1"
PAIRED_GATE_SCHEMA_VERSION = "decision_quality_paired_gate.v1"
PAIRED_CASE_SCHEMA_VERSION = "decision_quality_paired_case.v1"
GATE_POLICY_SCHEMA_VERSION = "decision_quality_gate_policy.v1"
CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION = "decision_quality_claim_audit_wrapper.v1"
CLAIM_AUDIT_SCHEMA_VERSION = "fund_lookthrough_claim_audit.v1"
CANDIDATE_SELECTION_CASE_SCHEMA_VERSION_V1 = (
    "decision_quality_candidate_selection_case.v1"
)
CANDIDATE_SELECTION_CASE_SCHEMA_VERSION = (
    "decision_quality_candidate_selection_case.v2"
)
CANDIDATE_SELECTION_EVALUATOR_VERSION = (
    "decision_quality_candidate_selection_evaluator.v2"
)
CANDIDATE_SELECTION_EVIDENCE_SCOPE = (
    "source_verified_provider_and_post_commit_receipts"
)
CANDIDATE_FORMAL_LABEL_POLICY_VERSION = "candidate_label_policy.2026-07.v3"
_CANDIDATE_CALENDAR_PROVIDER = "akshare.tool_trade_date_hist_sina"
_CANDIDATE_CALENDAR_OPERATION = "tool_trade_date_hist_sina"
_CANDIDATE_NAV_PROVIDER = "akshare.fund_open_fund_info_em"
_CANDIDATE_NAV_OPERATION = "fund_open_fund_info_em"
CANDIDATE_MIN_SHADOW_MATURE_DECISION_DAYS = 20
CANDIDATE_MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS = 60
CANDIDATE_MIN_MANUAL_REVIEW_COVERAGE_PERCENT = 80.0
_CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS = 300
_CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS = 300

_HASH_LENGTH = 64
_TERMINAL_STATUSES = {"mature", "hit", "miss", "settled", "terminal"}
_ACTIONABLE_CLASSES = {"bullish", "bearish", "buy"}
_ABSTENTION_CLASSES = {"observation", "watch_only", "conditional_wait"}
_CLAIM_STATUSES = {"clean", "sanitized", "violation"}
_VARIANT_VERSION_FIELDS = (
    "model_version",
    "prompt_version",
    "strategy_version",
    "policy_version",
    "data_version",
    "fee_model_version",
)
_VARIANT_HASH_FIELDS = (
    "model_hash",
    "prompt_hash",
    "prompt_contract_hash",
    "strategy_hash",
    "policy_hash",
    "data_hash",
    "evidence_hash",
    "fee_model_hash",
    "variant_hash",
)
_CANDIDATE_PROVIDER_REF_FIELDS = frozenset(
    {
        "receipt_id",
        "content_hash",
        "provider",
        "operation",
        "capture_mode",
        "request_hash",
        "adapter_output_sha256",
        "normalized_payload_hash",
        "origin_fetched_at",
        "completed_at",
        "origin_receipt_hash",
        "adapter_policy_id",
        "adapter_policy_hash",
        "adapter_contract_version",
        "adapter_script_sha256",
        "adapter_policy_script_sha256",
        "adapter_library_name",
        "adapter_library_version",
        "adapter_python_version",
    }
)
_CANDIDATE_RECEIPT_FIELDS = frozenset(
    {
        "audit_commit_receipt_status",
        "audit_commit_receipt_id",
        "audit_commit_receipt_content_hash",
        "audit_commit_receipt_source_visible_at",
        "outcome_commit_receipt_status",
        "outcome_artifact_id",
        "outcome_content_hash",
        "outcome_commit_receipt_id",
        "outcome_commit_receipt_content_hash",
        "outcome_commit_receipt_source_visible_at",
        "label_storage_created_at",
        "provider_receipt_count",
        "provider_receipt_manifest_hash",
        "provider_receipt_refs",
        "provider_adapter_stratum",
        "provider_adapter_stratum_hash",
        "decision_at",
        "audit_source_row_created_at",
        "capture_status",
        "capture_reason",
        "capture_reason_hash",
        "source_capture_delay_seconds",
        "capture_artifact_type",
    }
)


@dataclass(frozen=True)
class _Slot:
    event: dict[str, Any]
    horizon: int
    metrics: dict[str, dict[str, Any]]
    matched: bool
    abstained: bool
    replay_eligible: bool


def evaluate_decision_quality(
    decision_events: object,
    outcome_observations: object,
    *,
    claim_audits: object = None,
    abstention_shadow_labels: object = None,
    candidate_selection_cases: object = None,
    evaluation_as_of: str | datetime | None = None,
    min_calibration_samples: int = 30,
    calibration_bins: int = 10,
    calibration_metric: str = "gross_direction",
    paired_comparison: Mapping[str, Any] | None = None,
    gate_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate frozen events and outcomes without fetching or mutating data."""

    evaluation_cutoff = _aware_datetime(evaluation_as_of)
    config_reasons = _validate_evaluation_config(
        evaluation_as_of=evaluation_cutoff,
        min_calibration_samples=min_calibration_samples,
        calibration_bins=calibration_bins,
        calibration_metric=calibration_metric,
    )
    safe_minimum_samples = (
        min_calibration_samples
        if _positive_int(min_calibration_samples) is not None
        else 1
    )
    safe_calibration_bins = (
        calibration_bins
        if _positive_int(calibration_bins) is not None
        and calibration_bins <= 100
        else 10
    )
    safe_calibration_metric = (
        calibration_metric
        if calibration_metric in METRIC_NAMES
        else "gross_direction"
    )
    events, event_exclusions, event_records = _normalize_events(
        decision_events,
        evaluation_as_of=evaluation_cutoff,
    )
    observations, observation_exclusions, observation_records = (
        _normalize_observations(
            outcome_observations,
            evaluation_as_of=evaluation_cutoff,
        )
    )
    shadow_labels, shadow_exclusions, shadow_records = _normalize_shadow_labels(
        abstention_shadow_labels,
        events=events,
        evaluation_as_of=evaluation_cutoff,
    )

    slots: list[_Slot] = []
    matched_keys: set[tuple[str, int]] = set()
    formal_matched_keys: set[tuple[str, int]] = set()
    match_records: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        abstained = _is_abstained(event)
        replay_eligible, replay_reason = _event_replay_eligibility(event)
        knowledge_cutoff = _event_knowledge_cutoff(event)
        for horizon in event["horizons"]:
            key = (event_id, int(horizon))
            observation = observations.get(key)
            if observation is None:
                metrics = _pending_metric_set(event)
                matched = False
                match_status = "label_unavailable"
                match_reason = "terminal_outcome_not_found"
            else:
                metric_error = _metric_contract_error(event, observation)
                if metric_error is not None:
                    observation_exclusions[metric_error] += 1
                    metrics = _pending_metric_set(event)
                    matched = False
                    match_status = "metric_contract_excluded"
                    match_reason = metric_error
                else:
                    metrics = {
                        name: dict(observation["metrics"][name])
                        for name in METRIC_NAMES
                    }
                    matched = True
                    matched_keys.add(key)
                    if replay_eligible:
                        formal_matched_keys.add(key)
                    match_status = "matched_terminal"
                    match_reason = None
            match_records.append(
                {
                    "event_id": event_id,
                    "horizon_trading_days": int(horizon),
                    "match_status": match_status,
                    "reason": match_reason,
                    "decision_knowledge_cutoff": (
                        knowledge_cutoff.isoformat()
                        if knowledge_cutoff is not None
                        else None
                    ),
                    "label_available_at": (
                        observation.get("_evaluation_label_available_at")
                        if observation is not None
                        else None
                    ),
                    "label_source_available_at": (
                        observation.get("_evaluation_label_source_available_at")
                        if observation is not None
                        else None
                    ),
                    "label_availability_basis": (
                        observation.get("_evaluation_label_availability_basis")
                        if observation is not None
                        else None
                    ),
                    "label_first_observed_at": (
                        observation.get("_evaluation_label_first_observed_at")
                        if observation is not None
                        else None
                    ),
                    "label_recorded_at": (
                        observation.get("_evaluation_label_recorded_at")
                        if observation is not None
                        else None
                    ),
                    "replay_status": (
                        "eligible" if replay_eligible else "ineligible"
                    ),
                    "replay_reason": replay_reason,
                    "formal_score_status": (
                        "included"
                        if replay_eligible
                        else "excluded_replay_ineligible"
                    ),
                }
            )
            slots.append(
                _Slot(
                    event=event,
                    horizon=int(horizon),
                    metrics=metrics,
                    matched=matched,
                    abstained=abstained,
                    replay_eligible=replay_eligible,
                )
            )

    valid_event_keys = {
        (str(event["event_id"]), int(horizon))
        for event in events
        for horizon in event["horizons"]
    }
    for key in observations:
        if key not in valid_event_keys:
            observation_exclusions["outcome_without_matching_event_horizon"] += 1
    orphan_outcomes = [
        {
            "event_id": key[0],
            "horizon_trading_days": key[1],
            "reason": "outcome_without_matching_event_horizon",
        }
        for key in sorted(observations)
        if key not in valid_event_keys
    ]
    formal_slots = [slot for slot in slots if slot.replay_eligible]

    base: dict[str, Any] = {
        "schema_version": DECISION_QUALITY_EVALUATION_SCHEMA_VERSION,
        "status": "unavailable",
        "reason_codes": [],
        "input_audit": {
            "evaluation_as_of": (
                evaluation_cutoff.isoformat() if evaluation_cutoff else None
            ),
            "valid_event_count": len(events),
            "input_event_horizon_count": len(slots),
            "formal_event_horizon_count": len(formal_slots),
            "matched_terminal_outcome_input_count": len(matched_keys),
            "matched_terminal_outcome_count": len(formal_matched_keys),
            "event_exclusions": _counter_rows(event_exclusions),
            "outcome_exclusions": _counter_rows(observation_exclusions),
            "shadow_label_exclusions": _counter_rows(shadow_exclusions),
            "event_records": event_records,
            "outcome_records": observation_records,
            "event_horizon_matches": match_records,
            "orphan_outcomes": orphan_outcomes,
            "shadow_label_records": shadow_records,
        },
        "overall": _summarize_slots(
            slots,
            shadow_labels=shadow_labels,
            min_calibration_samples=safe_minimum_samples,
            calibration_bins=safe_calibration_bins,
            calibration_metric=safe_calibration_metric,
        ),
        "stratified": {
            "decision_kind": _group_slots(
                formal_slots,
                lambda slot: str(slot.event.get("decision_kind") or "missing"),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "action": _group_slots(
                formal_slots,
                lambda slot: str(
                    slot.event.get("final_action")
                    or slot.event.get("action")
                    or "missing"
                ),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "horizon": _group_slots(
                formal_slots,
                lambda slot: slot.horizon,
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "fund_type": _group_slots(
                formal_slots,
                lambda slot: _frozen_dimension(slot.event.get("fund_type")),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "market_regime": _group_slots(
                formal_slots,
                lambda slot: _frozen_dimension(
                    slot.event.get("market_regime")
                    if slot.event.get("market_regime") is not None
                    else slot.event.get("market_state")
                ),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "data_completeness": _group_slots(
                formal_slots,
                lambda slot: _frozen_dimension(
                    slot.event.get("data_completeness")
                ),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
            "variant": _group_slots(
                formal_slots,
                lambda slot: _variant(slot.event),
                shadow_labels=shadow_labels,
                min_calibration_samples=safe_minimum_samples,
                calibration_bins=safe_calibration_bins,
                calibration_metric=safe_calibration_metric,
            ),
        },
        "claim_audits": _summarize_claim_audits(
            claim_audits,
            events=events,
            evaluation_as_of=evaluation_cutoff,
        ),
        "candidate_selection": _evaluate_candidate_selection_cases(
            candidate_selection_cases,
            evaluation_as_of=evaluation_cutoff,
        ),
        "automatic_promotion_allowed": False,
        "evaluation_hash": None,
    }
    if config_reasons:
        _invalidate_calibration_outputs(base, config_reasons)
        base["reason_codes"] = config_reasons
    elif not slots:
        base["reason_codes"] = ["formal_decision_events_unavailable"]
    elif not formal_slots:
        base["reason_codes"] = ["formal_decision_events_unavailable"]
    elif (
        not formal_matched_keys
        and base["overall"]["abstention"]["quality_status"] != "available"
    ):
        base["reason_codes"] = ["mature_terminal_labels_unavailable"]
    else:
        base["status"] = "available"

    if paired_comparison is not None or gate_policy is not None:
        champion = (
            paired_comparison.get("champion")
            if isinstance(paired_comparison, Mapping)
            else None
        )
        challenger = (
            paired_comparison.get("challenger")
            if isinstance(paired_comparison, Mapping)
            else None
        )
        base["paired_gate"] = evaluate_paired_champion_challenger_gate(
            champion,
            challenger,
            policy=gate_policy,
            evaluation_as_of=evaluation_cutoff,
        )
    base["evaluation_hash"] = _canonical_hash(
        {key: value for key, value in base.items() if key != "evaluation_hash"}
    )
    return base


def _invalidate_calibration_outputs(
    evaluation: Mapping[str, Any],
    reason_codes: Sequence[str],
) -> None:
    summaries: list[object] = [evaluation.get("overall")]
    stratified = evaluation.get("stratified")
    if isinstance(stratified, Mapping):
        for groups in stratified.values():
            if isinstance(groups, Sequence) and not isinstance(groups, (str, bytes)):
                summaries.extend(groups)
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        calibration = summary.get("calibration")
        if not isinstance(calibration, dict):
            continue
        calibration.update(
            {
                "status": "unavailable",
                "reason": "evaluation_config_invalid",
                "reason_codes": list(reason_codes),
                "ece": None,
                "brier": None,
                "bins": [],
            }
        )


def evaluate_paired_champion_challenger_gate(
    champion_cases: object,
    challenger_cases: object,
    *,
    policy: Mapping[str, Any] | None,
    evaluation_as_of: str | datetime | None = None,
) -> dict[str, Any]:
    """Return only a human-review eligibility decision, never a promotion."""

    evaluation_cutoff = _aware_datetime(evaluation_as_of)
    policy_value, policy_reasons = _validate_gate_policy(policy)
    if evaluation_cutoff is None:
        policy_reasons.append("paired_evaluation_as_of_missing_or_invalid")
    champion, champion_reasons = _normalize_paired_cases(champion_cases)
    challenger, challenger_reasons = _normalize_paired_cases(challenger_cases)
    champion_keys = set(champion)
    challenger_keys = set(challenger)
    shared_keys = sorted(champion_keys & challenger_keys)
    pairing_reasons: list[str] = []
    if champion_keys != challenger_keys:
        pairing_reasons.append("paired_case_key_sets_differ")

    deltas: list[tuple[float, float, str]] = []
    first_output: datetime | None = None
    for key in shared_keys:
        left = champion[key]
        right = challenger[key]
        left_output = _aware_datetime(left.get("output_at"))
        right_output = _aware_datetime(right.get("output_at"))
        left_label_at = _aware_datetime(left.get("label_available_at"))
        right_label_at = _aware_datetime(right.get("label_available_at"))
        if (
            left_output is None
            or right_output is None
            or left_label_at is None
            or right_label_at is None
            or left_label_at != right_label_at
            or left_label_at <= max(left_output, right_output)
        ):
            pairing_reasons.append("label_not_strictly_after_both_outputs")
            continue
        if (
            evaluation_cutoff is None
            or max(
                left_output,
                right_output,
                left_label_at,
                right_label_at,
            )
            > evaluation_cutoff
        ):
            pairing_reasons.append("paired_case_after_evaluation_boundary")
            continue
        if left.get("label_hash") != right.get("label_hash"):
            pairing_reasons.append("paired_label_hash_conflict")
            continue
        if left.get("decision_at") != right.get("decision_at"):
            pairing_reasons.append("paired_decision_time_conflict")
            continue
        utility_left = _finite_number(left.get("utility"))
        utility_right = _finite_number(right.get("utility"))
        risk_left = _finite_number(left.get("risk"))
        risk_right = _finite_number(right.get("risk"))
        if None in {utility_left, utility_right, risk_left, risk_right}:
            pairing_reasons.append("paired_utility_or_risk_missing")
            continue
        if left.get("replay_eligible") is not True or right.get("replay_eligible") is not True:
            pairing_reasons.append("paired_case_replay_ineligible")
            continue
        first_output = min(
            value
            for value in (first_output, left_output, right_output)
            if value is not None
        )
        deltas.append(
            (
                float(utility_right) - float(utility_left),
                float(risk_right) - float(risk_left),
                str(right.get("claim_status")),
            )
        )

    reasons = [*policy_reasons, *champion_reasons, *challenger_reasons, *pairing_reasons]
    if policy_value is not None and first_output is not None:
        registered_at = _aware_datetime(policy_value.get("registered_at"))
        if registered_at is None or registered_at >= first_output:
            reasons.append("gate_policy_not_preregistered_before_outputs")
    if policy_value is not None and evaluation_cutoff is not None:
        registered_at = _aware_datetime(policy_value.get("registered_at"))
        if registered_at is None or registered_at > evaluation_cutoff:
            reasons.append("gate_policy_after_evaluation_boundary")

    pair_count = len(deltas)
    mean_utility_delta = _mean([row[0] for row in deltas])
    mean_risk_delta = _mean([row[1] for row in deltas])
    violation_rate = (
        sum(row[2] == "violation" for row in deltas) / pair_count
        if pair_count
        else None
    )
    sanitized_rate = (
        sum(row[2] == "sanitized" for row in deltas) / pair_count
        if pair_count
        else None
    )
    threshold_results: dict[str, bool] = {}
    if policy_value is not None:
        threshold_results = {
            "min_pairs": pair_count >= int(policy_value["min_pairs"]),
            "minimum_mean_utility_delta": bool(
                mean_utility_delta is not None
                and mean_utility_delta
                >= float(policy_value["minimum_mean_utility_delta"])
            ),
            "maximum_mean_risk_delta": bool(
                mean_risk_delta is not None
                and mean_risk_delta
                <= float(policy_value["maximum_mean_risk_delta"])
            ),
            "maximum_claim_violation_rate": bool(
                violation_rate is not None
                and violation_rate
                <= float(policy_value["maximum_claim_violation_rate"])
            ),
            "maximum_claim_sanitized_rate": bool(
                sanitized_rate is not None
                and sanitized_rate
                <= float(policy_value["maximum_claim_sanitized_rate"])
            ),
        }
        reasons.extend(
            f"threshold_failed:{name}"
            for name, passed in threshold_results.items()
            if not passed
        )
    reasons = sorted(set(reasons))
    status = (
        "eligible_for_human_review"
        if policy_value is not None and not reasons and all(threshold_results.values())
        else "blocked"
    )
    result = {
        "schema_version": PAIRED_GATE_SCHEMA_VERSION,
        "evaluation_as_of": (
            evaluation_cutoff.isoformat() if evaluation_cutoff else None
        ),
        "status": status,
        "reason_codes": reasons,
        "policy_id": policy_value.get("policy_id") if policy_value else None,
        "policy_hash": policy_value.get("policy_hash") if policy_value else None,
        "paired_case_count": pair_count,
        "champion_case_count": len(champion),
        "challenger_case_count": len(challenger),
        "mean_utility_delta": _rounded(mean_utility_delta),
        "mean_risk_delta": _rounded(mean_risk_delta),
        "challenger_claim_violation_rate": _rounded(violation_rate),
        "challenger_claim_sanitized_rate": _rounded(sanitized_rate),
        "threshold_results": threshold_results,
        "automatic_promotion_allowed": False,
        "gate_hash": None,
    }
    result["gate_hash"] = _canonical_hash(
        {key: value for key, value in result.items() if key != "gate_hash"}
    )
    return result


def _normalize_events(
    value: object,
    *,
    evaluation_as_of: datetime | None,
) -> tuple[list[dict[str, Any]], Counter[str], list[dict[str, Any]]]:
    rows = _raw_sequence(value)
    exclusions: Counter[str] = Counter()
    by_id: dict[str, dict[str, Any]] = {}
    conflicted: set[str] = set()
    records: list[dict[str, Any]] = []
    accepted_record_by_id: dict[str, int] = {}
    if rows is None:
        exclusions["decision_event_collection_invalid"] += 1
        return [], exclusions, [
            {
                "input_index": None,
                "event_id": None,
                "status": "excluded",
                "reason": "decision_event_collection_invalid",
            }
        ]
    for index, raw in enumerate(rows):
        event, wrapper = _materialize(raw)
        event_id = _text(event.get("event_id"))
        reason = _event_contract_error(
            event,
            wrapper,
            evaluation_as_of=evaluation_as_of,
        )
        if reason is not None:
            exclusions[reason] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": reason,
                }
            )
            continue
        knowledge_cutoff = _normalized_event_knowledge_cutoff(event, wrapper)
        if knowledge_cutoff is None:
            exclusions["decision_event_knowledge_cutoff_invalid"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": "decision_event_knowledge_cutoff_invalid",
                }
            )
            continue
        # Evaluation-only metadata is attached after the signed payload and
        # storage envelope have both been verified.  In particular, a durable
        # event cannot be scored against a label that was already known when
        # that event finally reached the primary store.
        event["_evaluation_knowledge_cutoff"] = knowledge_cutoff.isoformat()
        event_id = str(event["event_id"])
        if event_id in conflicted:
            exclusions["decision_event_duplicate_conflict"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": "decision_event_duplicate_conflict",
                }
            )
            continue
        prior = by_id.get(event_id)
        if prior is None:
            by_id[event_id] = event
            accepted_record_by_id[event_id] = len(records)
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "accepted",
                    "reason": None,
                }
            )
        elif prior == event:
            exclusions["decision_event_duplicate_identical"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": "decision_event_duplicate_identical",
                }
            )
        else:
            exclusions["decision_event_duplicate_conflict"] += 2
            conflicted.add(event_id)
            by_id.pop(event_id, None)
            prior_record = accepted_record_by_id.pop(event_id)
            records[prior_record]["status"] = "excluded"
            records[prior_record]["reason"] = "decision_event_duplicate_conflict"
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": "decision_event_duplicate_conflict",
                }
            )
    return [by_id[key] for key in sorted(by_id)], exclusions, records


def _normalize_observations(
    value: object,
    *,
    evaluation_as_of: datetime | None,
) -> tuple[
    dict[tuple[str, int], dict[str, Any]],
    Counter[str],
    list[dict[str, Any]],
]:
    rows = _raw_sequence(value)
    exclusions: Counter[str] = Counter()
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    conflicted: set[tuple[str, int]] = set()
    records: list[dict[str, Any]] = []
    accepted_record_by_key: dict[tuple[str, int], int] = {}
    if rows is None:
        exclusions["outcome_observation_collection_invalid"] += 1
        return {}, exclusions, [
            {
                "input_index": None,
                "event_id": None,
                "horizon_trading_days": None,
                "status": "excluded",
                "reason": "outcome_observation_collection_invalid",
            }
        ]
    for index, raw in enumerate(rows):
        observation, wrapper = _materialize(raw)
        event_id = _text(
            observation.get("event_id")
            or observation.get("decision_event_id")
        )
        horizon = _positive_int(observation.get("horizon_trading_days"))
        reason = _observation_contract_error(
            observation,
            wrapper,
            evaluation_as_of=evaluation_as_of,
        )
        if reason is not None:
            exclusions[reason] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "excluded",
                    "reason": reason,
                }
            )
            continue
        key = (
            str(observation["event_id"]),
            int(observation["horizon_trading_days"]),
        )
        if key in conflicted:
            exclusions["outcome_observation_duplicate_conflict"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": key[0],
                    "horizon_trading_days": key[1],
                    "status": "excluded",
                    "reason": "outcome_observation_duplicate_conflict",
                }
            )
            continue
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = observation
            accepted_record_by_key[key] = len(records)
            records.append(
                {
                    "input_index": index,
                    "event_id": key[0],
                    "horizon_trading_days": key[1],
                    "status": "accepted",
                    "reason": None,
                }
            )
        elif prior == observation:
            exclusions["outcome_observation_duplicate_identical"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": key[0],
                    "horizon_trading_days": key[1],
                    "status": "excluded",
                    "reason": "outcome_observation_duplicate_identical",
                }
            )
        else:
            exclusions["outcome_observation_duplicate_conflict"] += 2
            conflicted.add(key)
            by_key.pop(key, None)
            prior_record = accepted_record_by_key.pop(key)
            records[prior_record]["status"] = "excluded"
            records[prior_record]["reason"] = "outcome_observation_duplicate_conflict"
            records.append(
                {
                    "input_index": index,
                    "event_id": key[0],
                    "horizon_trading_days": key[1],
                    "status": "excluded",
                    "reason": "outcome_observation_duplicate_conflict",
                }
            )
    return by_key, exclusions, records


def _materialize(raw: object) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}, {}
    wrapper = dict(raw)
    payload = wrapper.get("payload")
    return (
        (dict(payload), wrapper)
        if isinstance(payload, Mapping)
        else (dict(wrapper), {})
    )


def _event_contract_error(
    event: Mapping[str, Any],
    wrapper: Mapping[str, Any],
    *,
    evaluation_as_of: datetime | None,
) -> str | None:
    if any(str(key).startswith("_evaluation_") for key in event):
        return "decision_event_reserved_field_present"
    if event.get("schema_version") != "decision_event.v2":
        return "decision_event_schema_invalid"
    event_id = _text(event.get("event_id"))
    if event_id is None:
        return "decision_event_id_missing"
    if event.get("is_backfilled") is True or event.get("backfilled") is True:
        return "decision_event_backfilled"
    if event.get("metric_eligible") is not True:
        return "decision_event_metric_ineligible"
    if event.get("audit_eligible") is not True:
        return "decision_event_audit_ineligible"
    if event.get("store_authority") != "primary":
        return "decision_event_not_primary"
    decision_kind = _text(event.get("decision_kind"))
    if decision_kind not in {"daily", "discovery"}:
        return "decision_event_kind_invalid"
    if _text(event.get("source_type")) != decision_kind:
        return "decision_event_source_kind_conflict"
    decision_at = _aware_datetime(event.get("decision_at"))
    if decision_at is None:
        return "decision_event_time_invalid"
    if evaluation_as_of is None:
        return "decision_event_evaluation_boundary_unavailable"
    if decision_at > evaluation_as_of:
        return "decision_event_after_evaluation_boundary"
    quality_contract_version = _text(event.get("quality_contract_version"))
    replay_contract_flag = event.get("replay_contract_required")
    strict_replay_contract = (
        quality_contract_version == DECISION_QUALITY_CONTRACT_VERSION
        and replay_contract_flag is True
    )
    if (
        quality_contract_version is not None
        or replay_contract_flag is not None
    ) and not strict_replay_contract:
        return "decision_event_quality_contract_invalid"
    replay_bundle = event.get("replay_bundle")
    if strict_replay_contract and not isinstance(replay_bundle, Mapping):
        return "decision_event_replay_bundle_missing_or_invalid"
    if (
        strict_replay_contract
        and replay_bundle.get("schema_version") != DECISION_REPLAY_BUNDLE_SCHEMA_VERSION
    ):
        return "decision_event_replay_bundle_schema_invalid"
    if strict_replay_contract and not wrapper:
        recorded_at = _aware_datetime(replay_bundle.get("recorded_at"))
        if recorded_at is None:
            return "decision_event_receipt_time_missing_or_invalid"
        if recorded_at < decision_at:
            return "decision_event_receipt_before_decision"
        if recorded_at > evaluation_as_of:
            return "decision_event_receipt_after_evaluation_boundary"
    evaluation_class = _text(event.get("evaluation_class"))
    if evaluation_class not in _ACTIONABLE_CLASSES | _ABSTENTION_CLASSES:
        return "decision_event_action_contract_invalid"
    if _text(event.get("final_action") or event.get("action")) is None:
        return "decision_event_final_action_missing"
    if evaluation_class in _ACTIONABLE_CLASSES and event.get("eligible") is not True:
        return "decision_event_action_eligibility_conflict"
    if evaluation_class in _ABSTENTION_CLASSES and event.get("eligible") is not False:
        return "decision_event_abstention_eligibility_conflict"
    action_category = _text(event.get("action_category"))
    if action_category is not None and action_category != evaluation_class:
        return "decision_event_action_category_conflict"
    prompt_contract = event.get("prompt_contract")
    if prompt_contract is not None and not isinstance(prompt_contract, Mapping):
        return "decision_event_prompt_contract_invalid"
    variant_manifest = event.get("variant_manifest")
    if strict_replay_contract:
        if (
            not isinstance(variant_manifest, Mapping)
            or variant_manifest.get("schema_version")
            != DECISION_VARIANT_MANIFEST_SCHEMA_VERSION
        ):
            return "decision_event_variant_manifest_missing_or_invalid"
        for field in _VARIANT_VERSION_FIELDS:
            if _text(event.get(field)) is None:
                return f"decision_event_variant_version_missing:{field}"
        for field in _VARIANT_HASH_FIELDS:
            if not _is_hash(_text(event.get(field))):
                return f"decision_event_variant_hash_invalid:{field}"
        if not _is_hash(_text(event.get("replay_bundle_hash"))):
            return "decision_event_replay_bundle_hash_missing_or_invalid"
    else:
        legacy_hashes = {
            field: event.get(field)
            for field in (*_VARIANT_HASH_FIELDS, "replay_bundle_hash")
        }
        if isinstance(prompt_contract, Mapping):
            legacy_hashes["prompt_contract.contract_hash"] = prompt_contract.get(
                "contract_hash"
            )
        for field, raw_hash in legacy_hashes.items():
            if raw_hash is not None and not _is_hash(_text(raw_hash)):
                return f"decision_event_variant_hash_invalid:{field}"
    horizons = _positive_ints(event.get("horizons"))
    if not horizons:
        return "decision_event_horizons_invalid"
    if list(event.get("horizons") or []) != horizons:
        return "decision_event_horizons_not_canonical"
    if horizons != sorted(horizons):
        return "decision_event_horizons_not_canonical"
    supplied_hash = _text(event.get("payload_hash"))
    if not _is_hash(supplied_hash):
        return "decision_event_payload_hash_missing_or_invalid"
    event_material = {
        key: value for key, value in event.items() if key != "payload_hash"
    }
    try:
        _canonical_json(event_material)
        expected_hash = payload_hash(event_material)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "decision_event_payload_hash_uncomputable"
    if supplied_hash.lower() != expected_hash:
        return "decision_event_payload_hash_mismatch"
    if wrapper:
        for key in (
            "event_id",
            "schema_version",
            "event_type",
            "source_type",
            "decision_at",
            "fund_code",
            "final_action",
            "action_category",
            "eligible",
            "is_backfilled",
            "metric_eligible",
        ):
            if (
                key in wrapper
                and wrapper.get(key) is not None
                and wrapper.get(key) != event.get(key)
            ):
                return "decision_event_storage_contract_conflict"
        content_hash = _text(wrapper.get("content_hash"))
        if not _is_hash(content_hash):
            return "decision_event_content_hash_missing_or_invalid"
        try:
            expected_content_hash = decision_event_content_hash(event)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return "decision_event_content_hash_uncomputable"
        if content_hash.lower() != expected_content_hash:
            return "decision_event_content_hash_mismatch"
        created_at = _aware_datetime(wrapper.get("created_at"))
        if created_at is None:
            return "decision_event_receipt_time_missing_or_invalid"
        if created_at < decision_at:
            return "decision_event_receipt_before_decision"
        if created_at > evaluation_as_of:
            return "decision_event_receipt_after_evaluation_boundary"
        if strict_replay_contract:
            bundle_recorded_at = _aware_datetime(replay_bundle.get("recorded_at"))
            if bundle_recorded_at is None or bundle_recorded_at > created_at:
                return "decision_event_receipt_order_invalid"
            for ref in replay_bundle.get("replay_refs") or []:
                if not isinstance(ref, Mapping):
                    continue
                first_observed_at = _aware_datetime(ref.get("first_observed_at"))
                if first_observed_at is not None and first_observed_at > created_at:
                    return "decision_event_receipt_before_replay_evidence"
    normalized = dict(event)
    normalized["horizons"] = horizons
    event.clear() if isinstance(event, dict) else None
    if isinstance(event, dict):
        event.update(normalized)
    return None


def _normalized_event_knowledge_cutoff(
    event: Mapping[str, Any],
    wrapper: Mapping[str, Any],
) -> datetime | None:
    decision_at = _aware_datetime(event.get("decision_at"))
    if decision_at is None:
        return None
    candidates = [decision_at]
    if (
        event.get("quality_contract_version") == DECISION_QUALITY_CONTRACT_VERSION
        and event.get("replay_contract_required") is True
    ):
        bundle = event.get("replay_bundle")
        recorded_at = (
            _aware_datetime(bundle.get("recorded_at"))
            if isinstance(bundle, Mapping)
            else None
        )
        if recorded_at is None:
            return None
        candidates.append(recorded_at)
    if wrapper:
        created_at = _aware_datetime(wrapper.get("created_at"))
        if created_at is None:
            return None
        candidates.append(created_at)
    return max(candidates)


def _observation_contract_error(
    observation: Mapping[str, Any],
    wrapper: Mapping[str, Any],
    *,
    evaluation_as_of: datetime | None,
) -> str | None:
    if any(str(key).startswith("_evaluation_") for key in observation):
        return "outcome_observation_reserved_field_present"
    if observation.get("schema_version") != "outcome_observation.v2":
        return "outcome_observation_schema_invalid"
    event_id = _text(
        observation.get("event_id") or observation.get("decision_event_id")
    )
    horizon = _positive_int(observation.get("horizon_trading_days"))
    if event_id is None or horizon is None:
        return "outcome_observation_key_invalid"
    observation_id = _text(observation.get("observation_id"))
    if observation_id != f"{event_id}:T+{horizon}":
        return "outcome_observation_id_contract_conflict"
    if observation.get("backfilled") is True or observation.get("is_backfilled") is True:
        return "outcome_observation_backfilled"
    if observation.get("is_terminal") is not True or observation.get("mature") is not True:
        return "outcome_observation_not_terminal_mature"
    if _text(observation.get("status")) not in _TERMINAL_STATUSES:
        return "outcome_observation_status_not_terminal"
    if observation.get("metric_eligible") is False or observation.get("audit_eligible") is False:
        return "outcome_observation_explicitly_ineligible"
    if observation.get("store_authority") not in {None, "primary"}:
        return "outcome_observation_not_primary"
    metric_contract_version = observation.get("metric_contract_version")
    if (
        metric_contract_version is not None
        and metric_contract_version != METRIC_CONTRACT_VERSION
    ):
        return "outcome_observation_metric_contract_version_conflict"
    if not isinstance(observation.get("metrics"), Mapping):
        return "outcome_observation_metrics_missing"
    supplied_payload_hash = _text(observation.get("payload_hash"))
    if supplied_payload_hash is not None:
        observation_material = {
            key: value
            for key, value in observation.items()
            if key != "payload_hash"
        }
        try:
            _canonical_json(observation_material)
            expected_payload_hash = payload_hash(observation_material)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return "outcome_observation_payload_hash_uncomputable"
        if (
            not _is_hash(supplied_payload_hash)
            or supplied_payload_hash.lower() != expected_payload_hash
        ):
            return "outcome_observation_payload_hash_mismatch"
    if wrapper:
        for key in (
            "observation_id",
            "schema_version",
            "status",
            "is_terminal",
            "horizon_trading_days",
            "target_date",
        ):
            if (
                key in wrapper
                and wrapper.get(key) is not None
                and wrapper.get(key) != observation.get(key)
            ):
                return "outcome_observation_storage_contract_conflict"
        wrapper_event = wrapper.get("decision_event_id") or wrapper.get("event_id")
        if wrapper_event is not None and str(wrapper_event) != event_id:
            return "outcome_observation_storage_contract_conflict"
        content_hash = _text(wrapper.get("content_hash"))
        if not _is_hash(content_hash):
            return "outcome_observation_content_hash_missing_or_invalid"
        try:
            expected_content_hash = _outcome_content_hash(observation)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return "outcome_observation_content_hash_uncomputable"
        if content_hash.lower() != expected_content_hash:
            return "outcome_observation_content_hash_mismatch"
    elif supplied_payload_hash is None:
        return "outcome_observation_hash_missing"
    source_values = (
        observation.get("label_available_at"),
        observation.get("source_available_at"),
    )
    if wrapper:
        finalized_at = _aware_datetime(wrapper.get("finalized_at"))
        if finalized_at is None:
            return "outcome_observation_finalized_time_missing_or_invalid"
        receipt_times = (
            wrapper.get("finalized_at"),
            wrapper.get("observed_at"),
            wrapper.get("recorded_at"),
            wrapper.get("created_at"),
            wrapper.get("updated_at"),
        )
    else:
        receipt_times = (
            observation.get("finalized_at"),
            observation.get("observed_at"),
            observation.get("observation_at"),
            observation.get("recorded_at"),
        )
    if not any(value is not None for value in receipt_times):
        return "outcome_observation_receipt_time_missing"
    label_available_at = _outcome_label_available_at(observation, wrapper)
    if label_available_at is None:
        return "outcome_observation_label_time_missing_or_invalid"
    source_available_times: list[datetime] = []
    for value in source_values:
        if value is None:
            continue
        timestamp = _aware_datetime(value)
        if timestamp is None:
            return "outcome_observation_source_time_missing_or_invalid"
        source_available_times.append(timestamp)
    if not source_available_times and not wrapper:
        # A bare payload has no independently owned receipt.  It must continue
        # to carry a signed source-availability time; only a verified primary
        # storage envelope may conservatively substitute its terminal receipt.
        return "outcome_observation_source_time_missing"
    source_available_at = (
        max(source_available_times) if source_available_times else None
    )
    availability_basis = (
        "source_timestamp_and_storage_receipt"
        if source_available_times and wrapper
        else "source_timestamp_and_signed_receipt"
        if source_available_times
        else "storage_terminal_receipt"
    )
    if evaluation_as_of is None:
        return "outcome_observation_evaluation_boundary_unavailable"
    if label_available_at > evaluation_as_of:
        return "outcome_observation_after_evaluation_boundary"
    normalized = dict(observation)
    normalized["event_id"] = event_id
    normalized["horizon_trading_days"] = horizon
    normalized["_evaluation_label_available_at"] = label_available_at.isoformat()
    normalized["_evaluation_label_source_available_at"] = (
        source_available_at.isoformat() if source_available_at is not None else None
    )
    normalized["_evaluation_label_availability_basis"] = availability_basis
    normalized["_evaluation_label_first_observed_at"] = (
        wrapper.get("created_at") if wrapper else None
    )
    normalized["_evaluation_label_recorded_at"] = (
        wrapper.get("finalized_at") if wrapper else None
    )
    observation.clear() if isinstance(observation, dict) else None
    if isinstance(observation, dict):
        observation.update(normalized)
    return None


def _metric_contract_error(
    event: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> str | None:
    knowledge_cutoff = _event_knowledge_cutoff(event)
    label_available_at = _aware_datetime(
        observation.get("_evaluation_label_available_at")
    )
    source_available_at = _aware_datetime(
        observation.get("_evaluation_label_source_available_at")
    )
    availability_basis = _text(
        observation.get("_evaluation_label_availability_basis")
    )
    if (
        knowledge_cutoff is None
        or label_available_at is None
        or label_available_at <= knowledge_cutoff
    ):
        return "outcome_label_not_strictly_after_replay_boundary"
    if availability_basis not in {
        "source_timestamp_and_storage_receipt",
        "source_timestamp_and_signed_receipt",
        "storage_terminal_receipt",
    }:
        return "outcome_label_availability_basis_invalid"
    if (
        availability_basis != "storage_terminal_receipt"
        and (
            source_available_at is None
            or source_available_at <= knowledge_cutoff
        )
    ):
        return "outcome_label_not_strictly_after_replay_boundary"
    expected = _pending_metric_set(event)
    raw_metrics = observation.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        return "outcome_metric_contract_missing"
    for name in METRIC_NAMES:
        metric = raw_metrics.get(name)
        if not isinstance(metric, Mapping):
            return f"outcome_metric_missing:{name}"
        if metric.get("eligible") is not expected[name].get("eligible"):
            return f"outcome_metric_eligibility_conflict:{name}"
        if not isinstance(metric.get("mature"), bool):
            return f"outcome_metric_maturity_invalid:{name}"
        if metric.get("mature") is True:
            if metric.get("eligible") is not True or not isinstance(metric.get("hit"), bool):
                return f"outcome_metric_label_invalid:{name}"
            value = _finite_number(metric.get("value_percent"))
            if value is None:
                return f"outcome_metric_value_invalid:{name}"
            expected_hit = _metric_hit(
                name,
                evaluation_class=str(event.get("evaluation_class") or ""),
                value=value,
            )
            if metric.get("hit") is not expected_hit:
                return f"outcome_metric_hit_value_conflict:{name}"
            if metric.get("unavailable_reason") is not None:
                return f"outcome_metric_mature_unavailable_reason_present:{name}"
        elif metric.get("hit") is not None or metric.get("value_percent") is not None:
            return f"outcome_metric_immature_label_present:{name}"
        elif metric.get("eligible") is True and _text(
            metric.get("unavailable_reason")
        ) is None:
            return f"outcome_metric_unavailable_reason_missing:{name}"
        elif metric.get("eligible") is False and metric.get("unavailable_reason") is not None:
            return f"outcome_metric_ineligible_reason_present:{name}"
    if raw_metrics["gross_direction"].get("mature") is not True:
        return "outcome_gross_direction_not_mature"
    return None


def _pending_metric_set(event: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return evaluate_decision_metrics(
        gross_return_percent=None,
        evaluation_class=str(event.get("evaluation_class") or ""),
        fee_policy=event.get("fee_policy") or event.get("fee_model") or {},
        benchmark_result={},
    )


def _metric_hit(
    metric_name: str,
    *,
    evaluation_class: str,
    value: float,
) -> bool:
    if metric_name in {"gross_direction", "gross_excess"}:
        return value < 0 if evaluation_class == "bearish" else value > 0
    return value > 0


def _summarize_slots(
    slots: Sequence[_Slot],
    *,
    shadow_labels: Mapping[tuple[str, int], bool],
    min_calibration_samples: int,
    calibration_bins: int,
    calibration_metric: str,
) -> dict[str, Any]:
    formal_slots = [slot for slot in slots if slot.replay_eligible]
    actionable = [slot for slot in formal_slots if not slot.abstained]
    replay_count = sum(slot.replay_eligible for slot in slots)
    return {
        "input_event_horizon_count": len(slots),
        "event_horizon_count": len(formal_slots),
        "replay_excluded_event_horizon_count": len(slots) - len(formal_slots),
        "actionable_event_horizon_count": len(actionable),
        "matched_terminal_outcome_count": sum(slot.matched for slot in actionable),
        "metrics": summarize_metrics(slot.metrics for slot in actionable),
        "label_coverage_percent": (
            round(sum(slot.matched for slot in actionable) / len(actionable) * 100.0, 1)
            if actionable
            else None
        ),
        "calibration": _calibration(
            actionable,
            min_samples=min_calibration_samples,
            bins=calibration_bins,
            metric_name=calibration_metric,
        ),
        "abstention": _abstention(formal_slots, shadow_labels=shadow_labels),
        "replay": {
            "eligible_count": replay_count,
            "ineligible_count": len(slots) - replay_count,
            "coverage_percent": (
                round(replay_count / len(slots) * 100.0, 1) if slots else None
            ),
        },
    }


def _group_slots(
    slots: Sequence[_Slot],
    key: Callable[[_Slot], object],
    **summary_kwargs: Any,
) -> list[dict[str, Any]]:
    groups: dict[str, tuple[object, list[_Slot]]] = {}
    for slot in slots:
        value = key(slot)
        canonical = _canonical_json(value)
        groups.setdefault(canonical, (value, []))[1].append(slot)
    return [
        {"value": value, **_summarize_slots(rows, **summary_kwargs)}
        for _, (value, rows) in sorted(groups.items())
    ]


def _calibration(
    slots: Sequence[_Slot],
    *,
    min_samples: int,
    bins: int,
    metric_name: str,
) -> dict[str, Any]:
    samples: list[tuple[float, int]] = []
    explicit_probability_count = 0
    for slot in slots:
        probability = _explicit_probability(slot.event.get("success_probability"))
        if probability is None:
            continue
        explicit_probability_count += 1
        metric = slot.metrics.get(metric_name) or {}
        if (
            slot.matched
            and metric.get("eligible") is True
            and metric.get("mature") is True
            and isinstance(metric.get("hit"), bool)
        ):
            samples.append((probability, int(metric["hit"])))
    base = {
        "metric": metric_name,
        "explicit_probability_count": explicit_probability_count,
        "sample_count": len(samples),
        "minimum_sample_count": min_samples,
        "ece": None,
        "brier": None,
        "bins": [],
    }
    if len(samples) < min_samples:
        return {
            **base,
            "status": "unavailable",
            "reason": "explicit_probability_label_sample_insufficient",
        }
    bucket_rows: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        values = [
            row
            for row in samples
            if row[0] >= lower and (row[0] < upper or index == bins - 1)
        ]
        if not values:
            continue
        mean_probability = sum(row[0] for row in values) / len(values)
        observed_rate = sum(row[1] for row in values) / len(values)
        gap = abs(mean_probability - observed_rate)
        ece += gap * len(values) / len(samples)
        bucket_rows.append(
            {
                "lower_bound": _rounded(lower),
                "upper_bound": _rounded(upper),
                "count": len(values),
                "mean_probability": _rounded(mean_probability),
                "observed_success_rate": _rounded(observed_rate),
                "absolute_gap": _rounded(gap),
            }
        )
    brier = sum((probability - label) ** 2 for probability, label in samples) / len(
        samples
    )
    return {
        **base,
        "status": "available",
        "reason": None,
        "ece": _rounded(ece),
        "brier": _rounded(brier),
        "bins": bucket_rows,
    }


def _abstention(
    slots: Sequence[_Slot],
    *,
    shadow_labels: Mapping[tuple[str, int], bool],
) -> dict[str, Any]:
    abstained = [slot for slot in slots if slot.abstained]
    labelled = [
        slot
        for slot in abstained
        if (str(slot.event["event_id"]), slot.horizon) in shadow_labels
    ]
    base = {
        "event_horizon_count": len(slots),
        "abstained_count": len(abstained),
        "decision_coverage_percent": (
            round((len(slots) - len(abstained)) / len(slots) * 100.0, 1)
            if slots
            else None
        ),
        "abstention_rate_percent": (
            round(len(abstained) / len(slots) * 100.0, 1) if slots else None
        ),
        "shadow_label_count": len(labelled),
        "shadow_label_coverage_percent": (
            round(len(labelled) / len(abstained) * 100.0, 1) if abstained else None
        ),
        "correct_abstention_rate_percent": None,
    }
    if not abstained:
        return {**base, "quality_status": "unavailable", "reason": "no_abstentions"}
    if len(labelled) != len(abstained):
        return {
            **base,
            "quality_status": "unavailable",
            "reason": "abstained_shadow_labels_incomplete",
        }
    correct = sum(
        shadow_labels[(str(slot.event["event_id"]), slot.horizon)]
        for slot in labelled
    )
    return {
        **base,
        "quality_status": "available",
        "reason": None,
        "correct_abstention_rate_percent": round(correct / len(labelled) * 100.0, 1),
    }


def _normalize_shadow_labels(
    value: object,
    *,
    events: Sequence[Mapping[str, Any]],
    evaluation_as_of: datetime | None,
) -> tuple[
    dict[tuple[str, int], bool],
    Counter[str],
    list[dict[str, Any]],
]:
    labels: dict[tuple[str, int], bool] = {}
    exclusions: Counter[str] = Counter()
    conflicted: set[tuple[str, int]] = set()
    records: list[dict[str, Any]] = []
    accepted_record_by_key: dict[tuple[str, int], int] = {}
    accepted_hash_by_key: dict[tuple[str, int], str] = {}
    all_abstention_boundaries = {
        (str(event["event_id"]), int(horizon)): _event_knowledge_cutoff(event)
        for event in events
        if _is_abstained(event)
        for horizon in event.get("horizons") or []
    }
    abstention_boundaries = {
        key: knowledge_cutoff
        for key, knowledge_cutoff in all_abstention_boundaries.items()
        if any(
            str(event.get("event_id")) == key[0]
            and _event_replay_eligibility(event)[0]
            for event in events
        )
    }
    rows = _raw_sequence(value)
    if rows is None and value is not None:
        exclusions["abstention_shadow_label_collection_invalid"] += 1
        return {}, exclusions, [
            {
                "input_index": None,
                "event_id": None,
                "horizon_trading_days": None,
                "status": "excluded",
                "reason": "abstention_shadow_label_collection_invalid",
            }
        ]
    for index, raw in enumerate(rows or []):
        row = raw if isinstance(raw, Mapping) else {}
        event_id = _text(row.get("event_id"))
        horizon = _positive_int(row.get("horizon_trading_days"))
        beneficial = row.get("beneficial")
        label_available_at = _aware_datetime(row.get("label_available_at"))
        content_hash = _text(row.get("content_hash"))
        key = (event_id or "", horizon or 0)
        knowledge_cutoff = abstention_boundaries.get(key)
        reason: str | None = None
        if not _is_hash(content_hash):
            reason = "abstention_shadow_label_hash_missing_or_invalid"
        else:
            try:
                expected_content_hash = _canonical_hash(
                    {
                        key: item
                        for key, item in row.items()
                        if key != "content_hash"
                    }
                )
            except (TypeError, ValueError, OverflowError, RecursionError):
                reason = "abstention_shadow_label_hash_uncomputable"
            else:
                if content_hash.lower() != expected_content_hash:
                    reason = "abstention_shadow_label_hash_mismatch"
        if reason is None and (
            event_id is None
            or horizon is None
            or not isinstance(beneficial, bool)
            or row.get("mature") is not True
            or label_available_at is None
        ):
            reason = "abstention_shadow_label_invalid"
        elif reason is None and evaluation_as_of is None:
            reason = "abstention_shadow_label_evaluation_boundary_unavailable"
        elif reason is None and label_available_at > evaluation_as_of:
            reason = "abstention_shadow_label_after_evaluation_boundary"
        elif (
            reason is None
            and key in all_abstention_boundaries
            and knowledge_cutoff is None
        ):
            reason = "abstention_shadow_label_replay_ineligible"
        elif reason is None and knowledge_cutoff is None:
            reason = "abstention_shadow_label_without_matching_abstention"
        elif reason is None and label_available_at <= knowledge_cutoff:
            reason = "abstention_shadow_label_not_strictly_after_replay_boundary"
        elif reason is None and not _immutable_source_ref(row.get("source_ref")):
            reason = "abstention_shadow_label_source_ref_invalid"
        if reason is not None:
            exclusions[reason] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "excluded",
                    "reason": reason,
                }
            )
            continue
        key = (event_id, horizon)
        if key in conflicted:
            exclusions["abstention_shadow_label_duplicate_conflict"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "excluded",
                    "reason": "abstention_shadow_label_duplicate_conflict",
                }
            )
        elif key in labels and (
            labels[key] != beneficial
            or accepted_hash_by_key.get(key) != content_hash
        ):
            labels.pop(key, None)
            accepted_hash_by_key.pop(key, None)
            conflicted.add(key)
            exclusions["abstention_shadow_label_duplicate_conflict"] += 2
            prior_record = accepted_record_by_key.pop(key)
            records[prior_record]["status"] = "excluded"
            records[prior_record]["reason"] = (
                "abstention_shadow_label_duplicate_conflict"
            )
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "excluded",
                    "reason": "abstention_shadow_label_duplicate_conflict",
                }
            )
        elif key in labels:
            exclusions["abstention_shadow_label_duplicate_identical"] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "excluded",
                    "reason": "abstention_shadow_label_duplicate_identical",
                }
            )
        else:
            labels[key] = beneficial
            accepted_hash_by_key[key] = str(content_hash)
            accepted_record_by_key[key] = len(records)
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "horizon_trading_days": horizon,
                    "status": "accepted",
                    "reason": None,
                }
            )
    return labels, exclusions, records


def _summarize_claim_audits(
    value: object,
    *,
    events: Sequence[Mapping[str, Any]],
    evaluation_as_of: datetime | None,
) -> dict[str, Any]:
    raw_rows = [value] if isinstance(value, Mapping) else _raw_sequence(value)
    rows = raw_rows or []
    counts: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    formal_events = {
        str(event["event_id"]): event
        for event in events
        if _event_replay_eligibility(event)[0]
    }
    if raw_rows is None and value is not None:
        exclusions["claim_audit_collection_invalid"] += 1
        records.append(
            {
                "input_index": None,
                "event_id": None,
                "status": "excluded",
                "reason": "claim_audit_collection_invalid",
            }
        )

    for index, raw in enumerate(rows):
        wrapper = raw if isinstance(raw, Mapping) else {}
        event_id = _text(wrapper.get("event_id"))
        reason = _claim_audit_wrapper_error(
            wrapper,
            formal_events=formal_events,
            evaluation_as_of=evaluation_as_of,
        )
        if reason is not None:
            exclusions[reason] += 1
            records.append(
                {
                    "input_index": index,
                    "event_id": event_id,
                    "status": "excluded",
                    "reason": reason,
                }
            )
            continue
        audit = wrapper["audit"]
        audit_status = str(audit["status"])
        counts[audit_status] += 1
        records.append(
            {
                "input_index": index,
                "event_id": event_id,
                "status": "accepted",
                "reason": None,
                "classification": audit_status,
            }
        )

    valid = sum(counts.values())
    total = len(rows)
    return {
        "status": (
            "available"
            if total > 0 and valid == total
            else "partial"
            if valid > 0
            else "unavailable"
        ),
        "audit_count": total,
        "classified_count": valid,
        "unclassified_count": total - valid,
        "coverage_percent": round(valid / total * 100.0, 1) if total else None,
        "clean_count": counts["clean"],
        "sanitized_count": counts["sanitized"],
        "violation_count": counts["violation"],
        "exclusion_reasons": _counter_rows(exclusions),
        "records": records,
        "raw_claims_included": False,
    }


def _claim_audit_wrapper_error(
    wrapper: Mapping[str, Any],
    *,
    formal_events: Mapping[str, Mapping[str, Any]],
    evaluation_as_of: datetime | None,
) -> str | None:
    if wrapper.get("schema_version") != CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION:
        return "claim_audit_wrapper_schema_invalid"
    event_id = _text(wrapper.get("event_id"))
    event = formal_events.get(event_id or "")
    if event is None:
        return "claim_audit_event_missing_or_replay_ineligible"
    decision_at = _aware_datetime(wrapper.get("decision_at"))
    event_decision_at = _aware_datetime(event.get("decision_at"))
    knowledge_cutoff = _event_knowledge_cutoff(event)
    if (
        decision_at is None
        or event_decision_at is None
        or knowledge_cutoff is None
        or decision_at != event_decision_at
    ):
        return "claim_audit_decision_time_conflict"
    event_payload_hash = _text(wrapper.get("decision_event_payload_hash"))
    expected_event_payload_hash = _text(event.get("payload_hash"))
    if (
        not _is_hash(event_payload_hash)
        or not _is_hash(expected_event_payload_hash)
        or event_payload_hash.lower() != expected_event_payload_hash.lower()
    ):
        return "claim_audit_decision_hash_conflict"
    available_at = _aware_datetime(wrapper.get("available_at"))
    recorded_at = _aware_datetime(wrapper.get("recorded_at"))
    if available_at is None or recorded_at is None:
        return "claim_audit_time_missing_or_invalid"
    if evaluation_as_of is None:
        return "claim_audit_evaluation_boundary_unavailable"
    if (
        available_at <= knowledge_cutoff
        or recorded_at < available_at
        or recorded_at > evaluation_as_of
    ):
        return "claim_audit_time_not_point_in_time_eligible"
    audit = wrapper.get("audit")
    if not isinstance(audit, Mapping):
        return "claim_audit_payload_missing"
    if audit.get("schema_version") != CLAIM_AUDIT_SCHEMA_VERSION:
        return "claim_audit_schema_invalid"
    if _text(audit.get("status")) not in _CLAIM_STATUSES:
        return "claim_audit_status_invalid"
    audit_contract_error = _claim_audit_payload_contract_error(audit)
    if audit_contract_error is not None:
        return audit_contract_error
    audit_hash = _text(audit.get("audit_hash"))
    if not _is_hash(audit_hash):
        return "claim_audit_hash_missing_or_invalid"
    try:
        expected_audit_hash = _canonical_hash(
            {key: item for key, item in audit.items() if key != "audit_hash"}
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "claim_audit_hash_uncomputable"
    if audit_hash.lower() != expected_audit_hash:
        return "claim_audit_hash_mismatch"
    content_hash = _text(wrapper.get("content_hash"))
    if not _is_hash(content_hash):
        return "claim_audit_wrapper_hash_missing_or_invalid"
    try:
        expected_content_hash = _canonical_hash(
            {
                key: item
                for key, item in wrapper.items()
                if key != "content_hash"
            }
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "claim_audit_wrapper_hash_uncomputable"
    if content_hash.lower() != expected_content_hash:
        return "claim_audit_wrapper_hash_mismatch"
    return None


def _claim_audit_payload_contract_error(
    audit: Mapping[str, Any],
) -> str | None:
    if audit.get("hash_algorithm") != "sha256":
        return "claim_audit_hash_algorithm_invalid"
    if audit.get("facts_status") not in {"available", "unavailable"}:
        return "claim_audit_facts_status_invalid"
    count_fields = (
        "scanned_field_count",
        "lookthrough_field_count",
        "changed_field_count",
        "change_count",
    )
    counts = {field: _nonnegative_int(audit.get(field)) for field in count_fields}
    if any(value is None for value in counts.values()):
        return "claim_audit_count_contract_invalid"
    changes = audit.get("changes")
    if (
        not isinstance(changes, Sequence)
        or isinstance(changes, (str, bytes))
        or len(changes) != counts["change_count"]
    ):
        return "claim_audit_changes_contract_invalid"
    paths: set[str] = set()
    for change in changes:
        if not isinstance(change, Mapping):
            return "claim_audit_change_contract_invalid"
        path = _text(change.get("path"))
        if (
            path is None
            or not _is_hash(_text(change.get("original_hash")))
            or _text(change.get("reason")) is None
            or _text(change.get("replacement")) is None
        ):
            return "claim_audit_change_contract_invalid"
        paths.add(path)
    if len(paths) != counts["changed_field_count"]:
        return "claim_audit_changed_field_count_conflict"
    reason_counts = audit.get("reason_counts")
    if not isinstance(reason_counts, Mapping):
        return "claim_audit_reason_counts_invalid"
    normalized_reason_count = 0
    for reason, count in reason_counts.items():
        parsed_count = _nonnegative_int(count)
        if _text(reason) is None or parsed_count is None:
            return "claim_audit_reason_counts_invalid"
        normalized_reason_count += parsed_count
    if normalized_reason_count != counts["change_count"]:
        return "claim_audit_reason_count_conflict"
    status = str(audit.get("status"))
    if status == "clean" and counts["change_count"] != 0:
        return "claim_audit_clean_change_conflict"
    if status == "sanitized" and counts["change_count"] == 0:
        return "claim_audit_sanitized_change_conflict"
    return None


def _evaluate_candidate_selection_cases(
    value: object,
    *,
    evaluation_as_of: datetime | None,
) -> dict[str, Any]:
    rows = _sequence(value)
    evaluations: list[dict[str, Any]] = []
    aggregation_rows: list[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ] = []
    for index, row in enumerate(rows):
        formal_case = (
            row.get("schema_version")
            == CANDIDATE_SELECTION_CASE_SCHEMA_VERSION
        )
        capture_status = row.get("capture_status") if formal_case else None
        audit = row.get("audit")
        labels = row.get("outcome_labels")
        if formal_case and capture_status in {
            "capture_late",
            "capture_ineligible",
        }:
            capture_error = _candidate_nonformal_capture_case_error(
                row,
                audit=audit,
                labels=labels,
                evaluation_as_of=evaluation_as_of,
            )
            if capture_error is not None:
                evaluations.append(
                    {
                        "case_id": _text(row.get("case_id")) or str(index),
                        "status": "unavailable",
                        "reason": capture_error,
                        "formal_status": "invalid_capture_classification",
                        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
                    }
                )
                aggregation_rows.append(
                    (row, audit if isinstance(audit, Mapping) else None, None)
                )
                continue
            reason = (
                "candidate_selection_capture_late"
                if capture_status == "capture_late"
                else "candidate_selection_capture_ineligible"
            )
            evaluation = _candidate_pending_receipt_evaluation({}, reason=reason)
            evaluation["capture_status"] = capture_status
            evaluation["capture_reason"] = row.get("capture_reason")
            evaluations.append(
                {
                    "case_id": _text(row.get("case_id")) or str(index),
                    "evaluation": evaluation,
                    "status": "unavailable",
                    "reason": reason,
                    "formal_status": capture_status,
                    "capture_reason": row.get("capture_reason"),
                    "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
                }
            )
            aggregation_rows.append(
                (row, audit if isinstance(audit, Mapping) else None, evaluation)
            )
            continue
        if not isinstance(audit, Mapping):
            evaluations.append(
                {
                    "case_id": _text(row.get("case_id")) or str(index),
                    "status": "unavailable",
                    "reason": (
                        "candidate_selection_formal_capture_audit_conflict"
                        if formal_case
                        else "candidate_selection_audit_missing"
                    ),
                }
            )
            aggregation_rows.append((row, None, None))
            continue
        pit_error = _candidate_case_pit_error(
            row,
            audit,
            labels,
            evaluation_as_of=evaluation_as_of,
        )
        if pit_error is not None:
            evaluations.append(
                {
                    "case_id": _text(row.get("case_id")) or str(index),
                    "status": "unavailable",
                    "reason": pit_error,
                }
            )
            aggregation_rows.append((row, audit, None))
            continue
        label_input = (
            labels
            if isinstance(labels, (Mapping, Sequence))
            and not isinstance(labels, (str, bytes))
            else None
        )
        evaluation = evaluate_candidate_selection_audit(
            audit,
            label_input,
            k=_positive_int(row.get("k")) or 3,
            universe_stage=_text(row.get("universe_stage")) or "prescreen",
        )
        source_verified = formal_case and _candidate_case_source_verified(row)
        if formal_case and not source_verified:
            evaluation = _candidate_pending_receipt_evaluation(
                evaluation,
                reason=_candidate_pending_receipt_reason(row),
            )
        elif formal_case:
            evaluation = dict(evaluation)
            evaluation["source_verified"] = True
            evaluation["evidence_scope"] = CANDIDATE_SELECTION_EVIDENCE_SCOPE
        evaluations.append(
            {
                "case_id": _text(row.get("case_id")) or str(index),
                "evaluation": evaluation,
                "status": evaluation.get("status"),
                "reason": evaluation.get("reason"),
                **(
                    {
                        "formal_status": "source_verified"
                        if source_verified
                        else (
                            "receipt_policy_gap"
                            if row.get("audit_commit_receipt_status") == "late"
                            else "receipt_pending"
                        ),
                        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
                    }
                    if formal_case
                    else {"formal_status": "legacy_diagnostic_only"}
                ),
            }
        )
        aggregation_rows.append((row, audit, evaluation))
    evaluations.sort(key=lambda row: str(row["case_id"]))
    pit_eligible_case_count = sum("evaluation" in row for row in evaluations)
    metric_available_case_count = sum(
        row.get("status") in {"available", "partial"} for row in evaluations
    )
    formal_rows = [
        row
        for row in aggregation_rows
        if row[0].get("schema_version")
        == CANDIDATE_SELECTION_CASE_SCHEMA_VERSION
    ]
    formal_eligible_rows = [
        row for row in formal_rows if row[2] is not None
    ]
    formal_pit_eligible_case_count = sum(
        evaluation is not None for _, _, evaluation in formal_eligible_rows
    )
    formal_metric_available_case_count = sum(
        isinstance(evaluation, Mapping)
        and evaluation.get("status") in {"available", "partial"}
        and _candidate_case_source_verified(case)
        for case, _, evaluation in formal_eligible_rows
    )
    formal_fully_available_case_count = sum(
        isinstance(evaluation, Mapping)
        and evaluation.get("status") == "available"
        and _candidate_case_source_verified(case)
        for case, _, evaluation in formal_eligible_rows
    )
    pooled_aggregate = _candidate_selection_aggregate(formal_eligible_rows)
    source_capture_eligible_rows = [
        row
        for row in formal_eligible_rows
        if row[0].get("capture_status") == "eligible"
    ]
    strata = _candidate_selection_strata(source_capture_eligible_rows)
    aggregate = (
        pooled_aggregate
        if len(strata) <= 1
        else _candidate_stratified_only_aggregate(
            pooled_aggregate,
            stratum_count=len(strata),
        )
    )
    metric_case_coverage_percent = (
        round(
            formal_metric_available_case_count
            / formal_pit_eligible_case_count
            * 100.0,
            8,
        )
        if formal_pit_eligible_case_count
        else None
    )
    if len(strata) == 1:
        readiness = _candidate_selection_readiness(
            formal_eligible_rows,
            pooled_aggregate,
        )
    elif strata:
        readiness = {
            "status": "stratified_only",
            "stratum_count": len(strata),
            "eligible_stratum_count": sum(
                row.get("readiness", {}).get("status")
                == "eligible_for_human_review"
                for row in strata
            ),
            "mature_decision_day_count": len(
                _candidate_mature_decision_dates(formal_rows)
            ),
            "mature_decision_date_basis": (
                "immutable_entry_date_or_audit_receipt_live_cohort_date"
            ),
            "declared_mature_decision_day_count": len(
                _candidate_declared_mature_decision_dates(formal_rows)
            ),
            "minimum_shadow_mature_decision_days": (
                CANDIDATE_MIN_SHADOW_MATURE_DECISION_DAYS
            ),
            "minimum_manual_review_mature_decision_days": (
                CANDIDATE_MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
            ),
            "minimum_manual_review_coverage_percent": (
                CANDIDATE_MIN_MANUAL_REVIEW_COVERAGE_PERCENT
            ),
            "automatic_promotion_allowed": False,
        }
    else:
        readiness = _candidate_selection_readiness(
            formal_eligible_rows,
            pooled_aggregate,
        )
    formal_fully_available = bool(formal_eligible_rows) and all(
        isinstance(evaluation, Mapping)
        and evaluation.get("status") == "available"
        and _candidate_case_source_verified(case)
        for case, _, evaluation in formal_eligible_rows
    )
    return {
        "status": (
            "available"
            if formal_fully_available and len(strata) <= 1
            else "partial"
            if formal_metric_available_case_count
            else "unavailable"
        ),
        "diagnostic_status": (
            "available"
            if bool(evaluations)
            and all(row.get("status") == "available" for row in evaluations)
            else "partial"
            if metric_available_case_count
            else "unavailable"
        ),
        "case_count": len(evaluations),
        "pit_eligible_case_count": pit_eligible_case_count,
        "metric_available_case_count": metric_available_case_count,
        "formal_case_count": len(formal_rows),
        "formal_invalid_case_count": len(formal_rows) - len(formal_eligible_rows),
        "formal_pit_eligible_case_count": formal_pit_eligible_case_count,
        "formal_metric_available_case_count": (
            formal_metric_available_case_count
        ),
        "formal_fully_available_case_count": (
            formal_fully_available_case_count
        ),
        "metric_available_case_coverage_percent": metric_case_coverage_percent,
        "capture_coverage": _candidate_capture_coverage(formal_rows),
        "aggregate": aggregate,
        "stratified": strata,
        "readiness": readiness,
        "evaluations": evaluations,
        "ranking_algorithm": "delegated_to_candidate_selection_audit",
        "metric_scope": "deterministic_prescreen_to_final_candidate_policy",
        "candidate_evaluator_version": CANDIDATE_SELECTION_EVALUATOR_VERSION,
        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
        "automatic_promotion_allowed": False,
    }


def _candidate_pending_receipt_evaluation(
    evaluation: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Retain a formal case denominator while making metrics unavailable."""

    result = dict(evaluation)
    result["status"] = "unavailable"
    result["reason"] = reason
    coverage = result.get("coverage")
    if isinstance(coverage, Mapping):
        frozen_coverage = dict(coverage)
        frozen_coverage["mature_label_count"] = 0
        frozen_coverage["top_k_mature_label_count"] = 0
        result["coverage"] = frozen_coverage
    for metric_name in ("precision_at_k", "ndcg_at_k", "regret_at_k"):
        metric = result.get(metric_name)
        frozen_metric = dict(metric) if isinstance(metric, Mapping) else {}
        frozen_metric.update({"status": "unavailable", "value": None})
        if metric_name == "precision_at_k":
            frozen_metric.update({"numerator": 0, "denominator": 0})
        result[metric_name] = frozen_metric
    result["source_verified"] = False
    result["evidence_scope"] = CANDIDATE_SELECTION_EVIDENCE_SCOPE
    return result


def _candidate_capture_coverage(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
) -> dict[str, Any]:
    statuses = Counter(str(case.get("capture_status") or "invalid") for case, _, _ in rows)
    reasons = Counter(
        str(case.get("capture_reason") or "invalid") for case, _, _ in rows
    )
    return {
        "observed_capture_count": len(rows),
        "eligible_capture_count": statuses.get("eligible", 0),
        "capture_late_count": statuses.get("capture_late", 0),
        "capture_ineligible_count": statuses.get("capture_ineligible", 0),
        "invalid_capture_count": sum(
            count
            for status, count in statuses.items()
            if status not in {"eligible", "capture_late", "capture_ineligible"}
        ),
        "status_counts": dict(sorted(statuses.items())),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _candidate_pending_receipt_reason(case: Mapping[str, Any]) -> str:
    if case.get("audit_commit_receipt_status") == "pending":
        return "candidate_selection_audit_commit_receipt_pending"
    if case.get("audit_commit_receipt_status") == "late":
        return "candidate_selection_audit_commit_receipt_late"
    if case.get("outcome_commit_receipt_status") == "pending":
        return "candidate_selection_outcome_commit_receipt_pending"
    return "candidate_selection_outcome_artifact_absent"


def _candidate_case_source_verified(case: Mapping[str, Any]) -> bool:
    return bool(
        case.get("schema_version") == CANDIDATE_SELECTION_CASE_SCHEMA_VERSION
        and case.get("capture_status") == "eligible"
        and case.get("audit_commit_receipt_status") == "verified"
        and case.get("outcome_commit_receipt_status") == "verified"
    )


def _candidate_evaluation_has_mature_labels(
    evaluation: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(evaluation, Mapping):
        return False
    coverage = evaluation.get("coverage")
    return bool(
        isinstance(coverage, Mapping)
        and (_nonnegative_int(coverage.get("mature_label_count")) or 0) > 0
    )


def _candidate_stratified_only_aggregate(
    pooled: Mapping[str, Any],
    *,
    stratum_count: int,
) -> dict[str, Any]:
    """Withhold incomparable pooled metrics while retaining case counts."""

    unavailable = _candidate_selection_aggregate([])
    unavailable.update(
        {
            "status": "stratified_only",
            "reason": "mixed_candidate_policy_strata",
            "stratum_count": stratum_count,
            "case_count": _nonnegative_int(pooled.get("case_count")) or 0,
            "pit_eligible_case_count": (
                _nonnegative_int(pooled.get("pit_eligible_case_count")) or 0
            ),
            "automatic_promotion_allowed": False,
        }
    )
    return unavailable


def _candidate_selection_aggregate(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
) -> dict[str, Any]:
    evaluations = [
        evaluation
        for case, _, evaluation in rows
        if isinstance(evaluation, Mapping)
        and _candidate_case_source_verified(case)
    ]
    coverage_evaluations = [
        evaluation
        for _, _, evaluation in rows
        if isinstance(evaluation, Mapping)
    ]
    precision_rows = _candidate_available_metrics(evaluations, "precision_at_k")
    ndcg_rows = _candidate_available_metrics(evaluations, "ndcg_at_k")
    regret_rows = _candidate_available_metrics(evaluations, "regret_at_k")

    precision_values = [float(row["value"]) for row in precision_rows]
    precision_numerator = sum(
        _nonnegative_int(row.get("numerator")) or 0 for row in precision_rows
    )
    precision_denominator = sum(
        _positive_int(row.get("denominator")) or 0 for row in precision_rows
    )
    ndcg_values = [float(row["value"]) for row in ndcg_rows]
    regret_values = [float(row["value"]) for row in regret_rows]
    regret_bases = sorted(
        {
            basis
            for row in regret_rows
            if (basis := _text(row.get("utility_basis"))) is not None
        }
    )
    comparable_regret = bool(regret_rows) and len(regret_bases) == 1

    universe_count = 0
    mature_label_count = 0
    top_k_count = 0
    top_k_mature_label_count = 0
    selected_top_k_count = 0
    for evaluation in coverage_evaluations:
        coverage = evaluation.get("coverage")
        if not isinstance(coverage, Mapping):
            continue
        universe_count += _nonnegative_int(coverage.get("universe_count")) or 0
        mature_label_count += (
            _nonnegative_int(coverage.get("mature_label_count")) or 0
        )
        top_k_count += _nonnegative_int(coverage.get("top_k_count")) or 0
        top_k_mature_label_count += (
            _nonnegative_int(coverage.get("top_k_mature_label_count")) or 0
        )
        selected_top_k_count += (
            _nonnegative_int(coverage.get("selected_top_k_count")) or 0
        )

    return {
        "case_count": len(rows),
        "pit_eligible_case_count": len(coverage_evaluations),
        "precision_at_k": {
            "status": "available" if precision_rows else "unavailable",
            "case_count": len(precision_rows),
            "macro_average": _rounded(_mean(precision_values)),
            "micro_average": _rounded(
                precision_numerator / precision_denominator
                if precision_denominator
                else None
            ),
            "numerator": precision_numerator,
            "denominator": precision_denominator,
        },
        "ndcg_at_k": {
            "status": "available" if ndcg_rows else "unavailable",
            "case_count": len(ndcg_rows),
            "mean": _rounded(_mean(ndcg_values)),
        },
        "regret_at_k": {
            "status": "available" if comparable_regret else "unavailable",
            "case_count": len(regret_rows),
            "mean": _rounded(_mean(regret_values)) if comparable_regret else None,
            "median": (
                _rounded(_median(regret_values)) if comparable_regret else None
            ),
            "utility_basis": regret_bases[0] if comparable_regret else None,
            "observed_utility_bases": regret_bases,
            "reason": (
                None
                if comparable_regret
                else "mixed_utility_basis_across_cases"
                if len(regret_bases) > 1
                else "regret_metric_unavailable"
            ),
        },
        "coverage": {
            "status": "available" if universe_count else "unavailable",
            "mature_label_count": mature_label_count,
            "universe_count": universe_count,
            "universe_label_coverage_percent": _rounded(
                mature_label_count / universe_count * 100.0
                if universe_count
                else None
            ),
            "top_k_mature_label_count": top_k_mature_label_count,
            "top_k_count": top_k_count,
            "top_k_label_coverage_percent": _rounded(
                top_k_mature_label_count / top_k_count * 100.0
                if top_k_count
                else None
            ),
            "selected_top_k_count": selected_top_k_count,
            "selection_at_k_coverage_percent": _rounded(
                selected_top_k_count / top_k_count * 100.0
                if top_k_count
                else None
            ),
        },
        "automatic_promotion_allowed": False,
    }


def _candidate_available_metrics(
    evaluations: Sequence[Mapping[str, Any]],
    key: str,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for evaluation in evaluations:
        metric = evaluation.get(key)
        if not isinstance(metric, Mapping) or metric.get("status") != "available":
            continue
        if _finite_number(metric.get("value")) is None:
            continue
        rows.append(metric)
    return rows


def _candidate_selection_strata(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[int | None, int | None, str, str, str, str],
        list[
            tuple[
                Mapping[str, Any],
                Mapping[str, Any] | None,
                Mapping[str, Any] | None,
            ]
        ],
    ] = {}
    for case, audit, evaluation in rows:
        versions = audit.get("versions") if isinstance(audit, Mapping) else None
        selection_version = _text(case.get("selection_policy_version"))
        if selection_version is None and isinstance(versions, Mapping):
            selection_version = _text(versions.get("selection_policy"))
        key = (
            _positive_int(case.get("horizon_trading_days")),
            _positive_int(case.get("k")),
            _text(case.get("universe_stage")) or "unknown",
            _text(case.get("label_policy_version")) or "unregistered",
            selection_version or "unknown",
            _text(case.get("provider_adapter_stratum_hash")) or "invalid",
        )
        grouped.setdefault(key, []).append((case, audit, evaluation))
    result: list[dict[str, Any]] = []
    for key in sorted(
        grouped,
        key=lambda item: (
            item[0] or 0,
            item[1] or 0,
            item[2],
            item[3],
            item[4],
            item[5],
        ),
    ):
        aggregate = _candidate_selection_aggregate(grouped[key])
        result.append({
            "dimensions": {
                "horizon_trading_days": key[0],
                "k": key[1],
                "universe_stage": key[2],
                "label_policy_version": key[3],
                "selection_policy_version": key[4],
                "provider_adapter_stratum_hash": key[5],
                "provider_adapter_stratum": [
                    dict(item)
                    for item in grouped[key][0][0].get(
                        "provider_adapter_stratum", []
                    )
                    if isinstance(item, Mapping)
                ],
            },
            "aggregate": aggregate,
            "readiness": _candidate_selection_readiness(grouped[key], aggregate),
        })
    return result


def _candidate_mature_decision_dates(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
) -> list[str]:
    dates: set[str] = set()
    for case, _, evaluation in rows:
        if not _candidate_case_source_verified(case):
            continue
        if not _candidate_evaluation_has_mature_labels(evaluation):
            continue
        value = _candidate_live_cohort_date(case)
        if value is None:
            continue
        try:
            normalized = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
        if normalized == value:
            dates.add(value)
    return sorted(dates)


def _candidate_live_cohort_date(case: Mapping[str, Any]) -> str | None:
    labels = case.get("outcome_labels")
    entry_dates: set[str] = set()
    if isinstance(labels, Mapping):
        for label in labels.values():
            if not isinstance(label, Mapping):
                continue
            value = _text(label.get("entry_date"))
            if value is not None:
                entry_dates.add(value)
    if len(entry_dates) == 1:
        value = next(iter(entry_dates))
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    visible_at = _aware_datetime(
        case.get("audit_commit_receipt_source_visible_at")
    )
    if visible_at is None:
        return None
    # Asia/Shanghai has a fixed UTC+08 offset for the complete product horizon.
    return visible_at.astimezone(
        timezone(timedelta(hours=8))
    ).date().isoformat()


def _candidate_declared_mature_decision_dates(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
) -> list[str]:
    dates: set[str] = set()
    for case, _, evaluation in rows:
        if not _candidate_case_source_verified(case) or not _candidate_evaluation_has_mature_labels(
            evaluation
        ):
            continue
        value = _text(
            case.get("declared_decision_date_local")
            or case.get("decision_date_local")
        )
        if value is None:
            continue
        try:
            if datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value:
                dates.add(value)
        except ValueError:
            continue
    return sorted(dates)


def _candidate_selection_readiness(
    rows: Sequence[
        tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]
    ],
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    pit_count = sum(evaluation is not None for _, _, evaluation in rows)
    fully_available_count = sum(
        isinstance(evaluation, Mapping)
        and evaluation.get("status") == "available"
        and _candidate_case_source_verified(case)
        for case, _, evaluation in rows
    )
    metric_coverage = (
        round(fully_available_count / pit_count * 100.0, 8)
        if pit_count
        else None
    )
    mature_dates = _candidate_mature_decision_dates(rows)
    declared_mature_dates = _candidate_declared_mature_decision_dates(rows)
    coverage = aggregate.get("coverage")
    universe_coverage = (
        coverage.get("universe_label_coverage_percent")
        if isinstance(coverage, Mapping)
        else None
    )
    status = "insufficient_data"
    if len(mature_dates) >= CANDIDATE_MIN_SHADOW_MATURE_DECISION_DAYS:
        status = "shadow_only"
    if (
        len(mature_dates) >= CANDIDATE_MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
        and _finite_number(universe_coverage) is not None
        and float(universe_coverage)
        >= CANDIDATE_MIN_MANUAL_REVIEW_COVERAGE_PERCENT
        and metric_coverage is not None
        and metric_coverage >= CANDIDATE_MIN_MANUAL_REVIEW_COVERAGE_PERCENT
    ):
        status = "eligible_for_human_review"
    return {
        "status": status,
        "mature_decision_day_count": len(mature_dates),
        "mature_decision_date_basis": (
            "immutable_entry_date_or_audit_receipt_live_cohort_date"
        ),
        "declared_mature_decision_day_count": len(declared_mature_dates),
        "fully_available_case_count": fully_available_count,
        "fully_available_case_coverage_percent": metric_coverage,
        "minimum_shadow_mature_decision_days": (
            CANDIDATE_MIN_SHADOW_MATURE_DECISION_DAYS
        ),
        "minimum_manual_review_mature_decision_days": (
            CANDIDATE_MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
        ),
        "minimum_manual_review_coverage_percent": (
            CANDIDATE_MIN_MANUAL_REVIEW_COVERAGE_PERCENT
        ),
        "automatic_promotion_allowed": False,
    }


def _candidate_case_pit_error(
    case: Mapping[str, Any],
    audit: Mapping[str, Any],
    labels: object,
    *,
    evaluation_as_of: datetime | None,
) -> str | None:
    decision_at = _aware_datetime(audit.get("decision_at"))
    if decision_at is None:
        return "candidate_selection_decision_at_invalid"
    recorded_at = _aware_datetime(case.get("recorded_at"))
    if recorded_at is None:
        return "candidate_selection_recorded_at_missing_or_invalid"
    if evaluation_as_of is None:
        return "candidate_selection_evaluation_boundary_unavailable"
    if decision_at > evaluation_as_of:
        return "candidate_selection_decision_after_evaluation_boundary"
    if recorded_at < decision_at:
        return "candidate_selection_recorded_before_decision"
    if recorded_at > evaluation_as_of:
        return "candidate_selection_recorded_after_evaluation_boundary"
    label_rows = _candidate_label_rows(labels)
    if label_rows is None:
        return "candidate_selection_outcome_labels_invalid"
    formal_case = (
        case.get("schema_version") == CANDIDATE_SELECTION_CASE_SCHEMA_VERSION
    )
    if formal_case:
        return _formal_candidate_case_pit_error(
            case,
            audit,
            label_rows,
            decision_at=decision_at,
            recorded_at=recorded_at,
            evaluation_as_of=evaluation_as_of,
        )
    for mapping_key, label in label_rows:
        error = _candidate_label_identity_error(mapping_key, label)
        if error is not None:
            return error
        usable = (
            label.get("mature") is True
            and label.get("skipped") is not True
            and label.get("eligible") is not False
        )
        if not usable:
            continue
        label_available_at = _aware_datetime(label.get("label_available_at"))
        if label_available_at is None:
            return "candidate_selection_label_time_missing_or_invalid"
        if label_available_at <= recorded_at:
            return "candidate_selection_label_not_strictly_after_recorded_at"
        if label_available_at > evaluation_as_of:
            return "candidate_selection_label_after_evaluation_boundary"
        if not _immutable_source_ref(label.get("source_ref")):
            return "candidate_selection_label_source_ref_invalid"
    return None


def _formal_candidate_case_pit_error(
    case: Mapping[str, Any],
    audit: Mapping[str, Any],
    label_rows: Sequence[tuple[str | None, Mapping[str, Any]]],
    *,
    decision_at: datetime,
    recorded_at: datetime,
    evaluation_as_of: datetime,
) -> str | None:
    """Validate the D4 source-verification boundary without persistence I/O."""

    if not _CANDIDATE_RECEIPT_FIELDS <= set(case):
        return "candidate_selection_formal_case_receipt_fields_missing"
    case_id = _text(case.get("case_id"))
    audit_artifact_id = _text(case.get("audit_artifact_id"))
    audit_content_hash = _text(case.get("audit_content_hash"))
    audit_snapshot_hash = _text(case.get("audit_snapshot_hash"))
    decision_date_local = _text(case.get("decision_date_local"))
    case_decision_at = _aware_datetime(case.get("decision_at"))
    audit_source_row_created_at = _aware_datetime(
        case.get("audit_source_row_created_at")
    )
    source_capture_delay = _finite_number(
        case.get("source_capture_delay_seconds")
    )
    try:
        normalized_local_date = (
            datetime.strptime(decision_date_local or "", "%Y-%m-%d")
            .date()
            .isoformat()
        )
    except ValueError:
        normalized_local_date = None
    expected_local_date = decision_at.astimezone(
        timezone(timedelta(hours=8))
    ).date().isoformat()
    if (
        case.get("automatic_promotion_allowed") is not False
        or case.get("candidate_evaluator_version")
        != CANDIDATE_SELECTION_EVALUATOR_VERSION
        or case.get("evidence_scope") != CANDIDATE_SELECTION_EVIDENCE_SCOPE
        or case.get("label_policy_version")
        != CANDIDATE_FORMAL_LABEL_POLICY_VERSION
        or not case_id
        or not case_id.startswith("candidate_case_")
        or not _is_hash(case_id.removeprefix("candidate_case_"))
        or not _content_addressed_id(audit_artifact_id, "dqa_")
        or not _is_hash(audit_content_hash)
        or audit_artifact_id != f"dqa_{audit_content_hash}"
        or not _is_hash(audit_snapshot_hash)
        or audit_snapshot_hash != _text(audit.get("snapshot_hash"))
        or not _is_hash(_text(case.get("label_plan_hash")))
        or normalized_local_date != decision_date_local
        or decision_date_local != expected_local_date
        or _positive_int(case.get("horizon_trading_days")) is None
        or _positive_int(case.get("k")) is None
        or _text(case.get("universe_stage")) is None
        or _text(case.get("selection_policy_version")) is None
        or case_decision_at != decision_at
        or audit_source_row_created_at is None
        or audit_source_row_created_at < decision_at
        or audit_source_row_created_at > recorded_at
        or source_capture_delay is None
        or abs(
            source_capture_delay
            - (audit_source_row_created_at - decision_at).total_seconds()
        )
        > 1e-6
        or source_capture_delay
        > _CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        or case.get("capture_status") != "eligible"
        or case.get("capture_reason") != "eligible"
        or case.get("capture_reason_hash")
        != _canonical_hash({"reason": "eligible"})
        or case.get("capture_artifact_type") != "candidate_selection_audit"
        or (
            case.get("declared_decision_date_local") is not None
            and case.get("declared_decision_date_local") != decision_date_local
        )
    ):
        return "candidate_selection_formal_case_contract_invalid"

    audit_status = case.get("audit_commit_receipt_status")
    outcome_status = case.get("outcome_commit_receipt_status")
    if audit_status not in {"verified", "pending", "late"}:
        return "candidate_selection_audit_commit_receipt_status_invalid"
    if outcome_status not in {"verified", "pending", "absent"}:
        return "candidate_selection_outcome_commit_receipt_status_invalid"

    audit_receipt_fields = (
        case.get("audit_commit_receipt_id"),
        case.get("audit_commit_receipt_content_hash"),
        case.get("audit_commit_receipt_source_visible_at"),
    )
    audit_visible_at: datetime | None = None
    if audit_status in {"verified", "late"}:
        if not _receipt_identity_valid(
            audit_receipt_fields[0], audit_receipt_fields[1]
        ):
            return "candidate_selection_audit_commit_receipt_invalid"
        audit_visible_at = _aware_datetime(audit_receipt_fields[2])
        if audit_visible_at is None:
            return "candidate_selection_audit_commit_receipt_invalid"
        if audit_visible_at < audit_source_row_created_at:
            return "candidate_selection_audit_receipt_before_source_row"
        if audit_visible_at > evaluation_as_of:
            return "candidate_selection_audit_receipt_after_evaluation_boundary"
        delay_seconds = (
            audit_visible_at - audit_source_row_created_at
        ).total_seconds()
        if (
            audit_status == "verified"
            and delay_seconds > _CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        ):
            return "candidate_selection_audit_verified_receipt_delay_exceeded"
        if (
            audit_status == "late"
            and delay_seconds <= _CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        ):
            return "candidate_selection_audit_late_receipt_delay_not_exceeded"
    elif any(value is not None for value in audit_receipt_fields):
        return "candidate_selection_audit_pending_receipt_field_conflict"

    outcome_artifact_id = case.get("outcome_artifact_id")
    outcome_content_hash = case.get("outcome_content_hash")
    outcome_receipt_fields = (
        case.get("outcome_commit_receipt_id"),
        case.get("outcome_commit_receipt_content_hash"),
        case.get("outcome_commit_receipt_source_visible_at"),
    )
    label_storage_value = case.get("label_storage_created_at")
    outcome_visible_at: datetime | None = None
    if outcome_status == "verified":
        if audit_status != "verified" or audit_visible_at is None:
            return "candidate_selection_outcome_without_verified_audit_receipt"
        if (
            not _content_addressed_id(outcome_artifact_id, "dqa_")
            or not _is_hash(_text(outcome_content_hash))
            or outcome_artifact_id != f"dqa_{outcome_content_hash}"
            or not _receipt_identity_valid(
                outcome_receipt_fields[0], outcome_receipt_fields[1]
            )
        ):
            return "candidate_selection_outcome_commit_receipt_invalid"
        outcome_visible_at = _aware_datetime(outcome_receipt_fields[2])
        label_storage_created_at = _aware_datetime(label_storage_value)
        if outcome_visible_at is None or label_storage_created_at is None:
            return "candidate_selection_outcome_commit_receipt_invalid"
        if label_storage_created_at != outcome_visible_at:
            return "candidate_selection_label_storage_receipt_mismatch"
        if outcome_visible_at <= audit_visible_at:
            return "candidate_selection_outcome_receipt_not_after_audit_receipt"
        if outcome_visible_at > evaluation_as_of:
            return "candidate_selection_outcome_receipt_after_evaluation_boundary"
    elif outcome_status == "pending":
        if (
            audit_status != "verified"
            or not _content_addressed_id(outcome_artifact_id, "dqa_")
            or not _is_hash(_text(outcome_content_hash))
            or outcome_artifact_id != f"dqa_{outcome_content_hash}"
            or any(value is not None for value in outcome_receipt_fields)
            or label_storage_value is not None
        ):
            return "candidate_selection_outcome_pending_receipt_field_conflict"
    else:
        if any(
            value is not None
            for value in (
                outcome_artifact_id,
                outcome_content_hash,
                *outcome_receipt_fields,
                label_storage_value,
            )
        ):
            return "candidate_selection_outcome_absent_field_conflict"

    if audit_status != "verified" and outcome_status != "absent":
        return (
            "candidate_selection_audit_late_outcome_conflict"
            if audit_status == "late"
            else "candidate_selection_audit_pending_outcome_conflict"
        )
    if outcome_status != "verified" and label_rows:
        return "candidate_selection_receipt_pending_label_conflict"

    provider_refs, provider_error = _candidate_provider_receipt_refs(
        case,
        evaluation_as_of=evaluation_as_of,
        outcome_visible_at=outcome_visible_at,
    )
    if provider_error is not None:
        return provider_error
    try:
        expected_provider_stratum = candidate_provider_adapter_stratum(
            provider_refs
        )
        expected_provider_stratum_hash = (
            candidate_provider_adapter_stratum_hash(provider_refs)
        )
    except CandidateProviderAdapterPolicyError:
        return "candidate_selection_provider_adapter_stratum_invalid"
    if (
        case.get("provider_adapter_stratum") != expected_provider_stratum
        or case.get("provider_adapter_stratum_hash")
        != expected_provider_stratum_hash
    ):
        return "candidate_selection_provider_adapter_stratum_mismatch"
    if outcome_status == "verified":
        if not label_rows or not provider_refs:
            return "candidate_selection_verified_outcome_evidence_missing"
    elif provider_refs:
        return "candidate_selection_pending_provider_receipt_conflict"

    declared_live_cohort = _text(case.get("live_cohort_date_local"))
    derived_live_cohort = _candidate_live_cohort_date(case)
    if (
        declared_live_cohort is not None
        and declared_live_cohort != derived_live_cohort
    ):
        return "candidate_selection_live_cohort_date_mismatch"

    provider_by_identity = {
        (str(ref["receipt_id"]), str(ref["content_hash"])): ref
        for ref in provider_refs
    }
    calendar_refs = [
        ref
        for ref in provider_refs
        if ref.get("provider") == _CANDIDATE_CALENDAR_PROVIDER
        and ref.get("operation") == _CANDIDATE_CALENDAR_OPERATION
    ]
    nav_refs = [
        ref
        for ref in provider_refs
        if ref.get("provider") == _CANDIDATE_NAV_PROVIDER
        and ref.get("operation") == _CANDIDATE_NAV_OPERATION
    ]
    if outcome_status == "verified" and len(calendar_refs) != 1:
        return "candidate_selection_calendar_provider_receipt_set_invalid"
    if outcome_status == "verified" and (
        len(nav_refs) != len(label_rows)
        or len(provider_refs) != len(calendar_refs) + len(nav_refs)
    ):
        return "candidate_selection_provider_receipt_scope_invalid"
    bound_nav_receipts: set[tuple[str, str]] = set()
    for mapping_key, label in label_rows:
        error = _candidate_label_identity_error(mapping_key, label)
        if error is not None:
            return error
        label_available_at = _aware_datetime(label.get("label_available_at"))
        if (
            label_available_at is None
            or outcome_visible_at is None
            or label_available_at != outcome_visible_at
            or label.get("availability_basis")
            != "post_commit_artifact_receipt"
        ):
            return "candidate_selection_label_not_bound_to_post_commit_receipt"
        if label_available_at <= decision_at:
            return "candidate_selection_label_not_strictly_after_decision"
        if label_available_at > evaluation_as_of:
            return "candidate_selection_label_after_evaluation_boundary"
        source_ref = label.get("source_ref")
        provider_ref = (
            source_ref.get("provider_receipt_ref")
            if isinstance(source_ref, Mapping)
            else None
        )
        if (
            not isinstance(source_ref, Mapping)
            or source_ref.get("source") != _CANDIDATE_NAV_PROVIDER
            or _text(source_ref.get("ref_id")) is None
            or not _is_hash(_text(source_ref.get("content_hash")))
            or not _is_hash(
                _text(source_ref.get("normalized_payload_projection_hash"))
            )
            or not isinstance(provider_ref, Mapping)
            or dict(provider_ref) not in provider_refs
            or provider_ref.get("provider") != _CANDIDATE_NAV_PROVIDER
            or provider_ref.get("operation") != _CANDIDATE_NAV_OPERATION
            or (
                str(provider_ref.get("receipt_id")),
                str(provider_ref.get("content_hash")),
            )
            not in provider_by_identity
        ):
            return "candidate_selection_label_provider_receipt_ref_invalid"
        provider_identity = (
            str(provider_ref["receipt_id"]),
            str(provider_ref["content_hash"]),
        )
        if provider_identity in bound_nav_receipts:
            return "candidate_selection_label_provider_receipt_duplicate"
        bound_nav_receipts.add(provider_identity)
        provider_completed_at = _aware_datetime(provider_ref.get("completed_at"))
        if (
            audit_visible_at is None
            or provider_completed_at is None
            or provider_completed_at <= audit_visible_at
            or provider_completed_at > outcome_visible_at
        ):
            return "candidate_selection_label_provider_receipt_time_invalid"
    expected_nav_receipts = {
        (str(ref["receipt_id"]), str(ref["content_hash"])) for ref in nav_refs
    }
    if bound_nav_receipts != expected_nav_receipts:
        return "candidate_selection_provider_receipt_label_closure_invalid"
    return None


def _candidate_nonformal_capture_case_error(
    case: Mapping[str, Any],
    *,
    audit: object,
    labels: object,
    evaluation_as_of: datetime | None,
) -> str | None:
    """Validate storage-derived capture gaps without accepting caller pseudo-states."""

    if evaluation_as_of is None:
        return "candidate_selection_evaluation_boundary_unavailable"
    if not _CANDIDATE_RECEIPT_FIELDS <= set(case):
        return "candidate_selection_formal_case_receipt_fields_missing"
    case_id = _text(case.get("case_id"))
    artifact_id = _text(case.get("audit_artifact_id"))
    content_hash = _text(case.get("audit_content_hash"))
    decision_at = _aware_datetime(case.get("decision_at"))
    source_created_at = _aware_datetime(case.get("audit_source_row_created_at"))
    recorded_at = _aware_datetime(case.get("recorded_at"))
    capture_status = case.get("capture_status")
    capture_reason = _text(case.get("capture_reason"))
    capture_delay = _finite_number(case.get("source_capture_delay_seconds"))
    artifact_type = case.get("capture_artifact_type")
    if (
        case.get("schema_version") != CANDIDATE_SELECTION_CASE_SCHEMA_VERSION
        or case.get("candidate_evaluator_version")
        != CANDIDATE_SELECTION_EVALUATOR_VERSION
        or case.get("evidence_scope") != CANDIDATE_SELECTION_EVIDENCE_SCOPE
        or case.get("automatic_promotion_allowed") is not False
        or not case_id
        or not case_id.startswith("candidate_case_")
        or not _is_hash(case_id.removeprefix("candidate_case_"))
        or not _content_addressed_id(artifact_id, "dqa_")
        or not _is_hash(content_hash)
        or artifact_id != f"dqa_{content_hash}"
        or decision_at is None
        or source_created_at is None
        or recorded_at is None
        or not decision_at <= source_created_at <= recorded_at <= evaluation_as_of
        or capture_delay is None
        or abs(
            capture_delay - (source_created_at - decision_at).total_seconds()
        )
        > 1e-6
        or capture_reason is None
        or case.get("capture_reason_hash")
        != _canonical_hash({"reason": capture_reason})
        or case.get("label_policy_version")
        != CANDIDATE_FORMAL_LABEL_POLICY_VERSION
        or case.get("live_cohort_date_local") is not None
    ):
        return "candidate_selection_capture_case_contract_invalid"
    if capture_status == "capture_late":
        if (
            artifact_type != "candidate_selection_audit"
            or capture_reason != "source_capture_delay_exceeded"
            or capture_delay
            <= _CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
            or not isinstance(audit, Mapping)
        ):
            return "candidate_selection_capture_late_state_invalid"
    elif capture_status == "capture_ineligible":
        if artifact_type == "candidate_selection_capture_failure":
            if (
                audit is not None
                or capture_reason
                not in {
                    "candidate_selection_audit_missing",
                    "candidate_selection_audit_not_mapping",
                }
                or _text(case.get("selection_policy_version"))
                != "capture_unavailable"
            ):
                return "candidate_selection_capture_failure_state_invalid"
        elif artifact_type == "candidate_selection_audit":
            validation = (
                validate_candidate_selection_audit(audit)
                if isinstance(audit, Mapping)
                else None
            )
            if (
                not isinstance(validation, Mapping)
                or (
                    validation.get("decision_eligible") is True
                    and capture_reason != "anchor_event_not_audit_eligible"
                )
                or (
                    validation.get("decision_eligible") is not True
                    and capture_reason
                    != "candidate_audit_not_decision_eligible"
                )
                or capture_delay
                > _CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
            ):
                return "candidate_selection_capture_ineligible_state_invalid"
        else:
            return "candidate_selection_capture_artifact_type_invalid"
    else:
        return "candidate_selection_capture_status_invalid"
    if not isinstance(labels, Mapping) or labels:
        return "candidate_selection_nonformal_capture_label_conflict"
    if (
        case.get("outcome_commit_receipt_status") != "absent"
        or any(
            case.get(field) is not None
            for field in (
                "outcome_artifact_id",
                "outcome_content_hash",
                "outcome_commit_receipt_id",
                "outcome_commit_receipt_content_hash",
                "outcome_commit_receipt_source_visible_at",
                "label_storage_created_at",
            )
        )
        or case.get("provider_receipt_refs") != []
        or case.get("provider_receipt_count") != 0
        or case.get("provider_receipt_manifest_hash") != _canonical_hash([])
        or case.get("provider_adapter_stratum") != []
        or case.get("provider_adapter_stratum_hash")
        != candidate_provider_adapter_stratum_hash([])
    ):
        return "candidate_selection_nonformal_capture_outcome_conflict"
    receipt_status = case.get("audit_commit_receipt_status")
    receipt_fields = (
        case.get("audit_commit_receipt_id"),
        case.get("audit_commit_receipt_content_hash"),
        case.get("audit_commit_receipt_source_visible_at"),
    )
    if receipt_status == "pending":
        if any(value is not None for value in receipt_fields):
            return "candidate_selection_audit_pending_receipt_field_conflict"
        return None
    if receipt_status not in {"verified", "late"}:
        return "candidate_selection_audit_commit_receipt_status_invalid"
    if not _receipt_identity_valid(receipt_fields[0], receipt_fields[1]):
        return "candidate_selection_audit_commit_receipt_invalid"
    visible_at = _aware_datetime(receipt_fields[2])
    if (
        visible_at is None
        or visible_at < source_created_at
        or visible_at > evaluation_as_of
    ):
        return "candidate_selection_audit_commit_receipt_invalid"
    receipt_delay = (visible_at - source_created_at).total_seconds()
    if (
        receipt_status == "verified"
        and receipt_delay > _CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
    ):
        return "candidate_selection_audit_verified_receipt_delay_exceeded"
    if (
        receipt_status == "late"
        and receipt_delay <= _CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
    ):
        return "candidate_selection_audit_late_receipt_delay_not_exceeded"
    return None


def _candidate_label_identity_error(
    mapping_key: str | None,
    label: Mapping[str, Any],
) -> str | None:
    fund_code = _text(label.get("fund_code"))
    if (
        fund_code is None
        or len(fund_code) != 6
        or not fund_code.isdigit()
        or (mapping_key is not None and mapping_key != fund_code)
    ):
        return "candidate_selection_label_fund_code_invalid"
    label_hash = _text(label.get("label_hash"))
    if not _is_hash(label_hash):
        return "candidate_selection_label_hash_missing_or_invalid"
    try:
        expected_label_hash = _canonical_hash(
            {key: value for key, value in label.items() if key != "label_hash"}
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "candidate_selection_label_hash_uncomputable"
    if label_hash.lower() != expected_label_hash:
        return "candidate_selection_label_hash_mismatch"
    return None


def _candidate_provider_receipt_refs(
    case: Mapping[str, Any],
    *,
    evaluation_as_of: datetime,
    outcome_visible_at: datetime | None,
) -> tuple[list[dict[str, Any]], str | None]:
    raw_refs = case.get("provider_receipt_refs")
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes)):
        return [], "candidate_selection_provider_receipt_refs_invalid"
    refs: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for raw in raw_refs:
        if not isinstance(raw, Mapping) or set(raw) != _CANDIDATE_PROVIDER_REF_FIELDS:
            return [], "candidate_selection_provider_receipt_ref_invalid"
        ref = dict(raw)
        receipt_id = _text(ref.get("receipt_id"))
        content_hash = _text(ref.get("content_hash"))
        policy_registered = candidate_adapter_policy_is_registered(
            provider=ref.get("provider"),
            operation=ref.get("operation"),
            contract_version=ref.get("adapter_contract_version"),
            policy_id=ref.get("adapter_policy_id"),
        )
        if (
            not _content_addressed_id(receipt_id, "dqpr_")
            or not _is_hash(content_hash)
            or receipt_id != f"dqpr_{content_hash}"
            or _text(ref.get("provider")) is None
            or _text(ref.get("operation")) is None
            or ref.get("capture_mode") != "live"
            or not policy_registered
            or ref.get("adapter_library_name") != "akshare"
            or any(
                _text(ref.get(field)) is None
                for field in (
                    "adapter_contract_version",
                    "adapter_library_version",
                    "adapter_python_version",
                )
            )
            or any(
                not _is_hash(_text(ref.get(field)))
                for field in (
                    "request_hash",
                    "adapter_output_sha256",
                    "normalized_payload_hash",
                    "origin_receipt_hash",
                    "adapter_policy_hash",
                    "adapter_script_sha256",
                    "adapter_policy_script_sha256",
                )
            )
        ):
            return [], "candidate_selection_provider_receipt_ref_invalid"
        origin = _aware_datetime(ref.get("origin_fetched_at"))
        completed = _aware_datetime(ref.get("completed_at"))
        if (
            origin is None
            or completed is None
            or origin > completed
            or completed > evaluation_as_of
            or (
                outcome_visible_at is not None
                and completed > outcome_visible_at
            )
        ):
            return [], "candidate_selection_provider_receipt_time_invalid"
        identity = (receipt_id or "", content_hash or "")
        if identity in identities:
            return [], "candidate_selection_provider_receipt_duplicate"
        identities.add(identity)
        refs.append(ref)
    expected_order = sorted(
        refs,
        key=lambda ref: (
            str(ref["provider"]),
            str(ref["operation"]),
            str(ref["receipt_id"]),
        ),
    )
    if refs != expected_order:
        return [], "candidate_selection_provider_receipt_order_invalid"
    if _nonnegative_int(case.get("provider_receipt_count")) != len(refs):
        return [], "candidate_selection_provider_receipt_count_mismatch"
    try:
        manifest_hash = _canonical_hash(refs)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return [], "candidate_selection_provider_receipt_manifest_uncomputable"
    if case.get("provider_receipt_manifest_hash") != manifest_hash:
        return [], "candidate_selection_provider_receipt_manifest_hash_mismatch"
    return refs, None


def _content_addressed_id(value: object, prefix: str) -> bool:
    normalized = _text(value)
    return bool(
        normalized is not None
        and normalized.startswith(prefix)
        and _is_hash(normalized[len(prefix) :])
    )


def _receipt_identity_valid(receipt_id: object, content_hash: object) -> bool:
    normalized_id = _text(receipt_id)
    normalized_hash = _text(content_hash)
    return bool(
        _content_addressed_id(normalized_id, "dqr_")
        and _is_hash(normalized_hash)
        and normalized_id == f"dqr_{normalized_hash}"
    )


def _candidate_label_rows(
    value: object,
) -> list[tuple[str | None, Mapping[str, Any]]] | None:
    if value is None:
        return []
    if isinstance(value, Mapping):
        rows = [
            (_text(key), row)
            for key, row in value.items()
            if isinstance(key, str)
        ]
        if len(rows) != len(value):
            return None
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [(None, row) for row in value]
    else:
        return None
    return rows if all(isinstance(row, Mapping) for _, row in rows) else None


def _immutable_source_ref(value: object) -> bool:
    if isinstance(value, str):
        return _is_hash(_text(value))
    if not isinstance(value, Mapping):
        return False
    ref_id = _text(
        value.get("ref_id")
        or value.get("observation_id")
        or value.get("event_id")
    )
    digest = _text(
        value.get("content_hash")
        or value.get("snapshot_hash")
        or value.get("payload_hash")
    )
    return (
        _text(value.get("source")) is not None
        and ref_id is not None
        and _is_hash(digest)
    )


def _variant(event: Mapping[str, Any]) -> dict[str, str]:
    prompt_contract = event.get("prompt_contract")
    embedded_prompt_contract_hash = (
        _text(prompt_contract.get("contract_hash"))
        if isinstance(prompt_contract, Mapping)
        else None
    )
    prompt_contract_hash = (
        _text(event.get("prompt_contract_hash"))
        or embedded_prompt_contract_hash
    )
    return {
        "model_version": _text(event.get("model_version")) or "missing",
        "model_hash": _text(event.get("model_hash")) or "missing",
        "prompt_version": _text(event.get("prompt_version")) or "missing",
        "prompt_hash": _text(event.get("prompt_hash")) or "missing",
        "prompt_contract_hash": prompt_contract_hash or "missing",
        "strategy_version": _text(event.get("strategy_version")) or "missing",
        "strategy_hash": _text(event.get("strategy_hash")) or "missing",
        "policy_version": _text(event.get("policy_version")) or "missing",
        "policy_hash": _text(event.get("policy_hash")) or "missing",
        "data_version": _text(event.get("data_version")) or "missing",
        "data_hash": _text(event.get("data_hash")) or "missing",
        "evidence_hash": _text(event.get("evidence_hash")) or "missing",
        "fee_model_version": _text(event.get("fee_model_version")) or "missing",
        "fee_model_hash": _text(event.get("fee_model_hash")) or "missing",
    }


def _frozen_dimension(value: object) -> object:
    if value is None or value == "":
        return "missing"
    if isinstance(value, str):
        return value.strip() or "missing"
    if isinstance(value, (bool, int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else "missing"
    return "missing"


def _event_knowledge_cutoff(event: Mapping[str, Any]) -> datetime | None:
    """Return the latest input time known to the decision, not its business clock."""

    normalized = _aware_datetime(event.get("_evaluation_knowledge_cutoff"))
    if normalized is not None:
        return normalized

    if (
        event.get("quality_contract_version") == DECISION_QUALITY_CONTRACT_VERSION
        and event.get("replay_contract_required") is True
    ):
        bundle = event.get("replay_bundle")
        return (
            _aware_datetime(bundle.get("recorded_at"))
            if isinstance(bundle, Mapping)
            else None
        )
    # Pre-D2 v2 rows remain inspectable, but _event_replay_eligibility keeps
    # them out of every formal denominator.
    return _aware_datetime(event.get("decision_at"))


def _event_replay_eligibility(
    event: Mapping[str, Any],
) -> tuple[bool, str | None]:
    bundle = event.get("replay_bundle")
    if (
        event.get("quality_contract_version") != DECISION_QUALITY_CONTRACT_VERSION
        or event.get("replay_contract_required") is not True
    ):
        return (
            (False, "replay_bundle_missing")
            if not isinstance(bundle, Mapping)
            else (False, "replay_contract_version_missing")
        )
    if not isinstance(bundle, Mapping):
        return False, "replay_bundle_missing"
    replay_boundary = _aware_datetime(bundle.get("recorded_at"))
    refs = event.get("replay_refs")
    refs_eligible, refs_reason = _replay_refs_eligibility(replay_boundary, refs)
    if not refs_eligible:
        return False, refs_reason
    bundle_error = decision_replay_bundle_error(bundle)
    if bundle_error is not None:
        return False, bundle_error
    if not isinstance(bundle, Mapping):
        return False, "replay_bundle_missing_or_invalid"
    if event.get("replay_bundle_hash") != bundle.get("bundle_hash"):
        return False, "replay_bundle_event_hash_mismatch"
    if event.get("replay_refs") != bundle.get("replay_refs"):
        return False, "replay_refs_not_bound_to_bundle"
    if event.get("decision_kind") != bundle.get("decision_kind"):
        return False, "replay_bundle_decision_kind_conflict"
    if event.get("decision_at") != bundle.get("decision_at"):
        return False, "replay_bundle_decision_time_conflict"
    if event.get("recorded_at") != bundle.get("recorded_at"):
        return False, "replay_bundle_recorded_time_conflict"
    report_id = event.get("source_report_id") or event.get("report_id")
    if report_id is not None and str(report_id) != str(bundle.get("source_report_id")):
        return False, "replay_bundle_source_report_conflict"
    manifest = bundle.get("variant_manifest")
    if not isinstance(manifest, Mapping):
        return False, "replay_bundle_variant_manifest_mismatch"
    if event.get("variant_manifest") != manifest:
        return False, "replay_variant_manifest_not_bound_to_bundle"
    for field in (*_VARIANT_VERSION_FIELDS, *_VARIANT_HASH_FIELDS):
        if event.get(field) != manifest.get(field):
            return False, f"replay_variant_field_not_bound_to_bundle:{field}"
    if event.get("prompt_contract") != bundle.get("prompt_contract_snapshot"):
        return False, "replay_prompt_contract_not_bound_to_bundle"
    if event.get("fee_policy") != bundle.get("fee_policy_snapshot"):
        return False, "replay_fee_policy_not_bound_to_bundle"
    return True, None


def _replay_refs_eligibility(
    replay_boundary: datetime | None,
    refs: object,
) -> tuple[bool, str | None]:
    if (
        replay_boundary is None
        or not isinstance(refs, Sequence)
        or isinstance(refs, (str, bytes))
        or not refs
    ):
        return False, "replay_refs_missing"
    for ref in refs:
        if not isinstance(ref, Mapping):
            return False, "replay_ref_invalid"
        available = _aware_datetime(ref.get("available_at"))
        first_observed = _aware_datetime(ref.get("first_observed_at"))
        digest = _text(
            ref.get("snapshot_hash")
            or ref.get("content_hash")
            or ref.get("payload_hash")
        )
        if _text(ref.get("source")) is None or _text(ref.get("ref_id")) is None:
            return False, "replay_ref_invalid"
        if not _is_hash(digest):
            return False, "replay_ref_hash_missing_or_invalid"
        if available is None or first_observed is None:
            return False, "replay_ref_time_invalid"
        if available > first_observed:
            return False, "replay_ref_time_order_invalid"
        if first_observed > replay_boundary:
            return False, "replay_ref_not_point_in_time_eligible"
    return True, None


def _normalize_paired_cases(
    value: object,
) -> tuple[dict[tuple[str, int], dict[str, Any]], list[str]]:
    rows = _sequence(value)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    reasons: list[str] = []
    conflicted: set[tuple[str, int]] = set()
    for raw in rows:
        reason = _paired_case_error(raw)
        if reason is not None:
            reasons.append(reason)
            continue
        case = dict(raw)
        key = (str(case["frozen_case_hash"]).lower(), int(case["horizon_trading_days"]))
        replay_eligible, _ = _paired_replay_eligibility(case)
        case["replay_eligible"] = replay_eligible
        if key in conflicted:
            reasons.append("paired_case_duplicate_conflict")
        elif key not in result:
            result[key] = case
        elif result[key] == case:
            reasons.append("paired_case_duplicate_identical")
        else:
            result.pop(key, None)
            conflicted.add(key)
            reasons.append("paired_case_duplicate_conflict")
    return result, reasons


def _paired_case_error(case: Mapping[str, Any]) -> str | None:
    if case.get("schema_version") != PAIRED_CASE_SCHEMA_VERSION:
        return "paired_case_schema_invalid"
    if not _is_hash(_text(case.get("frozen_case_hash"))):
        return "paired_frozen_case_hash_invalid"
    if _positive_int(case.get("horizon_trading_days")) is None:
        return "paired_horizon_invalid"
    decision_at = _aware_datetime(case.get("decision_at"))
    output_at = _aware_datetime(case.get("output_at"))
    if decision_at is None or output_at is None or output_at < decision_at:
        return "paired_case_time_invalid"
    if not _is_hash(_text(case.get("label_hash"))):
        return "paired_label_hash_invalid"
    if _aware_datetime(case.get("label_available_at")) is None:
        return "paired_label_time_invalid"
    if _finite_number(case.get("utility")) is None or _finite_number(case.get("risk")) is None:
        return "paired_utility_or_risk_invalid"
    if _text(case.get("claim_status")) not in _CLAIM_STATUSES:
        return "paired_claim_status_invalid"
    if "replay_eligible" in case:
        return "paired_case_reserved_field_present"
    output_hash = _text(case.get("output_hash"))
    content_hash = _text(case.get("content_hash"))
    if not _is_hash(output_hash):
        return "paired_output_hash_missing_or_invalid"
    if not _is_hash(content_hash):
        return "paired_content_hash_missing_or_invalid"
    try:
        expected_output_hash = _canonical_hash(
            {
                key: value
                for key, value in case.items()
                if key not in {"output_hash", "content_hash"}
            }
        )
        expected_content_hash = _canonical_hash(
            {key: value for key, value in case.items() if key != "content_hash"}
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "paired_case_hash_uncomputable"
    if output_hash.lower() != expected_output_hash:
        return "paired_output_hash_mismatch"
    if content_hash.lower() != expected_content_hash:
        return "paired_content_hash_mismatch"
    return None


def _paired_replay_eligibility(case: Mapping[str, Any]) -> tuple[bool, str | None]:
    return _replay_refs_eligibility(
        _aware_datetime(case.get("output_at")),
        case.get("replay_refs"),
    )


def _validate_gate_policy(
    policy: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(policy, Mapping):
        return None, ["gate_policy_missing"]
    value = dict(policy)
    reasons: list[str] = []
    if value.get("schema_version") != GATE_POLICY_SCHEMA_VERSION:
        reasons.append("gate_policy_schema_invalid")
    if _text(value.get("policy_id")) is None:
        reasons.append("gate_policy_id_missing")
    if _aware_datetime(value.get("registered_at")) is None:
        reasons.append("gate_policy_registered_at_invalid")
    if _positive_int(value.get("min_pairs")) is None:
        reasons.append("gate_policy_min_pairs_invalid")
    for key in ("minimum_mean_utility_delta", "maximum_mean_risk_delta"):
        if _finite_number(value.get(key)) is None:
            reasons.append(f"gate_policy_threshold_invalid:{key}")
    for key in (
        "maximum_claim_violation_rate",
        "maximum_claim_sanitized_rate",
    ):
        threshold = _finite_number(value.get(key))
        if threshold is None or not 0 <= threshold <= 1:
            reasons.append(f"gate_policy_threshold_invalid:{key}")
    supplied_hash = _text(value.get("policy_hash"))
    try:
        expected_hash = _canonical_hash(
            {key: item for key, item in value.items() if key != "policy_hash"}
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        reasons.append("gate_policy_hash_uncomputable")
        expected_hash = None
    if (
        expected_hash is not None
        and (
            not _is_hash(supplied_hash)
            or supplied_hash.lower() != expected_hash
        )
    ):
        reasons.append("gate_policy_hash_invalid")
    return (value if not reasons else None), reasons


def _validate_evaluation_config(
    *,
    evaluation_as_of: object,
    min_calibration_samples: object,
    calibration_bins: object,
    calibration_metric: object,
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(evaluation_as_of, datetime):
        reasons.append("evaluation_as_of_missing_or_invalid")
    if _positive_int(min_calibration_samples) is None:
        reasons.append("minimum_calibration_samples_invalid")
    if (
        _positive_int(calibration_bins) is None
        or int(calibration_bins) > 100
    ):
        reasons.append("calibration_bins_invalid")
    if calibration_metric not in METRIC_NAMES:
        reasons.append("calibration_metric_invalid")
    return reasons


def _outcome_content_hash(observation: Mapping[str, Any]) -> str:
    omitted = {
        "content_hash",
        "created_at",
        "updated_at",
        "observation_at",
        "observed_at",
        "finalized_at",
        "revision_no",
    }
    return _canonical_hash(
        {str(key): value for key, value in observation.items() if str(key) not in omitted}
    )


def _outcome_label_available_at(
    observation: Mapping[str, Any],
    wrapper: Mapping[str, Any],
) -> datetime | None:
    values = (
        observation.get("label_available_at"),
        observation.get("source_available_at"),
        wrapper.get("finalized_at"),
        wrapper.get("observed_at"),
        wrapper.get("recorded_at"),
        wrapper.get("created_at"),
        wrapper.get("updated_at"),
        observation.get("finalized_at"),
        observation.get("observed_at"),
        observation.get("observation_at"),
        observation.get("recorded_at"),
    )
    parsed: list[datetime] = []
    for value in values:
        if value is None:
            continue
        timestamp = _aware_datetime(value)
        if timestamp is None:
            return None
        parsed.append(timestamp)
    return max(parsed) if parsed else None


def _counter_rows(value: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(value.items())
        if count > 0
    ]


def _sequence(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _raw_sequence(value: object) -> list[object] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    return list(value)


def _positive_ints(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    output: list[int] = []
    for item in value:
        parsed = _positive_int(item)
        if parsed is None or parsed in output:
            return []
        output.append(parsed)
    return output


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = float(value)
    except (OverflowError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _explicit_probability(value: object) -> float | None:
    probability = _finite_number(value)
    return probability if probability is not None and 0 <= probability <= 1 else None


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_abstained(event: Mapping[str, Any]) -> bool:
    return bool(
        event.get("abstained") is True
        or _text(event.get("evaluation_class")) in _ABSTENTION_CLASSES
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_hash(value: str | None) -> bool:
    return bool(
        value
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _rounded(value: float | None) -> float | None:
    return round(float(value), 8) if value is not None else None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "CLAIM_AUDIT_SCHEMA_VERSION",
    "CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION",
    "DECISION_QUALITY_EVALUATION_SCHEMA_VERSION",
    "GATE_POLICY_SCHEMA_VERSION",
    "PAIRED_CASE_SCHEMA_VERSION",
    "PAIRED_GATE_SCHEMA_VERSION",
    "evaluate_decision_quality",
    "evaluate_paired_champion_challenger_gate",
]
