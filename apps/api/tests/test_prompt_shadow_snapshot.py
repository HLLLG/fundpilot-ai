from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from app.db_migrations import run_migrations
from app.services.decision_quality_snapshot import (
    build_decision_quality_snapshot,
    redact_decision_quality_snapshot,
)
from app.services.decision_repository import (
    canonical_json,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_evaluation_snapshot,
)
from app.services.prompt_shadow_contracts import build_prompt_shadow_input_artifact
from test_prompt_shadow_contracts import USER_ID, _bundle
from scripts.migrate_sqlite_to_mysql import (
    MigrationError,
    _validate_snapshot_manifest_closure,
)


def _artifact_row(artifact: dict, receipt_ref: dict) -> dict:
    envelope = build_prompt_shadow_input_artifact(user_id=USER_ID, artifact=artifact)
    assert envelope["artifact_id"] == receipt_ref["artifact_id"]
    return {
        "userId": USER_ID,
        "payload": envelope,
        "created_at": receipt_ref["source_row_created_at"],
    }


def _receipt_row(receipt_ref: dict) -> dict:
    payload = normalize_decision_quality_artifact_receipt(
        {
            "user_id": USER_ID,
            "artifact_id": receipt_ref["artifact_id"],
            "artifact_type": receipt_ref["artifact_type"],
            "artifact_content_hash": receipt_ref["artifact_content_hash"],
            "source_row_created_at": receipt_ref["source_row_created_at"],
            "source_visible_at": receipt_ref["source_visible_at"],
            "store_authority": "primary",
        }
    )
    assert payload["receipt_id"] == receipt_ref["receipt_id"]
    assert payload["content_hash"] == receipt_ref["receipt_content_hash"]
    return {
        "userId": USER_ID,
        "payload": payload,
        "created_at": receipt_ref["source_visible_at"],
    }


def _insert_artifact_row(connection: sqlite3.Connection, row: dict) -> None:
    payload = row["payload"]
    connection.execute(
        "INSERT INTO decision_quality_input_artifacts "
        "(userId, artifact_id, schema_version, artifact_type, "
        "artifact_schema_version, logical_key, source_type, source_report_id, "
        "decision_event_id, decision_at, available_at, recorded_at, "
        "store_authority, audit_eligible, content_hash, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["userId"],
            payload["artifact_id"],
            payload["schema_version"],
            payload["artifact_type"],
            payload["artifact_schema_version"],
            payload["logical_key"],
            payload["source_type"],
            payload["source_report_id"],
            payload["decision_event_id"],
            payload["decision_at"],
            payload["available_at"],
            payload["recorded_at"],
            payload["store_authority"],
            int(payload["audit_eligible"]),
            payload["content_hash"],
            canonical_json(payload),
            row["created_at"],
        ),
    )


def _insert_receipt_row(connection: sqlite3.Connection, row: dict) -> None:
    payload = row["payload"]
    connection.execute(
        "INSERT INTO decision_quality_artifact_receipts "
        "(userId, receipt_id, schema_version, receipt_policy, artifact_id, "
        "artifact_type, artifact_content_hash, source_row_created_at, "
        "source_visible_at, store_authority, content_hash, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["userId"],
            payload["receipt_id"],
            payload["schema_version"],
            payload["receipt_policy"],
            payload["artifact_id"],
            payload["artifact_type"],
            payload["artifact_content_hash"],
            payload["source_row_created_at"],
            payload["source_visible_at"],
            payload["store_authority"],
            payload["content_hash"],
            canonical_json(payload),
            row["created_at"],
        ),
    )


def test_snapshot_v4_counts_incomplete_assignment_and_exposes_only_safe_gate(
) -> None:
    bundle = _bundle()
    artifact_rows = [
        _artifact_row(bundle["policy"], bundle["policy_receipt"]),
        _artifact_row(bundle["registration"], bundle["registration_receipt"]),
    ]
    receipt_rows = [
        _receipt_row(bundle["policy_receipt"]),
        _receipt_row(bundle["registration_receipt"]),
    ]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    for row in artifact_rows:
        _insert_artifact_row(connection, row)
    for row in receipt_rows:
        _insert_receipt_row(connection, row)
    connection.commit()

    snapshot = build_decision_quality_snapshot(
        user_id=USER_ID,
        evaluation_as_of=bundle["evaluation_as_of"],
        window_days=365,
        connection=connection,
    )
    normalized = normalize_decision_quality_evaluation_snapshot(snapshot)
    manifest = normalized["input_manifest"]
    gate = normalized["evaluation"]["prompt_shadow_gate"]

    assert manifest["schema_version"] == "decision_quality_input_manifest.v4"
    assert manifest["prompt_shadow_input_artifact_count"] == 2
    assert manifest["prompt_shadow_artifact_receipt_count"] == 2
    assert manifest["prompt_shadow_assigned_registration_count"] == 1
    assert manifest["prompt_shadow_paired_case_count"] == 0
    assert manifest["prompt_shadow_gate_count"] == 1
    assert gate["status"] == "shadow_evaluation"
    assert gate["assigned_registration_count"] == 1
    assert gate["challenger_valid_completion_rate"] == 0.0
    assert gate["automatic_promotion_allowed"] is False

    redacted = redact_decision_quality_snapshot(normalized)
    assert redacted["prompt_shadow_gate"]["status"] == "shadow_evaluation"
    assert redacted["input_counts"]["prompt_shadow_assigned_registration_count"] == 1
    serialized = str({"manifest": manifest, "redacted": redacted})
    assert "You are the production champion" not in serialized
    assert "You are the preregistered challenger" not in serialized
    assert "template_snapshot" not in serialized

    _validate_snapshot_manifest_closure(
        connection,
        user_id=USER_ID,
        manifest=manifest,
        evaluation=normalized["evaluation"],
    )


def test_migration_v4_closure_validates_prompt_manifest_hash_and_gate_refs() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    snapshot = build_decision_quality_snapshot(
        user_id=99,
        evaluation_as_of="2026-07-15T12:00:00+00:00",
        window_days=365,
        connection=connection,
    )

    _validate_snapshot_manifest_closure(
        connection,
        user_id=99,
        manifest=snapshot["input_manifest"],
        evaluation=snapshot["evaluation"],
    )
    tampered = deepcopy(snapshot["input_manifest"])
    tampered["prompt_shadow_evidence"]["assigned_registration_count"] = 1
    with pytest.raises(MigrationError, match="manifest hash mismatch"):
        _validate_snapshot_manifest_closure(
            connection,
            user_id=99,
            manifest=tampered,
            evaluation=snapshot["evaluation"],
        )
