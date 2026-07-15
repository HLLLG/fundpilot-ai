from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services.decision_repository import canonical_hash
from app.services.prompt_shadow_contracts import build_prompt_shadow_registration
from app.services.prompt_shadow_evaluation import (
    PROMPT_SHADOW_EVALUATOR_VERSION,
    PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION,
    PromptShadowEvaluationError,
    build_prompt_shadow_paired_case,
    evaluate_prompt_shadow_gate,
    normalize_prompt_shadow_paired_case,
    prompt_shadow_stratum_hash,
)
from test_prompt_shadow_contracts import _bundle


def _registration(index: int, *, policy: dict) -> dict:
    value = deepcopy(_bundle()["registration"])
    value.pop("registration_hash")
    value["run_id"] = "dqsr_" + canonical_hash({"run": index})
    return build_prompt_shadow_registration(value, policy=policy)


def _case(
    registration: dict,
    *,
    day: str,
    utility_delta: float = 0.06,
    drawdown_delta: float = -0.02,
    challenger_status: str = "valid",
    formal: bool = True,
) -> dict:
    policy_ref = registration["policy_ref"]
    champion_hash = canonical_hash(
        {"run_id": registration["run_id"], "role": "champion"}
    )
    challenger_hash = canonical_hash(
        {"run_id": registration["run_id"], "role": "challenger"}
    )
    reasons = [] if formal else ["prompt_shadow_outcome_labels_incomplete"]
    source_refs = [
        {
            "artifact_id": f"artifact-{index}",
            "artifact_content_hash": canonical_hash(
                {"run_id": registration["run_id"], "artifact": index}
            ),
            "receipt_id": f"receipt-{index}",
            "receipt_content_hash": canonical_hash(
                {"run_id": registration["run_id"], "receipt": index}
            ),
        }
        for index in range(8)
    ]
    value = {
        "schema_version": PROMPT_SHADOW_PAIRED_CASE_SCHEMA_VERSION,
        "evaluator_version": PROMPT_SHADOW_EVALUATOR_VERSION,
        "case_id": "dqpsc_"
        + canonical_hash(
            {
                "run_id": registration["run_id"],
                "policy_hash": policy_ref["policy_hash"],
            }
        ),
        "run_id": registration["run_id"],
        "policy_id": policy_ref["policy_id"],
        "policy_hash": policy_ref["policy_hash"],
        "stratum_hash": prompt_shadow_stratum_hash(registration),
        "decision_at": registration["decision_at"],
        "live_cohort_date_local": day if formal else None,
        "label_knowledge_boundary": (
            datetime.fromisoformat(registration["decision_at"]) + timedelta(days=30)
        ).isoformat()
        if formal
        else None,
        "champion_decision_projection_hash": champion_hash,
        "challenger_decision_projection_hash": challenger_hash,
        "differing": True,
        "champion_parse_status": "valid",
        "challenger_parse_status": challenger_status,
        "champion_claim_status": "clean",
        "challenger_claim_status": "clean",
        "champion_utility_percent": 1.0 if formal else None,
        "challenger_utility_percent": round(1.0 + utility_delta, 8)
        if formal
        else None,
        "utility_delta_pp": utility_delta if formal else None,
        "champion_max_drawdown_percent": 2.0 if formal else None,
        "challenger_max_drawdown_percent": round(2.0 + drawdown_delta, 8)
        if formal
        else None,
        "drawdown_delta_pp": drawdown_delta if formal else None,
        "formal": formal,
        "reason_codes": reasons,
        "source_refs": source_refs,
        "automatic_promotion_allowed": False,
    }
    value["content_hash"] = canonical_hash(value)
    return normalize_prompt_shadow_paired_case(value)


def test_gate_is_deterministic_day_clustered_and_manual_review_only() -> None:
    policy = _bundle()["policy"]
    registrations = [_registration(index, policy=policy) for index in range(60)]
    cases = [
        _case(registration, day=f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}")
        for index, registration in enumerate(registrations)
    ]

    first = evaluate_prompt_shadow_gate(
        policy=policy,
        registrations=registrations,
        paired_cases=cases,
        evaluation_as_of="2026-12-31T23:59:59+00:00",
    )
    second = evaluate_prompt_shadow_gate(
        policy=policy,
        registrations=list(reversed(registrations)),
        paired_cases=list(reversed(cases)),
        evaluation_as_of="2026-12-31T23:59:59+00:00",
    )

    assert first == second
    assert first["status"] == "ready_for_manual_review"
    assert first["mature_decision_day_count"] == 60
    assert first["paired_label_coverage"] == 1.0
    assert first["mean_utility_delta_pp"] == 0.06
    assert first["utility_ci95_pp"] == {"lower": 0.06, "upper": 0.06}
    assert first["mean_drawdown_delta_pp"] == -0.02
    assert first["drawdown_ci95_pp"] == {"lower": -0.02, "upper": -0.02}
    assert all(first["threshold_results"].values())
    assert first["automatic_promotion_allowed"] is False


def test_complete_receipted_bundle_derives_t20_path_metrics_without_raw_content() -> None:
    bundle = _bundle()
    candidate_ref = bundle["champion_output"]["candidate_audit_ref"]
    observations = [
        {
            "date": (datetime(2026, 7, 16, tzinfo=timezone.utc) + timedelta(days=index))
            .date()
            .isoformat(),
            "nav": round(1.0 * (1.01**index), 10),
            "daily_growth": None if index == 0 else 1.0,
        }
        for index in range(21)
    ]
    candidate_case = {
        "audit_artifact_id": candidate_ref["artifact_id"],
        "audit_content_hash": candidate_ref["artifact_content_hash"],
        "audit_commit_receipt_id": candidate_ref["receipt_id"],
        "audit_commit_receipt_content_hash": candidate_ref[
            "receipt_content_hash"
        ],
        "outcome_commit_receipt_status": "verified",
        "outcome_artifact_id": "dqa_" + canonical_hash({"outcome": 1}),
        "outcome_content_hash": canonical_hash({"outcome-content": 1}),
        "outcome_commit_receipt_id": "dqr_" + canonical_hash(
            {"outcome-receipt": 1}
        ),
        "outcome_commit_receipt_content_hash": canonical_hash(
            {"outcome-receipt-content": 1}
        ),
        "label_storage_created_at": bundle["label_knowledge_boundary"],
        "horizon_trading_days": 20,
        "live_cohort_date_local": "2026-07-16",
        "outcome_labels": {
            "000001": {
                "mature": True,
                "eligible": True,
                "evidence": {"observations": observations},
            }
        },
    }

    case = build_prompt_shadow_paired_case(
        policy=bundle["policy"],
        policy_receipt=bundle["policy_receipt"],
        registration=bundle["registration"],
        registration_receipt=bundle["registration_receipt"],
        champion_attempt=bundle["champion_attempt"],
        champion_attempt_receipt=bundle["champion_attempt_receipt"],
        champion_output=bundle["champion_output"],
        champion_output_receipt=bundle["champion_output_receipt"],
        challenger_attempt=bundle["challenger_attempt"],
        challenger_attempt_receipt=bundle["challenger_attempt_receipt"],
        challenger_output=bundle["challenger_output"],
        challenger_output_receipt=bundle["challenger_output_receipt"],
        candidate_case=candidate_case,
        evaluation_as_of=bundle["evaluation_as_of"],
        expected_user_id=bundle["expected_user_id"],
    )

    expected_return = round(0.6 * ((1.01**20) - 1.0) * 100.0, 8)
    assert case["formal"] is True
    assert case["differing"] is False
    assert case["champion_utility_percent"] == expected_return
    assert case["challenger_utility_percent"] == expected_return
    assert case["utility_delta_pp"] == 0.0
    assert case["champion_max_drawdown_percent"] == 0.0
    assert case["drawdown_delta_pp"] == 0.0
    assert len(case["source_refs"]) == 8
    assert "raw_content" not in str(case)
    assert "template_snapshot" not in str(case)


def test_gate_keeps_missing_and_failed_attempts_in_assignment_denominator() -> None:
    policy = _bundle()["policy"]
    registrations = [_registration(index, policy=policy) for index in range(25)]
    cases = [
        _case(registration, day=f"2026-01-{1 + index:02d}")
        for index, registration in enumerate(registrations[:20])
    ]
    cases.append(
        _case(
            registrations[20],
            day="2026-01-21",
            challenger_status="timeout",
            formal=False,
        )
    )

    gate = evaluate_prompt_shadow_gate(
        policy=policy,
        registrations=registrations,
        paired_cases=cases,
        evaluation_as_of="2026-12-31T23:59:59+00:00",
    )

    assert gate["assigned_registration_count"] == 25
    assert gate["formal_paired_case_count"] == 20
    assert gate["paired_label_coverage"] == 0.8
    assert gate["challenger_valid_completion_rate"] == 0.8
    assert gate["challenger_timeout_rate"] == 0.04
    assert gate["status"] == "shadow_evaluation"
    assert gate["threshold_results"]["minimum_challenger_valid_completion_rate"] is False


def test_gate_equal_weights_live_days_instead_of_high_volume_cases() -> None:
    policy = _bundle()["policy"]
    registrations = [_registration(index, policy=policy) for index in range(69)]
    cases = [
        _case(registration, day="2026-01-01", utility_delta=1.0)
        for registration in registrations[:10]
    ]
    cases.extend(
        _case(
            registration,
            day=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index))
            .date()
            .isoformat(),
            utility_delta=0.06,
        )
        for index, registration in enumerate(registrations[10:], start=1)
    )

    gate = evaluate_prompt_shadow_gate(
        policy=policy,
        registrations=registrations,
        paired_cases=cases,
        evaluation_as_of="2026-12-31T23:59:59+00:00",
    )

    assert gate["mature_decision_day_count"] == 60
    assert gate["day_cluster_count"] == 60
    assert gate["mean_utility_delta_pp"] == round((1.0 + 59 * 0.06) / 60, 8)
    assert gate["mean_utility_delta_pp"] != round((10 * 1.0 + 59 * 0.06) / 69, 8)


def test_gate_rejects_mixed_execution_strata() -> None:
    policy = _bundle()["policy"]
    left = _registration(1, policy=policy)
    right_value = deepcopy(_bundle()["registration"])
    right_value.pop("registration_hash")
    right_value["run_id"] = "dqsr_" + canonical_hash({"run": "stream"})
    right_value["prompt_pair"]["transport"] = "stream"
    for role in ("champion", "challenger"):
        payload = right_value["prompt_pair"][f"{role}_provider_payload"]
        payload["stream"] = True
        right_value["prompt_pair"][f"{role}_provider_payload_hash"] = canonical_hash(
            payload
        )
    right = build_prompt_shadow_registration(right_value, policy=policy)

    with pytest.raises(PromptShadowEvaluationError, match="cannot mix execution strata"):
        evaluate_prompt_shadow_gate(
            policy=policy,
            registrations=[left, right],
            paired_cases=[],
            evaluation_as_of=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )


def test_paired_case_hash_and_delta_tampering_fail_closed() -> None:
    policy = _bundle()["policy"]
    registration = _registration(1, policy=policy)
    value = _case(registration, day="2026-01-01")
    value["utility_delta_pp"] = 99.0
    value["content_hash"] = canonical_hash(
        {key: item for key, item in value.items() if key != "content_hash"}
    )

    with pytest.raises(PromptShadowEvaluationError, match="metric delta"):
        normalize_prompt_shadow_paired_case(value)
