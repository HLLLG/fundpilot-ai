from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.decision_repository import (
    canonical_hash,
    canonical_json,
    normalize_decision_quality_artifact_receipt,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_FINAL_PROJECTION_SCHEMA_VERSION,
    PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    PROMPT_GATE_POLICY_SCHEMA_VERSION,
    PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
    PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
    PROMPT_SHADOW_CAPTURE_MODE,
    PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
    PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
    PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
    PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
    PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
    PromptShadowContractError,
    build_prompt_final_projection,
    build_prompt_gate_policy,
    build_prompt_shadow_attempt,
    build_prompt_shadow_input_artifact,
    build_prompt_shadow_output,
    build_prompt_shadow_registration,
    decision_projection_hash,
    normalize_prompt_final_projection,
    normalize_prompt_gate_policy,
    normalize_prompt_shadow_attempt,
    normalize_prompt_shadow_output,
    normalize_prompt_shadow_registration,
    prompt_shadow_nonformal_reason,
    validate_prompt_shadow_time_chain,
)
from app.services.provider_call_trace import normalize_provider_call_trace


USER_ID = 17
BASE = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat()


def _scope() -> dict[str, Any]:
    return {
        "source_type": "discovery",
        "scan_mode": "full_market",
        "analysis_mode": "fast",
        "horizon_trading_days": 20,
        "provider": "deepseek",
        "provider_mode": "live_only",
        "default_prompt_only": True,
        "news_tool_rounds": 0,
        "judge_mode": "rule_only",
    }


def _policy_material() -> dict[str, Any]:
    champion = "You are the production champion."
    challenger = "You are the preregistered challenger."
    return {
        "schema_version": PROMPT_GATE_POLICY_SCHEMA_VERSION,
        "policy_id": "prompt-paired-gate-v2-20260715",
        "registered_at": _ts(0),
        "effective_from": _ts(0),
        "effective_until": None,
        "scope": _scope(),
        "champion_prompt": {
            "template_version": "discovery.system.v7",
            "template_snapshot": champion,
            "template_hash": canonical_hash(champion),
        },
        "challenger_prompt": {
            "template_version": "discovery.system.v8-candidate",
            "template_snapshot": challenger,
            "template_hash": canonical_hash(challenger),
        },
        "pairing": {
            "exact_match_fields": [
                "user_payload",
                "model",
                "temperature",
                "max_tokens",
                "response_format",
                "transport",
                "guard_context",
            ],
            "allowed_difference_fields": ["effective_system_prompt"],
            "max_registration_source_lag_seconds": 300,
            "max_receipt_delay_seconds": 300,
            "max_challenger_start_delay_seconds": 900,
            "max_raw_response_bytes": PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
            "attempts_per_role": 1,
            "capture_mode": PROMPT_SHADOW_CAPTURE_MODE,
        },
        "assignment": {
            "algorithm": "hmac_sha256_mod_10000.v1",
            "key_id": "shadow-assignment-2026-07",
            "sample_basis_points": 1000,
        },
        "budget": {
            "timezone": "Asia/Shanghai",
            "scope_key": "global",
            "max_challenger_calls_per_day": 100,
            "reservation_policy": "consume_never_release.v1",
        },
        "gate_thresholds": {
            "minimum_mature_decision_days": 60,
            "minimum_paired_label_coverage": 0.80,
            "minimum_differing_case_count": 20,
            "minimum_challenger_valid_completion_rate": 0.95,
            "maximum_challenger_timeout_rate": 0.05,
            "maximum_challenger_invalid_rate": 0.02,
            "maximum_integrity_failure_count": 0,
            "maximum_tenant_failure_count": 0,
            "maximum_guard_failure_count": 0,
            "minimum_mean_utility_delta_pp": 0.05,
            "minimum_utility_ci95_lower_pp": -0.10,
            "maximum_mean_drawdown_delta_pp": 0.0,
            "maximum_drawdown_ci95_upper_pp": 0.10,
            "maximum_sanitized_rate_delta": 0.02,
            "maximum_budget_violation_count": 0,
        },
        "statistics": {
            "cluster_key": "live_cohort_date_local",
            "aggregation": "equal_weighted_day_means",
            "bootstrap_iterations": 10_000,
            "permutation_iterations": 10_000,
            "confidence_level": 0.95,
            "seed_derivation": (
                "sha256(prompt-paired-gate-v2|policy_hash|stratum_hash)"
            ),
        },
        "automatic_promotion_allowed": False,
    }


def _receipt(
    *,
    name: str,
    artifact_type: str,
    envelope: dict[str, Any],
    source_at: float,
    visible_at: float,
) -> dict[str, Any]:
    stored = normalize_decision_quality_artifact_receipt(
        {
            "user_id": USER_ID,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": artifact_type,
            "artifact_content_hash": envelope["content_hash"],
            "source_row_created_at": _ts(source_at),
            "source_visible_at": _ts(visible_at),
            "store_authority": "primary",
        }
    )
    return {
        "user_id": USER_ID,
        "artifact_id": envelope["artifact_id"],
        "artifact_type": artifact_type,
        "artifact_content_hash": envelope["content_hash"],
        "receipt_id": stored["receipt_id"],
        "receipt_content_hash": stored["content_hash"],
        "source_row_created_at": _ts(source_at),
        "source_visible_at": _ts(visible_at),
    }


def _policy_ref(policy: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": policy["policy_id"],
        "policy_hash": policy["policy_hash"],
        **receipt,
    }


def _registration_ref(
    registration: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    return {"registration_hash": registration["registration_hash"], **receipt}


def _attempt_ref(attempt: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    return {"attempt_hash": attempt["attempt_hash"], **receipt}


def _candidate_ref() -> dict[str, Any]:
    artifact_hash = _hash("candidate-audit-artifact")
    stored = normalize_decision_quality_artifact_receipt(
        {
            "user_id": USER_ID,
            "artifact_id": f"dqa_{artifact_hash}",
            "artifact_type": "candidate_selection_audit",
            "artifact_content_hash": artifact_hash,
            "source_row_created_at": _ts(1),
            "source_visible_at": _ts(2),
            "store_authority": "primary",
        }
    )
    return {
        "user_id": USER_ID,
        "artifact_id": f"dqa_{artifact_hash}",
        "artifact_type": "candidate_selection_audit",
        "artifact_content_hash": artifact_hash,
        "receipt_id": stored["receipt_id"],
        "receipt_content_hash": stored["content_hash"],
        "source_row_created_at": _ts(1),
        "source_visible_at": _ts(2),
    }


def _rehash_prompt_pair(pair: dict[str, Any]) -> None:
    pair["user_payload_hash"] = canonical_hash(pair["user_payload"])
    pair["champion_messages_hash"] = canonical_hash(pair["champion_messages"])
    pair["challenger_messages_hash"] = canonical_hash(pair["challenger_messages"])
    pair["champion_provider_payload_hash"] = canonical_hash(
        pair["champion_provider_payload"]
    )
    pair["challenger_provider_payload_hash"] = canonical_hash(
        pair["challenger_provider_payload"]
    )


def _prompt_pair() -> dict[str, Any]:
    user_payload = {"candidate_codes": ["000001", "000002"], "budget": 1000}
    user_content = canonical_json(user_payload)
    champion_messages = [
        {"role": "system", "content": "Champion system"},
        {"role": "user", "content": user_content},
    ]
    challenger_messages = [
        {"role": "system", "content": "Challenger system"},
        {"role": "user", "content": user_content},
    ]
    common = {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }
    pair = {
        "user_payload": user_payload,
        "user_payload_hash": "",
        "champion_messages": champion_messages,
        "champion_messages_hash": "",
        "challenger_messages": challenger_messages,
        "challenger_messages_hash": "",
        "champion_provider_payload": {**common, "messages": champion_messages},
        "champion_provider_payload_hash": "",
        "challenger_provider_payload": {**common, "messages": challenger_messages},
        "challenger_provider_payload_hash": "",
        "transport": "sync",
    }
    _rehash_prompt_pair(pair)
    return pair


def _registration_material(
    policy: dict[str, Any], policy_receipt: dict[str, Any], *, decision_at: float
) -> dict[str, Any]:
    guard_context = {
        "target_sectors": ["科技"],
        "focus_sectors": ["科技"],
        "scan_mode": "full_market",
        "candidate_pool": [{"fund_code": "000001"}, {"fund_code": "000002"}],
        "discovery_facts": {"market_regime": "neutral"},
        "profile": {"risk_level": "balanced"},
        "held_codes": [],
        "requested_budget_yuan": 1000.0,
        "sector_heat": [],
        "market_news": [],
        "topic_briefs": [],
        "analysis_mode": "fast",
        "decision_at": _ts(decision_at),
    }
    return {
        "schema_version": PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
        "run_id": f"dqsr_{_hash('paired-run')}",
        "policy_ref": _policy_ref(policy, policy_receipt),
        "decision_at": _ts(decision_at),
        "registered_at": _ts(decision_at + 1),
        "capture_mode": PROMPT_SHADOW_CAPTURE_MODE,
        "scope": _scope(),
        "assignment": {
            "algorithm": "hmac_sha256_mod_10000.v1",
            "key_id": "shadow-assignment-2026-07",
            "assignment_input_hash": _hash("assignment-input"),
            "hmac_digest": _hash("assignment-hmac"),
            "modulus": 10_000,
            "bucket": 7,
            "threshold": 1000,
            "included": True,
        },
        "prompt_pair": _prompt_pair(),
        "guard_context": guard_context,
        "guard_context_hash": canonical_hash(guard_context),
        "candidate_audit_snapshot_hash": _hash("candidate-snapshot"),
        "versions": {
            "discovery_prompt_contract_version": "discovery.prompt.v7",
            "discovery_guard_contract_version": "discovery.guard.v5",
            "discovery_allocator_contract_version": "discovery.allocator.v3",
            "claim_validator_contract_version": "claim.validator.v2",
            "candidate_label_policy_version": "candidate.label.v2",
        },
        "label_plan": {
            "horizon_trading_days": 20,
            "utility_basis": "allocation_weighted_total_return_before_costs",
            "risk_basis": "allocation_weighted_full_path_max_drawdown",
            "cash_return_percent": 0.0,
        },
        "automatic_promotion_allowed": False,
    }


def _attempt_material(
    *,
    role: str,
    registration: dict[str, Any],
    policy_receipt: dict[str, Any],
    registration_receipt: dict[str, Any],
    preregistered_at: float,
    max_calls: int = 100,
) -> dict[str, Any]:
    challenger = role == "challenger"
    lease = (
        {
            "owner_hash": _hash("lease-owner"),
            "token_hash": _hash("lease-token"),
            "acquired_at": _ts(preregistered_at - 1),
            "expires_at": _ts(preregistered_at + 1000),
        }
        if challenger
        else None
    )
    reservation = (
        {
            "scope_key": "global",
            "budget_date_local": "2026-07-15",
            "policy_hash": registration["policy_ref"]["policy_hash"],
            "max_calls": max_calls,
            "reserved_ordinal": 1,
            "reserved_at": _ts(preregistered_at),
        }
        if challenger
        else None
    )
    return {
        "schema_version": PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
        "run_id": registration["run_id"],
        "role": role,
        "attempt_number": 1,
        "decision_at": registration["decision_at"],
        "policy_ref": registration["policy_ref"],
        "registration_ref": _registration_ref(registration, registration_receipt),
        "provider": "deepseek",
        "operation": "chat_completions",
        "endpoint_base_url": "https://api.deepseek.com",
        "provider_payload_hash": registration["prompt_pair"][
            f"{role}_provider_payload_hash"
        ],
        "transport": "sync",
        "pre_network_registered_at": _ts(preregistered_at),
        "lease": lease,
        "budget_reservation": reservation,
        "automatic_promotion_allowed": False,
    }


def _trace(
    *,
    request_body: dict[str, Any],
    content: str | bytes,
    requested_at: float,
) -> dict[str, Any]:
    content_bytes = content.encode("utf-8") if isinstance(content, str) else content
    envelope = b'{"choices":[{"message":{"content":"redacted"}}]}'
    return normalize_provider_call_trace(
        {
            "schema_version": "provider_call_trace.v1",
            "provider": "deepseek",
            "operation": "chat_completions",
            "transport": "sync",
            "request_hash": canonical_hash(request_body),
            "requested_model": request_body["model"],
            "requested_at": _ts(requested_at),
            "response_started_at": _ts(requested_at + 1),
            "first_content_at": _ts(requested_at + 1),
            "completed_at": _ts(requested_at + 2),
            "http_status": 200,
            "provider_request_id_hash": _hash("provider-request-id"),
            "actual_model": "deepseek-chat",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 100,
            },
            "chunk_count": 1,
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "content_bytes": len(content_bytes),
            "transport_envelope_sha256": hashlib.sha256(envelope).hexdigest(),
            "transport_envelope_bytes": len(envelope),
            "envelope_hash_basis": "sync_http_body_bytes",
            "outcome": "success",
            "error_category": None,
            "interrupted_salvaged": False,
        }
    )


def _projection(reason: str = "Evidence-backed selection") -> dict[str, Any]:
    return build_prompt_final_projection(
        {
            "schema_version": PROMPT_FINAL_PROJECTION_SCHEMA_VERSION,
            "recommendations": [
                {
                    "fund_code": "000001",
                    "action": "分批买入",
                    "confidence": "中",
                    "reason": reason,
                }
            ],
            "allocation_plan": {
                "allocations": [
                    {"fund_code": "000001", "suggested_amount_yuan": 600}
                ],
                "summary": reason,
            },
            "eliminated_candidates": [
                {"fund_code": "000002", "reason": "Lower deterministic rank"}
            ],
            "claim_audit": {"status": "clean", "notes": []},
            "requested_budget_yuan": 1000,
            "selected_codes": ["000001"],
            "allocations": [
                {"fund_code": "000001", "suggested_amount_yuan": 600}
            ],
            "unallocated_budget_yuan": 400,
            "guard_reason_codes": [],
            "versions": {
                "discovery_guard_contract_version": "discovery.guard.v5",
                "discovery_allocator_contract_version": "discovery.allocator.v3",
                "claim_validator_contract_version": "claim.validator.v2",
            },
            "automatic_promotion_allowed": False,
        }
    )


def _output_material(
    *,
    role: str,
    registration: dict[str, Any],
    attempt: dict[str, Any],
    attempt_receipt: dict[str, Any],
    requested_at: float,
) -> dict[str, Any]:
    raw_payload = {"role": role, "provider_answer": "ok"}
    raw_content = canonical_json(raw_payload)
    trace = _trace(
        request_body=registration["prompt_pair"][f"{role}_provider_payload"],
        content=raw_content,
        requested_at=requested_at,
    )
    projection = _projection(f"{role} explanatory prose")
    return {
        "schema_version": PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
        "run_id": registration["run_id"],
        "role": role,
        "decision_at": registration["decision_at"],
        "policy_ref": registration["policy_ref"],
        "registration_ref": attempt["registration_ref"],
        "attempt_ref": _attempt_ref(attempt, attempt_receipt),
        "champion_report_id": "report-20260715-001",
        "variant_report_id": "report-20260715-001" if role == "champion" else None,
        "candidate_audit_ref": _candidate_ref(),
        "trace": trace,
        "response": {
            "raw_content": raw_content,
            "raw_content_sha256": hashlib.sha256(raw_content.encode()).hexdigest(),
            "raw_content_bytes": len(raw_content.encode()),
            "parse_status": "valid",
            "parsed_payload": raw_payload,
            "parsed_payload_hash": canonical_hash(raw_payload),
            "error_category": None,
        },
        "final_projection": projection,
        "final_projection_hash": projection["projection_hash"],
        "decision_projection_hash": decision_projection_hash(projection),
        "output_materialized_at": _ts(requested_at + 3),
        "automatic_promotion_allowed": False,
    }


def _bundle(
    *,
    late_receipt: str | None = None,
    challenger_start_delay: int = 10,
    challenger_max_calls: int = 100,
) -> dict[str, Any]:
    def visible(name: str, source: float) -> float:
        return source + (301 if late_receipt == name else 1)

    policy = build_prompt_gate_policy(_policy_material())
    policy_envelope = build_prompt_shadow_input_artifact(user_id=USER_ID, artifact=policy)
    policy_source = 1
    policy_visible = visible("policy", policy_source)
    policy_receipt = _receipt(
        name="policy",
        artifact_type=PROMPT_GATE_POLICY_ARTIFACT_TYPE,
        envelope=policy_envelope,
        source_at=policy_source,
        visible_at=policy_visible,
    )

    decision_at = policy_visible + 1
    registration = build_prompt_shadow_registration(
        _registration_material(policy, policy_receipt, decision_at=decision_at),
        policy=policy,
        expected_user_id=USER_ID,
    )
    registration_envelope = build_prompt_shadow_input_artifact(
        user_id=USER_ID, artifact=registration
    )
    registration_source = decision_at + 2
    registration_visible = visible("registration", registration_source)
    registration_receipt = _receipt(
        name="registration",
        artifact_type=PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
        envelope=registration_envelope,
        source_at=registration_source,
        visible_at=registration_visible,
    )

    champion_preregistered = registration_visible + 1
    champion_attempt = build_prompt_shadow_attempt(
        _attempt_material(
            role="champion",
            registration=registration,
            policy_receipt=policy_receipt,
            registration_receipt=registration_receipt,
            preregistered_at=champion_preregistered,
        ),
        registration=registration,
        expected_user_id=USER_ID,
    )
    champion_attempt_envelope = build_prompt_shadow_input_artifact(
        user_id=USER_ID, artifact=champion_attempt
    )
    champion_attempt_source = champion_preregistered + 1
    champion_attempt_visible = visible("champion_attempt", champion_attempt_source)
    champion_attempt_receipt = _receipt(
        name="champion-attempt",
        artifact_type=PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        envelope=champion_attempt_envelope,
        source_at=champion_attempt_source,
        visible_at=champion_attempt_visible,
    )
    champion_request = champion_attempt_visible + 1
    champion_output = build_prompt_shadow_output(
        _output_material(
            role="champion",
            registration=registration,
            attempt=champion_attempt,
            attempt_receipt=champion_attempt_receipt,
            requested_at=champion_request,
        ),
        registration=registration,
        attempt=champion_attempt,
        expected_user_id=USER_ID,
    )
    champion_output_envelope = build_prompt_shadow_input_artifact(
        user_id=USER_ID, artifact=champion_output
    )
    champion_output_source = champion_request + 4
    champion_output_visible = visible("champion_output", champion_output_source)
    champion_output_receipt = _receipt(
        name="champion-output",
        artifact_type=PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
        envelope=champion_output_envelope,
        source_at=champion_output_source,
        visible_at=champion_output_visible,
    )

    challenger_request = champion_output_visible + challenger_start_delay
    challenger_preregistered = challenger_request - 3
    challenger_attempt = build_prompt_shadow_attempt(
        _attempt_material(
            role="challenger",
            registration=registration,
            policy_receipt=policy_receipt,
            registration_receipt=registration_receipt,
            preregistered_at=challenger_preregistered,
            max_calls=challenger_max_calls,
        ),
        registration=registration,
        expected_user_id=USER_ID,
    )
    challenger_attempt_envelope = build_prompt_shadow_input_artifact(
        user_id=USER_ID, artifact=challenger_attempt
    )
    challenger_attempt_source = challenger_preregistered + 1
    challenger_attempt_visible = visible(
        "challenger_attempt", challenger_attempt_source
    )
    if challenger_attempt_visible > challenger_request:
        challenger_request = challenger_attempt_visible
    challenger_attempt_receipt = _receipt(
        name="challenger-attempt",
        artifact_type=PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        envelope=challenger_attempt_envelope,
        source_at=challenger_attempt_source,
        visible_at=challenger_attempt_visible,
    )
    challenger_output = build_prompt_shadow_output(
        _output_material(
            role="challenger",
            registration=registration,
            attempt=challenger_attempt,
            attempt_receipt=challenger_attempt_receipt,
            requested_at=challenger_request,
        ),
        registration=registration,
        attempt=challenger_attempt,
        expected_user_id=USER_ID,
    )
    challenger_output_envelope = build_prompt_shadow_input_artifact(
        user_id=USER_ID, artifact=challenger_output
    )
    challenger_output_source = challenger_request + 4
    challenger_output_visible = visible("challenger_output", challenger_output_source)
    challenger_output_receipt = _receipt(
        name="challenger-output",
        artifact_type=PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
        envelope=challenger_output_envelope,
        source_at=challenger_output_source,
        visible_at=challenger_output_visible,
    )
    return {
        "policy": policy,
        "policy_receipt": policy_receipt,
        "registration": registration,
        "registration_receipt": registration_receipt,
        "champion_attempt": champion_attempt,
        "champion_attempt_receipt": champion_attempt_receipt,
        "champion_output": champion_output,
        "champion_output_receipt": champion_output_receipt,
        "challenger_attempt": challenger_attempt,
        "challenger_attempt_receipt": challenger_attempt_receipt,
        "challenger_output": challenger_output,
        "challenger_output_receipt": challenger_output_receipt,
        "label_knowledge_boundary": _ts(challenger_output_visible + 10),
        "evaluation_as_of": _ts(challenger_output_visible + 20),
        "expected_user_id": USER_ID,
    }


def test_all_inner_artifacts_round_trip_and_share_the_outer_envelope() -> None:
    bundle = _bundle()
    assert normalize_prompt_gate_policy(bundle["policy"]) == bundle["policy"]
    assert (
        normalize_prompt_shadow_registration(
            bundle["registration"],
            policy=bundle["policy"],
            expected_user_id=USER_ID,
        )
        == bundle["registration"]
    )
    for role in ("champion", "challenger"):
        attempt = bundle[f"{role}_attempt"]
        output = bundle[f"{role}_output"]
        assert (
            normalize_prompt_shadow_attempt(
                attempt,
                registration=bundle["registration"],
                expected_user_id=USER_ID,
            )
            == attempt
        )
        assert (
            normalize_prompt_shadow_output(
                output,
                registration=bundle["registration"],
                attempt=attempt,
                expected_user_id=USER_ID,
            )
            == output
        )
    for key in (
        "policy",
        "registration",
        "champion_attempt",
        "champion_output",
        "challenger_attempt",
        "challenger_output",
    ):
        envelope = build_prompt_shadow_input_artifact(
            user_id=USER_ID, artifact=bundle[key]
        )
        assert envelope["artifact_id"] == f"dqa_{envelope['content_hash']}"
        assert envelope["store_authority"] == "primary"
        assert envelope["audit_eligible"] is True


def test_unknown_missing_stale_hash_and_noncanonical_json_are_rejected() -> None:
    material = _policy_material()
    material["unknown"] = True
    with pytest.raises(PromptShadowContractError, match="unsupported fields"):
        build_prompt_gate_policy(material)

    missing = _policy_material()
    del missing["scope"]
    with pytest.raises(PromptShadowContractError, match="missing fields"):
        build_prompt_gate_policy(missing)

    policy = build_prompt_gate_policy(_policy_material())
    stale = deepcopy(policy)
    stale["policy_id"] = "tampered"
    with pytest.raises(PromptShadowContractError, match="policy_hash mismatch"):
        normalize_prompt_gate_policy(stale)

    bundle = _bundle()
    registration = deepcopy(bundle["registration"])
    registration.pop("registration_hash")
    pair = registration["prompt_pair"]
    noncanonical = '{"budget": 1000, "candidate_codes": ["000001", "000002"]}'
    pair["champion_messages"][1]["content"] = noncanonical
    pair["challenger_messages"][1]["content"] = noncanonical
    pair["champion_provider_payload"]["messages"] = pair["champion_messages"]
    pair["challenger_provider_payload"]["messages"] = pair["challenger_messages"]
    _rehash_prompt_pair(pair)
    with pytest.raises(PromptShadowContractError, match="canonical JSON"):
        build_prompt_shadow_registration(
            registration, policy=bundle["policy"], expected_user_id=USER_ID
        )


def test_cross_tenant_receipt_references_are_rejected() -> None:
    bundle = _bundle()
    with pytest.raises(PromptShadowContractError, match="tenant boundary"):
        normalize_prompt_shadow_registration(
            bundle["registration"],
            policy=bundle["policy"],
            expected_user_id=USER_ID + 1,
        )
    tampered = deepcopy(bundle)
    tampered["challenger_output_receipt"]["user_id"] = USER_ID + 1
    with pytest.raises(PromptShadowContractError, match="tenant boundary"):
        validate_prompt_shadow_time_chain(**tampered)


def test_prompt_pair_may_differ_only_in_effective_system_prompt() -> None:
    bundle = _bundle()
    material = deepcopy(bundle["registration"])
    material.pop("registration_hash")
    material["prompt_pair"]["challenger_provider_payload"]["model"] = "other-model"
    _rehash_prompt_pair(material["prompt_pair"])
    with pytest.raises(PromptShadowContractError, match="only in their system message"):
        build_prompt_shadow_registration(
            material, policy=bundle["policy"], expected_user_id=USER_ID
        )

    same_system = deepcopy(bundle["registration"])
    same_system.pop("registration_hash")
    pair = same_system["prompt_pair"]
    pair["challenger_messages"][0]["content"] = pair["champion_messages"][0][
        "content"
    ]
    pair["challenger_provider_payload"]["messages"] = pair["challenger_messages"]
    _rehash_prompt_pair(pair)
    with pytest.raises(PromptShadowContractError, match="system messages must differ"):
        build_prompt_shadow_registration(
            same_system, policy=bundle["policy"], expected_user_id=USER_ID
        )


def test_decision_projection_ignores_prose_but_final_projection_does_not() -> None:
    first = _projection("first prose")
    second_material = deepcopy(first)
    second_material.pop("projection_hash")
    second_material["recommendations"][0]["reason"] = "entirely different prose"
    second_material["allocation_plan"]["summary"] = "different allocation prose"
    second_material["eliminated_candidates"][0]["reason"] = "different elimination"
    second = build_prompt_final_projection(second_material)
    assert first["projection_hash"] != second["projection_hash"]
    assert decision_projection_hash(first) == decision_projection_hash(second)
    assert normalize_prompt_final_projection(second) == second


def test_sensitive_fields_and_endpoint_credentials_are_rejected() -> None:
    bundle = _bundle()
    registration = deepcopy(bundle["registration"])
    registration.pop("registration_hash")
    pair = registration["prompt_pair"]
    pair["user_payload"]["headers"] = {"Authorization": "Bearer secret"}
    user_content = canonical_json(pair["user_payload"])
    for name in ("champion_messages", "challenger_messages"):
        pair[name][1]["content"] = user_content
    pair["champion_provider_payload"]["messages"] = pair["champion_messages"]
    pair["challenger_provider_payload"]["messages"] = pair["challenger_messages"]
    _rehash_prompt_pair(pair)
    with pytest.raises(PromptShadowContractError, match="sensitive field"):
        build_prompt_shadow_registration(
            registration, policy=bundle["policy"], expected_user_id=USER_ID
        )

    attempt = deepcopy(bundle["champion_attempt"])
    attempt.pop("attempt_hash")
    attempt["endpoint_base_url"] = "https://user:pass@api.deepseek.com/v1?key=x"
    with pytest.raises(PromptShadowContractError, match="credential-free HTTPS"):
        build_prompt_shadow_attempt(
            attempt,
            registration=bundle["registration"],
            expected_user_id=USER_ID,
        )


def test_role_attempt_lease_and_budget_contracts_fail_closed() -> None:
    bundle = _bundle()
    champion = deepcopy(bundle["champion_attempt"])
    champion.pop("attempt_hash")
    champion["attempt_number"] = 2
    with pytest.raises(PromptShadowContractError, match="only one provider attempt"):
        build_prompt_shadow_attempt(
            champion,
            registration=bundle["registration"],
            expected_user_id=USER_ID,
        )

    champion = deepcopy(bundle["champion_attempt"])
    champion.pop("attempt_hash")
    champion["lease"] = deepcopy(bundle["challenger_attempt"]["lease"])
    with pytest.raises(PromptShadowContractError, match="cannot have a lease"):
        build_prompt_shadow_attempt(
            champion,
            registration=bundle["registration"],
            expected_user_id=USER_ID,
        )

    challenger = deepcopy(bundle["challenger_attempt"])
    challenger.pop("attempt_hash")
    challenger["budget_reservation"]["budget_date_local"] = "2026-07-16"
    with pytest.raises(PromptShadowContractError, match="budget date conflicts"):
        build_prompt_shadow_attempt(
            challenger,
            registration=bundle["registration"],
            expected_user_id=USER_ID,
        )

    with pytest.raises(PromptShadowContractError, match="conflicts with gate policy"):
        validate_prompt_shadow_time_chain(**_bundle(challenger_max_calls=99))


@pytest.mark.parametrize(
    "artifact_key,field",
    [
        ("policy", "policy_hash"),
        ("registration", "registration_hash"),
        ("champion_attempt", "attempt_hash"),
        ("champion_output", "output_hash"),
    ],
)
def test_automatic_promotion_and_hash_tampering_never_upgrade(
    artifact_key: str, field: str
) -> None:
    bundle = _bundle()
    artifact = deepcopy(bundle[artifact_key])
    artifact["automatic_promotion_allowed"] = True
    artifact[field] = _hash("forged")
    normalizers = {
        "policy": lambda value: normalize_prompt_gate_policy(value),
        "registration": lambda value: normalize_prompt_shadow_registration(
            value, policy=bundle["policy"], expected_user_id=USER_ID
        ),
        "champion_attempt": lambda value: normalize_prompt_shadow_attempt(
            value,
            registration=bundle["registration"],
            expected_user_id=USER_ID,
        ),
        "champion_output": lambda value: normalize_prompt_shadow_output(
            value,
            registration=bundle["registration"],
            attempt=bundle["champion_attempt"],
            expected_user_id=USER_ID,
        ),
    }
    with pytest.raises(PromptShadowContractError, match="must be false"):
        normalizers[artifact_key](artifact)


def test_provider_trace_payload_and_response_binding_are_strict() -> None:
    bundle = _bundle()
    material = deepcopy(bundle["champion_output"])
    material.pop("output_hash")
    material["trace"] = _trace(
        request_body=bundle["registration"]["prompt_pair"][
            "challenger_provider_payload"
        ],
        content=material["response"]["raw_content"],
        requested_at=100,
    )
    material["output_materialized_at"] = _ts(103)
    with pytest.raises(PromptShadowContractError, match="conflicts with provider attempt"):
        build_prompt_shadow_output(
            material,
            registration=bundle["registration"],
            attempt=bundle["champion_attempt"],
            expected_user_id=USER_ID,
        )

    material = deepcopy(bundle["champion_output"])
    material.pop("output_hash")
    material["response"]["raw_content_sha256"] = _hash("different-content")
    with pytest.raises(PromptShadowContractError, match="hash or byte count mismatch"):
        build_prompt_shadow_output(
            material,
            registration=bundle["registration"],
            attempt=bundle["champion_attempt"],
            expected_user_id=USER_ID,
        )


def test_raw_response_limit_requires_hash_only_oversize_evidence() -> None:
    bundle = _bundle()
    base = deepcopy(bundle["champion_output"])
    base.pop("output_hash")
    too_large = b"x" * (PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES + 1)
    base["trace"] = _trace(
        request_body=bundle["registration"]["prompt_pair"]["champion_provider_payload"],
        content=too_large,
        requested_at=100,
    )
    base["response"] = {
        "raw_content": None,
        "raw_content_sha256": hashlib.sha256(too_large).hexdigest(),
        "raw_content_bytes": len(too_large),
        "parse_status": "oversize",
        "parsed_payload": None,
        "parsed_payload_hash": None,
        "error_category": "oversize",
    }
    base["final_projection"] = None
    base["final_projection_hash"] = None
    base["decision_projection_hash"] = None
    base["output_materialized_at"] = _ts(103)
    output = build_prompt_shadow_output(
        base,
        registration=bundle["registration"],
        attempt=bundle["champion_attempt"],
        expected_user_id=USER_ID,
    )
    assert output["response"]["raw_content"] is None
    assert output["response"]["raw_content_bytes"] == len(too_large)

    retained = deepcopy(base)
    retained["response"]["raw_content"] = too_large.decode()
    with pytest.raises(PromptShadowContractError, match="exceeds the 4 MiB"):
        build_prompt_shadow_output(
            retained,
            registration=bundle["registration"],
            attempt=bundle["champion_attempt"],
            expected_user_id=USER_ID,
        )


def test_complete_receipt_and_time_chain_is_formal() -> None:
    result = validate_prompt_shadow_time_chain(**_bundle())
    assert result == {"formal": True, "reason_codes": []}


def test_receipt_delay_and_challenger_900_second_boundary() -> None:
    late = validate_prompt_shadow_time_chain(**_bundle(late_receipt="registration"))
    assert late["formal"] is False
    assert "registration_receipt_late" in late["reason_codes"]

    boundary = validate_prompt_shadow_time_chain(
        **_bundle(challenger_start_delay=900)
    )
    assert boundary["formal"] is True
    too_late = validate_prompt_shadow_time_chain(
        **_bundle(challenger_start_delay=901)
    )
    assert too_late["formal"] is False
    assert "challenger_start_late" in too_late["reason_codes"]


def test_missing_receipt_prelabel_order_and_receipt_substitution_are_rejected() -> None:
    missing = _bundle()
    missing["challenger_output_receipt"] = None
    assert validate_prompt_shadow_time_chain(**missing) == {
        "formal": False,
        "reason_codes": ["prompt_shadow_receipt_missing"],
    }

    post_label = _bundle()
    post_label["label_knowledge_boundary"] = post_label[
        "challenger_output_receipt"
    ]["source_visible_at"]
    result = validate_prompt_shadow_time_chain(**post_label)
    assert result["formal"] is False
    assert "prompt_shadow_output_not_prelabel" in result["reason_codes"]

    substituted = _bundle()
    substituted["registration_receipt"] = deepcopy(
        substituted["champion_attempt_receipt"]
    )
    substituted["registration_receipt"]["artifact_type"] = (
        PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE
    )
    with pytest.raises(PromptShadowContractError, match="content hash is invalid"):
        validate_prompt_shadow_time_chain(**substituted)


def test_legacy_and_backfill_inputs_remain_nonformal_without_upgrade() -> None:
    assert (
        prompt_shadow_nonformal_reason(
            {"schema_version": "decision_quality_prompt_shadow_registration.v0"}
        )
        == "legacy_prompt_shadow_schema_nonformal"
    )
    bundle = _bundle()
    backfill = deepcopy(bundle["registration"])
    backfill["capture_mode"] = "historical_backfill"
    assert prompt_shadow_nonformal_reason(backfill) == "prompt_shadow_backfill_nonformal"

    legacy_chain = _bundle()
    legacy_chain["policy"] = {"schema_version": "decision_quality_prompt_gate_policy.v1"}
    assert validate_prompt_shadow_time_chain(**legacy_chain) == {
        "formal": False,
        "reason_codes": ["legacy_prompt_shadow_schema_nonformal"],
    }
