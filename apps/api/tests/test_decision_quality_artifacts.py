from __future__ import annotations

import hashlib
import sqlite3

import pytest

from app.db_migrations import run_migrations
from app.services.candidate_selection_audit import (
    build_candidate_selection_audit_v2,
)
from app.services.decision_quality_artifacts import (
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3,
    CANDIDATE_AUDIT_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_MODE,
    CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS,
    CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS,
    CANDIDATE_LABEL_PLAN_SCHEMA_VERSION,
    CANDIDATE_LABEL_PLAN_SCHEMA_VERSION_V2,
    CANDIDATE_LABEL_POLICY_VERSION,
    CANDIDATE_LABEL_POLICY_VERSION_V2,
    build_candidate_label_plan,
    candidate_label_entry_not_before_date_from_post_commit_receipt,
    persist_report_decision_quality_artifacts,
)
from app.services.decision_repository import (
    canonical_hash,
    list_decision_quality_input_artifacts,
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    return connection


def test_zero_event_discovery_freezes_only_candidate_audit_at_report_receipt() -> None:
    connection = _connection()
    report = {
        "id": "zero-selection-report",
        "created_at": "2026-01-02T03:00:00+00:00",
        "discovery_facts": {
            "candidate_selection_audit": {
                "schema_version": "discovery_candidate_selection_audit.v1",
                "rows": [],
            },
            # A claim audit has no honest event/hash anchor in an abstention.
            "fund_lookthrough_claim_audit": {
                "schema_version": "fund_lookthrough_claim_audit.v1",
                "status": "clean",
            },
        },
    }

    first = persist_report_decision_quality_artifacts(
        user_id=1,
        report=report,
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-01-02T03:05:00+00:00",
        connection=connection,
    )
    retry = persist_report_decision_quality_artifacts(
        user_id=1,
        report=report,
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-01-02T03:06:00+00:00",
        connection=connection,
    )

    assert first == retry
    assert len(first) == 1
    envelope = first[0]["payload"]
    assert envelope["artifact_type"] == CANDIDATE_AUDIT_ARTIFACT_TYPE
    assert envelope["artifact_schema_version"] == (
        CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
    )
    assert envelope["decision_event_id"] is None
    assert envelope["decision_at"] == "2026-01-02T03:00:00+00:00"
    assert envelope["available_at"] == "2026-01-02T03:05:00+00:00"
    assert envelope["recorded_at"] == "2026-01-02T03:05:00+00:00"
    assert envelope["audit_eligible"] is False
    assert envelope["artifact"]["recorded_at"] == envelope["recorded_at"]
    assert envelope["artifact"]["recorded_at_source"] == (
        "report_transaction_post_insert"
    )
    assert envelope["artifact"]["registration_phase"] == "phase1_preregistered"
    assert envelope["artifact"]["provider_receipt_required"] is True
    assert envelope["artifact"]["capture_mode"] == CANDIDATE_CAPTURE_MODE
    assert envelope["artifact"]["post_commit_receipt_required"] is True
    assert envelope["artifact"]["formal_receipt_max_delay_seconds"] == 300
    assert envelope["artifact"]["formal_source_capture_max_delay_seconds"] == 300
    assert envelope["artifact"]["audit"]["schema_version"] == (
        "discovery_candidate_selection_audit.v1"
    )
    plan = envelope["artifact"]["label_plan"]
    assert plan["schema_version"] == CANDIDATE_LABEL_PLAN_SCHEMA_VERSION
    assert plan["status"] == "ineligible"
    assert plan["preregistered_at"] == envelope["recorded_at"]
    assert "registered_at" not in plan
    assert "entry_not_before_date" not in plan
    assert plan["provider_receipt_required"] is True
    assert plan["capture_mode"] == CANDIDATE_CAPTURE_MODE
    assert plan["post_commit_receipt_required"] is True
    assert plan["formal_receipt_max_delay_seconds"] == 300
    assert plan["formal_source_capture_max_delay_seconds"] == 300
    assert plan["automatic_promotion_allowed"] is False

    stored = list_decision_quality_input_artifacts(
        user_id=1,
        source_report_id="zero-selection-report",
        connection=connection,
    )
    assert stored == first


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        ({}, "candidate_selection_audit_missing"),
        (
            {"candidate_selection_audit": "not-an-object"},
            "candidate_selection_audit_not_mapping",
        ),
    ],
)
def test_missing_candidate_capture_writes_one_immutable_report_sentinel(
    facts: dict,
    reason: str,
) -> None:
    connection = _connection()
    report = {
        "id": f"capture-failure-{reason}",
        "created_at": "2026-07-15T01:00:00+00:00",
        "discovery_facts": facts,
    }
    first = persist_report_decision_quality_artifacts(
        user_id=3,
        report=report,
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T01:00:01+00:00",
        connection=connection,
    )
    retry = persist_report_decision_quality_artifacts(
        user_id=3,
        report=report,
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T01:01:00+00:00",
        connection=connection,
    )
    assert len(first) == len(retry) == 1
    assert first[0]["artifact_id"] == retry[0]["artifact_id"]
    envelope = first[0]["payload"]
    sentinel = envelope["artifact"]
    assert envelope["artifact_type"] == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE
    assert (
        envelope["artifact_schema_version"]
        == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
    )
    assert envelope["logical_key"] == f"candidate_capture_failure:{report['id']}"
    assert envelope["decision_event_id"] is None
    assert envelope["audit_eligible"] is False
    assert sentinel["source_report_id"] == report["id"]
    assert sentinel["decision_at"] == report["created_at"]
    assert sentinel["recorded_at"] == "2026-07-15T01:00:01+00:00"
    assert sentinel["capture_status"] == "capture_ineligible"
    assert sentinel["capture_reason"] == reason
    assert sentinel["capture_reason_hash"] == canonical_hash({"reason": reason})
    assert sentinel["formal_source_capture_max_delay_seconds"] == (
        CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
    )
    assert sentinel["automatic_promotion_allowed"] is False


def test_candidate_label_plan_preregisters_receipt_without_entry_date() -> None:
    early = build_candidate_label_plan(
        decision_at="2026-01-02T05:59:59+00:00",
        registered_at="2026-01-02T06:00:01+00:00",
        decision_eligible=True,
    )
    before_close = build_candidate_label_plan(
        decision_at="2026-01-02T06:59:59+00:00",
        registered_at="2026-01-02T07:00:01+00:00",
        decision_eligible=True,
    )
    at_close = build_candidate_label_plan(
        decision_at="2026-01-02T07:00:00+00:00",
        registered_at="2026-01-02T07:00:01+00:00",
        decision_eligible=True,
    )

    # Phase 1 clocks are not proof of transaction visibility.  No preregistered
    # plan may manufacture an actual entry date from them.
    assert all(
        "entry_not_before_date" not in plan
        for plan in (early, before_close, at_close)
    )
    assert before_close["same_day_entry_allowed"] is False
    assert before_close["commit_visibility_policy"] == (
        "post_commit_receipt_required"
    )
    assert before_close["schema_version"] == "candidate_label_plan.v3"
    assert before_close["policy_version"] == CANDIDATE_LABEL_POLICY_VERSION
    assert before_close["status"] == "preregistered"
    assert before_close["reason"] == "awaiting_post_commit_receipt"
    assert before_close["provider_receipt_required"] is True
    assert before_close["capture_mode"] == "live_only_no_backfill"
    assert before_close["post_commit_receipt_required"] is True
    assert before_close["formal_receipt_max_delay_seconds"] == (
        CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
    )
    assert before_close["formal_receipt_delay_basis"] == (
        "audit_post_commit_receipt_source_visible_at_minus_source_row_created_at"
    )
    assert before_close["entry_anchor_basis"] == (
        "later_of_decision_at_and_audit_post_commit_receipt_source_visible_at"
    )
    assert before_close["entry_calendar_day_rule"] == (
        "next_asia_shanghai_calendar_day"
    )
    assert before_close["entry_date_status"] == "pending_post_commit_receipt"
    assert before_close["horizon_trading_days"] == 20
    assert before_close["k"] == 3
    assert before_close["universe_stage"] == "prescreen"
    assert before_close["utility_basis"] == "total_return_percent_before_costs"
    assert len(before_close["plan_hash"]) == 64
    assert before_close["automatic_promotion_allowed"] is False

    # Phase 2 uses the later of decision time and the actual post-commit
    # visibility receipt, converted to Shanghai before adding a calendar day.
    assert candidate_label_entry_not_before_date_from_post_commit_receipt(
        decision_at="2026-01-02T15:30:00+00:00",
        audit_receipt_source_visible_at="2026-01-02T16:00:01+00:00",
    ) == "2026-01-04"


def test_candidate_label_legacy_schema_constants_remain_available() -> None:
    assert CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3 == (
        "decision_quality_candidate_audit_artifact.v3"
    )
    assert CANDIDATE_LABEL_PLAN_SCHEMA_VERSION_V2 == "candidate_label_plan.v2"
    assert CANDIDATE_LABEL_POLICY_VERSION_V2 == (
        "candidate_label_policy.2026-07.v2"
    )
    assert CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION.endswith(".v4")
    assert CANDIDATE_LABEL_PLAN_SCHEMA_VERSION.endswith(".v3")


def test_zero_event_daily_claim_audit_is_not_persisted_without_event_anchor() -> None:
    connection = _connection()
    persisted = persist_report_decision_quality_artifacts(
        user_id=1,
        report={
            "id": "daily-without-event",
            "created_at": "2026-01-02T03:00:00+00:00",
            "analysis_facts": {
                "fund_lookthrough_claim_audit": {
                    "schema_version": "fund_lookthrough_claim_audit.v1",
                    "status": "clean",
                }
            },
        },
        saved_events=[],
        source_type="daily",
        store_authority="primary",
        report_recorded_at="2026-01-02T03:05:00+00:00",
        connection=connection,
    )

    assert persisted == []
    assert list_decision_quality_input_artifacts(
        user_id=1,
        connection=connection,
    ) == []


def test_candidate_audit_phase1_does_not_forge_entry_from_insert_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    decision_at = "2026-01-02T06:59:00+00:00"
    stage_contexts = {}
    for stage in ("recall", "gate", "prescreen", "final"):
        ref_id = f"source:{stage}"
        context = {
            "version": f"{stage}.v1",
            "source_refs": [
                {
                    "ref_id": ref_id,
                    "source": "fixture",
                    "version": "v1",
                    "snapshot_hash": hashlib.sha256(ref_id.encode()).hexdigest(),
                }
            ],
            "pit_refs": [
                {
                    "fact_id": f"fact:{stage}",
                    "source_ref_id": ref_id,
                    "available_at": "2026-01-02T06:58:00+00:00",
                    "snapshot_hash": hashlib.sha256(
                        f"fact:{stage}".encode()
                    ).hexdigest(),
                }
            ],
        }
        if stage == "recall":
            context["scope"] = {
                "definition": "empty complete recall",
                "complete": True,
                "candidate_count_total": 0,
                "candidate_count_retained": 0,
                "catalogue_rows_embedded": False,
            }
        stage_contexts[stage] = context
    audit = build_candidate_selection_audit_v2(
        decision_at=decision_at,
        recall_candidates=[],
        gate_candidates=[],
        prescreen_candidates=[],
        final_candidates=[],
        versions={"selection_policy": "candidate_policy.test.v1"},
        stage_contexts=stage_contexts,
    )
    assert audit["validation"]["decision_eligible"] is True
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )

    persisted = persist_report_decision_quality_artifacts(
        user_id=1,
        report={
            "id": "cutoff-crossing-report",
            "created_at": decision_at,
            "recommendations": [],
            "discovery_facts": {"candidate_selection_audit": audit},
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-01-02T06:59:59+00:00",
        connection=connection,
    )

    assert len(persisted) == 1
    plan = persisted[0]["payload"]["artifact"]["label_plan"]
    assert plan["status"] == "preregistered"
    assert plan["preregistered_at"] == "2026-01-02T06:59:59+00:00"
    assert plan["entry_date_status"] == "pending_post_commit_receipt"
    assert "entry_not_before_date" not in plan
    assert plan["same_day_entry_allowed"] is False
