from __future__ import annotations

import sqlite3

import pytest

from app.db_migrations import SCHEMA_VERSION, run_migrations


def _current_schema_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )
    run_migrations(connection)
    return connection


def test_run_migrations_backfills_global_primary_sector_table_at_current_version():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE schema_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )

    run_migrations(connection)

    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fund_primary_sectors_global'"
    ).fetchone()
    assert row is not None


def test_current_schema_still_ensures_factor_ic_snapshot_table() -> None:
    assert SCHEMA_VERSION == 17
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )

    run_migrations(connection)

    table = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='factor_ic_snapshots'"
    ).fetchone()
    assert table is not None


def test_current_schema_ensures_append_only_factor_ic_nav_observations() -> None:
    connection = _current_schema_connection()

    columns = tuple(
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(factor_ic_nav_observations)"
        ).fetchall()
    )
    assert columns == (
        "observation_id",
        "schema_version",
        "fund_code",
        "nav_date",
        "source",
        "first_observed_at",
        "available_at",
        "availability_basis",
        "unit_nav",
        "cumulative_nav",
        "daily_growth_percent",
        "content_hash",
        "payload",
        "source_commit",
        "source_run_id",
        "created_at",
    )

    indexes = {
        row[1]
        for row in connection.execute(
            "PRAGMA index_list(factor_ic_nav_observations)"
        ).fetchall()
    }
    assert {
        "uq_factor_ic_nav_observation_content",
        "idx_factor_ic_nav_observation_code_pit",
        "idx_factor_ic_nav_observation_observed",
        "idx_factor_ic_nav_observation_run",
    } <= indexes

    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name = 'factor_ic_nav_observations'"
        ).fetchall()
    }
    assert triggers == {
        "trg_factor_ic_nav_observation_no_update",
        "trg_factor_ic_nav_observation_no_delete",
    }


def test_current_schema_rejects_factor_ic_nav_observation_trigger_tampering() -> None:
    connection = _current_schema_connection()
    connection.execute("DROP TRIGGER trg_factor_ic_nav_observation_no_update")
    connection.execute(
        """
        CREATE TRIGGER trg_factor_ic_nav_observation_no_update
        BEFORE UPDATE ON factor_ic_nav_observations
        BEGIN
            SELECT 1;
        END
        """
    )

    with pytest.raises(RuntimeError, match="trigger .* conflicts"):
        run_migrations(connection)


def test_current_schema_ensures_prompt_shadow_operational_tables() -> None:
    connection = _current_schema_connection()

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "prompt_shadow_runs",
        "prompt_shadow_budget_counters",
    } <= tables

    run_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(prompt_shadow_runs)")
    }
    assert {
        "userId",
        "run_id",
        "status",
        "state_version",
        "challenger_network_started_at",
        "budget_reserved_at",
    } <= run_columns


def test_current_schema_rejects_prompt_shadow_index_tampering() -> None:
    connection = _current_schema_connection()
    connection.execute("DROP INDEX idx_prompt_shadow_runs_worker")
    connection.execute(
        "CREATE INDEX idx_prompt_shadow_runs_worker "
        "ON prompt_shadow_runs (status, created_at)"
    )

    with pytest.raises(RuntimeError, match="idx_prompt_shadow_runs_worker"):
        run_migrations(connection)


@pytest.mark.parametrize(
    ("trigger_name", "table"),
    [
        (
            "decision_quality_artifacts_no_update",
            "decision_quality_input_artifacts",
        ),
        (
            "decision_quality_rollout_no_delete",
            "decision_quality_contract_rollouts",
        ),
    ],
)
def test_current_schema_rejects_same_name_non_enforcing_trigger(
    trigger_name: str,
    table: str,
) -> None:
    connection = _current_schema_connection()
    connection.execute(f"DROP TRIGGER {trigger_name}")
    connection.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT 1;
        END
        """
    )

    with pytest.raises(RuntimeError, match="trigger .* conflicts"):
        run_migrations(connection)


def test_current_schema_rejects_partial_logical_identity_index() -> None:
    connection = _current_schema_connection()
    connection.execute("DROP INDEX uq_decision_quality_artifact_logical_key")
    connection.execute(
        """
        CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key
        ON decision_quality_input_artifacts
            (userId, artifact_type, logical_key)
        WHERE logical_key IS NOT NULL
        """
    )

    with pytest.raises(RuntimeError, match="logical identity index conflicts"):
        run_migrations(connection)
