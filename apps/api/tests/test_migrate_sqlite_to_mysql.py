from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Sequence

import pytest

from app.db_migrations import run_migrations
from app.services.decision_quality_provider_receipts import (
    build_provider_origin_receipt,
)
from app.services.decision_quality_rollout import (
    build_decision_quality_rollout_marker,
    normalize_decision_quality_rollout_marker,
)
from app.services.decision_repository import (
    canonical_json,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_evaluation_snapshot,
    normalize_decision_quality_input_artifact,
    normalize_decision_quality_provider_receipt,
)
from scripts import migrate_sqlite_to_mysql as migration


_ROLLOUT_COLUMNS = (
    "contract_name",
    "schema_version",
    "contract_version",
    "required_from",
    "created_at",
    "hash_algorithm",
    "canonicalization",
    "marker_hash",
)


def _source(path, *, version: int, include_marker: bool = True) -> dict[str, str] | None:
    marker = (
        build_decision_quality_rollout_marker("2026-07-14T00:00:00+00:00")
        if include_marker
        else None
    )
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (version,),
    )
    if include_marker:
        connection.execute(
            """
            CREATE TABLE decision_quality_contract_rollouts (
                contract_name TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                required_from TEXT NOT NULL,
                created_at TEXT NOT NULL,
                hash_algorithm TEXT NOT NULL,
                canonicalization TEXT NOT NULL,
                marker_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        assert marker is not None
        connection.execute(
            "INSERT INTO decision_quality_contract_rollouts "
            f"({', '.join(_ROLLOUT_COLUMNS)}) VALUES "
            f"({', '.join('?' for _ in _ROLLOUT_COLUMNS)})",
            tuple(marker[column] for column in _ROLLOUT_COLUMNS),
        )
    connection.commit()
    connection.close()
    return marker


def _current_source(path) -> dict[str, str]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.commit()
    row = connection.execute(
        "SELECT * FROM decision_quality_contract_rollouts"
    ).fetchone()
    assert row is not None
    marker = normalize_decision_quality_rollout_marker(dict(row))
    connection.close()
    return marker


def _valid_provider_receipt() -> dict[str, Any]:
    normalized_payload = {"data": [{"date": "2026-07-14", "nav": 1.25}]}
    origin = build_provider_origin_receipt(
        provider_id="akshare.fixture",
        operation="fund_open_fund_info_em",
        request_parameters={"fund_code": "000001", "trading_days": 30},
        request_started_at="2026-07-15T00:00:00+00:00",
        response_completed_at="2026-07-15T00:00:01+00:00",
        response_status="success",
        adapter_contract_version="fixture_adapter.v1",
        adapter_script="print('fixture')",
        library_name="akshare",
        library_version="1.17.0",
        python_version="3.13.5",
        cache_policy="fixture_hour_cache.v1",
        cache_key_material={"fund_code": "000001", "hour": 0},
        stdout_bytes=b'{"data":[]}',
        parsed_payload=normalized_payload,
        normalized_payload=normalized_payload,
        upstream_raw_unavailable_reason="fixture adapter boundary",
    )
    return normalize_decision_quality_provider_receipt(
        {
            "provider": origin["provider_id"],
            "operation": origin["operation"],
            "capture_mode": origin["capture_mode"],
            "request_hash": origin["request"]["request_hash"],
            "adapter_output": origin,
            "normalized_payload_hash": origin["response"][
                "normalized_payload_hash"
            ],
            "origin_fetched_at": origin["cache"]["origin_fetched_at"],
            "completed_at": origin["response"]["completed_at"],
        }
    )


def _insert_provider_receipt(
    connection: sqlite3.Connection,
    receipt: dict[str, Any],
) -> None:
    columns = dict(migration.TABLES)["decision_quality_provider_receipts"]
    values = {
        **receipt,
        "payload": canonical_json(receipt),
        "created_at": receipt["completed_at"],
    }
    connection.execute(
        "INSERT INTO decision_quality_provider_receipts "
        f"({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _v3_manifest(
    marker: dict[str, str],
    *,
    evaluation_as_of: str,
) -> dict[str, Any]:
    return {
        "schema_version": "decision_quality_input_manifest.v3",
        "contract_rollout_marker": marker,
        "window_start": "2025-07-15T00:00:00+00:00",
        "evaluation_as_of": evaluation_as_of,
        "decision_event_count": 0,
        "nonformal_decision_event_count": 0,
        "observed_decision_event_count": 0,
        "terminal_outcome_count": 0,
        "input_artifact_count": 0,
        "consumed_input_artifact_count": 0,
        "ignored_artifact_count": 0,
        "artifact_receipt_count": 0,
        "provider_receipt_count": 0,
        "candidate_capture_count": 0,
        "candidate_capture_status_counts": {},
        "candidate_capture_reason_counts": {},
        "candidate_capture_records": [],
        "mature_decision_dates": [],
        "mature_decision_day_count": 0,
        "decision_events": [],
        "nonformal_decision_events": [],
        "terminal_outcomes": [],
        "input_artifacts": [],
        "ignored_input_artifacts": [],
        "artifact_receipts": [],
        "provider_receipts": [],
    }


def _insert_evaluation_snapshot(
    connection: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    evaluation_as_of: str,
) -> dict[str, Any]:
    from app.services.decision_quality_evaluation import evaluate_decision_quality

    evaluation = evaluate_decision_quality(
        [],
        [],
        evaluation_as_of=evaluation_as_of,
    )
    snapshot = normalize_decision_quality_evaluation_snapshot(
        {
            "evaluation_as_of": evaluation_as_of,
            "evaluator_version": "decision_quality_evaluator.migration-test.v1",
            "input_manifest": manifest,
            "config": {"window_days": 365},
            "store_authority": "primary",
            "audit_eligible": True,
            "readiness_status": "insufficient_data",
            "evaluation": evaluation,
        }
    )
    columns = dict(migration.TABLES)[
        "decision_quality_evaluation_snapshots"
    ]
    values = {
        **snapshot,
        "userId": 1,
        "payload": canonical_json(snapshot),
        "created_at": evaluation_as_of,
    }
    connection.execute(
        "INSERT INTO decision_quality_evaluation_snapshots "
        f"({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    return snapshot


class _SqliteAsMysqlCursor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._cursor: sqlite3.Cursor | None = None

    def execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> "_SqliteAsMysqlCursor":
        self._cursor = self._connection.execute(
            sql.replace("%s", "?"),
            tuple(params),
        )
        return self

    def fetchone(self) -> object:
        assert self._cursor is not None
        return self._cursor.fetchone()

    def fetchall(self) -> list[object]:
        assert self._cursor is not None
        return list(self._cursor.fetchall())


class _SqliteAsMysqlConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def cursor(self) -> _SqliteAsMysqlCursor:
        return _SqliteAsMysqlCursor(self.connection)


def _create_destination_table(
    connection: sqlite3.Connection,
    table: str,
) -> None:
    columns = dict(migration.TABLES)[table]
    definitions = [
        f"{column} {'INTEGER' if column == 'adapter_output_bytes' else 'TEXT'}"
        for column in columns
    ]
    connection.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")


def _insert_atomic_quality_pair(path) -> None:
    now = datetime.now(timezone.utc) - timedelta(seconds=2)
    recorded_at = now.isoformat()
    artifact = normalize_decision_quality_input_artifact(
        {
            "artifact_type": "candidate_selection_audit",
            "source_type": "discovery",
            "source_report_id": "concurrent-report",
            "decision_event_id": "discovery:concurrent-report:0:000001",
            "decision_at": recorded_at,
            "available_at": recorded_at,
            "recorded_at": recorded_at,
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": {
                "schema_version": "discovery_candidate_selection_audit.v4",
                "automatic_promotion_allowed": False,
                "rows": [],
            },
        }
    )
    visible_at = (now + timedelta(microseconds=1)).isoformat()
    receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": 1,
            "artifact_id": artifact["artifact_id"],
            "artifact_type": artifact["artifact_type"],
            "artifact_content_hash": artifact["content_hash"],
            "source_row_created_at": recorded_at,
            "source_visible_at": visible_at,
            "store_authority": "primary",
        }
    )
    writer = sqlite3.connect(path, timeout=5)
    try:
        artifact_columns = dict(migration.TABLES)[
            "decision_quality_input_artifacts"
        ]
        artifact_values = {
            **artifact,
            "userId": 1,
            "payload": canonical_json(artifact),
            "created_at": recorded_at,
        }
        writer.execute(
            "INSERT INTO decision_quality_input_artifacts "
            f"({', '.join(artifact_columns)}) VALUES "
            f"({', '.join('?' for _ in artifact_columns)})",
            tuple(artifact_values.get(column) for column in artifact_columns),
        )
        receipt_columns = dict(migration.TABLES)[
            "decision_quality_artifact_receipts"
        ]
        receipt_values = {
            **receipt,
            "userId": 1,
            "payload": canonical_json(receipt),
            "created_at": visible_at,
        }
        writer.execute(
            "INSERT INTO decision_quality_artifact_receipts "
            f"({', '.join(receipt_columns)}) VALUES "
            f"({', '.join('?' for _ in receipt_columns)})",
            tuple(receipt_values.get(column) for column in receipt_columns),
        )
        writer.commit()
    finally:
        writer.close()


def test_migration_catalog_covers_every_decision_quality_ledger() -> None:
    tables = {table: columns for table, columns in migration.TABLES}
    expected = {
        "decision_quality_input_artifacts",
        "decision_quality_artifact_receipts",
        "decision_quality_provider_receipts",
        "decision_quality_evaluation_snapshots",
        "decision_quality_contract_rollouts",
    }

    assert expected <= set(tables)
    assert expected <= set(migration.IMMUTABLE_TABLES)
    assert "logical_key" in tables["decision_quality_input_artifacts"]
    assert migration.SOURCE_COLUMN_DEFAULTS[
        "decision_quality_input_artifacts"
    ]["logical_key"] == "NULL"


def test_plan_preserves_the_exact_source_rollout_marker(tmp_path) -> None:
    path = tmp_path / "source-v15.db"
    marker = _current_source(path)

    plan = migration.plan_sqlite_source(path)

    assert marker is not None
    assert plan["decision_quality_rollout_marker"] == {
        "status": "preserved",
        "contract_name": marker["contract_name"],
        "marker_hash": marker["marker_hash"],
    }
    rollout = next(
        row
        for row in plan["tables"]
        if row["table"] == "decision_quality_contract_rollouts"
    )
    assert rollout["write_policy"] == "insert_only_compare"
    assert rollout["rows"] == 1


def test_v14_source_missing_rollout_marker_is_not_healed(tmp_path) -> None:
    path = tmp_path / "source-v14-missing-marker.db"
    _source(path, version=14, include_marker=False)

    with pytest.raises(
        migration.MigrationError,
        match="missing decision-quality rollout table",
    ):
        migration.plan_sqlite_source(path)


def test_pre_v14_source_can_establish_a_new_destination_boundary(tmp_path) -> None:
    path = tmp_path / "source-v13.db"
    _source(path, version=13, include_marker=False)

    plan = migration.plan_sqlite_source(path)

    assert plan["decision_quality_rollout_marker"] == {
        "status": "source_pre_v14_or_absent"
    }


def test_tampered_source_rollout_marker_fails_closed(tmp_path) -> None:
    path = tmp_path / "source-v15-tampered.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER decision_quality_rollout_no_update")
    connection.execute(
        "UPDATE decision_quality_contract_rollouts SET marker_hash = ?",
        ("0" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        migration.MigrationError,
        match="failed canonical validation",
    ):
        migration.plan_sqlite_source(path)


@pytest.mark.parametrize(
    "table",
    [
        "decision_quality_input_artifacts",
        "decision_quality_artifact_receipts",
        "decision_quality_provider_receipts",
        "decision_quality_evaluation_snapshots",
    ],
)
def test_v15_source_missing_quality_ledger_fails_closed(tmp_path, table: str) -> None:
    path = tmp_path / f"source-v15-missing-{table}.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    connection.execute(f"DROP TABLE {table}")
    connection.commit()
    connection.close()

    with pytest.raises(
        migration.MigrationError,
        match=rf"missing required decision-quality table {table}",
    ):
        migration.plan_sqlite_source(path)


@pytest.mark.parametrize(
    "mutation",
    ["missing_trigger", "wrong_unique_index"],
)
def test_v15_source_quality_schema_tampering_fails_closed(
    tmp_path,
    mutation: str,
) -> None:
    path = tmp_path / f"source-v15-{mutation}.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    if mutation == "missing_trigger":
        connection.execute(
            "DROP TRIGGER decision_quality_provider_receipts_no_delete"
        )
    else:
        connection.execute(
            "DROP INDEX uq_decision_quality_artifact_receipt_content"
        )
        connection.execute(
            "CREATE INDEX uq_decision_quality_artifact_receipt_content "
            "ON decision_quality_artifact_receipts (userId, content_hash)"
        )
    connection.commit()
    connection.close()

    with pytest.raises(
        migration.MigrationError,
        match="source decision-quality contract mismatch",
    ):
        migration.plan_sqlite_source(path)


def test_v14_source_requires_v14_ledgers_but_not_v15_receipts(tmp_path) -> None:
    path = tmp_path / "source-v14-complete.db"
    marker = _current_source(path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE schema_meta SET version = 14 WHERE id = 1")
    connection.execute("DROP TABLE decision_quality_artifact_receipts")
    connection.execute("DROP TABLE decision_quality_provider_receipts")
    connection.execute("DROP TABLE prompt_shadow_runs")
    connection.execute("DROP TABLE prompt_shadow_budget_counters")
    connection.execute("DROP TABLE factor_ic_nav_observations")
    connection.execute("DROP TABLE password_reset_tokens")
    connection.execute("DROP TABLE admin_audit_events")
    connection.commit()
    connection.close()

    plan = migration.plan_sqlite_source(path)

    assert plan["source_schema_version"] == 14
    assert plan["decision_quality_rollout_marker"]["marker_hash"] == marker[
        "marker_hash"
    ]


def test_true_v14_input_table_without_logical_key_remains_compatible(
    tmp_path,
) -> None:
    path = tmp_path / "source-v14-pre-logical-key.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE decision_quality_input_artifacts")
    connection.executescript(
        """
        CREATE TABLE decision_quality_input_artifacts (
            userId INTEGER NOT NULL,
            artifact_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_schema_version TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_report_id TEXT,
            decision_event_id TEXT,
            decision_at TEXT,
            available_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            store_authority TEXT NOT NULL,
            audit_eligible INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, artifact_id),
            UNIQUE (userId, artifact_type, content_hash)
        );
        CREATE INDEX idx_decision_quality_artifacts_report
        ON decision_quality_input_artifacts
            (userId, artifact_type, source_report_id, recorded_at DESC);
        CREATE INDEX idx_decision_quality_artifacts_event
        ON decision_quality_input_artifacts
            (userId, decision_event_id, artifact_type);
        CREATE TRIGGER decision_quality_artifacts_no_update
        BEFORE UPDATE ON decision_quality_input_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'decision_quality_input_artifacts is append-only');
        END;
        CREATE TRIGGER decision_quality_artifacts_no_delete
        BEFORE DELETE ON decision_quality_input_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'decision_quality_input_artifacts is append-only');
        END;
        """
    )
    connection.execute("UPDATE schema_meta SET version = 14 WHERE id = 1")
    connection.execute("DROP TABLE decision_quality_artifact_receipts")
    connection.execute("DROP TABLE decision_quality_provider_receipts")
    connection.execute("DROP TABLE prompt_shadow_runs")
    connection.execute("DROP TABLE prompt_shadow_budget_counters")
    connection.execute("DROP TABLE factor_ic_nav_observations")
    connection.execute("DROP TABLE password_reset_tokens")
    connection.execute("DROP TABLE admin_audit_events")
    connection.commit()
    connection.close()

    plan = migration.plan_sqlite_source(path)

    artifact_plan = next(
        row
        for row in plan["tables"]
        if row["table"] == "decision_quality_input_artifacts"
    )
    assert plan["source_schema_version"] == 14
    assert artifact_plan["defaulted_columns"] == ["logical_key"]


def test_v14_additive_logical_key_column_order_remains_compatible(
    tmp_path,
) -> None:
    path = tmp_path / "source-v14-appended-logical-key.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX uq_decision_quality_artifact_logical_key")
    connection.execute(
        "ALTER TABLE decision_quality_input_artifacts DROP COLUMN logical_key"
    )
    connection.execute(
        "ALTER TABLE decision_quality_input_artifacts ADD COLUMN logical_key TEXT"
    )
    connection.execute(
        "CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key "
        "ON decision_quality_input_artifacts "
        "(userId, artifact_type, logical_key)"
    )
    connection.execute("UPDATE schema_meta SET version = 14 WHERE id = 1")
    connection.execute("DROP TABLE decision_quality_artifact_receipts")
    connection.execute("DROP TABLE decision_quality_provider_receipts")
    connection.execute("DROP TABLE prompt_shadow_runs")
    connection.execute("DROP TABLE prompt_shadow_budget_counters")
    connection.execute("DROP TABLE factor_ic_nav_observations")
    connection.execute("DROP TABLE password_reset_tokens")
    connection.execute("DROP TABLE admin_audit_events")
    connection.commit()
    connection.close()

    plan = migration.plan_sqlite_source(path)

    artifact_plan = next(
        row
        for row in plan["tables"]
        if row["table"] == "decision_quality_input_artifacts"
    )
    assert plan["source_schema_version"] == 14
    assert artifact_plan["defaulted_columns"] == []


def test_v14_snapshot_with_compact_v2_manifest_remains_compatible(tmp_path) -> None:
    path = tmp_path / "source-v14-v2-snapshot.db"
    marker = _current_source(path)
    evaluation_as_of = "2026-07-15T00:00:00+00:00"
    connection = sqlite3.connect(path)
    _insert_evaluation_snapshot(
        connection,
        manifest={
            "schema_version": "decision_quality_input_manifest.v2",
            "contract_rollout_marker": marker,
            "event_refs": [],
            "outcome_refs": [],
            "artifact_refs": [],
        },
        evaluation_as_of=evaluation_as_of,
    )
    connection.execute("UPDATE schema_meta SET version = 14 WHERE id = 1")
    connection.execute("DROP TABLE decision_quality_artifact_receipts")
    connection.execute("DROP TABLE decision_quality_provider_receipts")
    connection.execute("DROP TABLE prompt_shadow_runs")
    connection.execute("DROP TABLE prompt_shadow_budget_counters")
    connection.execute("DROP TABLE factor_ic_nav_observations")
    connection.execute("DROP TABLE password_reset_tokens")
    connection.execute("DROP TABLE admin_audit_events")
    connection.commit()
    connection.close()

    plan = migration.plan_sqlite_source(path)

    assert plan["source_schema_version"] == 14
    assert next(
        row["rows"]
        for row in plan["tables"]
        if row["table"] == "decision_quality_evaluation_snapshots"
    ) == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_artifact", "reference is missing"),
        ("candidate_count", "candidate_capture_count"),
        ("mature_count", "mature_decision_day_count"),
    ],
)
def test_v3_snapshot_manifest_closure_rejects_self_consistent_tampering(
    tmp_path,
    mutation: str,
    message: str,
) -> None:
    path = tmp_path / f"source-v15-manifest-{mutation}.db"
    marker = _current_source(path)
    evaluation_as_of = "2026-07-15T00:00:00+00:00"
    manifest = _v3_manifest(marker, evaluation_as_of=evaluation_as_of)
    if mutation == "missing_artifact":
        manifest["input_artifact_count"] = 1
        manifest["consumed_input_artifact_count"] = 1
        manifest["input_artifacts"] = [
            {
                "artifact_id": "dqa_" + "a" * 64,
                "content_hash": "a" * 64,
                "recorded_at": evaluation_as_of,
                "created_at": evaluation_as_of,
            }
        ]
    elif mutation == "candidate_count":
        manifest["candidate_capture_count"] = 1
    else:
        manifest["mature_decision_day_count"] = 1
    connection = sqlite3.connect(path)
    _insert_evaluation_snapshot(
        connection,
        manifest=manifest,
        evaluation_as_of=evaluation_as_of,
    )
    connection.commit()
    connection.close()

    with pytest.raises(migration.MigrationError, match=message):
        migration.plan_sqlite_source(path)


@pytest.mark.parametrize("ledger", ["event", "outcome"])
def test_snapshot_manifest_rejects_re_signed_source_storage_hash_tampering(
    tmp_path,
    ledger: str,
) -> None:
    from app.services.decision_quality_snapshot import (
        _decode_evidence_row,
        _event_manifest_rows,
        _outcome_manifest_rows,
    )
    from app.services.decision_repository import (
        _observation_hash,
        decision_event_content_hash,
        normalize_decision_event,
    )

    path = tmp_path / f"source-v15-snapshot-{ledger}-hash.db"
    marker = _current_source(path)
    evaluation_as_of = "2026-07-15T00:00:00+00:00"
    connection = sqlite3.connect(path)
    manifest = _v3_manifest(marker, evaluation_as_of=evaluation_as_of)
    forged_hash = "f" * 64

    if ledger == "event":
        event = normalize_decision_event(
            {
                "event_id": "daily:tampered-event",
                "decision_at": evaluation_as_of,
                "final_action": "hold",
                "action_category": "hold",
            }
        )
        columns = dict(migration.TABLES)["decision_events"]
        values = {
            **event,
            "userId": 1,
            "fee_model": event.get("fee_model_index"),
            "content_hash": decision_event_content_hash(event),
            "payload": canonical_json(event),
            "created_at": evaluation_as_of,
        }
        connection.execute(
            f"INSERT INTO decision_events ({', '.join(columns)}) VALUES "
            f"({', '.join('?' for _ in columns)})",
            tuple(values.get(column) for column in columns),
        )
        connection.execute(
            "UPDATE decision_events SET content_hash = ? "
            "WHERE userId = 1 AND event_id = ?",
            (forged_hash, event["event_id"]),
        )
        row = connection.execute(
            "SELECT * FROM decision_events WHERE userId = 1 AND event_id = ?",
            (event["event_id"],),
        ).fetchone()
        assert row is not None
        row_mapping = dict(
            zip(
                [column[0] for column in connection.execute(
                    "SELECT * FROM decision_events LIMIT 0"
                ).description],
                row,
                strict=True,
            )
        )
        manifest["decision_event_count"] = 1
        manifest["observed_decision_event_count"] = 1
        manifest["decision_events"] = _event_manifest_rows(
            [_decode_evidence_row(row_mapping)]
        )
    else:
        observation = {
            "observation_id": "daily:tampered-event:T+20",
            "decision_event_id": "daily:tampered-event",
            "horizon_trading_days": 20,
            "target_date": "2026-07-15",
            "status": "completed",
            "is_terminal": True,
        }
        columns = dict(migration.TABLES)["outcome_observations"]
        values = {
            **observation,
            "userId": 1,
            "revision_no": 1,
            "observed_at": evaluation_as_of,
            "finalized_at": evaluation_as_of,
            "content_hash": _observation_hash(observation),
            "payload": canonical_json(observation),
            "created_at": evaluation_as_of,
            "updated_at": evaluation_as_of,
        }
        connection.execute(
            f"INSERT INTO outcome_observations ({', '.join(columns)}) VALUES "
            f"({', '.join('?' for _ in columns)})",
            tuple(values.get(column) for column in columns),
        )
        connection.execute(
            "UPDATE outcome_observations SET content_hash = ? "
            "WHERE userId = 1 AND observation_id = ?",
            (forged_hash, observation["observation_id"]),
        )
        cursor = connection.execute(
            "SELECT * FROM outcome_observations "
            "WHERE userId = 1 AND observation_id = ?",
            (observation["observation_id"],),
        )
        row = cursor.fetchone()
        assert row is not None
        row_mapping = dict(zip([item[0] for item in cursor.description], row, strict=True))
        manifest["terminal_outcome_count"] = 1
        manifest["terminal_outcomes"] = _outcome_manifest_rows(
            [_decode_evidence_row(row_mapping)]
        )

    _insert_evaluation_snapshot(
        connection,
        manifest=manifest,
        evaluation_as_of=evaluation_as_of,
    )
    connection.commit()
    connection.close()

    with pytest.raises(migration.MigrationError, match=r"snapshot .* source"):
        migration.plan_sqlite_source(path)


def test_future_and_mixed_source_schema_versions_fail_closed(tmp_path) -> None:
    future_path = tmp_path / "source-v16.db"
    _current_source(future_path)
    future = sqlite3.connect(future_path)
    future.execute("UPDATE schema_meta SET version = 19 WHERE id = 1")
    future.commit()
    future.close()
    with pytest.raises(migration.MigrationError, match="newer than this migrator"):
        migration.plan_sqlite_source(future_path)

    mixed_path = tmp_path / "source-v14-with-v15-receipts.db"
    _current_source(mixed_path)
    mixed = sqlite3.connect(mixed_path)
    mixed.execute("UPDATE schema_meta SET version = 14 WHERE id = 1")
    mixed.commit()
    mixed.close()
    with pytest.raises(migration.MigrationError, match="post-v15"):
        migration.plan_sqlite_source(mixed_path)


def test_cli_dry_run_does_not_require_a_mysql_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    path = tmp_path / "dry-run-v13.db"
    _source(path, version=13, include_marker=False)
    monkeypatch.delenv("FUND_AI_DATABASE_URL", raising=False)

    assert migration.main(["--sqlite", str(path)]) == 0
    assert '"mode": "dry-run"' in capsys.readouterr().out


def test_cli_apply_requires_an_explicit_or_environment_mysql_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    path = tmp_path / "apply-v13.db"
    _source(path, version=13, include_marker=False)
    monkeypatch.delenv("FUND_AI_DATABASE_URL", raising=False)

    assert migration.main(["--sqlite", str(path), "--apply"]) == 2
    assert "requires --mysql-url" in capsys.readouterr().err


def test_dry_run_rejects_self_consistent_but_fake_provider_row(tmp_path) -> None:
    path = tmp_path / "source-v15-fake-provider.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    fake = normalize_decision_quality_provider_receipt(
        {
            "provider": "fake-provider",
            "operation": "fake-operation",
            "capture_mode": "live",
            "request_hash": "1" * 64,
            # This object can be content-addressed by the generic repository
            # envelope, but is not an adapter-origin receipt and therefore is
            # not admissible migration evidence.
            "adapter_output": {"self_declared": True},
            "normalized_payload_hash": "2" * 64,
            "origin_fetched_at": "2026-07-15T00:00:00+00:00",
            "completed_at": "2026-07-15T00:00:01+00:00",
        }
    )
    _insert_provider_receipt(connection, fake)
    connection.commit()
    connection.close()

    with pytest.raises(
        migration.MigrationError,
        match="provider_receipts",
    ):
        migration.plan_sqlite_source(path)


@pytest.mark.parametrize("mutation", ["duplicate_payload_key", "index_drift"])
def test_dry_run_rejects_noncanonical_or_index_drifted_provider_row(
    tmp_path,
    mutation: str,
) -> None:
    path = tmp_path / f"source-v15-provider-{mutation}.db"
    _current_source(path)
    connection = sqlite3.connect(path)
    receipt = _valid_provider_receipt()
    payload = canonical_json(receipt)
    values = {
        **receipt,
        "payload": payload,
        "created_at": receipt["completed_at"],
    }
    if mutation == "duplicate_payload_key":
        values["payload"] = (
            payload[:-1]
            + f',"content_hash":"{receipt["content_hash"]}"'
            + "}"
        )
    else:
        values["provider"] = "drifted-provider-index"
    columns = dict(migration.TABLES)["decision_quality_provider_receipts"]
    connection.execute(
        "INSERT INTO decision_quality_provider_receipts "
        f"({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    connection.commit()
    connection.close()

    with pytest.raises(migration.MigrationError, match="provider_receipts"):
        migration.plan_sqlite_source(path)


def test_same_hash_destination_with_different_payload_is_a_conflict(tmp_path) -> None:
    path = tmp_path / "source-v15-provider.db"
    _current_source(path)
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    source.execute("DELETE FROM users")
    receipt = _valid_provider_receipt()
    _insert_provider_receipt(source, receipt)
    source.commit()

    destination_raw = sqlite3.connect(":memory:")
    destination_raw.row_factory = sqlite3.Row
    _create_destination_table(
        destination_raw,
        "decision_quality_contract_rollouts",
    )
    _create_destination_table(
        destination_raw,
        "decision_quality_provider_receipts",
    )
    destination_payload = json.loads(canonical_json(receipt))
    destination_payload["adapter_output"]["automatic_promotion_allowed"] = True
    destination_values = {
        **receipt,
        "payload": canonical_json(destination_payload),
        "created_at": receipt["completed_at"],
    }
    columns = dict(migration.TABLES)["decision_quality_provider_receipts"]
    destination_raw.execute(
        "INSERT INTO decision_quality_provider_receipts "
        f"({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(destination_values[column] for column in columns),
    )
    destination_raw.commit()

    with pytest.raises(
        migration.ImmutableMigrationConflict,
        match="decision_quality_provider_receipts",
    ):
        migration.migrate_connections(
            source,
            _SqliteAsMysqlConnection(destination_raw),
            batch_size=1,
        )

    source.close()
    destination_raw.close()


def _provider_identity_values(receipt_id: str) -> tuple[Any, ...]:
    columns = dict(migration.TABLES)["decision_quality_provider_receipts"]
    values = {
        column: (
            receipt_id
            if column == "receipt_id"
            else 1
            if column == "adapter_output_bytes"
            else f"value:{column}"
        )
        for column in columns
    }
    return tuple(values[column] for column in columns)


def test_case_insensitive_destination_cannot_hide_identity_drift() -> None:
    table = "decision_quality_provider_receipts"
    columns = dict(migration.TABLES)[table]
    destination = sqlite3.connect(":memory:")
    definitions = [
        f"{column} "
        + (
            "TEXT COLLATE NOCASE"
            if column == "receipt_id"
            else "INTEGER"
            if column == "adapter_output_bytes"
            else "TEXT"
        )
        for column in columns
    ]
    destination.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")
    destination.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        _provider_identity_values("DQPR_CASE"),
    )

    with pytest.raises(
        migration.ImmutableMigrationConflict,
        match="decision_quality_provider_receipts",
    ):
        migration._insert_immutable_row(
            _SqliteAsMysqlCursor(destination),
            table=table,
            columns=columns,
            values=_provider_identity_values("dqpr_case"),
        )
    destination.close()


def test_duplicate_destination_identity_is_never_classified_identical() -> None:
    table = "decision_quality_provider_receipts"
    columns = dict(migration.TABLES)[table]
    destination = sqlite3.connect(":memory:")
    definitions = [
        f"{column} {'INTEGER' if column == 'adapter_output_bytes' else 'TEXT'}"
        for column in columns
    ]
    destination.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")
    values = _provider_identity_values("dqpr_duplicate")
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})"
    )
    destination.execute(statement, values)
    destination.execute(statement, values)

    with pytest.raises(
        migration.ImmutableMigrationConflict,
        match="duplicate destination identities",
    ):
        migration._insert_immutable_row(
            _SqliteAsMysqlCursor(destination),
            table=table,
            columns=columns,
            values=values,
        )
    destination.close()


@pytest.mark.parametrize("mode", ["plan", "apply"])
def test_quality_ledgers_use_one_wal_snapshot_during_full_scan(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    path = tmp_path / f"source-v15-wal-{mode}.db"
    _current_source(path)
    setup = sqlite3.connect(path)
    setup.execute("DELETE FROM users")
    setup.commit()
    assert setup.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    setup.close()

    original_projection = migration._source_projection
    inserted = False

    def projection_with_concurrent_commit(
        connection: sqlite3.Connection,
        *,
        table: str,
        columns: Sequence[str],
    ):
        nonlocal inserted
        if table == "decision_quality_artifact_receipts" and not inserted:
            inserted = True
            _insert_atomic_quality_pair(path)
        return original_projection(connection, table=table, columns=columns)

    monkeypatch.setattr(
        migration,
        "_source_projection",
        projection_with_concurrent_commit,
    )
    if mode == "plan":
        summary = migration.plan_sqlite_source(path, batch_size=1)
    else:
        source = sqlite3.connect(path)
        destination_raw = sqlite3.connect(":memory:")
        _create_destination_table(
            destination_raw,
            "decision_quality_contract_rollouts",
        )
        summary = migration.migrate_connections(
            source,
            _SqliteAsMysqlConnection(destination_raw),
            batch_size=1,
        )
        source.close()
        destination_raw.close()

    assert inserted is True
    tables = {item["table"]: item for item in summary["tables"]}
    assert tables["decision_quality_input_artifacts"]["rows"] == 0
    assert tables["decision_quality_artifact_receipts"]["rows"] == 0

    after = sqlite3.connect(path)
    assert after.execute(
        "SELECT COUNT(*) FROM decision_quality_input_artifacts"
    ).fetchone()[0] == 1
    assert after.execute(
        "SELECT COUNT(*) FROM decision_quality_artifact_receipts"
    ).fetchone()[0] == 1
    after.close()


def test_main_apply_freezes_marker_bootstrap_and_copy_in_one_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    path = tmp_path / "source-v15-main-wal.db"
    marker = _current_source(path)
    setup = sqlite3.connect(path)
    setup.execute("DELETE FROM users")
    setup.commit()
    assert setup.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    setup.close()

    class Destination:
        committed = False
        rolled_back = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    destination = Destination()
    observed: dict[str, Any] = {}

    guard: dict[str, Any] = {}

    def prepare_guard(
        _destination,
        *,
        source_schema_version: int,
        source_fingerprint: str,
        source_rollout_marker,
    ) -> dict[str, Any]:
        assert source_schema_version == 18
        assert len(source_fingerprint) == 64
        assert source_rollout_marker == marker
        guard.update(
            {
                "source_schema_version": source_schema_version,
                "source_fingerprint": source_fingerprint,
                "rollout_marker": source_rollout_marker,
            }
        )
        return guard

    def bootstrap(
        _destination,
        *,
        decision_quality_rollout_marker=None,
        migration_guard=None,
        defer_activation: bool,
        commit: bool,
    ) -> None:
        assert migration_guard is guard
        assert defer_activation is True
        assert commit is False
        observed["bootstrap_marker"] = decision_quality_rollout_marker
        _insert_atomic_quality_pair(path)

    def copy_frozen_snapshot(
        source: sqlite3.Connection,
        _destination,
        *,
        batch_size: int,
        source_version: int,
        rollout_marker,
    ) -> dict[str, Any]:
        assert source.in_transaction is True
        assert batch_size == 1
        assert source_version == 18
        assert rollout_marker == marker
        assert rollout_marker == observed["bootstrap_marker"]
        assert source.execute(
            "SELECT COUNT(*) FROM decision_quality_input_artifacts"
        ).fetchone()[0] == 0
        assert source.execute(
            "SELECT COUNT(*) FROM decision_quality_artifact_receipts"
        ).fetchone()[0] == 0
        return {"mode": "apply", "tables": []}

    import pymysql
    from app import mysql_bootstrap

    monkeypatch.setattr(pymysql, "connect", lambda **_kwargs: destination)
    monkeypatch.setattr(mysql_bootstrap, "prepare_mysql_migration_guard", prepare_guard)
    monkeypatch.setattr(mysql_bootstrap, "ensure_mysql_schema", bootstrap)
    monkeypatch.setattr(
        mysql_bootstrap,
        "finalize_mysql_migration_activation",
        lambda _destination, *, migration_guard: (
            None if migration_guard is guard else pytest.fail("wrong migration guard")
        ),
    )
    monkeypatch.setattr(
        migration,
        "_validate_mysql_migration_target_engines",
        lambda _destination: None,
    )
    monkeypatch.setattr(
        migration,
        "_migrate_connections_in_snapshot",
        copy_frozen_snapshot,
    )

    assert migration.main(
        [
            "--sqlite",
            str(path),
            "--mysql-url",
            "mysql://test:test@127.0.0.1/test",
            "--batch-size",
            "1",
            "--apply",
        ]
    ) == 0
    assert '"mode": "apply"' in capsys.readouterr().out
    assert destination.committed is True
    assert destination.rolled_back is False
    assert destination.closed is True

    after = sqlite3.connect(path)
    assert after.execute(
        "SELECT COUNT(*) FROM decision_quality_input_artifacts"
    ).fetchone()[0] == 1
    assert after.execute(
        "SELECT COUNT(*) FROM decision_quality_artifact_receipts"
    ).fetchone()[0] == 1
    after.close()


def test_main_apply_still_rolls_back_destination_on_copy_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    path = tmp_path / "source-v15-main-rollback.db"
    _current_source(path)

    class Destination:
        committed = False
        rolled_back = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    destination = Destination()

    import pymysql
    from app import mysql_bootstrap

    monkeypatch.setattr(pymysql, "connect", lambda **_kwargs: destination)
    guard: dict[str, Any] = {}

    def prepare_guard(
        _destination,
        *,
        source_schema_version: int,
        source_fingerprint: str,
        source_rollout_marker,
    ) -> dict[str, Any]:
        guard.update(
            {
                "source_schema_version": source_schema_version,
                "source_fingerprint": source_fingerprint,
                "rollout_marker": source_rollout_marker,
            }
        )
        return guard

    monkeypatch.setattr(mysql_bootstrap, "prepare_mysql_migration_guard", prepare_guard)
    monkeypatch.setattr(
        mysql_bootstrap,
        "ensure_mysql_schema",
        lambda _destination, **_kwargs: None,
    )
    monkeypatch.setattr(
        mysql_bootstrap,
        "finalize_mysql_migration_activation",
        lambda *_args, **_kwargs: pytest.fail("activation must not run"),
    )
    monkeypatch.setattr(
        migration,
        "_validate_mysql_migration_target_engines",
        lambda _destination: None,
    )

    def fail_copy(*_args, **_kwargs):
        raise migration.MigrationError("copy failure")

    monkeypatch.setattr(
        migration,
        "_migrate_connections_in_snapshot",
        fail_copy,
    )

    assert migration.main(
        [
            "--sqlite",
            str(path),
            "--mysql-url",
            "mysql://test:test@127.0.0.1/test",
            "--apply",
        ]
    ) == 2
    assert "copy failure" in capsys.readouterr().err
    assert destination.committed is False
    assert destination.rolled_back is True
    assert destination.closed is True


def test_main_apply_rejects_myisam_legacy_target_before_copy_or_activation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    path = tmp_path / "source-v15-myisam-target.db"
    marker = _current_source(path)
    required_tables = {table for table, _columns in migration.TABLES} | {
        "schema_meta",
        "decision_quality_migration_guard",
    }
    lifecycle: list[str] = []

    class Cursor:
        def execute(self, statement: str) -> None:
            assert "information_schema.TABLES" in statement

        def fetchall(self):
            return [
                (table, "MyISAM" if table == "users" else "InnoDB")
                for table in sorted(required_tables)
            ]

    class Destination:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            lifecycle.append("commit")

        def rollback(self) -> None:
            lifecycle.append("rollback")

        def close(self) -> None:
            lifecycle.append("close")

    destination = Destination()
    guard: dict[str, Any] = {}

    def prepare_guard(
        _destination,
        *,
        source_schema_version: int,
        source_fingerprint: str,
        source_rollout_marker,
    ) -> dict[str, Any]:
        lifecycle.append("guard")
        guard.update(
            {
                "source_schema_version": source_schema_version,
                "source_fingerprint": source_fingerprint,
                "rollout_marker": source_rollout_marker,
            }
        )
        return guard

    import pymysql
    from app import mysql_bootstrap

    monkeypatch.setattr(pymysql, "connect", lambda **_kwargs: destination)
    monkeypatch.setattr(mysql_bootstrap, "prepare_mysql_migration_guard", prepare_guard)
    monkeypatch.setattr(
        mysql_bootstrap,
        "ensure_mysql_schema",
        lambda _destination, **_kwargs: lifecycle.append("ddl"),
    )
    monkeypatch.setattr(
        mysql_bootstrap,
        "finalize_mysql_migration_activation",
        lambda *_args, **_kwargs: pytest.fail("activation must not run"),
    )
    monkeypatch.setattr(
        migration,
        "_migrate_connections_in_snapshot",
        lambda *_args, **_kwargs: pytest.fail("copy must not run"),
    )

    assert migration.main(
        [
            "--sqlite",
            str(path),
            "--mysql-url",
            "mysql://test:test@127.0.0.1/test",
            "--apply",
        ]
    ) == 2
    assert "must use InnoDB: users" in capsys.readouterr().err
    assert lifecycle == ["guard", "ddl", "rollback", "close"]
    assert guard["rollout_marker"] == marker
