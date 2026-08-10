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


def test_v21_creates_sector_identity_tables_and_backfills_legacy_rows() -> None:
    connection = _current_schema_connection()
    connection.execute(
        """
        INSERT INTO fund_primary_sectors_global (
            fund_code, sector_name, intraday_index_name, source,
            confidence, detail, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000711",
            "医药",
            "中证医药卫生指数",
            "precompute_holdings",
            0.92,
            '{"scores":{"医药":38.5},"report_period":"2026Q2"}',
            "2026-07-31T08:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO fund_primary_sectors_global (
            fund_code, sector_name, intraday_index_name, source,
            confidence, detail, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000712",
            "医药",
            None,
            "precompute_llm",
            0.85,
            None,
            "2026-07-31T08:00:00+00:00",
        ),
    )
    connection.commit()

    run_migrations(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "fund_sector_exposure_snapshots" in tables
    assert "fund_sector_current" in tables
    assert "fund_sector_resolution_status" in tables
    current = connection.execute(
        """
        SELECT fund_code, sector_name, exposure_percent, identity_status,
               source, evidence_snapshot_id, report_period, expires_at
        FROM fund_sector_current
        ORDER BY fund_code
        """
    ).fetchall()
    assert current[0][0:7] == (
        "000711",
        "医药",
        38.5,
        "verified",
        "precompute_holdings",
        current[0][5],
        "2026Q2",
    )
    assert len(current[0][5]) == 64
    assert current[0][7] > "2026-07-31T08:00:00+00:00"
    assert current[1][3] == "pending"
    snapshot_count = connection.execute(
        "SELECT COUNT(*) FROM fund_sector_exposure_snapshots"
    ).fetchone()[0]
    assert snapshot_count == 2
    resolution = connection.execute(
        """
        SELECT fund_code, resolution_status, stage, reason_code
        FROM fund_sector_resolution_status
        ORDER BY fund_code
        """
    ).fetchall()
    assert resolution == [
        (
            "000711",
            "verified",
            "precompute_holdings",
            "existing_verified_identity",
        )
    ]


def test_current_schema_still_ensures_factor_ic_snapshot_table() -> None:
    assert SCHEMA_VERSION == 23
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


def test_current_schema_still_ensures_sector_direction_state_table() -> None:
    connection = _current_schema_connection()

    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(sector_direction_states)"
        ).fetchall()
    }
    assert {
        "trade_date",
        "sector_label",
        "policy_version",
        "entry_state",
        "raw_entry_state",
        "consecutive_qualifying_days",
        # 2026-08：退出侧要靠这两列才能正确数「连续跌破退出线」的天数。
        # `trend_evidence_coverage` 区分「真实低分」与「证据不足时 ≤45 的兜底占位值」
        # （占位值必然低于退出线 52，不过滤会让连续天数被没有证据的日子灌水）；
        # `source` 区分当天真实捕获与事后按日线重算的回填，让发现基金的滞回只认前者。
        "trend_evidence_coverage",
        "source",
    } <= columns


def test_v19_and_v20_add_only_performance_metadata_to_operational_tables() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_meta VALUES (1, 18)")
    connection.execute(
        """
        CREATE TABLE reports (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            payload TEXT NOT NULL, userId INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE analysis_jobs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL,
            request_payload TEXT NOT NULL, userId INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )

    run_migrations(connection)

    assert connection.execute(
        "SELECT version FROM schema_meta WHERE id = 1"
    ).fetchone()[0] == 23
    report_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(reports)")
    }
    job_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(analysis_jobs)")
    }
    assert "summary_payload" in report_columns
    assert {"dedup_key", "active_dedup_key", "heartbeat_at"} <= job_columns
    assert connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'stream_sessions'"
    ).fetchone() is not None
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "report_summaries",
        "fund_discovery_report_summaries",
    } <= tables


def test_v20_adds_pending_transaction_covering_index() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_meta VALUES (1, 19)")
    connection.execute(
        """
        CREATE TABLE fund_transactions (
            id TEXT PRIMARY KEY,
            userId INTEGER NOT NULL,
            status TEXT NOT NULL,
            confirm_date TEXT NOT NULL,
            trade_time TEXT NOT NULL
        )
        """
    )

    run_migrations(connection)

    columns = [
        row[2]
        for row in connection.execute(
            "PRAGMA index_info(idx_fund_tx_pending_confirm)"
        )
    ]
    assert columns == ["userId", "status", "confirm_date", "trade_time"]


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
