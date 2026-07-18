from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from app.config import refresh_settings
from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.services.decision_quality_evaluation import evaluate_decision_quality
from app.services.decision_quality_rollout import build_decision_quality_rollout_marker
from app.services.decision_repository import (
    DECISION_QUALITY_PROVIDER_ADAPTER_OUTPUT_MAX_BYTES,
    DecisionQualityIntegrityError,
    DecisionQualityPrimaryStoreUnavailable,
    ImmutableRecordConflict,
    canonical_hash,
    finalize_decision_quality_artifact_receipt,
    get_decision_quality_artifact_receipt,
    get_decision_quality_evaluation_snapshot,
    get_decision_quality_contract_rollout,
    get_decision_quality_input_artifact,
    get_decision_quality_provider_receipt,
    list_decision_quality_artifact_receipts,
    list_decision_quality_evaluation_snapshots,
    list_decision_quality_input_artifacts,
    list_decision_quality_provider_receipts,
    normalize_decision_quality_provider_receipt,
    put_decision_quality_evaluation_snapshot,
    put_decision_quality_input_artifact,
    put_decision_quality_provider_receipt,
    reconcile_decision_quality_artifact_receipts,
)


_AS_OF = "2026-01-10T12:00:00+00:00"


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    return connection


def _clone_quality_row(
    connection: sqlite3.Connection,
    *,
    table: str,
    source_user_id: int,
    target_user_id: int,
    overrides: dict[str, object],
) -> None:
    source = connection.execute(
        f"SELECT * FROM {table} WHERE userId = ?",
        (source_user_id,),
    ).fetchone()
    assert source is not None
    row = dict(source)
    row["userId"] = target_user_id
    row.update(overrides)
    columns = tuple(row)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def _artifact(
    *,
    recorded_at: str = "2026-01-02T03:05:00+00:00",
    store_authority: str = "primary",
    audit_eligible: bool = True,
) -> dict[str, object]:
    return {
        "artifact_type": "candidate_selection_audit",
        "source_type": "discovery",
        "source_report_id": "report-1",
        "decision_event_id": "discovery:report-1:0:000001",
        "decision_at": "2026-01-02T11:00:00+08:00",
        "available_at": "2026-01-02T03:04:00+00:00",
        "recorded_at": recorded_at,
        "store_authority": store_authority,
        "audit_eligible": audit_eligible,
        "artifact": {
            "schema_version": "discovery_candidate_selection_audit.v2",
            "decision_at": "2026-01-02T03:00:00+00:00",
            "snapshot_hash": "a" * 64,
            "rows": [],
        },
    }


def _snapshot(
    *,
    evaluation_as_of: str = _AS_OF,
    connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    evaluation = evaluate_decision_quality(
        [],
        [],
        evaluation_as_of=evaluation_as_of,
    )
    return {
        "evaluation_as_of": evaluation_as_of,
        "evaluator_version": "decision_quality_evaluator.test.v1",
        "input_manifest": {
            "schema_version": "decision_quality_input_manifest.v2",
            "contract_rollout_marker": (
                get_decision_quality_contract_rollout(connection=connection)
                if connection is not None
                else build_decision_quality_rollout_marker(
                    "2026-01-01T00:00:00+00:00"
                )
            ),
            "event_refs": [],
            "outcome_refs": [],
            "artifact_refs": [],
        },
        "config": {
            "min_calibration_samples": 30,
            "calibration_bins": 10,
            "calibration_metric": "gross_direction",
        },
        "store_authority": "primary",
        "audit_eligible": True,
        "readiness_status": "insufficient_data",
        "evaluation": evaluation,
    }


def test_schema_v16_creates_and_self_heals_decision_quality_tables() -> None:
    assert SCHEMA_VERSION == 17
    connection = _connection()
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "decision_quality_input_artifacts",
        "decision_quality_evaluation_snapshots",
        "decision_quality_artifact_receipts",
        "decision_quality_provider_receipts",
        "decision_quality_contract_rollouts",
        "prompt_shadow_runs",
        "prompt_shadow_budget_counters",
    } <= names
    marker = connection.execute(
        "SELECT contract_version, required_from, marker_hash "
        "FROM decision_quality_contract_rollouts"
    ).fetchone()
    assert marker is not None
    assert marker[0] == "decision_quality_contract.v1"
    assert "+00:00" in marker[1]
    assert len(marker[2]) == 64

    connection.execute("DROP TABLE decision_quality_evaluation_snapshots")
    run_migrations(connection)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'decision_quality_evaluation_snapshots'"
    ).fetchone()

    receipt_indexes = {
        str(row[1])
        for table in (
            "decision_quality_artifact_receipts",
            "decision_quality_provider_receipts",
        )
        for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
    }
    assert {
        "uq_decision_quality_artifact_receipt_id",
        "uq_decision_quality_artifact_receipt_content",
        "idx_decision_quality_artifact_receipts_visibility",
        "uq_decision_quality_provider_receipt_content",
        "idx_decision_quality_provider_receipts_lookup",
    } <= receipt_indexes
    receipt_triggers = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    assert {
        "decision_quality_artifact_receipts_no_update",
        "decision_quality_artifact_receipts_no_delete",
        "decision_quality_provider_receipts_no_update",
        "decision_quality_provider_receipts_no_delete",
    } <= receipt_triggers


def test_v15_receipt_schema_rejects_partial_index_and_noop_trigger() -> None:
    partial = _connection()
    partial.execute("DROP INDEX uq_decision_quality_artifact_receipt_id")
    partial.execute(
        "CREATE UNIQUE INDEX uq_decision_quality_artifact_receipt_id "
        "ON decision_quality_artifact_receipts (userId, receipt_id) "
        "WHERE receipt_id IS NOT NULL"
    )
    with pytest.raises(RuntimeError, match="index.*conflicts"):
        run_migrations(partial)

    noop = _connection()
    noop.execute("DROP TRIGGER decision_quality_provider_receipts_no_update")
    noop.execute(
        "CREATE TRIGGER decision_quality_provider_receipts_no_update "
        "BEFORE UPDATE ON decision_quality_provider_receipts BEGIN SELECT 1; END"
    )
    with pytest.raises(RuntimeError, match="trigger.*conflicts"):
        run_migrations(noop)


def test_input_artifacts_are_content_addressed_idempotent_and_user_isolated() -> None:
    connection = _connection()
    first = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=connection,
    )
    retry = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=connection,
    )
    other_user = put_decision_quality_input_artifact(
        user_id=2,
        artifact=_artifact(),
        connection=connection,
    )

    assert first == retry
    assert first["artifact_id"] == first["payload"]["artifact_id"]
    assert first["content_hash"] == first["payload"]["content_hash"]
    assert first["payload"]["decision_at"] == "2026-01-02T03:00:00+00:00"
    assert other_user["artifact_id"] == first["artifact_id"]
    assert get_decision_quality_input_artifact(
        user_id=3,
        artifact_id=str(first["artifact_id"]),
        connection=connection,
    ) is None
    assert len(
        list_decision_quality_input_artifacts(user_id=1, connection=connection)
    ) == 1
    assert len(
        list_decision_quality_input_artifacts(user_id=2, connection=connection)
    ) == 1


def test_input_artifact_receipt_allows_only_one_lamport_tick_of_future_skew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T03:05:00.000000+00:00",
    )
    connection = _connection()
    one_tick = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(recorded_at="2026-01-02T03:05:00.000001+00:00"),
        connection=connection,
    )

    assert one_tick["created_at"] == "2026-01-02T03:05:00.000001+00:00"
    with pytest.raises(
        DecisionQualityIntegrityError,
        match="recorded_at exceeds its storage receipt clock",
    ):
        put_decision_quality_input_artifact(
            user_id=2,
            artifact=_artifact(
                recorded_at="2026-01-02T03:05:00.000002+00:00"
            ),
            connection=connection,
        )
    assert len(
        list_decision_quality_input_artifacts(user_id=2, connection=connection)
    ) == 0


def _file_connection_factory(path) -> object:
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=0.2)
        connection.row_factory = sqlite3.Row
        return connection

    return factory


def test_artifact_receipt_requires_committed_source_and_is_idempotent(
    tmp_path,
) -> None:
    path = tmp_path / "artifact-receipt.db"
    factory = _file_connection_factory(path)
    writer = factory()
    run_migrations(writer)
    writer.commit()
    artifact = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=writer,
    )

    with pytest.raises(
        DecisionQualityIntegrityError,
        match="not committed or does not exist",
    ):
        finalize_decision_quality_artifact_receipt(
            user_id=1,
            artifact_id=str(artifact["artifact_id"]),
            connection_factory=factory,
        )

    writer.commit()
    writer.close()
    first = finalize_decision_quality_artifact_receipt(
        user_id=1,
        artifact_id=str(artifact["artifact_id"]),
        connection_factory=factory,
    )
    retry = finalize_decision_quality_artifact_receipt(
        user_id=1,
        artifact_id=str(artifact["artifact_id"]),
        connection_factory=factory,
    )
    assert retry == first
    assert first["artifact_content_hash"] == artifact["content_hash"]
    assert first["source_row_created_at"] == artifact["created_at"]
    assert first["source_visible_at"] >= artifact["created_at"]

    reader = factory()
    assert get_decision_quality_artifact_receipt(
        user_id=1,
        artifact_id=str(artifact["artifact_id"]),
        connection=reader,
    ) == first
    assert get_decision_quality_artifact_receipt(
        user_id=2,
        artifact_id=str(artifact["artifact_id"]),
        connection=reader,
    ) is None
    assert list_decision_quality_artifact_receipts(
        user_id=1,
        connection=reader,
    ) == [first]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reader.execute(
            "UPDATE decision_quality_artifact_receipts "
            "SET source_visible_at = '2099-01-01T00:00:00+00:00'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reader.execute("DELETE FROM decision_quality_artifact_receipts")
    reader.close()


def test_artifact_receipt_reconcile_recovers_committed_gap(tmp_path) -> None:
    path = tmp_path / "artifact-reconcile.db"
    factory = _file_connection_factory(path)
    writer = factory()
    run_migrations(writer)
    artifact = put_decision_quality_input_artifact(
        user_id=7,
        artifact=_artifact(),
        connection=writer,
    )
    writer.commit()
    writer.close()

    result = reconcile_decision_quality_artifact_receipts(
        user_id=7,
        connection_factory=factory,
    )
    assert result["status"] == "completed"
    assert result["scanned_count"] == 1
    assert result["finalized_count"] == 1
    assert result["finalized_artifact_ids"] == [artifact["artifact_id"]]
    assert reconcile_decision_quality_artifact_receipts(
        user_id=7,
        connection_factory=factory,
    )["scanned_count"] == 0


def test_artifact_receipt_concurrent_finalizers_converge(tmp_path) -> None:
    path = tmp_path / "artifact-receipt-race.db"
    factory = _file_connection_factory(path)
    writer = factory()
    run_migrations(writer)
    artifact = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=writer,
    )
    writer.commit()
    writer.close()

    def finalize() -> dict:
        return finalize_decision_quality_artifact_receipt(
            user_id=1,
            artifact_id=str(artifact["artifact_id"]),
            connection_factory=factory,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(executor.map(lambda _: finalize(), range(4)))
    assert len({row["receipt_id"] for row in receipts}) == 1
    reader = factory()
    assert reader.execute(
        "SELECT COUNT(*) FROM decision_quality_artifact_receipts"
    ).fetchone()[0] == 1
    reader.close()


def _provider_receipt(*, adapter_output: dict | None = None) -> dict[str, object]:
    return {
        "provider": "fixture-provider",
        "operation": "fund_nav_history",
        "capture_mode": "live",
        "request_hash": "1" * 64,
        "adapter_output": adapter_output or {"rows": [{"date": "2026-01-02"}]},
        "normalized_payload_hash": "2" * 64,
        "origin_fetched_at": "2026-01-02T03:00:00+00:00",
        "completed_at": "2026-01-02T03:00:01+00:00",
    }


def test_provider_receipts_are_bounded_hashed_filterable_and_append_only() -> None:
    connection = _connection()
    first = put_decision_quality_provider_receipt(
        receipt=_provider_receipt(),
        connection=connection,
    )
    retry = put_decision_quality_provider_receipt(
        receipt=_provider_receipt(),
        connection=connection,
    )
    assert retry == first
    assert first["adapter_output_bytes"] > 0
    assert len(first["adapter_output_sha256"]) == 64
    assert get_decision_quality_provider_receipt(
        receipt_id=str(first["receipt_id"]),
        connection=connection,
    ) == first
    assert list_decision_quality_provider_receipts(
        provider="fixture-provider",
        operation="fund_nav_history",
        completed_at_lte="2026-01-02T03:00:02+00:00",
        connection=connection,
    ) == [first]
    assert list_decision_quality_provider_receipts(
        provider="other-provider",
        connection=connection,
    ) == []

    bad_hash = _provider_receipt()
    bad_hash["adapter_output_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="adapter_output_sha256 mismatch"):
        normalize_decision_quality_provider_receipt(bad_hash)
    with pytest.raises(ValueError, match="byte limit"):
        normalize_decision_quality_provider_receipt(
            _provider_receipt(
                adapter_output={
                    "blob": "x"
                    * (DECISION_QUALITY_PROVIDER_ADAPTER_OUTPUT_MAX_BYTES + 1)
                }
            )
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE decision_quality_provider_receipts SET provider = 'tampered'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM decision_quality_provider_receipts")


def test_provider_receipt_reader_rehashes_inline_adapter_output() -> None:
    connection = _connection()
    saved = put_decision_quality_provider_receipt(
        receipt=_provider_receipt(),
        connection=connection,
    )
    tampered = deepcopy(saved["payload"])
    tampered["adapter_output"] = {"rows": []}
    connection.execute("DROP TRIGGER decision_quality_provider_receipts_no_update")
    connection.execute(
        "UPDATE decision_quality_provider_receipts SET payload = ? "
        "WHERE receipt_id = ?",
        (json.dumps(tampered, ensure_ascii=False), saved["receipt_id"]),
    )
    with pytest.raises(DecisionQualityIntegrityError, match="immutable contract"):
        get_decision_quality_provider_receipt(
            receipt_id=str(saved["receipt_id"]),
            connection=connection,
        )


def test_optional_logical_key_preserves_legacy_hashes_and_is_unique() -> None:
    connection = _connection()
    legacy = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=connection,
    )
    assert "logical_key" not in legacy["payload"]
    legacy_id = str(legacy["artifact_id"])
    legacy_hash = str(legacy["content_hash"])

    run_migrations(connection)
    reread = get_decision_quality_input_artifact(
        user_id=1,
        artifact_id=legacy_id,
        connection=connection,
    )
    assert reread is not None
    assert reread["artifact_id"] == legacy_id
    assert reread["content_hash"] == legacy_hash
    assert "logical_key" not in reread["payload"]

    first = _artifact(recorded_at="2026-01-02T03:06:00+00:00")
    first["logical_key"] = "candidate_audit:report-logical"
    put_decision_quality_input_artifact(
        user_id=2,
        artifact=first,
        connection=connection,
    )
    conflicting = deepcopy(first)
    conflicting["recorded_at"] = "2026-01-02T03:07:00+00:00"
    with pytest.raises(ImmutableRecordConflict):
        put_decision_quality_input_artifact(
            user_id=2,
            artifact=conflicting,
            connection=connection,
        )


def test_input_artifact_filters_use_canonical_cutoff_and_primary_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    early = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=connection,
    )
    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_test",
    )
    refresh_settings()
    try:
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=_artifact(
                recorded_at="2026-01-02T03:10:00+00:00",
                store_authority="fallback_non_audited",
                audit_eligible=False,
            ),
            connection=connection,
        )

        cutoff_rows = list_decision_quality_input_artifacts(
            user_id=1,
            recorded_at_lte="2026-01-02T11:06:00+08:00",
            connection=connection,
        )
    finally:
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()
    eligible_rows = list_decision_quality_input_artifacts(
        user_id=1,
        audit_eligible_only=True,
        connection=connection,
    )
    assert [row["artifact_id"] for row in cutoff_rows] == [early["artifact_id"]]
    assert [row["artifact_id"] for row in eligible_rows] == [early["artifact_id"]]


def test_input_artifact_rejects_hash_lies_and_detects_stored_tampering() -> None:
    connection = _connection()
    bad_hash = _artifact()
    bad_hash["content_hash"] = "0" * 64
    with pytest.raises(ValueError, match="content_hash mismatch"):
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=bad_hash,
            connection=connection,
        )

    promoted = _artifact()
    promoted["artifact"]["automatic_promotion_allowed"] = True
    with pytest.raises(ValueError, match="automatic_promotion_allowed"):
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=promoted,
            connection=connection,
        )

    wrong_type = _artifact()
    wrong_type["source_type"] = 123
    with pytest.raises(ValueError, match="source_type must be a string"):
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=wrong_type,
            connection=connection,
        )

    saved = put_decision_quality_input_artifact(
        user_id=1,
        artifact=_artifact(),
        connection=connection,
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE decision_quality_input_artifacts SET artifact_type = 'claim_audit' "
            "WHERE userId = 1 AND artifact_id = ?",
            (saved["artifact_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "DELETE FROM decision_quality_input_artifacts "
            "WHERE userId = 1 AND artifact_id = ?",
            (saved["artifact_id"],),
        )


def test_input_artifact_list_verifies_tenant_before_cutoff_filter_and_limit() -> None:
    connection = _connection()
    healthy = put_decision_quality_input_artifact(
        user_id=81,
        artifact=_artifact(recorded_at="2026-01-02T03:06:00+00:00"),
        connection=connection,
    )
    put_decision_quality_input_artifact(
        user_id=80,
        artifact=_artifact(recorded_at="2026-01-02T03:05:00+00:00"),
        connection=connection,
    )
    _clone_quality_row(
        connection,
        table="decision_quality_input_artifacts",
        source_user_id=80,
        target_user_id=81,
        # The old SQL cutoff discarded this row before its payload/index
        # conflict could be observed, silently shrinking the denominator.
        overrides={"recorded_at": "2099-01-01T00:00:00+00:00"},
    )

    with pytest.raises(
        DecisionQualityIntegrityError,
        match="index field conflicts with payload: recorded_at",
    ):
        list_decision_quality_input_artifacts(
            user_id=81,
            artifact_type="candidate_selection_audit",
            recorded_at_lte="2026-01-03T00:00:00+00:00",
            limit=1,
            connection=connection,
        )

    # A corrupt tenant partition must not poison an isolated tenant.
    assert list_decision_quality_input_artifacts(
        user_id=80,
        limit=1,
        connection=connection,
    )[0]["artifact_id"] != healthy["artifact_id"]


def test_input_artifact_tenant_scan_pages_before_semantic_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    monkeypatch.setattr(
        "app.services.decision_repository._DECISION_QUALITY_TENANT_SCAN_PAGE_SIZE",
        2,
    )
    expected_recorded_at: list[str] = []
    for minute in range(5, 12):
        recorded_at = f"2026-01-02T03:{minute:02d}:00+00:00"
        expected_recorded_at.append(recorded_at)
        put_decision_quality_input_artifact(
            user_id=82,
            artifact=_artifact(recorded_at=recorded_at),
            connection=connection,
        )

    rows = list_decision_quality_input_artifacts(
        user_id=82,
        limit=3,
        connection=connection,
    )

    assert [row["payload"]["recorded_at"] for row in rows] == sorted(
        expected_recorded_at,
        reverse=True,
    )[:3]
    assert len(
        list_decision_quality_input_artifacts(
            user_id=82,
            limit=None,
            connection=connection,
        )
    ) == 7


def test_evaluation_snapshots_are_idempotent_filterable_and_user_isolated() -> None:
    connection = _connection()
    first = put_decision_quality_evaluation_snapshot(
        user_id=1,
        snapshot=_snapshot(connection=connection),
        connection=connection,
    )
    retry = put_decision_quality_evaluation_snapshot(
        user_id=1,
        snapshot=_snapshot(connection=connection),
        connection=connection,
    )
    put_decision_quality_evaluation_snapshot(
        user_id=2,
        snapshot=_snapshot(connection=connection),
        connection=connection,
    )

    assert first == retry
    assert first["snapshot_id"] == first["payload"]["snapshot_id"]
    assert first["evaluation_hash"] == first["payload"]["evaluation_hash"]
    assert first["automatic_promotion_allowed"] is False
    assert get_decision_quality_evaluation_snapshot(
        user_id=2,
        snapshot_id=str(first["snapshot_id"]),
        connection=connection,
    ) is not None
    assert get_decision_quality_evaluation_snapshot(
        user_id=3,
        snapshot_id=str(first["snapshot_id"]),
        connection=connection,
    ) is None
    assert len(
        list_decision_quality_evaluation_snapshots(
            user_id=1,
            status="unavailable",
            readiness_status="insufficient_data",
            evaluation_as_of_lte="2026-01-10T20:00:00+08:00",
            connection=connection,
        )
    ) == 1


def test_evaluation_snapshot_rejects_any_automatic_promotion_and_fallback_store() -> None:
    connection = _connection()
    promoted = _snapshot(connection=connection)
    evaluation = deepcopy(promoted["evaluation"])
    evaluation["automatic_promotion_allowed"] = True
    evaluation["evaluation_hash"] = canonical_hash(
        {
            key: value
            for key, value in evaluation.items()
            if key != "evaluation_hash"
        }
    )
    promoted["evaluation"] = evaluation
    with pytest.raises(ValueError, match="automatic_promotion_allowed"):
        put_decision_quality_evaluation_snapshot(
            user_id=1,
            snapshot=promoted,
            connection=connection,
        )

    promoted_config = _snapshot(connection=connection)
    promoted_config["config"]["automatic_promotion_allowed"] = True
    with pytest.raises(ValueError, match="automatic promotion"):
        put_decision_quality_evaluation_snapshot(
            user_id=1,
            snapshot=promoted_config,
            connection=connection,
        )

    fallback = _snapshot(connection=connection)
    fallback["store_authority"] = "fallback_non_audited"
    fallback["audit_eligible"] = False
    with pytest.raises(ValueError, match="primary evidence store"):
        put_decision_quality_evaluation_snapshot(
            user_id=1,
            snapshot=fallback,
            connection=connection,
        )


def test_evaluation_snapshot_detects_payload_and_index_tampering() -> None:
    connection = _connection()
    saved = put_decision_quality_evaluation_snapshot(
        user_id=1,
        snapshot=_snapshot(connection=connection),
        connection=connection,
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE decision_quality_evaluation_snapshots "
            "SET automatic_promotion_allowed = 1 "
            "WHERE userId = 1 AND snapshot_id = ?",
            (saved["snapshot_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "DELETE FROM decision_quality_evaluation_snapshots "
            "WHERE userId = 1 AND snapshot_id = ?",
            (saved["snapshot_id"],),
        )


def test_latest_snapshot_cannot_hide_poisoned_time_index_behind_limit() -> None:
    connection = _connection()
    old = put_decision_quality_evaluation_snapshot(
        user_id=91,
        snapshot=_snapshot(
            evaluation_as_of="2026-01-09T12:00:00+00:00",
            connection=connection,
        ),
        connection=connection,
    )
    put_decision_quality_evaluation_snapshot(
        user_id=90,
        snapshot=_snapshot(
            evaluation_as_of="2026-01-10T12:00:00+00:00",
            connection=connection,
        ),
        connection=connection,
    )
    _clone_quality_row(
        connection,
        table="decision_quality_evaluation_snapshots",
        source_user_id=90,
        target_user_id=91,
        # Under SQL ORDER BY ... LIMIT 1 this forged old index returned `old`
        # and left the newer corrupt snapshot unverified.
        overrides={"evaluation_as_of": "1900-01-01T00:00:00+00:00"},
    )

    with pytest.raises(
        DecisionQualityIntegrityError,
        match="index field conflicts with payload: evaluation_as_of",
    ):
        list_decision_quality_evaluation_snapshots(
            user_id=91,
            limit=1,
            connection=connection,
        )

    assert list_decision_quality_evaluation_snapshots(
        user_id=90,
        limit=1,
        connection=connection,
    )[0]["snapshot_id"] != old["snapshot_id"]


@pytest.mark.parametrize("user_id", [True, 0, -1, 1.5, "1.5"])
def test_decision_quality_repository_rejects_non_positive_integer_user_ids(
    user_id: object,
) -> None:
    connection = _connection()
    with pytest.raises(ValueError, match="positive integer"):
        list_decision_quality_input_artifacts(
            user_id=user_id,
            connection=connection,
        )
    with pytest.raises(ValueError, match="positive integer"):
        list_decision_quality_evaluation_snapshots(
            user_id=user_id,
            connection=connection,
        )


def test_decision_quality_get_and_list_inputs_are_strict() -> None:
    connection = _connection()
    with pytest.raises(ValueError, match="artifact_id must start"):
        get_decision_quality_input_artifact(
            user_id=1,
            artifact_id="not-content-addressed",
            connection=connection,
        )
    with pytest.raises(ValueError, match="snapshot_id must start"):
        get_decision_quality_evaluation_snapshot(
            user_id=1,
            snapshot_id="not-content-addressed",
            connection=connection,
        )
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        list_decision_quality_input_artifacts(
            user_id=1,
            limit=0,
            connection=connection,
        )
    with pytest.raises(ValueError, match="audit_eligible_only must be a boolean"):
        list_decision_quality_input_artifacts(
            user_id=1,
            audit_eligible_only="false",
            connection=connection,
        )
    with pytest.raises(ValueError, match="status is unsupported"):
        list_decision_quality_evaluation_snapshots(
            user_id=1,
            status="partial",
            connection=connection,
        )


def test_active_store_authority_is_enforced_for_artifacts_and_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    saved_snapshot = put_decision_quality_evaluation_snapshot(
        user_id=1,
        snapshot=_snapshot(connection=connection),
        connection=connection,
    )
    fallback_artifact = _artifact(
        store_authority="fallback_non_audited",
        audit_eligible=False,
    )
    with pytest.raises(ValueError, match="active evidence store"):
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=fallback_artifact,
            connection=connection,
        )

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_test",
    )
    refresh_settings()
    try:
        with pytest.raises(ValueError, match="active evidence store"):
            put_decision_quality_input_artifact(
                user_id=1,
                artifact=_artifact(),
                connection=connection,
            )
        put_decision_quality_input_artifact(
            user_id=1,
            artifact=fallback_artifact,
            connection=connection,
        )

        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            put_decision_quality_evaluation_snapshot(
                user_id=1,
                snapshot=_snapshot(connection=connection),
                connection=connection,
            )
        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            get_decision_quality_evaluation_snapshot(
                user_id=1,
                snapshot_id=str(saved_snapshot["snapshot_id"]),
                connection=connection,
            )
        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            list_decision_quality_evaluation_snapshots(
                user_id=1,
                connection=connection,
            )
        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            list_decision_quality_input_artifacts(
                user_id=1,
                audit_eligible_only=True,
                connection=connection,
            )
        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            put_decision_quality_provider_receipt(
                receipt=_provider_receipt(),
                connection=connection,
            )
        with pytest.raises(DecisionQualityPrimaryStoreUnavailable):
            list_decision_quality_provider_receipts(connection=connection)
    finally:
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()
