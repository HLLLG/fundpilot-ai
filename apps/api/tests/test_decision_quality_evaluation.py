from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import decision_quality_evaluation as quality
from app.services.benchmark_fee_evaluation import evaluate_decision_metrics
from app.services.decision_contract import (
    build_decision_replay_bundle,
    payload_hash,
)
from app.services.decision_repository import (
    decision_event_content_hash,
    normalize_decision_event,
)
from app.services.decision_quality_provider_policy import (
    candidate_adapter_policy_id_for_contract,
    candidate_provider_adapter_stratum,
    candidate_provider_adapter_stratum_hash,
    registered_candidate_adapter_policy_binding,
)


_AS_OF = "2026-02-01T00:00:00+00:00"
_DECISION_AT = "2026-01-02T10:00:00+00:00"
_LABEL_AT = "2026-01-15T10:00:00+00:00"
_CANDIDATE_RECORDED_AT = "2026-01-02T10:01:00+00:00"


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_ref(seed: str) -> dict[str, str]:
    return {
        "source": "fixture",
        "ref_id": seed,
        "content_hash": hashlib.sha256(seed.encode()).hexdigest(),
    }


def _sign_candidate_label(value: dict[str, Any]) -> dict[str, Any]:
    value.pop("label_hash", None)
    value["label_hash"] = _canonical_hash(value)
    return value


def _candidate_label(
    fund_code: str,
    *,
    label_available_at: str = _LABEL_AT,
    source_ref: object | None = None,
    availability_basis: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
            "fund_code": fund_code,
            "mature": True,
            "eligible": True,
            "skipped": False,
            "binary_relevance": True,
            "relevance": 2.0,
            "utility": 1.0,
            "label_available_at": label_available_at,
            "source_ref": (
                source_ref
                if source_ref is not None
                else _source_ref(f"candidate:{fund_code}")
            ),
        }
    if availability_basis is not None:
        value["availability_basis"] = availability_basis
    return _sign_candidate_label(value)


def _candidate_provider_ref(
    seed: str,
    *,
    completed_at: str,
    provider: str = "akshare.fund_open_fund_info_em",
    operation: str = "fund_open_fund_info_em",
) -> dict[str, Any]:
    content_hash = hashlib.sha256(f"provider-content:{seed}".encode()).hexdigest()
    origin_fetched_at = (
        datetime.fromisoformat(completed_at) - timedelta(seconds=1)
    ).isoformat()
    contract_version = (
        "decision_quality_trade_calendar_adapter.v1"
        if provider == "akshare.tool_trade_date_hist_sina"
        else "decision_quality_fund_nav_adapter.v1"
        if provider == "akshare.fund_open_fund_info_em"
        else "unsupported_adapter_contract.v1"
    )
    policy_id = candidate_adapter_policy_id_for_contract(
        provider,
        operation,
        contract_version,
    )
    registered = registered_candidate_adapter_policy_binding(
        provider=provider,
        operation=operation,
        contract_version=contract_version,
    )
    return {
        "receipt_id": f"dqpr_{content_hash}",
        "content_hash": content_hash,
        "provider": provider,
        "operation": operation,
        "capture_mode": "live",
        "request_hash": hashlib.sha256(f"request:{seed}".encode()).hexdigest(),
        "adapter_output_sha256": hashlib.sha256(
            f"adapter:{seed}".encode()
        ).hexdigest(),
        "normalized_payload_hash": hashlib.sha256(
            f"normalized:{seed}".encode()
        ).hexdigest(),
        "origin_fetched_at": origin_fetched_at,
        "completed_at": completed_at,
        "origin_receipt_hash": hashlib.sha256(
            f"origin:{seed}".encode()
        ).hexdigest(),
        "adapter_policy_id": policy_id or "unsupported_provider_policy.v1",
        "adapter_policy_hash": (
            registered["adapter_policy_hash"]
            if registered is not None
            else hashlib.sha256(f"unsupported-policy:{seed}".encode()).hexdigest()
        ),
        "adapter_contract_version": contract_version,
        "adapter_script_sha256": hashlib.sha256(
            f"adapter-script:{seed}".encode()
        ).hexdigest(),
        "adapter_policy_script_sha256": (
            registered["adapter_policy_script_sha256"]
            if registered is not None
            else hashlib.sha256(
                f"unsupported-policy-script:{seed}".encode()
            ).hexdigest()
        ),
        "adapter_library_name": "akshare",
        "adapter_library_version": "fixture",
        "adapter_python_version": "3.12.fixture",
    }


def _formal_candidate_case(
    seed: str,
    *,
    decision_at: str,
    label_available_at: str,
    label_storage_created_at: str,
    selection_policy_version: str = "selection.v1",
) -> dict[str, Any]:
    decision = datetime.fromisoformat(decision_at)
    snapshot_hash = hashlib.sha256(f"snapshot:{seed}".encode()).hexdigest()
    digest = hashlib.sha256(seed.encode()).hexdigest()
    audit_receipt_hash = hashlib.sha256(
        f"audit-receipt:{seed}".encode()
    ).hexdigest()
    outcome_content_hash = hashlib.sha256(
        f"outcome-content:{seed}".encode()
    ).hexdigest()
    outcome_receipt_hash = hashlib.sha256(
        f"outcome-receipt:{seed}".encode()
    ).hexdigest()
    audit_visible_at = (decision + timedelta(minutes=2)).isoformat()
    provider_ref = _candidate_provider_ref(
        seed,
        completed_at=label_available_at,
    )
    calendar_ref = _candidate_provider_ref(
        f"calendar:{seed}",
        completed_at=(decision - timedelta(days=1)).isoformat(),
        provider="akshare.tool_trade_date_hist_sina",
        operation="tool_trade_date_hist_sina",
    )
    provider_refs = sorted(
        [calendar_ref, provider_ref],
        key=lambda ref: (
            ref["provider"],
            ref["operation"],
            ref["receipt_id"],
        ),
    )
    label_source_ref = {
        "source": "akshare.fund_open_fund_info_em",
        "ref_id": f"candidate_case_{digest}:000001",
        "content_hash": hashlib.sha256(f"evidence:{seed}".encode()).hexdigest(),
        "provider_receipt_ref": provider_ref,
        "normalized_payload_projection_hash": hashlib.sha256(
            f"projection:{seed}".encode()
        ).hexdigest(),
    }
    return {
        "schema_version": "decision_quality_candidate_selection_case.v2",
        "case_id": f"candidate_case_{digest}",
        "recorded_at": (decision + timedelta(minutes=1)).isoformat(),
        "decision_at": decision_at,
        "audit_source_row_created_at": (
            decision + timedelta(minutes=1)
        ).isoformat(),
        "capture_status": "eligible",
        "capture_reason": "eligible",
        "capture_reason_hash": _canonical_hash({"reason": "eligible"}),
        "source_capture_delay_seconds": 60.0,
        "capture_artifact_type": "candidate_selection_audit",
        "audit_artifact_id": f"dqa_{digest}",
        "audit_content_hash": digest,
        "audit_snapshot_hash": snapshot_hash,
        "label_plan_hash": hashlib.sha256(f"plan:{seed}".encode()).hexdigest(),
        "audit_commit_receipt_status": "verified",
        "audit_commit_receipt_id": f"dqr_{audit_receipt_hash}",
        "audit_commit_receipt_content_hash": audit_receipt_hash,
        "audit_commit_receipt_source_visible_at": audit_visible_at,
        "outcome_commit_receipt_status": "verified",
        "outcome_artifact_id": f"dqa_{outcome_content_hash}",
        "outcome_content_hash": outcome_content_hash,
        "outcome_commit_receipt_id": f"dqr_{outcome_receipt_hash}",
        "outcome_commit_receipt_content_hash": outcome_receipt_hash,
        "outcome_commit_receipt_source_visible_at": label_storage_created_at,
        "label_storage_created_at": label_storage_created_at,
        "provider_receipt_count": len(provider_refs),
        "provider_receipt_manifest_hash": _canonical_hash(provider_refs),
        "provider_receipt_refs": provider_refs,
        "provider_adapter_stratum": candidate_provider_adapter_stratum(
            provider_refs
        ),
        "provider_adapter_stratum_hash": (
            candidate_provider_adapter_stratum_hash(provider_refs)
        ),
        "horizon_trading_days": 20,
        "decision_date_local": decision.astimezone(
            timezone(timedelta(hours=8))
        ).date().isoformat(),
        "declared_decision_date_local": decision.astimezone(
            timezone(timedelta(hours=8))
        ).date().isoformat(),
        "label_policy_version": "candidate_label_policy.2026-07.v3",
        "selection_policy_version": selection_policy_version,
        "candidate_evaluator_version": (
            "decision_quality_candidate_selection_evaluator.v2"
        ),
        "evidence_scope": (
            "source_verified_provider_and_post_commit_receipts"
        ),
        "audit": {
            "decision_at": decision_at,
            "snapshot_hash": snapshot_hash,
            "versions": {"selection_policy": selection_policy_version},
            "metric_seed": seed,
        },
        "outcome_labels": {
            "000001": _candidate_label(
                "000001",
                label_available_at=label_storage_created_at,
                source_ref=label_source_ref,
                availability_basis="post_commit_artifact_receipt",
            )
        },
        "k": 3,
        "universe_stage": "prescreen",
        "automatic_promotion_allowed": False,
    }


def _receipt_pending_candidate_case(
    seed: str,
    *,
    audit_status: str = "verified",
    outcome_status: str = "absent",
) -> dict[str, Any]:
    case = _formal_candidate_case(
        seed,
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    case["outcome_labels"] = {}
    case["provider_receipt_refs"] = []
    case["provider_receipt_count"] = 0
    case["provider_receipt_manifest_hash"] = _canonical_hash([])
    case["provider_adapter_stratum"] = []
    case["provider_adapter_stratum_hash"] = (
        candidate_provider_adapter_stratum_hash([])
    )
    case["label_storage_created_at"] = None
    case["outcome_commit_receipt_status"] = outcome_status
    case["outcome_commit_receipt_id"] = None
    case["outcome_commit_receipt_content_hash"] = None
    case["outcome_commit_receipt_source_visible_at"] = None
    if outcome_status == "absent":
        case["outcome_artifact_id"] = None
        case["outcome_content_hash"] = None
    if audit_status == "pending":
        case["audit_commit_receipt_status"] = "pending"
        case["audit_commit_receipt_id"] = None
        case["audit_commit_receipt_content_hash"] = None
        case["audit_commit_receipt_source_visible_at"] = None
    return case


def _late_candidate_case(seed: str) -> dict[str, Any]:
    case = _receipt_pending_candidate_case(seed, outcome_status="absent")
    recorded_at = datetime.fromisoformat(str(case["recorded_at"]))
    case["audit_commit_receipt_status"] = "late"
    case["audit_commit_receipt_source_visible_at"] = (
        recorded_at + timedelta(seconds=600)
    ).isoformat()
    return case


def _sign_shadow_label(value: dict[str, Any]) -> dict[str, Any]:
    value.pop("content_hash", None)
    value["content_hash"] = _canonical_hash(value)
    return value


def _shadow_label(
    event_id: str,
    *,
    beneficial: bool = True,
    label_available_at: str = _LABEL_AT,
    source_ref: object | None = None,
) -> dict[str, Any]:
    return _sign_shadow_label(
        {
            "event_id": event_id,
            "horizon_trading_days": 5,
            "mature": True,
            "beneficial": beneficial,
            "label_available_at": label_available_at,
            "source_ref": (
                source_ref
                if source_ref is not None
                else _source_ref(f"shadow:{event_id}")
            ),
        }
    )


def _claim_wrapper(
    event: dict[str, Any],
    status: str,
    *,
    available_at: str = "2026-01-02T10:05:00+00:00",
    recorded_at: str = "2026-01-02T10:06:00+00:00",
) -> dict[str, Any]:
    changes = (
        [
            {
                "path": "$.summary",
                "original_hash": hashlib.sha256(b"unsafe").hexdigest(),
                "reason": "fixture_sanitized_claim",
                "replacement": "sanitized fixture",
            }
        ]
        if status == "sanitized"
        else []
    )
    audit: dict[str, Any] = {
        "schema_version": quality.CLAIM_AUDIT_SCHEMA_VERSION,
        "status": status,
        "facts_status": "available",
        "scanned_field_count": 1,
        "lookthrough_field_count": 1,
        "changed_field_count": len({row["path"] for row in changes}),
        "change_count": len(changes),
        "reason_counts": (
            {"fixture_sanitized_claim": 1} if changes else {}
        ),
        "changes": changes,
        "hash_algorithm": "sha256",
    }
    audit["audit_hash"] = _canonical_hash(audit)
    wrapper: dict[str, Any] = {
        "schema_version": quality.CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION,
        "event_id": event["event_id"],
        "decision_at": event["decision_at"],
        "decision_event_payload_hash": event["payload_hash"],
        "available_at": available_at,
        "recorded_at": recorded_at,
        "audit": audit,
    }
    wrapper["content_hash"] = _canonical_hash(wrapper)
    return wrapper


def _stored_outcome_content_hash(value: dict[str, Any]) -> str:
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
        {key: item for key, item in value.items() if key not in omitted}
    )


def _event(
    event_id: str,
    *,
    decision_at: str = _DECISION_AT,
    evaluation_class: str = "buy",
    success_probability: float | None = None,
    horizons: list[int] | None = None,
    replay_refs: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    actionable = evaluation_class in {"buy", "bullish", "bearish"}
    decision_kind = str(overrides.get("decision_kind") or "daily")
    model_version = str(overrides.get("model_version") or "model.v1")
    prompt_version = str(overrides.get("prompt_version") or "prompt.v1")
    strategy_version = str(overrides.get("strategy_version") or "strategy.v1")
    policy_version = str(overrides.get("policy_version") or "policy.v1")
    fee_model_version = str(overrides.get("fee_model_version") or "fee.v1")
    fund_type = str(overrides.get("fund_type") or "equity")
    market_regime = str(overrides.get("market_regime") or "risk_on")
    data_completeness = str(overrides.get("data_completeness") or "complete")
    fee_policy = copy.deepcopy(
        overrides.get("fee_policy")
        or {
            "status": "available",
            "fee_source": "user_assumption",
            "round_trip_fee_percent": 0.1,
            "fee_calculation": "initial_principal_haircut",
        }
    )
    prompt_contract = copy.deepcopy(
        overrides.get("prompt_contract")
        or {
            "schema_version": "prompt_contract.v1",
            "template_version": prompt_version,
            "template_snapshot": "fixture prompt",
        }
    )
    prompt_contract.pop("contract_hash", None)
    prompt_contract["contract_hash"] = payload_hash(prompt_contract)
    source_refs = replay_refs or [
        {
            "source": "fixture",
            "ref_id": f"facts:{event_id}",
            "available_at": "2026-01-02T09:00:00+00:00",
            "first_observed_at": "2026-01-02T09:00:00+00:00",
        }
    ]
    evidence_items = [
        {
            "fact_id": str(ref.get("ref_id") or ""),
            "source": ref.get("source"),
            "source_type": "official",
            "as_of_date": "2026-01-02",
            "available_at": ref.get("available_at"),
            "fetched_at": ref.get("first_observed_at"),
            "freshness": "fresh",
            "confidence": "high",
            "is_estimate": False,
        }
        for ref in source_refs
    ]
    recorded_at = str(
        overrides.get("recorded_at")
        or max(
            [decision_at]
            + [
                str(ref.get("first_observed_at"))
                for ref in source_refs
                if ref.get("first_observed_at")
            ]
        )
    )
    facts = {
        "fund_type": fund_type,
        "market_regime": market_regime,
        "data_completeness": data_completeness,
        "data_evidence": {
            "schema_version": "1.0",
            "decision_ready": data_completeness == "complete",
            "items": evidence_items,
        },
    }
    replay_bundle = build_decision_replay_bundle(
        facts=facts,
        decision_kind=decision_kind,  # type: ignore[arg-type]
        decision_at=decision_at,
        recorded_at=recorded_at,
        source_report_id=f"report:{event_id}",
        model_version=model_version,
        prompt_version=prompt_version,
        prompt_contract=prompt_contract,
        strategy_version=strategy_version,
        policy_version=policy_version,
        fee_model_version=fee_model_version,
        fee_policy=fee_policy,
    )
    manifest = replay_bundle["variant_manifest"]
    value: dict[str, Any] = {
        "schema_version": "decision_event.v2",
        "quality_contract_version": "decision_quality_contract.v1",
        "replay_contract_required": True,
        "event_id": event_id,
        "event_type": "daily_fund_decision",
        "source_type": decision_kind,
        "decision_kind": decision_kind,
        "source_report_id": f"report:{event_id}",
        "decision_at": decision_at,
        "recorded_at": replay_bundle["recorded_at"],
        "fund_code": "000001",
        "action": "buy" if actionable else "observe",
        "final_action": "buy" if actionable else "observe",
        "evaluation_class": evaluation_class,
        "eligible": actionable,
        "horizons": list(horizons or [5]),
        "fee_policy": fee_policy,
        "model_version": manifest["model_version"],
        "model_hash": manifest["model_hash"],
        "prompt_version": manifest["prompt_version"],
        "prompt_hash": manifest["prompt_hash"],
        "prompt_contract_hash": manifest["prompt_contract_hash"],
        "prompt_contract": prompt_contract,
        "strategy_version": manifest["strategy_version"],
        "strategy_hash": manifest["strategy_hash"],
        "policy_version": manifest["policy_version"],
        "policy_hash": manifest["policy_hash"],
        "data_version": manifest["data_version"],
        "data_hash": manifest["data_hash"],
        "evidence_hash": manifest["evidence_hash"],
        "fee_model_version": manifest["fee_model_version"],
        "fee_model_hash": manifest["fee_model_hash"],
        "variant_hash": manifest["variant_hash"],
        "variant_manifest": copy.deepcopy(manifest),
        "fund_type": fund_type,
        "market_regime": market_regime,
        "data_completeness": data_completeness,
        "store_authority": "primary",
        "is_backfilled": False,
        "audit_eligible": True,
        "metric_eligible": True,
        "replay_refs": copy.deepcopy(replay_bundle["replay_refs"]),
        "replay_bundle": copy.deepcopy(replay_bundle),
        "replay_bundle_hash": replay_bundle["bundle_hash"],
    }
    if success_probability is not None:
        value["success_probability"] = success_probability
    value.update(overrides)
    value.pop("payload_hash", None)
    value["payload_hash"] = payload_hash(value)
    return value


def _outcome(
    event_id: str,
    *,
    gross_return_percent: float = 2.0,
    horizon: int = 5,
    label_at: str = _LABEL_AT,
    evaluation_class: str = "buy",
    mature: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    metrics = evaluate_decision_metrics(
        gross_return_percent=gross_return_percent if mature else None,
        evaluation_class=evaluation_class,
        fee_policy={
            "status": "available",
            "fee_source": "user_assumption",
            "round_trip_fee_percent": 0.1,
            "fee_calculation": "initial_principal_haircut",
        },
        benchmark_result={
            "available": True,
            "formal_excess_eligible": True,
            "return_percent": 0.0,
        },
    )
    value: dict[str, Any] = {
        "schema_version": "outcome_observation.v2",
        "observation_id": f"{event_id}:T+{horizon}",
        "event_id": event_id,
        "horizon_trading_days": horizon,
        "status": "mature" if mature else "immature",
        "mature": mature,
        "is_terminal": mature,
        "source_available_at": label_at,
        "recorded_at": label_at,
        "metrics": metrics,
    }
    value.update(overrides)
    value.pop("payload_hash", None)
    value["payload_hash"] = payload_hash(value)
    return value


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {str(row["reason"]): int(row["count"]) for row in rows}


def _paired_case(
    seed: str,
    *,
    utility: float,
    risk: float,
    claim_status: str = "clean",
    output_at: str = "2026-01-02T10:05:00+00:00",
    label_at: str = "2026-01-02T11:00:00+00:00",
    replay_available_at: str = "2026-01-02T09:00:00+00:00",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": quality.PAIRED_CASE_SCHEMA_VERSION,
        "frozen_case_hash": hashlib.sha256(seed.encode()).hexdigest(),
        "horizon_trading_days": 5,
        "decision_at": _DECISION_AT,
        "output_at": output_at,
        "label_hash": hashlib.sha256(f"label:{seed}".encode()).hexdigest(),
        "label_available_at": label_at,
        "utility": utility,
        "risk": risk,
        "claim_status": claim_status,
        "replay_refs": [
            {
                "source": "fixture",
                "ref_id": f"case:{seed}",
                "available_at": replay_available_at,
                "first_observed_at": replay_available_at,
                "content_hash": hashlib.sha256(
                    f"case-ref:{seed}".encode()
                ).hexdigest(),
            }
        ],
    }
    return _sign_paired_case(value)


def _sign_paired_case(value: dict[str, Any]) -> dict[str, Any]:
    value.pop("content_hash", None)
    value.pop("output_hash", None)
    value["output_hash"] = _canonical_hash(value)
    value["content_hash"] = _canonical_hash(value)
    return value


def _gate_policy(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": quality.GATE_POLICY_SCHEMA_VERSION,
        "policy_id": "gate.v1",
        "registered_at": "2026-01-02T09:00:00+00:00",
        "min_pairs": 2,
        "minimum_mean_utility_delta": 0.05,
        "maximum_mean_risk_delta": 0.0,
        "maximum_claim_violation_rate": 0.0,
        "maximum_claim_sanitized_rate": 0.5,
    }
    value.update(overrides)
    value["policy_hash"] = _canonical_hash(value)
    return value


def test_no_data_and_invalid_config_are_unavailable_without_division_errors() -> None:
    empty = quality.evaluate_decision_quality([], [], evaluation_as_of=_AS_OF)
    assert empty["status"] == "unavailable"
    assert empty["reason_codes"] == ["formal_decision_events_unavailable"]
    assert empty["overall"]["metrics"]["gross_direction"]["hit_rate_percent"] is None

    invalid = quality.evaluate_decision_quality(
        [],
        [],
        evaluation_as_of=_AS_OF,
        min_calibration_samples=0,
        calibration_bins=0,
        calibration_metric="not-a-metric",
    )
    assert invalid["status"] == "unavailable"
    assert set(invalid["reason_codes"]) == {
        "minimum_calibration_samples_invalid",
        "calibration_bins_invalid",
        "calibration_metric_invalid",
    }
    assert invalid["overall"]["calibration"]["status"] == "unavailable"
    assert invalid["overall"]["calibration"]["reason"] == (
        "evaluation_config_invalid"
    )
    assert invalid["overall"]["calibration"]["ece"] is None
    assert invalid["overall"]["calibration"]["brier"] is None

    no_cutoff = quality.evaluate_decision_quality([_event("future-unknown")], [])
    assert no_cutoff["status"] == "unavailable"
    assert "evaluation_as_of_missing_or_invalid" in no_cutoff["reason_codes"]
    assert _reason_counts(no_cutoff["input_audit"]["event_exclusions"]) == {
        "decision_event_evaluation_boundary_unavailable": 1
    }


def test_strict_exclusions_matching_audit_and_future_pit_fail_closed() -> None:
    valid = _event("valid")
    future_label = _event("future-label")
    future_replay = _event("future-replay")
    future_replay["replay_refs"][0]["available_at"] = (
        "2026-01-03T00:00:00+00:00"
    )
    future_replay["replay_refs"][0]["first_observed_at"] = (
        "2026-01-03T00:00:00+00:00"
    )
    future_replay.pop("payload_hash")
    future_replay["payload_hash"] = payload_hash(future_replay)
    bad_hash = _event("bad-hash")
    bad_hash["action"] = "tampered"
    backfilled = _event("backfilled", is_backfilled=True)
    secondary = _event("secondary", store_authority="fallback")
    future_decision = _event(
        "future-decision",
        decision_at="2026-03-01T00:00:00+00:00",
    )
    valid_outcome = _outcome("valid")
    cutoff_future_outcome = _outcome(
        "future-label",
        label_at="2026-03-01T00:00:00+00:00",
    )
    replay_outcome = _outcome("future-replay")

    result = quality.evaluate_decision_quality(
        [
            valid,
            future_label,
            future_replay,
            bad_hash,
            backfilled,
            secondary,
            future_decision,
        ],
        [valid_outcome, cutoff_future_outcome, replay_outcome],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "available"
    assert result["input_audit"]["valid_event_count"] == 3
    assert result["input_audit"]["matched_terminal_outcome_input_count"] == 2
    assert result["input_audit"]["matched_terminal_outcome_count"] == 1
    event_reasons = _reason_counts(result["input_audit"]["event_exclusions"])
    assert event_reasons == {
        "decision_event_after_evaluation_boundary": 1,
        "decision_event_backfilled": 1,
        "decision_event_not_primary": 1,
        "decision_event_payload_hash_mismatch": 1,
    }
    outcome_reasons = _reason_counts(result["input_audit"]["outcome_exclusions"])
    assert outcome_reasons["outcome_observation_after_evaluation_boundary"] == 1
    records = {
        row["event_id"]: row
        for row in result["input_audit"]["event_horizon_matches"]
    }
    assert records["valid"]["match_status"] == "matched_terminal"
    assert records["future-label"]["match_status"] == "label_unavailable"
    assert records["future-replay"]["match_status"] == "matched_terminal"
    assert records["future-replay"]["replay_status"] == "ineligible"
    assert (
        records["future-replay"]["replay_reason"]
        == "replay_ref_not_point_in_time_eligible"
    )
    excluded_by_id = {
        row["event_id"]: row["reason"]
        for row in result["input_audit"]["event_records"]
        if row["status"] == "excluded"
    }
    assert excluded_by_id["future-decision"] == "decision_event_after_evaluation_boundary"
    future_outcome_record = next(
        row
        for row in result["input_audit"]["outcome_records"]
        if row["event_id"] == "future-label"
    )
    assert future_outcome_record["status"] == "excluded"


def test_replay_ineligible_matched_outcome_cannot_poison_metrics_or_calibration() -> None:
    clean = _event("clean", success_probability=0.9)
    poisoned = _event(
        "poisoned",
        success_probability=0.01,
        model_version="poison-model",
    )
    poisoned["replay_refs"][0]["available_at"] = "2026-01-03T00:00:00+00:00"
    poisoned["replay_refs"][0]["first_observed_at"] = (
        "2026-01-03T00:00:00+00:00"
    )
    poisoned.pop("payload_hash")
    poisoned["payload_hash"] = payload_hash(poisoned)
    result = quality.evaluate_decision_quality(
        [clean, poisoned],
        [
            _outcome("clean", gross_return_percent=2.0),
            _outcome("poisoned", gross_return_percent=-99.0),
        ],
        evaluation_as_of=_AS_OF,
        min_calibration_samples=1,
    )

    assert result["input_audit"]["matched_terminal_outcome_input_count"] == 2
    assert result["input_audit"]["matched_terminal_outcome_count"] == 1
    assert result["overall"]["input_event_horizon_count"] == 2
    assert result["overall"]["event_horizon_count"] == 1
    assert result["overall"]["replay_excluded_event_horizon_count"] == 1
    gross = result["overall"]["metrics"]["gross_direction"]
    assert gross["eligible_count"] == 1
    assert gross["mature_count"] == 1
    assert gross["hit_count"] == 1
    assert gross["miss_count"] == 0
    calibration = result["overall"]["calibration"]
    assert calibration["sample_count"] == 1
    assert calibration["brier"] == pytest.approx(0.01)
    variants = result["stratified"]["variant"]
    assert len(variants) == 1
    assert variants[0]["value"]["model_version"] == "model.v1"


def test_replay_ref_without_content_hash_is_formally_unavailable() -> None:
    event = _event("unhashed")
    event["replay_refs"][0].pop("content_hash")
    event.pop("payload_hash")
    event["payload_hash"] = payload_hash(event)
    result = quality.evaluate_decision_quality(
        [event],
        [_outcome("unhashed")],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["formal_decision_events_unavailable"]
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["match_status"] == "matched_terminal"
    assert match["formal_score_status"] == "excluded_replay_ineligible"
    assert match["replay_reason"] == "replay_ref_hash_missing_or_invalid"
    assert result["overall"]["metrics"]["gross_direction"]["eligible_count"] == 0

    reversed_time = _event(
        "reversed-replay-time",
        replay_refs=[
            {
                "source": "fixture",
                "ref_id": "reversed-time",
                "available_at": "2026-01-02T09:30:00+00:00",
                "first_observed_at": "2026-01-02T09:00:00+00:00",
            }
        ],
    )
    reversed_result = quality.evaluate_decision_quality(
        [reversed_time],
        [_outcome("reversed-replay-time")],
        evaluation_as_of=_AS_OF,
    )
    reversed_match = reversed_result["input_audit"]["event_horizon_matches"][0]
    assert reversed_result["status"] == "unavailable"
    assert reversed_match["replay_reason"] == "replay_ref_time_order_invalid"


def test_arbitrary_placeholder_ref_cannot_impersonate_the_frozen_bundle() -> None:
    event = _event("placeholder-ref")
    event["replay_refs"] = [
        {
            "source": "fixture",
            "ref_id": "unrelated-placeholder",
            "available_at": "2026-01-02T09:00:00+00:00",
            "first_observed_at": "2026-01-02T09:00:00+00:00",
            "content_hash": hashlib.sha256(b"unrelated-placeholder").hexdigest(),
        }
    ]
    event.pop("payload_hash")
    event["payload_hash"] = payload_hash(event)

    result = quality.evaluate_decision_quality(
        [event],
        [_outcome("placeholder-ref")],
        evaluation_as_of=_AS_OF,
    )

    assert result["input_audit"]["valid_event_count"] == 1
    assert result["status"] == "unavailable"
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["replay_reason"] == "replay_refs_not_bound_to_bundle"


def test_outcome_between_decision_clock_and_replay_boundary_is_not_mature() -> None:
    event = _event(
        "between-clocks",
        replay_refs=[
            {
                "source": "fixture",
                "ref_id": "late-request-evidence",
                "available_at": "2026-01-02T09:59:00+00:00",
                "first_observed_at": "2026-01-02T10:02:00+00:00",
            }
        ],
    )
    assert event["decision_at"] == _DECISION_AT
    assert event["replay_bundle"]["recorded_at"] == (
        "2026-01-02T10:02:00+00:00"
    )
    result = quality.evaluate_decision_quality(
        [event],
        [
            _outcome(
                "between-clocks",
                label_at="2026-01-02T10:01:00+00:00",
            )
        ],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "unavailable"
    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_label_not_strictly_after_replay_boundary": 1
    }
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["match_status"] == "metric_contract_excluded"
    assert result["overall"]["metrics"]["gross_direction"]["mature_count"] == 0

    wrapped_event = _event("between-clocks-wrapped", replay_refs=[
        {
            "source": "fixture",
            "ref_id": "late-request-evidence",
            "available_at": "2026-01-02T09:59:00+00:00",
            "first_observed_at": "2026-01-02T10:02:00+00:00",
        }
    ])
    wrapped_outcome = _outcome(
        "between-clocks-wrapped",
        label_at="2026-01-02T10:01:00+00:00",
        recorded_at="2026-01-02T10:03:00+00:00",
    )
    wrapped_result = quality.evaluate_decision_quality(
        [wrapped_event],
        [
            {
                "payload": wrapped_outcome,
                "decision_event_id": "between-clocks-wrapped",
                "observation_id": "between-clocks-wrapped:T+5",
                "horizon_trading_days": 5,
                "status": "mature",
                "is_terminal": True,
                "content_hash": _stored_outcome_content_hash(wrapped_outcome),
                "observed_at": "2026-01-02T10:03:00+00:00",
                "finalized_at": "2026-01-02T10:03:00+00:00",
            }
        ],
        evaluation_as_of=_AS_OF,
    )
    assert _reason_counts(
        wrapped_result["input_audit"]["outcome_exclusions"]
    ) == {"outcome_label_not_strictly_after_replay_boundary": 1}


def test_auxiliary_results_must_follow_their_recorded_knowledge_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_ref = [
        {
            "source": "fixture",
            "ref_id": "late-request-evidence",
            "available_at": "2026-01-02T09:59:00+00:00",
            "first_observed_at": "2026-01-02T10:02:00+00:00",
        }
    ]
    abstention = _event(
        "shadow-between-clocks",
        evaluation_class="observation",
        replay_refs=replay_ref,
    )
    shadow_result = quality.evaluate_decision_quality(
        [abstention],
        [],
        abstention_shadow_labels=[
            _shadow_label(
                "shadow-between-clocks",
                label_available_at="2026-01-02T10:01:00+00:00",
            )
        ],
        evaluation_as_of=_AS_OF,
    )
    assert _reason_counts(
        shadow_result["input_audit"]["shadow_label_exclusions"]
    ) == {
        "abstention_shadow_label_not_strictly_after_replay_boundary": 1
    }

    event = _event("claim-between-clocks", replay_refs=replay_ref)
    claim_result = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim-between-clocks")],
        claim_audits=[
            _claim_wrapper(
                event,
                "clean",
                available_at="2026-01-02T10:01:00+00:00",
                recorded_at="2026-01-02T10:03:00+00:00",
            )
        ],
        evaluation_as_of=_AS_OF,
    )["claim_audits"]
    assert _reason_counts(claim_result["exclusion_reasons"]) == {
        "claim_audit_time_not_point_in_time_eligible": 1
    }

    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        lambda *_args, **_kwargs: pytest.fail("leaked candidate label was delegated"),
    )
    candidate_result = quality.evaluate_decision_quality(
        [_event("candidate-between-clocks")],
        [_outcome("candidate-between-clocks")],
        candidate_selection_cases=[
            {
                "case_id": "candidate-between-clocks",
                "recorded_at": "2026-01-02T10:02:00+00:00",
                "audit": {"decision_at": _DECISION_AT},
                "outcome_labels": {
                    "000001": _candidate_label(
                        "000001",
                        label_available_at="2026-01-02T10:01:00+00:00",
                    )
                },
            }
        ],
        evaluation_as_of=_AS_OF,
    )["candidate_selection"]
    assert candidate_result["evaluations"] == [
        {
            "case_id": "candidate-between-clocks",
            "status": "unavailable",
            "reason": "candidate_selection_label_not_strictly_after_recorded_at",
        }
    ]
    missing_receipt = quality.evaluate_decision_quality(
        [_event("candidate-missing-receipt")],
        [_outcome("candidate-missing-receipt")],
        candidate_selection_cases=[
            {
                "case_id": "candidate-missing-receipt",
                "audit": {"decision_at": _DECISION_AT},
                "outcome_labels": {
                    "000001": _candidate_label("000001")
                },
            }
        ],
        evaluation_as_of=_AS_OF,
    )["candidate_selection"]
    assert missing_receipt["evaluations"][0]["reason"] == (
        "candidate_selection_recorded_at_missing_or_invalid"
    )


def test_legacy_v2_event_is_audited_but_never_formally_scored() -> None:
    event = _event("legacy-forward-only")
    for field in (
        "quality_contract_version",
        "replay_contract_required",
        "replay_bundle",
        "replay_bundle_hash",
        "variant_manifest",
        "variant_hash",
    ):
        event.pop(field, None)
    event.pop("payload_hash")
    event["payload_hash"] = payload_hash(event)

    result = quality.evaluate_decision_quality(
        [event],
        [_outcome("legacy-forward-only")],
        evaluation_as_of=_AS_OF,
    )

    assert result["input_audit"]["valid_event_count"] == 1
    assert result["status"] == "unavailable"
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["match_status"] == "matched_terminal"
    assert match["replay_reason"] == "replay_bundle_missing"


def test_missing_labels_never_become_misses_and_four_metrics_keep_coverage() -> None:
    events = [_event("hit", success_probability=0.8), _event("missing")]
    result = quality.evaluate_decision_quality(
        events,
        [_outcome("hit")],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "available"
    assert result["overall"]["label_coverage_percent"] == 50.0
    for metric_name in (
        "gross_direction",
        "positive_net_return",
        "gross_excess",
        "net_excess",
    ):
        metric = result["overall"]["metrics"][metric_name]
        assert metric == {
            "eligible_count": 2,
            "mature_count": 1,
            "unavailable_count": 1,
            "hit_count": 1,
            "miss_count": 0,
            "coverage_percent": 50.0,
            "hit_rate_percent": 100.0,
        }
    assert result["input_audit"]["event_horizon_matches"][1]["match_status"] in {
        "matched_terminal",
        "label_unavailable",
    }
    match_by_id = {
        row["event_id"]: row["match_status"]
        for row in result["input_audit"]["event_horizon_matches"]
    }
    assert match_by_id == {"hit": "matched_terminal", "missing": "label_unavailable"}


def test_calibration_uses_only_explicit_probability_not_confidence_buckets() -> None:
    events = [
        _event("positive", success_probability=0.8),
        _event("negative", success_probability=0.2),
        _event("qualitative", confidence_level="high"),
    ]
    outcomes = [
        _outcome("positive", gross_return_percent=2.0),
        _outcome("negative", gross_return_percent=-2.0),
        _outcome("qualitative", gross_return_percent=2.0),
    ]
    result = quality.evaluate_decision_quality(
        events,
        outcomes,
        evaluation_as_of=_AS_OF,
        min_calibration_samples=2,
        calibration_bins=2,
    )

    calibration = result["overall"]["calibration"]
    assert calibration["status"] == "available"
    assert calibration["explicit_probability_count"] == 2
    assert calibration["sample_count"] == 2
    assert calibration["ece"] == pytest.approx(0.2)
    assert calibration["brier"] == pytest.approx(0.04)

    unavailable = quality.evaluate_decision_quality(
        [_event("qualitative-only", confidence="high")],
        [_outcome("qualitative-only")],
        evaluation_as_of=_AS_OF,
        min_calibration_samples=1,
    )
    assert unavailable["overall"]["calibration"]["status"] == "unavailable"
    assert unavailable["overall"]["calibration"]["ece"] is None
    assert unavailable["overall"]["calibration"]["brier"] is None


def test_abstention_quality_requires_complete_explicit_shadow_labels() -> None:
    event = _event("wait", evaluation_class="observation")
    original = copy.deepcopy(event)
    missing = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
    )
    assert missing["status"] == "unavailable"
    assert missing["overall"]["abstention"]["decision_coverage_percent"] == 0.0
    assert missing["overall"]["abstention"]["quality_status"] == "unavailable"
    assert missing["overall"]["abstention"]["correct_abstention_rate_percent"] is None

    labelled = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
        abstention_shadow_labels=[_shadow_label("wait")],
    )
    assert labelled["status"] == "available"
    assert labelled["overall"]["abstention"]["quality_status"] == "available"
    assert labelled["overall"]["abstention"]["correct_abstention_rate_percent"] == 100.0
    assert event == original

    future = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
        abstention_shadow_labels=[
            _shadow_label(
                "wait",
                label_available_at="2026-03-01T00:00:00+00:00",
            )
        ],
    )
    assert future["status"] == "unavailable"
    assert _reason_counts(future["input_audit"]["shadow_label_exclusions"]) == {
        "abstention_shadow_label_after_evaluation_boundary": 1
    }

    mutable_ref = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
        abstention_shadow_labels=[
            _shadow_label("wait", source_ref="mutable-observation-id")
        ],
    )
    assert mutable_ref["status"] == "unavailable"
    assert _reason_counts(
        mutable_ref["input_audit"]["shadow_label_exclusions"]
    ) == {"abstention_shadow_label_source_ref_invalid": 1}

    tampered_shadow = _shadow_label("wait")
    tampered_shadow["beneficial"] = False
    tampered = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
        abstention_shadow_labels=[tampered_shadow],
    )
    assert tampered["status"] == "unavailable"
    assert _reason_counts(
        tampered["input_audit"]["shadow_label_exclusions"]
    ) == {"abstention_shadow_label_hash_mismatch": 1}


def test_claim_audit_is_aggregate_only_and_candidate_ranking_is_delegated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object, int, str]] = []

    def fake_candidate_evaluator(
        audit: object,
        labels: object,
        *,
        k: int,
        universe_stage: str,
    ) -> dict[str, Any]:
        calls.append((audit, labels, k, universe_stage))
        return {"status": "available", "reason": None, "precision_at_k": 1.0}

    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        fake_candidate_evaluator,
    )
    audit = {"frozen": True, "decision_at": _DECISION_AT}
    labels = {"000001": _candidate_label("000001")}
    future_labels = copy.deepcopy(labels)
    future_labels["000001"]["label_available_at"] = (
        "2026-03-01T00:00:00+00:00"
    )
    _sign_candidate_label(future_labels["000001"])
    mutable_ref_labels = copy.deepcopy(labels)
    mutable_ref_labels["000001"]["source_ref"] = "mutable-observation-id"
    _sign_candidate_label(mutable_ref_labels["000001"])
    tampered_labels = copy.deepcopy(labels)
    tampered_labels["000001"]["utility"] = 999.0
    event = _event("claim")
    tampered_claim = _claim_wrapper(event, "clean")
    tampered_claim["audit"]["status"] = "violation"
    secret = "RAW-CLAIM-MUST-NOT-LEAK"
    result = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim")],
        evaluation_as_of=_AS_OF,
        claim_audits=[
            _claim_wrapper(event, "clean"),
            _claim_wrapper(event, "sanitized"),
            _claim_wrapper(event, "violation"),
            {"status": "clean", "original_text": secret},
            tampered_claim,
        ],
        candidate_selection_cases=[
            {
                "case_id": "case-1",
                "recorded_at": _CANDIDATE_RECORDED_AT,
                "audit": audit,
                "outcome_labels": labels,
                "k": 2,
                "universe_stage": "final",
            },
            {
                "case_id": "future-case",
                "recorded_at": _CANDIDATE_RECORDED_AT,
                "audit": audit,
                "outcome_labels": future_labels,
            },
            {
                "case_id": "mutable-ref-case",
                "recorded_at": _CANDIDATE_RECORDED_AT,
                "audit": audit,
                "outcome_labels": mutable_ref_labels,
            },
            {
                "case_id": "tampered-label-case",
                "recorded_at": _CANDIDATE_RECORDED_AT,
                "audit": audit,
                "outcome_labels": tampered_labels,
            },
        ],
    )

    claim_summary = result["claim_audits"]
    assert claim_summary["status"] == "partial"
    assert claim_summary["audit_count"] == 5
    assert claim_summary["classified_count"] == 3
    assert claim_summary["unclassified_count"] == 2
    assert claim_summary["coverage_percent"] == 60.0
    assert claim_summary["clean_count"] == 1
    assert claim_summary["sanitized_count"] == 1
    assert claim_summary["violation_count"] == 1
    assert claim_summary["raw_claims_included"] is False
    assert _reason_counts(claim_summary["exclusion_reasons"]) == {
        "claim_audit_hash_mismatch": 1,
        "claim_audit_wrapper_schema_invalid": 1,
    }
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert result["candidate_selection"]["ranking_algorithm"] == (
        "delegated_to_candidate_selection_audit"
    )
    assert result["candidate_selection"]["status"] == "unavailable"
    assert result["candidate_selection"]["diagnostic_status"] == "partial"
    assert result["candidate_selection"]["formal_case_count"] == 0
    assert result["candidate_selection"]["pit_eligible_case_count"] == 1
    assert result["candidate_selection"]["metric_available_case_count"] == 1
    assert calls == [(audit, labels, 2, "final")]
    candidate_rows = {
        row["case_id"]: row for row in result["candidate_selection"]["evaluations"]
    }
    assert candidate_rows["future-case"] == {
        "case_id": "future-case",
        "status": "unavailable",
        "reason": "candidate_selection_label_after_evaluation_boundary",
    }
    assert candidate_rows["mutable-ref-case"] == {
        "case_id": "mutable-ref-case",
        "status": "unavailable",
        "reason": "candidate_selection_label_source_ref_invalid",
    }
    assert candidate_rows["tampered-label-case"] == {
        "case_id": "tampered-label-case",
        "status": "unavailable",
        "reason": "candidate_selection_label_hash_mismatch",
    }

    bare = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim")],
        evaluation_as_of=_AS_OF,
        claim_audits=[{"status": "clean"}],
    )["claim_audits"]
    assert bare["status"] == "unavailable"
    assert bare["classified_count"] == 0
    assert bare["unclassified_count"] == 1
    assert bare["clean_count"] == 0


def test_formal_candidate_metrics_are_weighted_aggregated_and_receipt_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric_by_seed = {
        "one": (2, 3, 0.8, 1.0),
        "two": (1, 3, 0.6, 3.0),
    }

    def fake_candidate_evaluator(audit, _labels, **_kwargs):
        numerator, denominator, ndcg, regret = metric_by_seed[audit["metric_seed"]]
        return {
            "status": "available",
            "reason": None,
            "coverage": {
                "mature_label_count": 5,
                "universe_count": 5,
                "top_k_mature_label_count": 3,
                "top_k_count": 3,
                "selected_top_k_count": 3,
            },
            "precision_at_k": {
                "status": "available",
                "value": numerator / denominator,
                "numerator": numerator,
                "denominator": denominator,
            },
            "ndcg_at_k": {"status": "available", "value": ndcg},
            "regret_at_k": {
                "status": "available",
                "value": regret,
                "utility_basis": "total_return_percent_before_costs",
            },
        }

    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        fake_candidate_evaluator,
    )
    cases = [
        _formal_candidate_case(
            "one",
            decision_at="2026-01-02T10:00:00+00:00",
            label_available_at="2026-01-25T10:00:00+00:00",
            label_storage_created_at="2026-01-25T10:00:01+00:00",
        ),
        _formal_candidate_case(
            "two",
            decision_at="2026-01-03T10:00:00+00:00",
            label_available_at="2026-01-26T10:00:00+00:00",
            label_storage_created_at="2026-01-26T10:00:01+00:00",
        ),
    ]
    candidate = quality.evaluate_decision_quality(
        [],
        [],
        candidate_selection_cases=cases,
        evaluation_as_of="2026-02-01T00:00:00+00:00",
    )["candidate_selection"]

    aggregate = candidate["aggregate"]
    assert aggregate["precision_at_k"]["macro_average"] == 0.5
    assert aggregate["precision_at_k"]["micro_average"] == 0.5
    assert aggregate["ndcg_at_k"]["mean"] == 0.7
    assert aggregate["regret_at_k"]["mean"] == 2.0
    assert aggregate["regret_at_k"]["median"] == 2.0
    assert aggregate["regret_at_k"]["utility_basis"] == (
        "total_return_percent_before_costs"
    )
    assert aggregate["coverage"]["universe_label_coverage_percent"] == 100.0
    assert aggregate["coverage"]["top_k_label_coverage_percent"] == 100.0
    assert candidate["readiness"]["status"] == "insufficient_data"
    assert len(candidate["stratified"]) == 1
    assert candidate["stratified"][0]["dimensions"][
        "provider_adapter_stratum_hash"
    ] == cases[0]["provider_adapter_stratum_hash"]
    assert candidate["automatic_promotion_allowed"] is False

    forged_receipt = copy.deepcopy(cases[0])
    forged_receipt["label_storage_created_at"] = "2026-01-25T09:59:59+00:00"
    blocked = quality.evaluate_decision_quality(
        [],
        [],
        candidate_selection_cases=[forged_receipt],
        evaluation_as_of="2026-02-01T00:00:00+00:00",
    )["candidate_selection"]
    assert blocked["formal_pit_eligible_case_count"] == 0
    assert blocked["evaluations"][0]["reason"] == (
        "candidate_selection_label_storage_receipt_mismatch"
    )


def test_candidate_provider_runtime_metadata_is_a_formal_stratum_dimension() -> None:
    first = _formal_candidate_case(
        "runtime-one",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    second = _formal_candidate_case(
        "runtime-two",
        decision_at="2026-01-03T10:00:00+00:00",
        label_available_at="2026-01-26T10:00:00+00:00",
        label_storage_created_at="2026-01-26T10:00:01+00:00",
    )
    metric = {
        "status": "available",
        "coverage": {
            "mature_label_count": 1,
            "universe_count": 1,
            "top_k_mature_label_count": 1,
            "top_k_count": 1,
            "selected_top_k_count": 1,
        },
        "precision_at_k": {
            "status": "available",
            "value": 1.0,
            "numerator": 1,
            "denominator": 1,
        },
        "ndcg_at_k": {"status": "available", "value": 1.0},
        "regret_at_k": {
            "status": "available",
            "value": 0.0,
            "utility_basis": "total_return_percent_before_costs",
        },
    }
    same_runtime = quality._candidate_selection_strata(
        [(first, first["audit"], metric), (second, second["audit"], metric)]
    )
    assert len(same_runtime) == 1

    old_refs = second["provider_receipt_refs"]
    changed_refs = copy.deepcopy(old_refs)
    for ref in changed_refs:
        ref["adapter_library_version"] = "fixture-next"
        ref["adapter_python_version"] = "3.13.fixture"
    second["provider_receipt_refs"] = changed_refs
    second["provider_receipt_manifest_hash"] = _canonical_hash(changed_refs)
    second["provider_adapter_stratum"] = candidate_provider_adapter_stratum(
        changed_refs
    )
    second["provider_adapter_stratum_hash"] = (
        candidate_provider_adapter_stratum_hash(changed_refs)
    )
    changed_by_id = {ref["receipt_id"]: ref for ref in changed_refs}
    for label in second["outcome_labels"].values():
        source_ref = label["source_ref"]
        prior = source_ref["provider_receipt_ref"]
        source_ref["provider_receipt_ref"] = changed_by_id[prior["receipt_id"]]
        _sign_candidate_label(label)

    split_runtime = quality._candidate_selection_strata(
        [(first, first["audit"], metric), (second, second["audit"], metric)]
    )
    assert len(split_runtime) == 2
    assert {
        tuple(
            (row["adapter_library_version"], row["adapter_python_version"])
            for row in stratum["dimensions"]["provider_adapter_stratum"]
        )
        for stratum in split_runtime
    } == {
        (("fixture", "3.12.fixture"), ("fixture", "3.12.fixture")),
        (("fixture-next", "3.13.fixture"),) * 2,
    }


@pytest.mark.parametrize(
    ("case", "expected_reason", "expected_formal_status"),
    [
        (
            _receipt_pending_candidate_case(
                "pending-audit",
                audit_status="pending",
                outcome_status="absent",
            ),
            "candidate_selection_audit_commit_receipt_pending",
            "receipt_pending",
        ),
        (
            _receipt_pending_candidate_case(
                "pending-outcome",
                outcome_status="pending",
            ),
            "candidate_selection_outcome_commit_receipt_pending",
            "receipt_pending",
        ),
        (
            _receipt_pending_candidate_case("absent-outcome"),
            "candidate_selection_outcome_artifact_absent",
            "receipt_pending",
        ),
        (
            _late_candidate_case("late-audit"),
            "candidate_selection_audit_commit_receipt_late",
            "receipt_policy_gap",
        ),
    ],
)
def test_candidate_receipt_pending_cases_remain_formal_denominators_without_metrics(
    case: dict[str, Any],
    expected_reason: str,
    expected_formal_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even a buggy/decorated downstream evaluator cannot turn a receipt-gated
    # case into a mature metric before its source visibility is proven.
    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        lambda *_args, **_kwargs: {
            "status": "available",
            "reason": None,
            "coverage": {
                "mature_label_count": 9,
                "universe_count": 10,
                "top_k_mature_label_count": 3,
                "top_k_count": 3,
                "selected_top_k_count": 3,
            },
            "precision_at_k": {
                "status": "available",
                "value": 1.0,
                "numerator": 3,
                "denominator": 3,
            },
            "ndcg_at_k": {"status": "available", "value": 1.0},
            "regret_at_k": {"status": "available", "value": 0.0},
        },
    )
    candidate = quality.evaluate_decision_quality(
        [],
        [],
        candidate_selection_cases=[case],
        evaluation_as_of=_AS_OF,
    )["candidate_selection"]

    assert candidate["status"] == "unavailable"
    assert candidate["formal_case_count"] == 1
    assert candidate["formal_invalid_case_count"] == 0
    assert candidate["formal_pit_eligible_case_count"] == 1
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["metric_available_case_coverage_percent"] == 0.0
    assert candidate["aggregate"]["case_count"] == 1
    assert candidate["aggregate"]["pit_eligible_case_count"] == 1
    assert candidate["aggregate"]["precision_at_k"]["status"] == "unavailable"
    assert candidate["aggregate"]["coverage"]["mature_label_count"] == 0
    row = candidate["evaluations"][0]
    assert row["status"] == "unavailable"
    assert row["reason"] == expected_reason
    assert row["formal_status"] == expected_formal_status
    assert candidate["evidence_scope"] == (
        "source_verified_provider_and_post_commit_receipts"
    )
    assert candidate["automatic_promotion_allowed"] is False


def test_candidate_late_status_cannot_be_forged_with_a_timely_receipt() -> None:
    case = _late_candidate_case("forged-late")
    recorded_at = datetime.fromisoformat(str(case["recorded_at"]))
    case["audit_commit_receipt_source_visible_at"] = (
        recorded_at + timedelta(seconds=300)
    ).isoformat()

    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]

    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_audit_late_receipt_delay_not_exceeded"
    )


def test_candidate_verified_status_cannot_hide_a_late_receipt() -> None:
    case = _formal_candidate_case(
        "forged-verified-late",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    source_created_at = datetime.fromisoformat(
        str(case["audit_source_row_created_at"])
    )
    case["audit_commit_receipt_source_visible_at"] = (
        source_created_at + timedelta(seconds=301)
    ).isoformat()

    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]

    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_audit_verified_receipt_delay_exceeded"
    )


def test_candidate_capture_late_status_cannot_be_forged_from_timely_source() -> None:
    case = _receipt_pending_candidate_case("forged-capture-late")
    case["capture_status"] = "capture_late"
    case["capture_reason"] = "source_capture_delay_exceeded"
    case["capture_reason_hash"] = _canonical_hash(
        {"reason": "source_capture_delay_exceeded"}
    )

    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]

    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_capture_late_state_invalid"
    )


def test_candidate_pending_receipt_cannot_carry_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _receipt_pending_candidate_case(
        "pending-with-label",
        outcome_status="pending",
    )
    source = _formal_candidate_case(
        "pending-label-source",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    case["outcome_labels"] = source["outcome_labels"]
    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        lambda *_args, **_kwargs: pytest.fail("pending labels were delegated"),
    )

    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]

    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_receipt_pending_label_conflict"
    )


@pytest.mark.parametrize(
    ("field", "expected_reason"),
    [
        (
            "audit_commit_receipt_id",
            "candidate_selection_audit_commit_receipt_invalid",
        ),
        (
            "outcome_commit_receipt_content_hash",
            "candidate_selection_outcome_commit_receipt_invalid",
        ),
    ],
)
def test_candidate_verified_receipts_require_complete_content_addressed_identity(
    field: str,
    expected_reason: str,
) -> None:
    case = _formal_candidate_case(
        f"missing-{field}",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    case[field] = None
    row = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]["evaluations"][0]
    assert row["reason"] == expected_reason


def test_candidate_label_visibility_must_equal_post_commit_receipt() -> None:
    case = _formal_candidate_case(
        "label-clock-conflict",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    label = case["outcome_labels"]["000001"]
    label["label_available_at"] = "2026-01-25T10:00:00+00:00"
    _sign_candidate_label(label)

    row = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]["evaluations"][0]
    assert row["reason"] == (
        "candidate_selection_label_not_bound_to_post_commit_receipt"
    )


@pytest.mark.parametrize(
    ("tamper", "expected_reason"),
    [
        ("count", "candidate_selection_provider_receipt_count_mismatch"),
        (
            "manifest",
            "candidate_selection_provider_receipt_manifest_hash_mismatch",
        ),
        (
            "label_ref",
            "candidate_selection_label_provider_receipt_ref_invalid",
        ),
        (
            "drop_calendar_rehash",
            "candidate_selection_calendar_provider_receipt_set_invalid",
        ),
        (
            "unknown_provider_rehash",
            "candidate_selection_provider_receipt_ref_invalid",
        ),
    ],
)
def test_candidate_provider_receipt_manifest_and_label_binding_fail_closed(
    tamper: str,
    expected_reason: str,
) -> None:
    case = _formal_candidate_case(
        f"provider-{tamper}",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    if tamper == "count":
        case["provider_receipt_count"] = (
            int(case["provider_receipt_count"]) + 1
        )
    elif tamper == "manifest":
        case["provider_receipt_manifest_hash"] = "f" * 64
    elif tamper == "label_ref":
        label = case["outcome_labels"]["000001"]
        forged = dict(label["source_ref"]["provider_receipt_ref"])
        forged["request_hash"] = "e" * 64
        label["source_ref"]["provider_receipt_ref"] = forged
        _sign_candidate_label(label)
    elif tamper == "drop_calendar_rehash":
        case["provider_receipt_refs"] = [
            ref
            for ref in case["provider_receipt_refs"]
            if ref["provider"] != "akshare.tool_trade_date_hist_sina"
        ]
        case["provider_receipt_count"] = len(case["provider_receipt_refs"])
        case["provider_receipt_manifest_hash"] = _canonical_hash(
            case["provider_receipt_refs"]
        )
        case["provider_adapter_stratum"] = candidate_provider_adapter_stratum(
            case["provider_receipt_refs"]
        )
        case["provider_adapter_stratum_hash"] = (
            candidate_provider_adapter_stratum_hash(
                case["provider_receipt_refs"]
            )
        )
    else:
        unknown = _candidate_provider_ref(
            "unknown-provider",
            completed_at="2026-01-20T00:00:00+00:00",
            provider="unknown.market.adapter",
            operation="unknown_operation",
        )
        case["provider_receipt_refs"] = sorted(
            [*case["provider_receipt_refs"], unknown],
            key=lambda ref: (
                ref["provider"], ref["operation"], ref["receipt_id"]
            ),
        )
        case["provider_receipt_count"] = len(case["provider_receipt_refs"])
        case["provider_receipt_manifest_hash"] = _canonical_hash(
            case["provider_receipt_refs"]
        )

    row = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]["evaluations"][0]
    assert row["reason"] == expected_reason


def test_candidate_source_receipts_after_cutoff_are_rejected() -> None:
    case = _formal_candidate_case(
        "future-outcome-receipt",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-03-01T00:00:00+00:00",
    )
    label = case["outcome_labels"]["000001"]
    label["label_available_at"] = "2026-03-01T00:00:00+00:00"
    _sign_candidate_label(label)

    row = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]["evaluations"][0]
    assert row["reason"] == (
        "candidate_selection_outcome_receipt_after_evaluation_boundary"
    )


def test_legacy_candidate_case_is_diagnostic_only_not_formal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _formal_candidate_case(
        "legacy-v1",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    case["schema_version"] = quality.CANDIDATE_SELECTION_CASE_SCHEMA_VERSION_V1
    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        lambda *_args, **_kwargs: {"status": "available", "reason": None},
    )

    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]
    assert candidate["diagnostic_status"] == "available"
    assert candidate["status"] == "unavailable"
    assert candidate["formal_case_count"] == 0
    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["aggregate"]["case_count"] == 0
    assert candidate["readiness"]["status"] == "insufficient_data"
    assert candidate["evaluations"][0]["formal_status"] == (
        "legacy_diagnostic_only"
    )


def test_candidate_case_identity_and_promotion_tampering_fail_closed() -> None:
    case = _formal_candidate_case(
        "case-tamper",
        decision_at="2026-01-02T10:00:00+00:00",
        label_available_at="2026-01-25T10:00:00+00:00",
        label_storage_created_at="2026-01-25T10:00:01+00:00",
    )
    case["automatic_promotion_allowed"] = True
    candidate = quality.evaluate_decision_quality(
        [], [], candidate_selection_cases=[case], evaluation_as_of=_AS_OF
    )["candidate_selection"]
    assert candidate["formal_invalid_case_count"] == 1
    assert candidate["formal_pit_eligible_case_count"] == 0
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_formal_case_contract_invalid"
    )
    assert candidate["automatic_promotion_allowed"] is False


def test_candidate_readiness_is_complete_and_policy_stratum_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_candidate_evaluator(audit, _labels, **_kwargs):
        complete = not str(audit["metric_seed"]).startswith("partial")
        return {
            "status": "available" if complete else "partial",
            "reason": None if complete else "outcome_labels_incomplete",
            "coverage": {
                "mature_label_count": 3,
                "universe_count": 3,
                "top_k_mature_label_count": 3,
                "top_k_count": 3,
                "selected_top_k_count": 3,
            },
            "precision_at_k": {
                "status": "available",
                "value": 1.0,
                "numerator": 3,
                "denominator": 3,
            },
            "ndcg_at_k": {
                "status": "available" if complete else "unavailable",
                "value": 1.0 if complete else None,
            },
            "regret_at_k": {
                "status": "available" if complete else "unavailable",
                "value": 0.0 if complete else None,
                "utility_basis": "total_return_percent_before_costs",
            },
        }

    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        fake_candidate_evaluator,
    )
    start = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    old_cases = [
        _formal_candidate_case(
            f"old-{index}",
            decision_at=(start + timedelta(days=index)).isoformat(),
            label_available_at="2026-04-01T00:00:00+00:00",
            label_storage_created_at="2026-04-01T00:00:01+00:00",
            selection_policy_version="selection.old",
        )
        for index in range(60)
    ]
    new_case = _formal_candidate_case(
        "new-0",
        decision_at="2026-03-15T02:00:00+00:00",
        label_available_at="2026-04-01T00:00:00+00:00",
        label_storage_created_at="2026-04-01T00:00:01+00:00",
        selection_policy_version="selection.new",
    )
    candidate = quality.evaluate_decision_quality(
        [],
        [],
        candidate_selection_cases=[*old_cases, new_case],
        evaluation_as_of="2026-05-01T00:00:00+00:00",
    )["candidate_selection"]
    strata = {
        row["dimensions"]["selection_policy_version"]: row
        for row in candidate["stratified"]
    }
    assert candidate["status"] == "partial"
    assert strata["selection.old"]["readiness"]["status"] == (
        "eligible_for_human_review"
    )
    assert strata["selection.new"]["readiness"]["status"] == "insufficient_data"
    assert candidate["readiness"]["status"] == "stratified_only"
    assert candidate["aggregate"]["status"] == "stratified_only"
    assert candidate["aggregate"]["reason"] == "mixed_candidate_policy_strata"
    assert candidate["aggregate"]["precision_at_k"]["status"] == "unavailable"
    assert candidate["aggregate"]["precision_at_k"]["macro_average"] is None
    assert all(
        row["aggregate"]["precision_at_k"]["status"] == "available"
        for row in candidate["stratified"]
    )

    partial_cases = [
        _formal_candidate_case(
            f"partial-{index}",
            decision_at=(start + timedelta(days=index)).isoformat(),
            label_available_at="2026-04-01T00:00:00+00:00",
            label_storage_created_at="2026-04-01T00:00:01+00:00",
        )
        for index in range(60)
    ]
    partial = quality.evaluate_decision_quality(
        [],
        [],
        candidate_selection_cases=partial_cases,
        evaluation_as_of="2026-05-01T00:00:00+00:00",
    )["candidate_selection"]
    assert partial["readiness"]["status"] == "shadow_only"
    assert partial["readiness"]["fully_available_case_count"] == 0


@pytest.mark.parametrize(
    "field",
    [
        "binary_relevance",
        "relevance",
        "utility",
        "mature",
        "label_available_at",
        "source_ref",
    ],
)
def test_candidate_label_semantic_tampering_fails_canonical_hash(
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quality,
        "evaluate_candidate_selection_audit",
        lambda *_args, **_kwargs: pytest.fail("tampered label was delegated"),
    )
    label = _candidate_label("000001")
    if field == "binary_relevance":
        label[field] = False
    elif field in {"relevance", "utility"}:
        label[field] = 9.0
    elif field == "mature":
        label[field] = False
    elif field == "label_available_at":
        label[field] = "2026-01-16T10:00:00+00:00"
    else:
        label[field] = _source_ref("candidate:tampered")
    event = _event(f"candidate-{field}")
    result = quality.evaluate_decision_quality(
        [event],
        [_outcome(event["event_id"])],
        evaluation_as_of=_AS_OF,
        candidate_selection_cases=[
            {
                "case_id": "tampered",
                "recorded_at": _CANDIDATE_RECORDED_AT,
                "audit": {"decision_at": _DECISION_AT},
                "outcome_labels": {"000001": label},
            }
        ],
    )
    row = result["candidate_selection"]["evaluations"][0]
    assert row["status"] == "unavailable"
    assert row["reason"] == "candidate_selection_label_hash_mismatch"


@pytest.mark.parametrize(
    "field",
    ["beneficial", "mature", "label_available_at", "source_ref"],
)
def test_shadow_label_semantic_tampering_fails_canonical_hash(field: str) -> None:
    label = _shadow_label("shadow-tamper")
    if field == "beneficial":
        label[field] = False
    elif field == "mature":
        label[field] = False
    elif field == "label_available_at":
        label[field] = "2026-01-16T10:00:00+00:00"
    else:
        label[field] = _source_ref("shadow:tampered")
    event = _event("shadow-tamper", evaluation_class="observation")
    result = quality.evaluate_decision_quality(
        [event],
        [],
        evaluation_as_of=_AS_OF,
        abstention_shadow_labels=[label],
    )
    assert _reason_counts(result["input_audit"]["shadow_label_exclusions"]) == {
        "abstention_shadow_label_hash_mismatch": 1
    }


def test_claim_wrapper_hash_and_future_time_are_fail_closed() -> None:
    event = _event("claim-wrapper")
    tampered = _claim_wrapper(event, "clean")
    tampered["recorded_at"] = "2026-01-02T10:07:00+00:00"
    tampered_result = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim-wrapper")],
        evaluation_as_of=_AS_OF,
        claim_audits=[tampered],
    )["claim_audits"]
    assert tampered_result["status"] == "unavailable"
    assert _reason_counts(tampered_result["exclusion_reasons"]) == {
        "claim_audit_wrapper_hash_mismatch": 1
    }

    future = _claim_wrapper(
        event,
        "clean",
        available_at="2026-03-01T00:00:00+00:00",
        recorded_at="2026-03-01T00:01:00+00:00",
    )
    future_result = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim-wrapper")],
        evaluation_as_of=_AS_OF,
        claim_audits=[future],
    )["claim_audits"]
    assert future_result["status"] == "unavailable"
    assert _reason_counts(future_result["exclusion_reasons"]) == {
        "claim_audit_time_not_point_in_time_eligible": 1
    }

    malformed = _claim_wrapper(event, "clean")
    malformed["audit"].pop("reason_counts")
    malformed["audit"].pop("audit_hash")
    malformed["audit"]["audit_hash"] = _canonical_hash(malformed["audit"])
    malformed.pop("content_hash")
    malformed["content_hash"] = _canonical_hash(malformed)
    malformed_result = quality.evaluate_decision_quality(
        [event],
        [_outcome("claim-wrapper")],
        evaluation_as_of=_AS_OF,
        claim_audits=[malformed],
    )["claim_audits"]
    assert malformed_result["status"] == "unavailable"
    assert _reason_counts(malformed_result["exclusion_reasons"]) == {
        "claim_audit_reason_counts_invalid": 1
    }


def test_required_strata_do_not_mix_samples_and_missing_is_not_inferred() -> None:
    first = _event(
        "equity-risk-on",
        fund_type="equity",
        market_regime="risk_on",
        data_completeness="complete",
        model_version="model.a",
    )
    second = _event(
        "bond-risk-off",
        evaluation_class="bearish",
        horizons=[20],
        source_type="discovery",
        decision_kind="discovery",
        event_type="fund_discovery_decision",
        action="sell",
        final_action="sell",
        fund_type="bond",
        market_regime="risk_off",
        data_completeness="partial",
        model_version="model.b",
    )
    missing = _event("missing-strata", horizons=[60])
    for field in ("fund_type", "market_regime", "data_completeness"):
        missing.pop(field)
    missing.pop("payload_hash")
    missing["payload_hash"] = payload_hash(missing)
    result = quality.evaluate_decision_quality(
        [first, second, missing],
        [
            _outcome("equity-risk-on"),
            _outcome(
                "bond-risk-off",
                horizon=20,
                evaluation_class="bearish",
                gross_return_percent=-2.0,
            ),
            _outcome("missing-strata", horizon=60),
        ],
        evaluation_as_of=_AS_OF,
    )

    for key, expected_values in {
        "fund_type": {"equity", "bond", "missing"},
        "market_regime": {"risk_on", "risk_off", "missing"},
        "data_completeness": {"complete", "partial", "missing"},
    }.items():
        groups = result["stratified"][key]
        assert {group["value"] for group in groups} == expected_values
        assert all(group["event_horizon_count"] == 1 for group in groups)
        assert all(group["matched_terminal_outcome_count"] == 1 for group in groups)

    decision_groups = {
        group["value"]: group["event_horizon_count"]
        for group in result["stratified"]["decision_kind"]
    }
    action_groups = {
        group["value"]: group["event_horizon_count"]
        for group in result["stratified"]["action"]
    }
    horizon_groups = {
        group["value"]: group["event_horizon_count"]
        for group in result["stratified"]["horizon"]
    }
    assert decision_groups == {"daily": 2, "discovery": 1}
    assert action_groups == {"buy": 2, "sell": 1}
    assert horizon_groups == {5: 1, 20: 1, 60: 1}

    variants = result["stratified"]["variant"]
    assert len(variants) == 3
    assert all(group["event_horizon_count"] == 1 for group in variants)
    variant = variants[0]["value"]
    assert {
        "model_version",
        "model_hash",
        "prompt_version",
        "prompt_hash",
        "strategy_version",
        "strategy_hash",
        "data_version",
        "data_hash",
        "fee_model_version",
        "fee_model_hash",
    } <= set(variant)


def test_hash_and_storage_contract_conflicts_are_excluded() -> None:
    event = _event("wrapped")
    conflicting_wrapper = {
        "payload": event,
        "event_id": "different",
        "content_hash": decision_event_content_hash(event),
    }
    tampered_outcome = _outcome("wrapped")
    tampered_outcome["metrics"]["gross_direction"]["hit"] = False
    result = quality.evaluate_decision_quality(
        [conflicting_wrapper],
        [tampered_outcome],
        evaluation_as_of=_AS_OF,
    )
    assert result["status"] == "unavailable"
    assert _reason_counts(result["input_audit"]["event_exclusions"]) == {
        "decision_event_storage_contract_conflict": 1
    }
    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_observation_payload_hash_mismatch": 1
    }


def test_persisted_event_and_outcome_wrappers_are_verified_without_extra_io() -> None:
    persisted_event = normalize_decision_event(_event("persisted"))
    event_wrapper = {
        "payload": persisted_event,
        "event_id": persisted_event["event_id"],
        "schema_version": persisted_event["schema_version"],
        "event_type": persisted_event["event_type"],
        "source_type": persisted_event["source_type"],
        "decision_at": persisted_event["decision_at"],
        "fund_code": persisted_event["fund_code"],
        "final_action": persisted_event["final_action"],
        "action_category": persisted_event["action_category"],
        "eligible": persisted_event["eligible"],
        "is_backfilled": persisted_event["is_backfilled"],
        "metric_eligible": persisted_event["metric_eligible"],
        "created_at": "2026-01-02T10:01:00+00:00",
        "content_hash": decision_event_content_hash(persisted_event),
    }
    persisted_outcome = _outcome("persisted")
    persisted_outcome.pop("payload_hash")
    outcome_wrapper = {
        "payload": persisted_outcome,
        "decision_event_id": "persisted",
        "observation_id": "persisted:T+5",
        "horizon_trading_days": 5,
        "status": "mature",
        "is_terminal": True,
        "content_hash": _stored_outcome_content_hash(persisted_outcome),
        "observed_at": _LABEL_AT,
        "finalized_at": _LABEL_AT,
    }
    result = quality.evaluate_decision_quality(
        [event_wrapper],
        [outcome_wrapper],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "available"
    assert result["input_audit"]["event_exclusions"] == []
    assert result["input_audit"]["outcome_exclusions"] == []
    assert result["input_audit"]["matched_terminal_outcome_count"] == 1


def test_event_storage_receipt_is_part_of_the_no_lookahead_boundary() -> None:
    persisted_event = normalize_decision_event(_event("late-event-receipt"))
    event_wrapper = {
        "payload": persisted_event,
        "event_id": persisted_event["event_id"],
        "schema_version": persisted_event["schema_version"],
        "event_type": persisted_event["event_type"],
        "source_type": persisted_event["source_type"],
        "decision_at": persisted_event["decision_at"],
        "fund_code": persisted_event["fund_code"],
        "final_action": persisted_event["final_action"],
        "action_category": persisted_event["action_category"],
        "eligible": persisted_event["eligible"],
        "is_backfilled": persisted_event["is_backfilled"],
        "metric_eligible": persisted_event["metric_eligible"],
        # The label was visible on January 15, before this event reached the
        # durable primary store.  It therefore cannot score this event.
        "created_at": "2026-01-20T00:00:00+00:00",
        "content_hash": decision_event_content_hash(persisted_event),
    }
    result = quality.evaluate_decision_quality(
        [event_wrapper],
        [_outcome("late-event-receipt")],
        evaluation_as_of=_AS_OF,
    )

    assert result["input_audit"]["event_exclusions"] == []
    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_label_not_strictly_after_replay_boundary": 1
    }
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["decision_knowledge_cutoff"] == "2026-01-20T00:00:00+00:00"
    assert match["match_status"] == "metric_contract_excluded"


def test_primary_storage_terminal_receipt_conservatively_dates_source_less_label() -> None:
    persisted_event = normalize_decision_event(_event("receipt-dated-label"))
    event_wrapper = {
        "payload": persisted_event,
        "event_id": persisted_event["event_id"],
        "schema_version": persisted_event["schema_version"],
        "event_type": persisted_event["event_type"],
        "source_type": persisted_event["source_type"],
        "decision_at": persisted_event["decision_at"],
        "fund_code": persisted_event["fund_code"],
        "final_action": persisted_event["final_action"],
        "action_category": persisted_event["action_category"],
        "eligible": persisted_event["eligible"],
        "is_backfilled": persisted_event["is_backfilled"],
        "metric_eligible": persisted_event["metric_eligible"],
        "created_at": "2026-01-02T10:02:00+00:00",
        "content_hash": decision_event_content_hash(persisted_event),
    }
    persisted_outcome = _outcome("receipt-dated-label")
    persisted_outcome.pop("payload_hash")
    persisted_outcome.pop("recorded_at")
    persisted_outcome["source_available_at"] = None
    outcome_wrapper = {
        "payload": persisted_outcome,
        "decision_event_id": "receipt-dated-label",
        "observation_id": "receipt-dated-label:T+5",
        "horizon_trading_days": 5,
        "status": "mature",
        "is_terminal": True,
        "content_hash": _stored_outcome_content_hash(persisted_outcome),
        "created_at": "2026-01-03T00:00:00+00:00",
        "observed_at": "2026-01-15T10:00:00+00:00",
        "finalized_at": "2026-01-15T10:05:00+00:00",
        "updated_at": "2026-01-15T10:05:00+00:00",
    }

    result = quality.evaluate_decision_quality(
        [event_wrapper],
        [outcome_wrapper],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "available"
    assert result["input_audit"]["outcome_exclusions"] == []
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["match_status"] == "matched_terminal"
    assert match["label_available_at"] == "2026-01-15T10:05:00+00:00"
    assert match["label_source_available_at"] is None
    assert match["label_availability_basis"] == "storage_terminal_receipt"
    assert match["label_first_observed_at"] == "2026-01-03T00:00:00+00:00"
    assert match["label_recorded_at"] == "2026-01-15T10:05:00+00:00"


def test_bare_source_less_label_has_no_independent_receipt_and_is_excluded() -> None:
    source_less = _outcome("bare-source-less")
    source_less["source_available_at"] = None
    source_less.pop("payload_hash")
    source_less["payload_hash"] = payload_hash(source_less)

    result = quality.evaluate_decision_quality(
        [_event("bare-source-less")],
        [source_less],
        evaluation_as_of=_AS_OF,
    )

    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_observation_source_time_missing": 1
    }


def test_event_ingested_after_cutoff_is_excluded_even_if_decision_is_older() -> None:
    persisted_event = normalize_decision_event(_event("late-ingestion"))
    event_wrapper = {
        "payload": persisted_event,
        "event_id": persisted_event["event_id"],
        "schema_version": persisted_event["schema_version"],
        "event_type": persisted_event["event_type"],
        "source_type": persisted_event["source_type"],
        "decision_at": persisted_event["decision_at"],
        "fund_code": persisted_event["fund_code"],
        "final_action": persisted_event["final_action"],
        "action_category": persisted_event["action_category"],
        "eligible": persisted_event["eligible"],
        "is_backfilled": persisted_event["is_backfilled"],
        "metric_eligible": persisted_event["metric_eligible"],
        "created_at": "2026-03-01T00:00:00+00:00",
        "content_hash": decision_event_content_hash(persisted_event),
    }

    result = quality.evaluate_decision_quality(
        [event_wrapper],
        [],
        evaluation_as_of=_AS_OF,
    )

    assert _reason_counts(result["input_audit"]["event_exclusions"]) == {
        "decision_event_receipt_after_evaluation_boundary": 1
    }


def test_label_visibility_uses_latest_source_and_terminal_receipt_time() -> None:
    event = _event("late-finalization")
    outcome = _outcome(
        "late-finalization",
        label_at="2026-01-05T00:00:00+00:00",
    )
    outcome_wrapper = {
        "payload": outcome,
        "decision_event_id": "late-finalization",
        "observation_id": "late-finalization:T+5",
        "horizon_trading_days": 5,
        "status": "mature",
        "is_terminal": True,
        "content_hash": _stored_outcome_content_hash(outcome),
        "observed_at": "2026-03-01T00:00:00+00:00",
        "finalized_at": "2026-03-01T00:00:00+00:00",
    }

    result = quality.evaluate_decision_quality(
        [event],
        [outcome_wrapper],
        evaluation_as_of=_AS_OF,
    )

    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_observation_after_evaluation_boundary": 1
    }
    assert result["input_audit"]["matched_terminal_outcome_count"] == 0


def test_terminal_storage_wrapper_requires_finalized_receipt() -> None:
    event = _event("missing-finalized-receipt")
    outcome = _outcome("missing-finalized-receipt")
    outcome_wrapper = {
        "payload": outcome,
        "decision_event_id": "missing-finalized-receipt",
        "observation_id": "missing-finalized-receipt:T+5",
        "horizon_trading_days": 5,
        "status": "mature",
        "is_terminal": True,
        "content_hash": _stored_outcome_content_hash(outcome),
        "observed_at": _LABEL_AT,
        "created_at": _DECISION_AT,
    }

    result = quality.evaluate_decision_quality(
        [event],
        [outcome_wrapper],
        evaluation_as_of=_AS_OF,
    )

    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_observation_finalized_time_missing_or_invalid": 1
    }


def test_signed_but_internally_inconsistent_metric_is_not_counted_as_a_miss() -> None:
    event = _event("metric-conflict")
    outcome = _outcome("metric-conflict")
    outcome["metrics"]["gross_direction"]["hit"] = False
    outcome.pop("payload_hash")
    outcome["payload_hash"] = payload_hash(outcome)
    result = quality.evaluate_decision_quality(
        [event],
        [outcome],
        evaluation_as_of=_AS_OF,
    )

    assert result["status"] == "unavailable"
    assert _reason_counts(result["input_audit"]["outcome_exclusions"]) == {
        "outcome_metric_hit_value_conflict:gross_direction": 1
    }
    match = result["input_audit"]["event_horizon_matches"][0]
    assert match["match_status"] == "metric_contract_excluded"
    gross = result["overall"]["metrics"]["gross_direction"]
    assert gross["mature_count"] == 0
    assert gross["miss_count"] == 0
    assert gross["unavailable_count"] == 1


def test_non_finite_or_overflowing_evidence_is_excluded_without_raising() -> None:
    non_finite = _event("non-finite")
    non_finite["success_probability"] = float("nan")
    non_finite.pop("payload_hash")
    non_finite["payload_hash"] = payload_hash(non_finite)
    bad_event = quality.evaluate_decision_quality(
        [non_finite],
        [],
        evaluation_as_of=_AS_OF,
    )
    assert _reason_counts(bad_event["input_audit"]["event_exclusions"]) == {
        "decision_event_payload_hash_uncomputable": 1
    }

    valid = _event("overflow")
    overflow = _outcome("overflow")
    overflow["metrics"]["gross_direction"]["value_percent"] = 10**400
    overflow.pop("payload_hash")
    overflow["payload_hash"] = payload_hash(overflow)
    bad_outcome = quality.evaluate_decision_quality(
        [valid],
        [overflow],
        evaluation_as_of=_AS_OF,
    )
    assert _reason_counts(bad_outcome["input_audit"]["outcome_exclusions"]) == {
        "outcome_metric_value_invalid:gross_direction": 1
    }


def test_paired_gate_only_allows_human_review_after_preregistered_thresholds() -> None:
    champion = [
        _paired_case("a", utility=0.4, risk=0.3),
        _paired_case("b", utility=0.5, risk=0.4),
    ]
    challenger = [
        _paired_case("a", utility=0.5, risk=0.2),
        _paired_case("b", utility=0.6, risk=0.3, claim_status="sanitized"),
    ]
    gate = quality.evaluate_paired_champion_challenger_gate(
        champion,
        challenger,
        policy=_gate_policy(),
        evaluation_as_of=_AS_OF,
    )
    assert gate["status"] == "eligible_for_human_review"
    assert gate["paired_case_count"] == 2
    assert gate["mean_utility_delta"] == pytest.approx(0.1)
    assert gate["mean_risk_delta"] == pytest.approx(-0.1)
    assert gate["challenger_claim_violation_rate"] == 0.0
    assert gate["challenger_claim_sanitized_rate"] == 0.5
    assert gate["automatic_promotion_allowed"] is False
    assert all(gate["threshold_results"].values())

    late_labels = copy.deepcopy(challenger)
    late_labels[0]["label_available_at"] = late_labels[0]["output_at"]
    _sign_paired_case(late_labels[0])
    blocked = quality.evaluate_paired_champion_challenger_gate(
        champion,
        late_labels,
        policy=_gate_policy(),
        evaluation_as_of=_AS_OF,
    )
    assert blocked["status"] == "blocked"
    assert "label_not_strictly_after_both_outputs" in blocked["reason_codes"]
    assert blocked["automatic_promotion_allowed"] is False

    between_clocks = copy.deepcopy(challenger)
    between_clocks[0]["label_available_at"] = "2026-01-02T10:03:00+00:00"
    _sign_paired_case(between_clocks[0])
    between_blocked = quality.evaluate_paired_champion_challenger_gate(
        champion,
        between_clocks,
        policy=_gate_policy(),
        evaluation_as_of=_AS_OF,
    )
    assert between_blocked["status"] == "blocked"
    assert "label_not_strictly_after_both_outputs" in between_blocked["reason_codes"]

    future_champion = copy.deepcopy(champion)
    future_challenger = copy.deepcopy(challenger)
    for case in [*future_champion, *future_challenger]:
        case["label_available_at"] = "2026-03-01T00:00:00+00:00"
        _sign_paired_case(case)
    future_blocked = quality.evaluate_paired_champion_challenger_gate(
        future_champion,
        future_challenger,
        policy=_gate_policy(),
        evaluation_as_of=_AS_OF,
    )
    assert future_blocked["status"] == "blocked"
    assert future_blocked["paired_case_count"] == 0
    assert "paired_case_after_evaluation_boundary" in future_blocked["reason_codes"]

    no_cutoff = quality.evaluate_paired_champion_challenger_gate(
        champion,
        challenger,
        policy=_gate_policy(),
    )
    assert no_cutoff["status"] == "blocked"
    assert no_cutoff["paired_case_count"] == 0
    assert "paired_evaluation_as_of_missing_or_invalid" in no_cutoff["reason_codes"]


def test_paired_gate_blocks_future_replay_and_late_policy_registration() -> None:
    champion = [_paired_case("a", utility=0.4, risk=0.3)]
    challenger = [
        _paired_case(
            "a",
            utility=0.6,
            risk=0.2,
            replay_available_at="2026-01-03T00:00:00+00:00",
        )
    ]
    replay_blocked = quality.evaluate_paired_champion_challenger_gate(
        champion,
        challenger,
        policy=_gate_policy(min_pairs=1),
        evaluation_as_of=_AS_OF,
    )
    assert replay_blocked["status"] == "blocked"
    assert "paired_case_replay_ineligible" in replay_blocked["reason_codes"]

    eligible_challenger = [_paired_case("a", utility=0.6, risk=0.2)]
    late_policy = _gate_policy(
        min_pairs=1,
        registered_at="2026-01-02T10:06:00+00:00",
    )
    policy_blocked = quality.evaluate_paired_champion_challenger_gate(
        champion,
        eligible_challenger,
        policy=late_policy,
        evaluation_as_of=_AS_OF,
    )
    assert policy_blocked["status"] == "blocked"
    assert "gate_policy_not_preregistered_before_outputs" in policy_blocked["reason_codes"]


@pytest.mark.parametrize(
    "tampered_field",
    ["utility", "risk", "claim_status", "output_at", "replay_refs"],
)
def test_paired_case_semantic_tampering_is_blocked_by_output_hash(
    tampered_field: str,
) -> None:
    champion = [_paired_case("tamper", utility=0.4, risk=0.3)]
    challenger = [_paired_case("tamper", utility=0.6, risk=0.2)]
    frozen_hash = challenger[0]["frozen_case_hash"]
    label_hash = challenger[0]["label_hash"]
    if tampered_field == "utility":
        challenger[0]["utility"] = 9.0
    elif tampered_field == "risk":
        challenger[0]["risk"] = 9.0
    elif tampered_field == "claim_status":
        challenger[0]["claim_status"] = "violation"
    elif tampered_field == "output_at":
        challenger[0]["output_at"] = "2026-01-02T10:06:00+00:00"
    else:
        challenger[0]["replay_refs"][0]["ref_id"] = "tampered-ref"

    gate = quality.evaluate_paired_champion_challenger_gate(
        champion,
        challenger,
        policy=_gate_policy(min_pairs=1),
        evaluation_as_of=_AS_OF,
    )
    assert challenger[0]["frozen_case_hash"] == frozen_hash
    assert challenger[0]["label_hash"] == label_hash
    assert gate["status"] == "blocked"
    assert gate["paired_case_count"] == 0
    assert "paired_output_hash_mismatch" in gate["reason_codes"]
    assert gate["automatic_promotion_allowed"] is False


def test_paired_case_recomputed_output_hash_still_requires_content_hash() -> None:
    champion = [_paired_case("content", utility=0.4, risk=0.3)]
    challenger = [_paired_case("content", utility=0.6, risk=0.2)]
    challenger[0]["utility"] = 0.7
    challenger[0]["output_hash"] = _canonical_hash(
        {
            key: value
            for key, value in challenger[0].items()
            if key not in {"output_hash", "content_hash"}
        }
    )
    gate = quality.evaluate_paired_champion_challenger_gate(
        champion,
        challenger,
        policy=_gate_policy(min_pairs=1),
        evaluation_as_of=_AS_OF,
    )
    assert gate["status"] == "blocked"
    assert "paired_content_hash_mismatch" in gate["reason_codes"]


def test_evaluation_is_deterministic_and_does_not_mutate_inputs() -> None:
    events = [_event("pure", success_probability=0.7)]
    outcomes = [_outcome("pure")]
    before_events = copy.deepcopy(events)
    before_outcomes = copy.deepcopy(outcomes)
    first = quality.evaluate_decision_quality(
        events,
        outcomes,
        evaluation_as_of=_AS_OF,
        min_calibration_samples=1,
    )
    second = quality.evaluate_decision_quality(
        events,
        outcomes,
        evaluation_as_of=_AS_OF,
        min_calibration_samples=1,
    )
    assert first == second
    assert first["evaluation_hash"] == second["evaluation_hash"]
    assert events == before_events
    assert outcomes == before_outcomes
