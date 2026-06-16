from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


SCHEMA_VERSION = 7


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


def run_migrations(connection: sqlite3.Connection) -> None:
    version = _get_schema_version(connection)
    if version >= SCHEMA_VERSION:
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

    _ensure_migration_user(connection)
    _set_schema_version(connection, SCHEMA_VERSION)
