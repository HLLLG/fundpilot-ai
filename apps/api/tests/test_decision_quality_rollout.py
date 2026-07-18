from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import sqlite3

import pytest

from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.mysql_bootstrap import (
    MySqlBootstrapContractError,
    _ensure_decision_quality_append_only_mysql_triggers,
    _ensure_decision_quality_rollout_mysql_triggers,
    _ensure_factor_ic_nav_observation_mysql_triggers,
    _valid_rollout_immutable_trigger_row,
    ensure_mysql_schema,
)
from app.services.decision_contract import build_report_decision_bundle
from app.services.decision_quality_rollout import (
    build_decision_quality_rollout_marker,
    normalize_decision_quality_rollout_marker,
)
from app.services.decision_quality_snapshot import (
    DecisionQualitySnapshotContractError,
    build_decision_quality_snapshot,
)
from app.services.decision_repository import (
    DecisionQualityIntegrityError,
    canonical_json,
    decision_event_content_hash,
    get_decision_quality_contract_rollout,
    normalize_decision_event,
    normalize_decision_quality_evaluation_snapshot,
    put_decision_event,
    put_decision_quality_evaluation_snapshot,
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    return connection


def _formal_event(*, event_id: str = "daily:rollout-report:0:000001") -> dict:
    decision_at = "2026-01-02T10:00:00+00:00"
    evidence_at = "2026-01-02T10:00:02+00:00"
    report = {
        "id": event_id.split(":")[1],
        "created_at": decision_at,
        "provider": "deepseek-chat",
        "fund_recommendations": [
            {
                "fund_code": "000001",
                "fund_name": "rollout test fund",
                "fund_type": "equity",
                "action": "buy",
            }
        ],
        "analysis_facts": {
            "data_evidence": {
                "schema_version": "1.0",
                "generated_at": evidence_at,
                "decision_ready": True,
                "items": [
                    {
                        "fact_id": "fund.000001.official_nav",
                        "source": "official_nav",
                        "source_type": "official",
                        "available_at": "2026-01-02T09:58:00+00:00",
                        "fetched_at": evidence_at,
                        "freshness": "fresh",
                        "confidence": "high",
                        "is_estimate": False,
                    }
                ],
            }
        },
    }
    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]
    assert event["event_id"] == event_id
    return event


def _legacy_event(event_id: str) -> dict:
    return {
        "schema_version": "decision_event.v1",
        "event_id": event_id,
        "event_type": "fund_daily_decision",
        "source_type": "daily",
        "decision_at": "2026-01-02T10:00:00+00:00",
        "decision_date": "2026-01-02",
        "fund_code": "000001",
        "fund_name": "legacy fund",
        "final_action": "observe",
        "action_category": "observation",
        "eligible": False,
        "is_backfilled": False,
        "metric_eligible": True,
    }


def _insert_event(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    event: dict,
    created_at: str,
) -> None:
    normalized = normalize_decision_event(event)
    content_hash = decision_event_content_hash(normalized)
    connection.execute(
        """
        INSERT INTO decision_events (
            userId, event_id, schema_version, event_type, source_type,
            decision_at, decision_date, fund_code, fund_name, final_action,
            action_category, eligible, is_backfilled, metric_eligible,
            content_hash, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            normalized["event_id"],
            normalized["schema_version"],
            normalized["event_type"],
            normalized["source_type"],
            normalized["decision_at"],
            normalized["decision_date"],
            normalized.get("fund_code"),
            normalized.get("fund_name"),
            normalized["final_action"],
            normalized["action_category"],
            int(bool(normalized["eligible"])),
            int(bool(normalized["is_backfilled"])),
            int(bool(normalized["metric_eligible"])),
            content_hash,
            canonical_json(normalized),
            created_at,
        ),
    )


def test_marker_is_canonical_content_addressed_and_immutable() -> None:
    marker = build_decision_quality_rollout_marker("2026-07-14T10:00:00+08:00")
    assert marker["required_from"] == "2026-07-14T02:00:00+00:00"
    assert normalize_decision_quality_rollout_marker(marker) == marker

    tampered = {**marker, "required_from": "2026-07-15T02:00:00+00:00"}
    with pytest.raises(ValueError, match="receipt conflicts|hash mismatch"):
        normalize_decision_quality_rollout_marker(tampered)

    connection = _connection()
    stored = get_decision_quality_contract_rollout(connection=connection)
    assert len(stored["marker_hash"]) == 64
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE decision_quality_contract_rollouts SET required_from = ?",
            ("2027-01-01T00:00:00+00:00",),
        )


def test_v14_database_missing_marker_is_not_reinitialized() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (14,),
    )

    run_migrations(connection)

    assert connection.execute(
        "SELECT COUNT(*) FROM decision_quality_contract_rollouts"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT version FROM schema_meta WHERE id = 1"
    ).fetchone()[0] == 18
    with pytest.raises(DecisionQualityIntegrityError, match="marker is missing"):
        get_decision_quality_contract_rollout(connection=connection)


def test_mysql_v14_bootstrap_does_not_recreate_a_missing_marker() -> None:
    statements: list[str] = []

    class Cursor:
        last_statement = ""

        def __init__(self) -> None:
            self.logical_column_installed = False
            self.logical_index_installed = False

        def execute(self, statement: str) -> None:
            self.last_statement = statement
            statements.append(statement)
            normalized = " ".join(statement.split())
            if normalized.startswith(
                "ALTER TABLE decision_quality_input_artifacts ADD COLUMN"
            ):
                self.logical_column_installed = True
            if normalized.startswith(
                "CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key"
            ):
                self.logical_index_installed = True

        def fetchone(self):
            if "SELECT version FROM schema_meta" in self.last_statement:
                return (14,)
            if (
                "information_schema.COLUMNS" in self.last_statement
                and "COLUMN_NAME = 'logical_key'" in self.last_statement
            ):
                if self.logical_column_installed:
                    return ("varchar", 255, "YES")
                return None
            if "information_schema.STATISTICS" in self.last_statement:
                if self.logical_index_installed:
                    return (
                        0,
                        "userId,artifact_type,logical_key",
                        0,
                    )
                return None
            return None

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            return None

    ensure_mysql_schema(Connection())

    assert any(
        "CREATE TABLE IF NOT EXISTS decision_quality_contract_rollouts" in row
        for row in statements
    )
    assert any(
        "CREATE TRIGGER trg_decision_quality_rollout_no_update" in row
        and "BEFORE UPDATE" in row
        and "SIGNAL SQLSTATE '45000'" in row
        for row in statements
    )
    assert any(
        "CREATE TRIGGER trg_decision_quality_rollout_no_delete" in row
        and "BEFORE DELETE" in row
        and "SIGNAL SQLSTATE '45000'" in row
        for row in statements
    )
    assert not any(
        "INSERT IGNORE INTO decision_quality_contract_rollouts" in row
        for row in statements
    )


def test_mysql_bootstrap_rejects_conflicting_rollout_immutability_trigger() -> None:
    class Cursor:
        last_statement = ""

        def execute(self, statement: str) -> None:
            self.last_statement = statement

        def fetchone(self):
            if "information_schema.TRIGGERS" in self.last_statement:
                return (
                    "BEFORE",
                    "UPDATE",
                    "decision_quality_contract_rollouts",
                    "SET @rollout_marker_mutation_allowed = 1",
                )
            if "SELECT version FROM schema_meta" in self.last_statement:
                return (14,)
            return None

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            pytest.fail("conflicting trigger must fail before commit")

    with pytest.raises(RuntimeError, match="immutability trigger"):
        ensure_mysql_schema(Connection())


class _ConcurrentTriggerCursor:
    def __init__(self, *, concurrent_winner: bool) -> None:
        self.concurrent_winner = concurrent_winner
        self.rows: dict[str, tuple[str, str, str, str]] = {}
        self.queried_trigger = ""

    def execute(self, statement: str) -> None:
        normalized = " ".join(statement.split())
        if "information_schema.TRIGGERS" in normalized:
            self.queried_trigger = normalized.split(
                "TRIGGER_NAME = '",
                1,
            )[1].split("'", 1)[0]
            return
        if normalized.startswith("CREATE TRIGGER "):
            tokens = normalized.split()
            trigger_name = tokens[2]
            if self.concurrent_winner:
                action = normalized.split(" FOR EACH ROW ", 1)[1]
                self.rows[trigger_name] = (
                    "BEFORE",
                    tokens[4],
                    tokens[6],
                    action,
                )
            raise RuntimeError("simulated concurrent CREATE TRIGGER")

    def fetchone(self):
        return self.rows.get(self.queried_trigger)


def test_mysql_trigger_duplicate_ddl_is_idempotent_after_exact_recheck() -> None:
    cursor = _ConcurrentTriggerCursor(concurrent_winner=True)

    _ensure_decision_quality_rollout_mysql_triggers(cursor)
    _ensure_decision_quality_append_only_mysql_triggers(cursor)
    _ensure_factor_ic_nav_observation_mysql_triggers(cursor)

    assert set(cursor.rows) == {
        "trg_decision_quality_rollout_no_update",
        "trg_decision_quality_rollout_no_delete",
        "trg_decision_quality_artifacts_no_update",
        "trg_decision_quality_artifacts_no_delete",
        "trg_decision_quality_snapshots_no_update",
        "trg_decision_quality_snapshots_no_delete",
        "trg_decision_quality_artifact_receipts_no_update",
        "trg_decision_quality_artifact_receipts_no_delete",
        "trg_decision_quality_provider_receipts_no_update",
        "trg_decision_quality_provider_receipts_no_delete",
        "trg_admin_audit_events_no_update",
        "trg_admin_audit_events_no_delete",
        "trg_factor_ic_nav_observation_no_update",
        "trg_factor_ic_nav_observation_no_delete",
    }


@pytest.mark.parametrize(
    "ensure_contract, expected_message",
    [
        (
            _ensure_decision_quality_rollout_mysql_triggers,
            "rollout immutability trigger",
        ),
        (
            _ensure_decision_quality_append_only_mysql_triggers,
            "append-only trigger",
        ),
        (
            _ensure_factor_ic_nav_observation_mysql_triggers,
            "factor NAV trigger",
        ),
    ],
)
def test_mysql_trigger_ddl_failure_without_exact_winner_fails_closed(
    ensure_contract,
    expected_message: str,
) -> None:
    cursor = _ConcurrentTriggerCursor(concurrent_winner=False)

    with pytest.raises(MySqlBootstrapContractError, match=expected_message):
        ensure_contract(cursor)


@pytest.mark.parametrize(
    "action",
    [
        (
            "BEGIN IF FALSE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'decision quality rollout marker is immutable'; END IF; END"
        ),
        (
            "BEGIN /* SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'decision quality rollout marker is immutable'; */ SET @allowed = 1; END"
        ),
        (
            "BEGIN DECLARE CONTINUE HANDLER FOR SQLSTATE '45000' BEGIN END; "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'decision quality rollout marker is immutable'; END"
        ),
    ],
)
def test_mysql_rollout_trigger_contract_rejects_conditional_or_swallowed_signal(
    action: str,
) -> None:
    assert not _valid_rollout_immutable_trigger_row(
        (
            "BEFORE",
            "UPDATE",
            "decision_quality_contract_rollouts",
            action,
        ),
        event="UPDATE",
    )


@pytest.mark.parametrize("sqlstate_value", ["", " value"])
def test_mysql_rollout_trigger_contract_accepts_only_unconditional_signal(
    sqlstate_value: str,
) -> None:
    assert _valid_rollout_immutable_trigger_row(
        (
            "BEFORE",
            "DELETE",
            "decision_quality_contract_rollouts",
            (
                f"SIGNAL SQLSTATE{sqlstate_value} '45000' SET MESSAGE_TEXT = "
                "'decision quality rollout marker is immutable';"
            ),
        ),
        event="DELETE",
    )


def test_new_legacy_event_is_rejected_but_complete_d2_event_is_accepted() -> None:
    connection = _connection()
    with pytest.raises(
        DecisionQualityIntegrityError,
        match="post-rollout decision event failed",
    ):
        put_decision_event(
            user_id=1,
            event=_legacy_event("daily:legacy-after-rollout:0:000001"),
            connection=connection,
        )

    saved = put_decision_event(
        user_id=1,
        event=_formal_event(),
        connection=connection,
    )
    assert saved["payload"]["quality_contract_version"] == (
        "decision_quality_contract.v1"
    )
    assert saved["payload"]["replay_contract_required"] is True

    tampered = _formal_event(event_id="daily:stale-hash:0:000001")
    tampered["final_action"] = "hold instead"
    with pytest.raises(
        DecisionQualityIntegrityError,
        match="decision_event_payload_hash_mismatch",
    ):
        put_decision_event(
            user_id=1,
            event=tampered,
            connection=connection,
        )


def test_snapshot_grandfathers_only_events_stored_before_rollout() -> None:
    connection = _connection()
    marker = get_decision_quality_contract_rollout(connection=connection)
    boundary = datetime.fromisoformat(marker["required_from"])
    _insert_event(
        connection,
        user_id=1,
        event=_legacy_event("daily:legacy-before-rollout:0:000001"),
        created_at=(boundary - timedelta(seconds=1)).isoformat(),
    )

    snapshot = build_decision_quality_snapshot(
        user_id=1,
        evaluation_as_of=boundary + timedelta(days=1),
        window_days=3650,
        connection=connection,
    )
    manifest = snapshot["input_manifest"]
    assert manifest["schema_version"] == "decision_quality_input_manifest.v4"
    assert manifest["decision_event_count"] == 0
    assert manifest["nonformal_decision_event_count"] == 1
    assert manifest["contract_rollout_marker"] == marker

    _insert_event(
        connection,
        user_id=2,
        event=_legacy_event("daily:legacy-after-rollout:0:000001"),
        created_at=(boundary + timedelta(seconds=1)).isoformat(),
    )
    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="post-rollout decision event",
    ):
        build_decision_quality_snapshot(
            user_id=2,
            evaluation_as_of=boundary + timedelta(days=1),
            window_days=3650,
            connection=connection,
        )


def test_snapshot_rejects_partial_d2_contract_even_before_rollout() -> None:
    connection = _connection()
    marker = get_decision_quality_contract_rollout(connection=connection)
    boundary = datetime.fromisoformat(marker["required_from"])
    partial = _legacy_event("daily:partial-before-rollout:0:000001")
    partial["quality_contract_version"] = "decision_quality_contract.v1"
    _insert_event(
        connection,
        user_id=1,
        event=partial,
        created_at=(boundary - timedelta(seconds=1)).isoformat(),
    )

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="partial or invalid D2 replay contract",
    ):
        build_decision_quality_snapshot(
            user_id=1,
            evaluation_as_of=boundary + timedelta(days=1),
            window_days=3650,
            connection=connection,
        )


def test_snapshot_rejects_post_rollout_formal_event_with_bad_replay_integrity() -> None:
    connection = _connection()
    marker = get_decision_quality_contract_rollout(connection=connection)
    boundary = datetime.fromisoformat(marker["required_from"])
    event = deepcopy(_formal_event(event_id="daily:tampered:0:000001"))
    event["replay_bundle"]["facts_hash"] = "f" * 64
    _insert_event(
        connection,
        user_id=1,
        event=event,
        created_at=(boundary + timedelta(seconds=1)).isoformat(),
    )

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="not replay eligible",
    ):
        build_decision_quality_snapshot(
            user_id=1,
            evaluation_as_of=boundary + timedelta(days=1),
            window_days=3650,
            connection=connection,
        )


def test_snapshot_persistence_requires_the_current_canonical_rollout_marker() -> None:
    connection = _connection()
    current = get_decision_quality_contract_rollout(connection=connection)
    boundary = datetime.fromisoformat(current["required_from"])
    snapshot = build_decision_quality_snapshot(
        user_id=1,
        evaluation_as_of=boundary + timedelta(days=1),
        window_days=365,
        connection=connection,
    )

    missing = deepcopy(snapshot)
    missing["input_manifest"].pop("contract_rollout_marker")
    with pytest.raises(ValueError, match="contract_rollout_marker is required"):
        normalize_decision_quality_evaluation_snapshot(missing)

    tampered = deepcopy(snapshot)
    tampered["input_manifest"]["contract_rollout_marker"][
        "required_from"
    ] = (boundary + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="contract_rollout_marker is invalid"):
        normalize_decision_quality_evaluation_snapshot(tampered)

    wrong_current = deepcopy(snapshot)
    wrong_current["input_manifest"]["contract_rollout_marker"] = (
        build_decision_quality_rollout_marker(
            (boundary + timedelta(seconds=1)).isoformat()
        )
    )
    with pytest.raises(
        DecisionQualityIntegrityError,
        match="does not match the primary store",
    ):
        put_decision_quality_evaluation_snapshot(
            user_id=1,
            snapshot=wrong_current,
            connection=connection,
        )
