from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from app.services.decision_quality_rollout import (
    DECISION_QUALITY_ROLLOUT_CONTRACT_NAME,
    build_decision_quality_rollout_marker,
)


SCHEMA_VERSION = 18

# 迁移在应用/后台线程首次建立连接时触发（例如板块快照刷新会 daemon 线程预取资金流历史，
# 与主线程几乎同时首次打开 sqlite 连接）。同进程内多个线程各自用独立 connection 对同一
# 库文件跑 _migrate_* 会产生 "table already exists" 之类的竞态，这里用进程内锁串行化。
_MIGRATION_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _normalized_sql_contract(statement: str) -> str:
    return " ".join(str(statement or "").lower().split()).rstrip(";").strip()


def _ensure_sqlite_trigger_contract(
    connection: sqlite3.Connection,
    *,
    name: str,
    table: str,
    stored_sql: str,
) -> None:
    create_sql = stored_sql.replace(
        "CREATE TRIGGER ",
        "CREATE TRIGGER IF NOT EXISTS ",
        1,
    )
    connection.execute(create_sql)
    row = connection.execute(
        "SELECT tbl_name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = ?",
        (name,),
    ).fetchone()
    if (
        row is None
        or str(row[0]) != table
        or _normalized_sql_contract(row[1])
        != _normalized_sql_contract(stored_sql)
    ):
        raise RuntimeError(
            f"SQLite trigger {name} conflicts with immutable ledger contract"
        )


def _ensure_sqlite_table_contract(
    connection: sqlite3.Connection,
    *,
    name: str,
    stored_sql: str,
) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    if (
        row is None
        or _normalized_sql_contract(row[0])
        != _normalized_sql_contract(stored_sql)
    ):
        raise RuntimeError(f"SQLite table {name} conflicts with storage contract")


def _ensure_sqlite_index_contract(
    connection: sqlite3.Connection,
    *,
    table: str,
    name: str,
    columns: tuple[str, ...],
    unique: bool,
    stored_sql: str,
) -> None:
    rows = connection.execute(f"PRAGMA index_list({table})").fetchall()
    row = next((item for item in rows if str(item[1]) == name), None)
    observed_columns = tuple(
        str(item[2])
        for item in connection.execute(f"PRAGMA index_info({name})").fetchall()
    )
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (name,),
    ).fetchone()
    if (
        row is None
        or int(row[2]) != int(unique)
        or len(row) < 5
        or int(row[4]) != 0
        or observed_columns != columns
        or sql_row is None
        or _normalized_sql_contract(sql_row[0])
        != _normalized_sql_contract(stored_sql)
    ):
        raise RuntimeError(f"SQLite index {name} conflicts with storage contract")


def _get_schema_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_meta"):
        return 1
    row = connection.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()
    return int(row[0]) if row else 1


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, ?)",
        (version,),
    )


def _ensure_migration_user(connection: sqlite3.Connection) -> None:
    from app.auth.passwords import hash_password

    row = connection.execute("SELECT id FROM users WHERE id = 1").fetchone()
    if row is not None:
        return
    now = _now()
    connection.execute(
        """
        INSERT INTO users (
            id, userRole, username, userAccount, passwordHash,
            bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
        ) VALUES (1, 'user', '迁移用户', 'migration@local', ?, '', '', NULL, ?, ?, 0, NULL)
        """,
        (hash_password("migration-not-for-login"), now, now),
    )


def _migrate_fund_profiles(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "fund_profiles", "userId"):
        return
    connection.execute(
        """
        CREATE TABLE fund_profiles_new (
            userId INTEGER NOT NULL DEFAULT 1,
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (userId, fund_code)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO fund_profiles_new (userId, fund_code, fund_name, payload, updated_at)
        SELECT 1, fund_code, fund_name, payload, updated_at FROM fund_profiles
        """
    )
    connection.execute("DROP TABLE fund_profiles")
    connection.execute("ALTER TABLE fund_profiles_new RENAME TO fund_profiles")


def _migrate_portfolio_state(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "portfolio_state", "userId"):
        return
    connection.execute(
        """
        CREATE TABLE portfolio_state_new (
            userId INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO portfolio_state_new (userId, payload, updated_at)
        SELECT 1, payload, updated_at FROM portfolio_state WHERE id = 1
        """
    )
    connection.execute("DROP TABLE portfolio_state")
    connection.execute("ALTER TABLE portfolio_state_new RENAME TO portfolio_state")


def _migrate_investor_profile(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "investor_profile_state", "userId"):
        return
    connection.execute(
        """
        CREATE TABLE investor_profile_state_new (
            userId INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO investor_profile_state_new (userId, payload, updated_at)
        SELECT 1, payload, updated_at FROM investor_profile_state WHERE id = 1
        """
    )
    connection.execute("DROP TABLE investor_profile_state")
    connection.execute("ALTER TABLE investor_profile_state_new RENAME TO investor_profile_state")


def _migrate_snapshots(connection: sqlite3.Connection, table: str, date_col: str) -> None:
    if _column_exists(connection, table, "userId"):
        return
    connection.execute(
        f"""
        CREATE TABLE {table}_new (
            userId INTEGER NOT NULL DEFAULT 1,
            {date_col} TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (userId, {date_col})
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO {table}_new (userId, {date_col}, payload, updated_at)
        SELECT 1, {date_col}, payload, updated_at FROM {table}
        """
    )
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def _migrate_sector_mappings(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "sector_mappings", "userId"):
        return
    connection.execute(
        """
        CREATE TABLE sector_mappings_new (
            userId INTEGER NOT NULL DEFAULT 1,
            sector_label TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_code TEXT,
            source_name TEXT NOT NULL,
            confidence TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (userId, sector_label)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO sector_mappings_new (
            userId, sector_label, source_type, source_code, source_name, confidence, updated_at
        )
        SELECT 1, sector_label, source_type, source_code, source_name, confidence, updated_at
        FROM sector_mappings
        """
    )
    connection.execute("DROP TABLE sector_mappings")
    connection.execute("ALTER TABLE sector_mappings_new RENAME TO sector_mappings")


def _migrate_ocr_cache(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "ocr_text_cache", "userId"):
        return
    connection.execute(
        """
        CREATE TABLE ocr_text_cache_new (
            userId INTEGER NOT NULL DEFAULT 1,
            cache_key TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (userId, cache_key)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO ocr_text_cache_new (userId, cache_key, raw_text, updated_at)
        SELECT 1, cache_key, raw_text, updated_at FROM ocr_text_cache
        """
    )
    connection.execute("DROP TABLE ocr_text_cache")
    connection.execute("ALTER TABLE ocr_text_cache_new RENAME TO ocr_text_cache")


def _migrate_reports(connection: sqlite3.Connection) -> None:
    if _column_exists(connection, "reports", "userId"):
        return
    connection.execute("ALTER TABLE reports ADD COLUMN userId INTEGER NOT NULL DEFAULT 1")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports (userId, created_at DESC)"
    )


def _migrate_analysis_jobs(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "analysis_jobs"):
        return
    if _column_exists(connection, "analysis_jobs", "userId"):
        return
    connection.execute("ALTER TABLE analysis_jobs ADD COLUMN userId INTEGER NOT NULL DEFAULT 1")


def _migrate_fund_primary_sectors(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "fund_primary_sectors"):
        return
    connection.execute(
        """
        CREATE TABLE fund_primary_sectors (
            userId INTEGER NOT NULL DEFAULT 1,
            fund_code TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            intraday_index_name TEXT,
            source TEXT NOT NULL,
            confidence REAL,
            detail TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (userId, fund_code)
        )
        """
    )


def _migrate_fund_primary_sectors_global(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "fund_primary_sectors_global"):
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_primary_sectors_global_sector
            ON fund_primary_sectors_global (sector_name, confidence DESC, resolved_at DESC)
            """
        )
        return
    connection.execute(
        """
        CREATE TABLE fund_primary_sectors_global (
            fund_code TEXT PRIMARY KEY,
            sector_name TEXT NOT NULL,
            intraday_index_name TEXT,
            source TEXT NOT NULL,
            confidence REAL,
            detail TEXT,
            resolved_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_primary_sectors_global_resolved
        ON fund_primary_sectors_global (resolved_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_primary_sectors_global_sector
        ON fund_primary_sectors_global (sector_name, confidence DESC, resolved_at DESC)
        """
    )


def _migrate_analysis_prompt_state(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "analysis_prompt_state"):
        return
    connection.execute(
        """
        CREATE TABLE analysis_prompt_state (
            userId INTEGER NOT NULL PRIMARY KEY,
            role_prompt TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_discovery_tables(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "fund_discovery_reports"):
        connection.execute(
            """
            CREATE TABLE fund_discovery_reports (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                userId INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_discovery_reports_user_created
            ON fund_discovery_reports (userId, created_at DESC)
            """
        )
    if not _table_exists(connection, "discovery_jobs"):
        connection.execute(
            """
            CREATE TABLE discovery_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_payload TEXT NOT NULL,
                discovery_report_id TEXT,
                error TEXT,
                stage TEXT,
                stage_label TEXT,
                userId INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    if not _table_exists(connection, "discovery_chat_messages"):
        connection.execute(
            """
            CREATE TABLE discovery_chat_messages (
                id TEXT PRIMARY KEY,
                discovery_report_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_discovery_chat_report_id
            ON discovery_chat_messages (discovery_report_id, created_at)
            """
        )


def _migrate_discovery_prompt_state(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "discovery_prompt_state"):
        return
    connection.execute(
        """
        CREATE TABLE discovery_prompt_state (
            userId INTEGER NOT NULL PRIMARY KEY,
            role_prompt TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_swing_alert_fired(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "swing_alert_fired"):
        return
    connection.execute(
        """
        CREATE TABLE swing_alert_fired (
            userId INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            alert_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            fired_at TEXT NOT NULL,
            PRIMARY KEY (userId, trade_date, alert_key)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_swing_alert_fired_user_date
        ON swing_alert_fired (userId, trade_date, fired_at DESC)
        """
    )


def _migrate_factor_ic_snapshots(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_ic_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            run_date TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_generated
        ON factor_ic_snapshots (generated_at DESC)
        """
    )


def _migrate_factor_ic_universe_snapshots(connection: sqlite3.Connection) -> None:
    """Create the append-only point-in-time universe store.

    The two tables are deliberately independent from the current-survivor IC
    summary.  A universe snapshot is immutable evidence; later captures append
    another identity instead of replacing what a historical run could see.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source TEXT NOT NULL,
            source_share_count INTEGER NOT NULL,
            deduped_fund_count INTEGER NOT NULL,
            sampled_fund_count INTEGER NOT NULL,
            sample_target INTEGER NOT NULL,
            fund_type_count INTEGER NOT NULL,
            source_commit TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_universe_date
        ON factor_ic_universe_snapshots (snapshot_date DESC, available_at DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_members (
            snapshot_id TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            fund_type TEXT NOT NULL,
            share_class TEXT,
            canonical_portfolio_key TEXT NOT NULL,
            inception_date TEXT,
            available_at TEXT NOT NULL,
            source_rank INTEGER,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, fund_code)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_universe_member_code
        ON factor_ic_universe_members (fund_code, snapshot_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_universe_member_type
        ON factor_ic_universe_members (snapshot_id, fund_type)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_universe_member_portfolio
        ON factor_ic_universe_members (canonical_portfolio_key, snapshot_id)
        """
    )


def _migrate_factor_ic_nav_observations(connection: sqlite3.Connection) -> None:
    """Create the physically append-only NAV first-observation ledger."""

    table_sql = """
        CREATE TABLE factor_ic_nav_observations (
            observation_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            nav_date TEXT NOT NULL,
            source TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            availability_basis TEXT NOT NULL,
            unit_nav REAL NOT NULL,
            cumulative_nav REAL,
            daily_growth_percent REAL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """
    connection.execute(table_sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
    _ensure_sqlite_table_contract(
        connection,
        name="factor_ic_nav_observations",
        stored_sql=table_sql,
    )
    index_contracts = (
        (
            "uq_factor_ic_nav_observation_content",
            ("content_hash",),
            True,
            """
                CREATE UNIQUE INDEX uq_factor_ic_nav_observation_content
                ON factor_ic_nav_observations (content_hash)
            """,
        ),
        (
            "idx_factor_ic_nav_observation_code_pit",
            ("fund_code", "nav_date", "first_observed_at"),
            False,
            """
                CREATE INDEX idx_factor_ic_nav_observation_code_pit
                ON factor_ic_nav_observations
                    (fund_code, nav_date, first_observed_at)
            """,
        ),
        (
            "idx_factor_ic_nav_observation_observed",
            ("first_observed_at", "nav_date"),
            False,
            """
                CREATE INDEX idx_factor_ic_nav_observation_observed
                ON factor_ic_nav_observations (first_observed_at, nav_date)
            """,
        ),
        (
            "idx_factor_ic_nav_observation_run",
            ("source_run_id", "fund_code"),
            False,
            """
                CREATE INDEX idx_factor_ic_nav_observation_run
                ON factor_ic_nav_observations (source_run_id, fund_code)
            """,
        ),
    )
    for name, columns, unique, stored_sql in index_contracts:
        connection.execute(
            stored_sql.replace(
                " INDEX ",
                " INDEX IF NOT EXISTS ",
                1,
            )
        )
        _ensure_sqlite_index_contract(
            connection,
            table="factor_ic_nav_observations",
            name=name,
            columns=columns,
            unique=unique,
            stored_sql=stored_sql,
        )
    for name, event in (
        ("trg_factor_ic_nav_observation_no_update", "UPDATE"),
        ("trg_factor_ic_nav_observation_no_delete", "DELETE"),
    ):
        trigger_sql = f"""
            CREATE TRIGGER {name}
            BEFORE {event} ON factor_ic_nav_observations
            BEGIN
                SELECT RAISE(ABORT, 'factor IC NAV observations are append-only');
            END
        """
        _ensure_sqlite_trigger_contract(
            connection,
            name=name,
            table="factor_ic_nav_observations",
            stored_sql=trigger_sql,
        )


def _migrate_fund_holdings_snapshots(connection: sqlite3.Connection) -> None:
    """Create the immutable point-in-time fund holdings evidence store.

    ``available_at`` is nullable on purpose: a capture with unknown publication
    time is still useful audit evidence, but repository PIT reads must never
    treat it as information that was available to a historical decision.
    ``snapshot_hash`` identifies business content, so a repeated capture is
    idempotent while a corrected disclosure appends a new immutable row.
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fund_holdings_snapshots (
            id TEXT PRIMARY KEY,
            fund_master_key TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            report_period TEXT,
            as_of_date TEXT,
            available_at TEXT,
            first_observed_at TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fund_holdings_snapshot_hash
        ON fund_holdings_snapshots (snapshot_hash)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_holdings_snapshots_code_pit
        ON fund_holdings_snapshots
            (fund_code, available_at DESC, status, first_observed_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_holdings_snapshots_master_pit
        ON fund_holdings_snapshots
            (fund_master_key, available_at DESC, status, first_observed_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_holdings_snapshots_period
        ON fund_holdings_snapshots
            (fund_master_key, report_period, available_at DESC)
        """
    )


def _migrate_decision_accuracy_v2(connection: sqlite3.Connection) -> None:
    """Create the durable decision/outcome and append-only ledger substrate.

    This function is intentionally safe to run even when ``schema_meta`` already
    advertises the current version.  A previous bootstrap can be interrupted
    between DDL statements, and copied databases occasionally carry a version
    marker without every optional table.  ``IF NOT EXISTS`` makes that case
    self-healing without rewriting any existing decision evidence.

    No table has a foreign key to ``reports`` or discovery reports.  Those legacy
    stores use replace-style writes, while decision evidence must remain stable.
    """
    statements = [
        """
        CREATE TABLE IF NOT EXISTS decision_portfolio_snapshots (
            userId INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            snapshot_at TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            source_type TEXT NOT NULL,
            truth_status TEXT NOT NULL,
            ledger_version TEXT,
            cash_yuan REAL,
            total_market_value_yuan REAL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, snapshot_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_snapshots_user_date
        ON decision_portfolio_snapshots (userId, snapshot_date DESC, snapshot_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_events (
            userId INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_report_id TEXT,
            decision_at TEXT NOT NULL,
            decision_date TEXT NOT NULL,
            fund_code TEXT,
            fund_name TEXT,
            proposed_action TEXT,
            final_action TEXT NOT NULL,
            action_category TEXT NOT NULL,
            eligible INTEGER NOT NULL DEFAULT 0,
            amount_yuan REAL,
            portfolio_snapshot_id TEXT,
            benchmark_mapping_id TEXT,
            fee_model TEXT,
            is_backfilled INTEGER NOT NULL DEFAULT 0,
            metric_eligible INTEGER NOT NULL DEFAULT 1,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, event_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_events_user_report
        ON decision_events (userId, source_type, source_report_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_events_user_date
        ON decision_events (userId, decision_date DESC, fund_code)
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observations (
            userId INTEGER NOT NULL,
            observation_id TEXT NOT NULL,
            decision_event_id TEXT NOT NULL,
            horizon_trading_days INTEGER NOT NULL,
            target_date TEXT,
            status TEXT NOT NULL,
            is_terminal INTEGER NOT NULL DEFAULT 0,
            revision_no INTEGER NOT NULL DEFAULT 1,
            observed_at TEXT NOT NULL,
            finalized_at TEXT,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (userId, observation_id),
            UNIQUE (userId, decision_event_id, horizon_trading_days)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_observations_event
        ON outcome_observations (userId, decision_event_id, horizon_trading_days)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_observations_pending
        ON outcome_observations (userId, status, target_date)
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observation_revisions (
            userId INTEGER NOT NULL,
            observation_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            is_terminal INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, observation_id, revision_no)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_benchmark_mappings (
            userId INTEGER NOT NULL,
            mapping_id TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            benchmark_kind TEXT NOT NULL,
            completeness TEXT NOT NULL,
            benchmark_name TEXT NOT NULL,
            benchmark_code TEXT,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            source TEXT NOT NULL,
            source_ref TEXT,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, mapping_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_fund_benchmark_effective
        ON fund_benchmark_mappings
            (userId, fund_code, valid_from DESC, valid_to, benchmark_kind)
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_events (
            event_revision_id TEXT PRIMARY KEY,
            logical_event_id TEXT NOT NULL,
            userId INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            fund_code TEXT,
            effective_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT,
            event_hash TEXT NOT NULL,
            previous_hash TEXT,
            payload_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (userId, account_id, logical_event_id, revision_no)
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_ledger_source_ref
        ON portfolio_ledger_events (userId, account_id, source, source_ref)
        WHERE source_ref IS NOT NULL
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_ledger_effective
        ON portfolio_ledger_events
            (userId, account_id, effective_at, recorded_at, event_revision_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_heads (
            userId INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            chain_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (userId, account_id)
        )
        """,
    ]
    for statement in statements:
        connection.execute(statement)

    # ``fund_transactions`` predates schema versioning for confirmed execution
    # truth.  Additive repair preserves legacy rows (which remain explicitly
    # derived/unknown) and also heals databases whose version marker was copied.
    if _table_exists(connection, "fund_transactions"):
        transaction_columns = {
            "confirmed_shares": "REAL",
            "fee_yuan": "REAL",
            "shares_source": "TEXT",
            "in_progress": "INTEGER NOT NULL DEFAULT 0",
            "confirmed_at": "TEXT",
        }
        for column, definition in transaction_columns.items():
            if not _column_exists(connection, "fund_transactions", column):
                connection.execute(
                    f"ALTER TABLE fund_transactions ADD COLUMN {column} {definition}"
                )


def _migrate_decision_quality_snapshots(connection: sqlite3.Connection) -> None:
    """Create immutable inputs and outputs for point-in-time quality evaluation.

    The report stores are intentionally replaceable, so candidate-selection and
    claim audits cannot use them as their long-lived trust boundary.  These two
    content-addressed tables keep exact evaluation inputs and results independent
    from report retention, without adding foreign keys to replace-style stores.
    """

    statements = [
        """
        CREATE TABLE IF NOT EXISTS decision_quality_input_artifacts (
            userId INTEGER NOT NULL,
            artifact_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_schema_version TEXT NOT NULL,
            logical_key TEXT,
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
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_quality_artifacts_report
        ON decision_quality_input_artifacts
            (userId, artifact_type, source_report_id, recorded_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_quality_artifacts_event
        ON decision_quality_input_artifacts
            (userId, decision_event_id, artifact_type)
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_evaluation_snapshots (
            userId INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            evaluation_as_of TEXT NOT NULL,
            evaluator_schema_version TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            status TEXT NOT NULL,
            evaluation_hash TEXT NOT NULL,
            input_manifest_hash TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            human_review_status TEXT NOT NULL,
            automatic_promotion_allowed INTEGER NOT NULL DEFAULT 0,
            store_authority TEXT NOT NULL,
            audit_eligible INTEGER NOT NULL DEFAULT 1,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, snapshot_id),
            UNIQUE (userId, content_hash)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_quality_snapshots_cutoff
        ON decision_quality_evaluation_snapshots
            (userId, evaluation_as_of DESC, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_quality_snapshots_status
        ON decision_quality_evaluation_snapshots
            (userId, status, evaluation_as_of DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_quality_snapshots_review
        ON decision_quality_evaluation_snapshots
            (userId, readiness_status, human_review_status, evaluation_as_of DESC)
        """,
    ]
    for statement in statements:
        connection.execute(statement)
    if not _column_exists(
        connection, "decision_quality_input_artifacts", "logical_key"
    ):
        connection.execute(
            "ALTER TABLE decision_quality_input_artifacts "
            "ADD COLUMN logical_key TEXT"
        )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_decision_quality_artifact_logical_key "
        "ON decision_quality_input_artifacts "
        "(userId, artifact_type, logical_key)"
    )
    logical_index_rows = connection.execute(
        "PRAGMA index_list(decision_quality_input_artifacts)"
    ).fetchall()
    logical_index = next(
        (
            row
            for row in logical_index_rows
            if str(row[1]) == "uq_decision_quality_artifact_logical_key"
        ),
        None,
    )
    logical_columns = [
        str(row[2])
        for row in connection.execute(
            "PRAGMA index_info(uq_decision_quality_artifact_logical_key)"
        ).fetchall()
    ]
    logical_index_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'index' "
        "AND name = 'uq_decision_quality_artifact_logical_key'"
    ).fetchone()
    expected_logical_index_sql = (
        "CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key "
        "ON decision_quality_input_artifacts "
        "(userId, artifact_type, logical_key)"
    )
    if (
        logical_index is None
        or int(logical_index[2]) != 1
        or len(logical_index) < 5
        or int(logical_index[4]) != 0
        or logical_columns != ["userId", "artifact_type", "logical_key"]
        or logical_index_sql_row is None
        or _normalized_sql_contract(logical_index_sql_row[0])
        != _normalized_sql_contract(expected_logical_index_sql)
    ):
        raise RuntimeError(
            "decision-quality logical identity index conflicts with contract"
        )
    for table, prefix in (
        ("decision_quality_input_artifacts", "decision_quality_artifacts"),
        ("decision_quality_evaluation_snapshots", "decision_quality_snapshots"),
    ):
        _ensure_sqlite_trigger_contract(
            connection,
            name=f"{prefix}_no_update",
            table=table,
            stored_sql=f"""
            CREATE TRIGGER {prefix}_no_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is append-only');
            END
            """,
        )
        _ensure_sqlite_trigger_contract(
            connection,
            name=f"{prefix}_no_delete",
            table=table,
            stored_sql=f"""
            CREATE TRIGGER {prefix}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is append-only');
            END
            """,
        )


def _migrate_decision_quality_receipts(connection: sqlite3.Connection) -> None:
    artifact_table_sql = """
        CREATE TABLE decision_quality_artifact_receipts (
            userId INTEGER NOT NULL,
            artifact_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            receipt_policy TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_content_hash TEXT NOT NULL,
            source_row_created_at TEXT NOT NULL,
            source_visible_at TEXT NOT NULL,
            store_authority TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (userId, artifact_id)
        )
    """
    provider_table_sql = """
        CREATE TABLE decision_quality_provider_receipts (
            receipt_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            provider TEXT NOT NULL,
            operation TEXT NOT NULL,
            capture_mode TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            adapter_output_sha256 TEXT NOT NULL,
            adapter_output_bytes INTEGER NOT NULL,
            normalized_payload_hash TEXT NOT NULL,
            origin_fetched_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """
    connection.execute(
        artifact_table_sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
    )
    connection.execute(
        provider_table_sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
    )
    _ensure_sqlite_table_contract(
        connection,
        name="decision_quality_artifact_receipts",
        stored_sql=artifact_table_sql,
    )
    _ensure_sqlite_table_contract(
        connection,
        name="decision_quality_provider_receipts",
        stored_sql=provider_table_sql,
    )

    index_contracts = (
        (
            "decision_quality_artifact_receipts",
            "uq_decision_quality_artifact_receipt_id",
            ("userId", "receipt_id"),
            True,
        ),
        (
            "decision_quality_artifact_receipts",
            "uq_decision_quality_artifact_receipt_content",
            ("userId", "content_hash"),
            True,
        ),
        (
            "decision_quality_artifact_receipts",
            "idx_decision_quality_artifact_receipts_visibility",
            ("userId", "source_visible_at", "artifact_id"),
            False,
        ),
        (
            "decision_quality_provider_receipts",
            "uq_decision_quality_provider_receipt_content",
            ("content_hash",),
            True,
        ),
        (
            "decision_quality_provider_receipts",
            "idx_decision_quality_provider_receipts_lookup",
            ("provider", "operation", "completed_at"),
            False,
        ),
    )
    for table, name, columns, unique in index_contracts:
        unique_sql = "UNIQUE " if unique else ""
        stored_sql = (
            f"CREATE {unique_sql}INDEX {name} ON {table} "
            f"({', '.join(columns)})"
        )
        connection.execute(
            stored_sql.replace(" INDEX ", " INDEX IF NOT EXISTS ", 1)
        )
        _ensure_sqlite_index_contract(
            connection,
            table=table,
            name=name,
            columns=columns,
            unique=unique,
            stored_sql=stored_sql,
        )

    for table, prefix in (
        (
            "decision_quality_artifact_receipts",
            "decision_quality_artifact_receipts",
        ),
        (
            "decision_quality_provider_receipts",
            "decision_quality_provider_receipts",
        ),
    ):
        for event in ("update", "delete"):
            _ensure_sqlite_trigger_contract(
                connection,
                name=f"{prefix}_no_{event}",
                table=table,
                stored_sql=f"""
                CREATE TRIGGER {prefix}_no_{event}
                BEFORE {event.upper()} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only');
                END
                """,
            )


def _migrate_decision_quality_rollout(
    connection: sqlite3.Connection,
    *,
    initialize: bool,
) -> None:
    """Create, and only during the v14 upgrade initialize, the D2 boundary.

    A database already advertising v14 must never receive a replacement marker:
    recreating it at a later wall-clock time would silently grandfather events
    written after the original activation boundary.  Missing/tampered rows are
    therefore left for repository and snapshot readers to reject fail-closed.
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_quality_contract_rollouts (
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
    if initialize:
        marker = build_decision_quality_rollout_marker(_now())
        connection.execute(
            """
            INSERT OR IGNORE INTO decision_quality_contract_rollouts (
                contract_name, schema_version, contract_version, required_from,
                created_at, hash_algorithm, canonicalization, marker_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                marker["contract_name"],
                marker["schema_version"],
                marker["contract_version"],
                marker["required_from"],
                marker["created_at"],
                marker["hash_algorithm"],
                marker["canonicalization"],
                marker["marker_hash"],
            ),
        )
    # SQLite has no table-level append-only primitive.  These triggers make the
    # singleton boundary immutable even to accidental application SQL; direct
    # corruption is additionally caught by the canonical marker hash.
    _ensure_sqlite_trigger_contract(
        connection,
        name="decision_quality_rollout_no_update",
        table="decision_quality_contract_rollouts",
        stored_sql=f"""
        CREATE TRIGGER decision_quality_rollout_no_update
        BEFORE UPDATE ON decision_quality_contract_rollouts
        WHEN OLD.contract_name = '{DECISION_QUALITY_ROLLOUT_CONTRACT_NAME}'
        BEGIN
            SELECT RAISE(ABORT, 'decision-quality rollout marker is immutable');
        END
        """,
    )
    _ensure_sqlite_trigger_contract(
        connection,
        name="decision_quality_rollout_no_delete",
        table="decision_quality_contract_rollouts",
        stored_sql=f"""
        CREATE TRIGGER decision_quality_rollout_no_delete
        BEFORE DELETE ON decision_quality_contract_rollouts
        WHEN OLD.contract_name = '{DECISION_QUALITY_ROLLOUT_CONTRACT_NAME}'
        BEGIN
            SELECT RAISE(ABORT, 'decision-quality rollout marker is immutable');
        END
        """,
    )


def _migrate_prompt_shadow_operations(connection: sqlite3.Connection) -> None:
    """Create the mutable D5.1 run-state and daily-budget ledgers.

    These tables are deliberately operational rather than evaluation evidence.
    Immutable policy, registration, attempt, and output records continue to use
    the decision-quality artifact ledger and its post-commit receipts.
    """

    run_table_sql = """
        CREATE TABLE prompt_shadow_runs (
            userId INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            registration_artifact_id TEXT NOT NULL,
            champion_attempt_artifact_id TEXT,
            champion_output_artifact_id TEXT,
            champion_report_id TEXT,
            challenger_attempt_artifact_id TEXT,
            challenger_output_artifact_id TEXT,
            status TEXT NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 0,
            challenger_deadline_at TEXT,
            lease_owner_hash TEXT,
            lease_token_hash TEXT,
            lease_acquired_at TEXT,
            lease_expires_at TEXT,
            champion_network_started_at TEXT,
            challenger_network_started_at TEXT,
            budget_scope_key TEXT,
            budget_date_local TEXT,
            budget_reserved_at TEXT,
            terminal_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (userId, run_id),
            UNIQUE (userId, registration_artifact_id),
            UNIQUE (userId, champion_attempt_artifact_id),
            UNIQUE (userId, champion_output_artifact_id),
            UNIQUE (userId, challenger_attempt_artifact_id),
            UNIQUE (userId, challenger_output_artifact_id)
        )
    """
    budget_table_sql = """
        CREATE TABLE prompt_shadow_budget_counters (
            scope_key TEXT NOT NULL,
            budget_date_local TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            max_calls INTEGER NOT NULL,
            reserved_calls INTEGER NOT NULL,
            started_calls INTEGER NOT NULL,
            completed_calls INTEGER NOT NULL,
            failed_calls INTEGER NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_key, budget_date_local)
        )
    """
    for table_sql in (run_table_sql, budget_table_sql):
        connection.execute(
            table_sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
        )
    _ensure_sqlite_table_contract(
        connection,
        name="prompt_shadow_runs",
        stored_sql=run_table_sql,
    )
    _ensure_sqlite_table_contract(
        connection,
        name="prompt_shadow_budget_counters",
        stored_sql=budget_table_sql,
    )

    index_contracts = (
        (
            "prompt_shadow_runs",
            "idx_prompt_shadow_runs_worker",
            ("status", "lease_expires_at", "challenger_deadline_at", "created_at"),
        ),
        (
            "prompt_shadow_runs",
            "idx_prompt_shadow_runs_decision",
            ("userId", "decision_at", "run_id"),
        ),
        (
            "prompt_shadow_runs",
            "idx_prompt_shadow_runs_report",
            ("userId", "champion_report_id"),
        ),
        (
            "prompt_shadow_budget_counters",
            "idx_prompt_shadow_budget_policy",
            ("policy_hash", "budget_date_local"),
        ),
    )
    for table, name, columns in index_contracts:
        stored_sql = f"CREATE INDEX {name} ON {table} ({', '.join(columns)})"
        connection.execute(
            stored_sql.replace(" INDEX ", " INDEX IF NOT EXISTS ", 1)
        )
        _ensure_sqlite_index_contract(
            connection,
            table=table,
            name=name,
            columns=columns,
            unique=False,
            stored_sql=stored_sql,
        )


def _migrate_admin_user_management(connection: sqlite3.Connection) -> None:
    """Add authoritative account state, reset tokens, and immutable admin audit."""

    if not _table_exists(connection, "users"):
        connection.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userRole TEXT NOT NULL DEFAULT 'user',
                username TEXT NOT NULL,
                userAccount TEXT NOT NULL UNIQUE,
                passwordHash TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '',
                avatarUrl TEXT NOT NULL DEFAULT '',
                cloudbaseUid TEXT,
                createdAt TEXT NOT NULL,
                updatedAt TEXT NOT NULL,
                isDeleted INTEGER NOT NULL DEFAULT 0,
                deletedAt TEXT,
                authVersion INTEGER NOT NULL DEFAULT 1,
                lastLoginAt TEXT,
                lastActiveAt TEXT,
                passwordUpdatedAt TEXT
            )
            """
        )
    user_columns = (
        ("authVersion", "INTEGER NOT NULL DEFAULT 1"),
        ("lastLoginAt", "TEXT"),
        ("lastActiveAt", "TEXT"),
        ("passwordUpdatedAt", "TEXT"),
    )
    for column, definition in user_columns:
        if not _column_exists(connection, "users", column):
            connection.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )
    connection.execute(
        """
        UPDATE users
        SET passwordUpdatedAt = createdAt
        WHERE passwordUpdatedAt IS NULL
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id TEXT PRIMARY KEY,
            userId INTEGER NOT NULL,
            tokenHash TEXT NOT NULL UNIQUE,
            expiresAt TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            usedAt TEXT,
            revokedAt TEXT,
            createdByAdminId INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_password_reset_user_active
        ON password_reset_tokens (userId, usedAt, revokedAt, expiresAt)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_events (
            eventId TEXT PRIMARY KEY,
            actorUserId INTEGER,
            targetUserId INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            beforeJson TEXT NOT NULL,
            afterJson TEXT NOT NULL,
            createdAt TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_admin_audit_created
        ON admin_audit_events (createdAt, eventId)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_admin_audit_target
        ON admin_audit_events (targetUserId, createdAt)
        """
    )
    for event in ("UPDATE", "DELETE"):
        event_lower = event.lower()
        _ensure_sqlite_trigger_contract(
            connection,
            name=f"admin_audit_events_no_{event_lower}",
            table="admin_audit_events",
            stored_sql=f"""
            CREATE TRIGGER admin_audit_events_no_{event_lower}
            BEFORE {event} ON admin_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'admin_audit_events is append-only');
            END
            """,
        )


def run_migrations(connection: sqlite3.Connection) -> None:
    with _MIGRATION_LOCK:
        _run_migrations_locked(connection)


def _run_migrations_locked(connection: sqlite3.Connection) -> None:
    version = _get_schema_version(connection)
    if version >= SCHEMA_VERSION:
        _migrate_fund_primary_sectors_global(connection)
        _migrate_factor_ic_snapshots(connection)
        _migrate_factor_ic_universe_snapshots(connection)
        _migrate_factor_ic_nav_observations(connection)
        _migrate_fund_holdings_snapshots(connection)
        _migrate_decision_accuracy_v2(connection)
        _migrate_decision_quality_snapshots(connection)
        _migrate_decision_quality_receipts(connection)
        _migrate_decision_quality_rollout(connection, initialize=False)
        _migrate_prompt_shadow_operations(connection)
        _migrate_admin_user_management(connection)
        return

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userRole TEXT NOT NULL DEFAULT 'user',
            username TEXT NOT NULL,
            userAccount TEXT NOT NULL UNIQUE,
            passwordHash TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            avatarUrl TEXT NOT NULL DEFAULT '',
            cloudbaseUid TEXT,
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL,
            isDeleted INTEGER NOT NULL DEFAULT 0,
            deletedAt TEXT,
            authVersion INTEGER NOT NULL DEFAULT 1,
            lastLoginAt TEXT,
            lastActiveAt TEXT,
            passwordUpdatedAt TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id TEXT PRIMARY KEY,
            userId INTEGER NOT NULL,
            tokenHash TEXT NOT NULL,
            expiresAt TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            revokedAt TEXT
        )
        """
    )

    if _table_exists(connection, "fund_profiles"):
        _migrate_fund_profiles(connection)
    if _table_exists(connection, "portfolio_state"):
        _migrate_portfolio_state(connection)
    if _table_exists(connection, "investor_profile_state"):
        _migrate_investor_profile(connection)
    if _table_exists(connection, "portfolio_daily_snapshots"):
        _migrate_snapshots(connection, "portfolio_daily_snapshots", "snapshot_date")
    if _table_exists(connection, "portfolio_intraday_curves"):
        _migrate_snapshots(connection, "portfolio_intraday_curves", "trade_date")
    if _table_exists(connection, "sector_mappings"):
        _migrate_sector_mappings(connection)
    if _table_exists(connection, "ocr_text_cache"):
        _migrate_ocr_cache(connection)
    if _table_exists(connection, "reports"):
        _migrate_reports(connection)
    _migrate_analysis_jobs(connection)
    _migrate_fund_primary_sectors(connection)
    _migrate_analysis_prompt_state(connection)
    _migrate_discovery_tables(connection)
    _migrate_discovery_prompt_state(connection)
    _migrate_swing_alert_fired(connection)
    _migrate_fund_primary_sectors_global(connection)
    _migrate_factor_ic_snapshots(connection)
    _migrate_factor_ic_universe_snapshots(connection)
    _migrate_factor_ic_nav_observations(connection)
    _migrate_fund_holdings_snapshots(connection)
    _migrate_decision_accuracy_v2(connection)
    _migrate_decision_quality_snapshots(connection)
    _migrate_decision_quality_receipts(connection)
    _migrate_decision_quality_rollout(connection, initialize=version < 14)
    _migrate_prompt_shadow_operations(connection)

    _ensure_migration_user(connection)
    _migrate_admin_user_management(connection)
    _set_schema_version(connection, SCHEMA_VERSION)
