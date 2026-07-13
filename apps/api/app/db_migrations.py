from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


SCHEMA_VERSION = 11

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


def run_migrations(connection: sqlite3.Connection) -> None:
    with _MIGRATION_LOCK:
        _run_migrations_locked(connection)


def _run_migrations_locked(connection: sqlite3.Connection) -> None:
    version = _get_schema_version(connection)
    if version >= SCHEMA_VERSION:
        _migrate_fund_primary_sectors_global(connection)
        _migrate_factor_ic_snapshots(connection)
        _migrate_factor_ic_universe_snapshots(connection)
        _migrate_decision_accuracy_v2(connection)
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
            deletedAt TEXT
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
    _migrate_decision_accuracy_v2(connection)

    _ensure_migration_user(connection)
    _set_schema_version(connection, SCHEMA_VERSION)
