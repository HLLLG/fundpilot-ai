from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from typing import Any

from app.services.decision_quality_rollout import (
    build_decision_quality_rollout_marker,
    normalize_decision_quality_rollout_marker,
)


MYSQL_SCHEMA_VERSION = 16

MYSQL_MIGRATION_GUARD_NAME = "sqlite_to_mysql"
MYSQL_SCHEMA_LOCK_NAME = "fundpilot.mysql_schema.v16"
MYSQL_MIGRATION_GUARD_TABLE = "decision_quality_migration_guard"
_MIGRATION_GUARD_STATUSES = frozenset({"in_progress", "complete"})
_MYSQL_MIGRATION_GUARD_DDL = f"""
    CREATE TABLE IF NOT EXISTS {MYSQL_MIGRATION_GUARD_TABLE} (
        guard_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        source_schema_version INT NOT NULL,
        source_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        rollout_marker_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        rollout_marker_payload LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        started_at VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        completed_at VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
        PRIMARY KEY (guard_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
_MYSQL_MIGRATION_GUARD_COLUMN_CONTRACT = (
    ("guard_name", "varchar", 64, "NO", "ascii_bin"),
    ("status", "varchar", 16, "NO", "ascii_bin"),
    ("source_schema_version", "int", None, "NO", None),
    ("source_fingerprint", "char", 64, "NO", "ascii_bin"),
    ("rollout_marker_hash", "char", 64, "NO", "ascii_bin"),
    ("rollout_marker_payload", "longtext", None, "NO", "utf8mb4_bin"),
    ("started_at", "varchar", 64, "NO", "ascii_bin"),
    ("completed_at", "varchar", 64, "YES", "ascii_bin"),
)

_ROLLOUT_IMMUTABLE_TRIGGER_MESSAGE = (
    "decision quality rollout marker is immutable"
)
_ROLLOUT_IMMUTABLE_TRIGGERS = (
    (
        "trg_decision_quality_rollout_no_update",
        "UPDATE",
    ),
    (
        "trg_decision_quality_rollout_no_delete",
        "DELETE",
    ),
)
_APPEND_ONLY_TABLES = (
    ("decision_quality_input_artifacts", "decision_quality_artifacts"),
    ("decision_quality_evaluation_snapshots", "decision_quality_snapshots"),
    ("decision_quality_artifact_receipts", "decision_quality_artifact_receipts"),
    ("decision_quality_provider_receipts", "decision_quality_provider_receipts"),
)
_DECISION_QUALITY_TRANSACTIONAL_TABLES = (
    "decision_quality_input_artifacts",
    "decision_quality_artifact_receipts",
    "decision_quality_provider_receipts",
    "decision_quality_evaluation_snapshots",
    "decision_quality_contract_rollouts",
    "prompt_shadow_runs",
    "prompt_shadow_budget_counters",
)


class MySqlBootstrapContractError(RuntimeError):
    """The primary MySQL schema cannot enforce a required safety contract."""


def _mysql_schema_lock_timeout_seconds() -> int:
    raw = os.getenv("FUND_AI_MYSQL_SCHEMA_LOCK_TIMEOUT_SECONDS", "60").strip()
    try:
        return max(10, min(int(raw), 300))
    except ValueError:
        return 60


def _canonical_guard_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _lower_sha256(value: object, *, name: str) -> str:
    text = str(value or "")
    if (
        text != text.lower()
        or len(text) != 64
        or any(char not in "0123456789abcdef" for char in text)
    ):
        raise MySqlBootstrapContractError(f"{name} must be a lowercase SHA-256")
    return text


def _cursor_supports_mysql_metadata(cursor: Any) -> bool:
    return callable(getattr(cursor, "fetchone", None)) and callable(
        getattr(cursor, "fetchall", None)
    )


@contextmanager
def _mysql_schema_named_lock(cursor: Any):
    """Serialize app bootstrap and migration-guard registration."""

    fetchone = getattr(cursor, "fetchone", None)
    if not _cursor_supports_mysql_metadata(cursor):
        # Metadata-free/partial DDL-capture cursors used by unit tests cannot
        # model connection-scoped MySQL locks.  PyMySQL cursors expose both.
        yield
        return
    cursor.execute(
        f"SELECT GET_LOCK('{MYSQL_SCHEMA_LOCK_NAME}', "
        f"{_mysql_schema_lock_timeout_seconds()}) AS lock_acquired"
    )
    row = fetchone()
    acquired = (
        row.get("lock_acquired") if isinstance(row, dict) else row[0] if row else None
    )
    if acquired != 1:
        raise MySqlBootstrapContractError(
            "MySQL schema bootstrap lock could not be acquired"
        )
    try:
        yield
    finally:
        try:
            cursor.execute(
                f"SELECT RELEASE_LOCK('{MYSQL_SCHEMA_LOCK_NAME}') AS lock_released"
            )
            fetchone()
        except Exception:
            # A connection-scoped named lock is released by MySQL when the
            # connection closes.  Never hide the original bootstrap failure.
            pass


def _guard_row_mapping(row: Any) -> dict[str, Any]:
    columns = (
        "guard_name",
        "status",
        "source_schema_version",
        "source_fingerprint",
        "rollout_marker_hash",
        "rollout_marker_payload",
        "started_at",
        "completed_at",
    )
    if isinstance(row, dict):
        return {
            column: row.get(column, row.get(column.upper())) for column in columns
        }
    try:
        return dict(zip(columns, tuple(row), strict=True))
    except (TypeError, ValueError) as exc:
        raise MySqlBootstrapContractError(
            "MySQL migration guard row shape is invalid"
        ) from exc


def _canonical_guard_timestamp(value: Any, *, name: str) -> tuple[str, datetime]:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise MySqlBootstrapContractError(
                f"MySQL migration guard {name} is not ASCII"
            ) from exc
    if not isinstance(value, str) or not value:
        raise MySqlBootstrapContractError(
            f"MySQL migration guard {name} is invalid"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MySqlBootstrapContractError(
            f"MySQL migration guard {name} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MySqlBootstrapContractError(
            f"MySQL migration guard {name} must be UTC-aware"
        )
    canonical = parsed.astimezone(timezone.utc).isoformat()
    if value != canonical:
        raise MySqlBootstrapContractError(
            f"MySQL migration guard {name} is not canonical UTC"
        )
    return canonical, parsed


def _normalize_mysql_migration_guard(row: Any) -> dict[str, Any]:
    raw = _guard_row_mapping(row)
    if raw.get("guard_name") != MYSQL_MIGRATION_GUARD_NAME:
        raise MySqlBootstrapContractError("MySQL migration guard identity is invalid")
    status = str(raw.get("status") or "")
    if status not in _MIGRATION_GUARD_STATUSES:
        raise MySqlBootstrapContractError("MySQL migration guard status is invalid")
    try:
        source_schema_version = int(raw.get("source_schema_version"))
    except (TypeError, ValueError) as exc:
        raise MySqlBootstrapContractError(
            "MySQL migration guard source schema is invalid"
        ) from exc
    if source_schema_version < 0 or source_schema_version > MYSQL_SCHEMA_VERSION:
        raise MySqlBootstrapContractError(
            "MySQL migration guard source schema is unsupported"
        )
    source_fingerprint = _lower_sha256(
        raw.get("source_fingerprint"), name="source_fingerprint"
    )
    marker_hash = _lower_sha256(
        raw.get("rollout_marker_hash"), name="rollout_marker_hash"
    )
    payload_text = raw.get("rollout_marker_payload")
    if isinstance(payload_text, bytes):
        payload_text = payload_text.decode("utf-8")
    if not isinstance(payload_text, str):
        raise MySqlBootstrapContractError(
            "MySQL migration guard rollout payload is invalid"
        )
    try:
        decoded = json.loads(payload_text)
        marker = normalize_decision_quality_rollout_marker(decoded)
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError) as exc:
        raise MySqlBootstrapContractError(
            "MySQL migration guard rollout payload failed validation"
        ) from exc
    if payload_text != _canonical_guard_json(marker) or marker["marker_hash"] != marker_hash:
        raise MySqlBootstrapContractError(
            "MySQL migration guard rollout payload is not canonical"
        )
    started_at, started_clock = _canonical_guard_timestamp(
        raw.get("started_at"),
        name="started_at",
    )
    completed_at = raw.get("completed_at")
    completed_clock: datetime | None = None
    if completed_at is not None:
        completed_at, completed_clock = _canonical_guard_timestamp(
            completed_at,
            name="completed_at",
        )
    if status == "in_progress" and completed_at is not None:
        raise MySqlBootstrapContractError(
            "in-progress MySQL migration guard cannot be completed"
        )
    if status == "complete" and completed_at is None:
        raise MySqlBootstrapContractError(
            "completed MySQL migration guard lacks completion time"
        )
    if completed_clock is not None and completed_clock < started_clock:
        raise MySqlBootstrapContractError(
            "MySQL migration guard completion predates its start"
        )
    return {
        "guard_name": MYSQL_MIGRATION_GUARD_NAME,
        "status": status,
        "source_schema_version": source_schema_version,
        "source_fingerprint": source_fingerprint,
        "rollout_marker": marker,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def _read_mysql_migration_guard(cursor: Any) -> dict[str, Any] | None:
    fetchone = getattr(cursor, "fetchone", None)
    if not callable(fetchone):
        return None
    columns = (
        "guard_name, status, source_schema_version, source_fingerprint, "
        "rollout_marker_hash, rollout_marker_payload, started_at, completed_at"
    )
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        cursor.execute(
            f"SELECT {columns} FROM {MYSQL_MIGRATION_GUARD_TABLE} "
            "ORDER BY guard_name"
        )
        rows = list(fetchall())
        if len(rows) > 1:
            raise MySqlBootstrapContractError(
                "MySQL migration guard table must contain at most one row"
            )
        row = rows[0] if rows else None
    else:
        # Backward-compatible path for metadata-light unit-test cursors.
        cursor.execute(
            f"SELECT {columns} FROM {MYSQL_MIGRATION_GUARD_TABLE} "
            f"WHERE guard_name = '{MYSQL_MIGRATION_GUARD_NAME}'"
        )
        row = fetchone()
    return _normalize_mysql_migration_guard(row) if row is not None else None


def _guard_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    marker = normalize_decision_quality_rollout_marker(
        value.get("rollout_marker") or {}
    )
    return (
        int(value.get("source_schema_version", -1)),
        _lower_sha256(value.get("source_fingerprint"), name="source_fingerprint"),
        marker["marker_hash"],
    )


def _verify_mysql_migration_guard_access(
    cursor: Any,
    *,
    migration_guard: dict[str, Any] | None,
) -> None:
    stored = _read_mysql_migration_guard(cursor)
    if stored is None:
        if migration_guard is not None:
            raise MySqlBootstrapContractError(
                "authorized MySQL migration guard is missing"
            )
        return
    if stored["status"] == "in_progress":
        if migration_guard is None:
            raise MySqlBootstrapContractError(
                "SQLite to MySQL migration is in progress; application bootstrap is blocked"
            )
        if _guard_identity(stored) != _guard_identity(migration_guard):
            raise MySqlBootstrapContractError(
                "MySQL migration guard belongs to a different source snapshot"
            )
    elif migration_guard is not None and _guard_identity(stored) != _guard_identity(
        migration_guard
    ):
        raise MySqlBootstrapContractError(
            "completed MySQL migration guard belongs to a different source snapshot"
        )


def _ensure_mysql_migration_guard_contract(cursor: Any, fetchall: Any) -> None:
    """Verify the crash guard cannot hide or rewrite migration state."""

    try:
        cursor.execute(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{MYSQL_MIGRATION_GUARD_TABLE}'"
        )
        table_rows = list(fetchall())
        if len(table_rows) != 1:
            raise MySqlBootstrapContractError(
                "MySQL migration guard table metadata must be a singleton"
            )
        table_row = table_rows[0]
        if isinstance(table_row, dict):
            table_name = table_row.get("TABLE_NAME", table_row.get("table_name"))
            engine = table_row.get("ENGINE", table_row.get("engine"))
        else:
            table_name, engine = table_row[:2]
        if table_name != MYSQL_MIGRATION_GUARD_TABLE or str(engine or "").lower() != "innodb":
            raise MySqlBootstrapContractError(
                "MySQL migration guard table must be exact InnoDB"
            )

        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
            "IS_NULLABLE, ORDINAL_POSITION, COLLATION_NAME "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{MYSQL_MIGRATION_GUARD_TABLE}' "
            "ORDER BY ORDINAL_POSITION"
        )
        columns = tuple(_mysql_dq_column_contract(row) for row in fetchall())
        expected_columns = tuple(
            (*contract[:4], position, contract[4])
            for position, contract in enumerate(
                _MYSQL_MIGRATION_GUARD_COLUMN_CONTRACT,
                start=1,
            )
        )
        legacy_payload_columns = tuple(
            (
                (*expected[:5], actual[5])
                if expected[0] == "rollout_marker_payload"
                else expected
            )
            for actual, expected in zip(columns, expected_columns, strict=False)
        )
        payload_collation = (
            columns[5][5]
            if len(columns) == len(expected_columns)
            else None
        )
        if (
            columns == legacy_payload_columns
            and columns != expected_columns
            and isinstance(payload_collation, str)
            and payload_collation.lower().startswith("utf8mb4_")
        ):
            # Early v16 builds created this column with the server's utf8mb4
            # default collation. Changing only its collation is lossless and
            # lets existing installations converge on the exact contract.
            cursor.execute(
                f"ALTER TABLE {MYSQL_MIGRATION_GUARD_TABLE} "
                "MODIFY COLUMN rollout_marker_payload LONGTEXT "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL"
            )
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "IS_NULLABLE, ORDINAL_POSITION, COLLATION_NAME "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                f"AND TABLE_NAME = '{MYSQL_MIGRATION_GUARD_TABLE}' "
                "ORDER BY ORDINAL_POSITION"
            )
            columns = tuple(_mysql_dq_column_contract(row) for row in fetchall())
        if columns != expected_columns:
            raise MySqlBootstrapContractError(
                "MySQL migration guard columns conflict with the exact contract"
            )

        cursor.execute(
            "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{MYSQL_MIGRATION_GUARD_TABLE}' "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
        )
        indexes = tuple(_mysql_receipt_index_part(row) for row in fetchall())
        if indexes != (("PRIMARY", 0, 1, "guard_name", None),):
            raise MySqlBootstrapContractError(
                "MySQL migration guard primary key conflicts with the exact contract"
            )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - guard metadata is a safety boundary
        raise MySqlBootstrapContractError(
            "MySQL migration guard metadata cannot be verified"
        ) from exc


def _ensure_mysql_guard_engine(cursor: Any) -> None:
    """Compatibility wrapper retained for callers of the former helper."""

    if _cursor_supports_mysql_metadata(cursor):
        _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)


def prepare_mysql_migration_guard(
    connection: Any,
    *,
    source_schema_version: int,
    source_fingerprint: str,
    source_rollout_marker: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist the crash guard before any decision-quality migration DDL."""

    if source_schema_version < 0 or source_schema_version > MYSQL_SCHEMA_VERSION:
        raise MySqlBootstrapContractError("migration source schema is unsupported")
    fingerprint = _lower_sha256(source_fingerprint, name="source_fingerprint")
    cursor = connection.cursor()
    if not _cursor_supports_mysql_metadata(cursor):
        raise MySqlBootstrapContractError(
            "MySQL migration guard metadata cannot be verified"
        )
    with _mysql_schema_named_lock(cursor):
        cursor.execute(_MYSQL_MIGRATION_GUARD_DDL)
        _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)
        stored = _read_mysql_migration_guard(cursor)
        if stored is not None:
            expected_prefix = (source_schema_version, fingerprint)
            stored_prefix = (
                stored["source_schema_version"],
                stored["source_fingerprint"],
            )
            if stored_prefix != expected_prefix:
                raise MySqlBootstrapContractError(
                    "MySQL migration guard belongs to a different source snapshot"
                )
            if source_rollout_marker is not None:
                source_marker = normalize_decision_quality_rollout_marker(
                    source_rollout_marker
                )
                if stored["rollout_marker"] != source_marker:
                    raise MySqlBootstrapContractError(
                        "MySQL migration guard rollout marker conflicts with source"
                    )
            if stored["status"] == "complete":
                cursor.execute(
                    f"UPDATE {MYSQL_MIGRATION_GUARD_TABLE} "
                    "SET status = %s, completed_at = NULL WHERE guard_name = %s",
                    ("in_progress", MYSQL_MIGRATION_GUARD_NAME),
                )
                connection.commit()
                stored = {**stored, "status": "in_progress", "completed_at": None}
            return stored

        marker = (
            normalize_decision_quality_rollout_marker(source_rollout_marker)
            if source_rollout_marker is not None
            else build_decision_quality_rollout_marker(
                datetime.now(timezone.utc).isoformat()
            )
        )
        started_at = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            f"INSERT INTO {MYSQL_MIGRATION_GUARD_TABLE} ("
            "guard_name, status, source_schema_version, source_fingerprint, "
            "rollout_marker_hash, rollout_marker_payload, started_at, completed_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)",
            (
                MYSQL_MIGRATION_GUARD_NAME,
                "in_progress",
                source_schema_version,
                fingerprint,
                marker["marker_hash"],
                _canonical_guard_json(marker),
                started_at,
            ),
        )
        connection.commit()
        return {
            "guard_name": MYSQL_MIGRATION_GUARD_NAME,
            "status": "in_progress",
            "source_schema_version": source_schema_version,
            "source_fingerprint": fingerprint,
            "rollout_marker": marker,
            "started_at": started_at,
            "completed_at": None,
        }


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


def _insert_mysql_rollout_marker(cursor: Any, marker: dict[str, Any]) -> None:
    normalized = normalize_decision_quality_rollout_marker(marker)
    statement = (
        "INSERT IGNORE INTO decision_quality_contract_rollouts ("
        + ", ".join(_ROLLOUT_COLUMNS)
        + ") VALUES ("
        + ", ".join("%s" for _ in _ROLLOUT_COLUMNS)
        + ")"
    )
    values = tuple(normalized[column] for column in _ROLLOUT_COLUMNS)
    if not callable(getattr(cursor, "fetchone", None)) and not callable(
        getattr(cursor, "fetchall", None)
    ):
        # Preserve the lightweight DDL-capture cursor contract used by schema
        # declaration tests.  Real PyMySQL cursors always take bound values.
        # Every value has already passed the strict rollout normalizer, so the
        # literal branch is both deterministic and limited to this metadata-
        # free test adapter.
        literals = ", ".join(
            "'" + str(value).replace("'", "''") + "'" for value in values
        )
        cursor.execute(
            "INSERT IGNORE INTO decision_quality_contract_rollouts ("
            + ", ".join(_ROLLOUT_COLUMNS)
            + ") VALUES ("
            + literals
            + ")"
        )
        return
    cursor.execute(statement, values)


def _read_mysql_rollout_singleton(
    cursor: Any,
    fetchall: Any,
    *,
    expected_marker: dict[str, Any] | None,
    allow_missing: bool,
) -> dict[str, str] | None:
    cursor.execute(
        "SELECT " + ", ".join(_ROLLOUT_COLUMNS)
        + " FROM decision_quality_contract_rollouts ORDER BY contract_name"
    )
    rows = list(fetchall())
    if not rows:
        if allow_missing:
            return None
        raise MySqlBootstrapContractError(
            "MySQL decision-quality rollout marker is missing"
        )
    if len(rows) != 1:
        raise MySqlBootstrapContractError(
            "MySQL decision-quality rollout table must contain exactly one marker"
        )
    row = rows[0]
    raw = (
        {column: row.get(column, row.get(column.upper())) for column in _ROLLOUT_COLUMNS}
        if isinstance(row, dict)
        else dict(zip(_ROLLOUT_COLUMNS, tuple(row), strict=True))
    )
    try:
        marker = normalize_decision_quality_rollout_marker(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MySqlBootstrapContractError(
            "MySQL decision-quality rollout marker failed validation"
        ) from exc
    if expected_marker is not None and marker != normalize_decision_quality_rollout_marker(
        expected_marker
    ):
        raise MySqlBootstrapContractError(
            "MySQL decision-quality rollout marker conflicts with source"
        )
    return marker


def finalize_mysql_migration_activation(
    connection: Any,
    *,
    migration_guard: dict[str, Any],
) -> None:
    """Stage the final marker/schema/guard DML without committing it."""

    cursor = connection.cursor()
    fetchall = getattr(cursor, "fetchall", None)
    if not _cursor_supports_mysql_metadata(cursor):
        raise MySqlBootstrapContractError(
            "MySQL migration activation metadata cannot be verified"
        )
    _ensure_mysql_migration_guard_contract(cursor, fetchall)
    stored = _read_mysql_migration_guard(cursor)
    if (
        stored is None
        or stored["status"] != "in_progress"
        or _guard_identity(stored) != _guard_identity(migration_guard)
    ):
        raise MySqlBootstrapContractError(
            "MySQL migration activation guard is missing or conflicts"
        )
    marker = normalize_decision_quality_rollout_marker(
        migration_guard.get("rollout_marker") or {}
    )
    existing = _read_mysql_rollout_singleton(
        cursor,
        fetchall,
        expected_marker=marker,
        allow_missing=True,
    )
    if existing is None:
        _insert_mysql_rollout_marker(cursor, marker)
    _read_mysql_rollout_singleton(
        cursor,
        fetchall,
        expected_marker=marker,
        allow_missing=False,
    )
    cursor.execute("SELECT version FROM schema_meta WHERE id = 1")
    version_row = cursor.fetchone()
    if version_row is not None:
        raw_version = (
            version_row.get("version")
            if isinstance(version_row, dict)
            else version_row[0]
        )
        if int(raw_version) > MYSQL_SCHEMA_VERSION:
            raise MySqlBootstrapContractError(
                "MySQL migration target schema is newer than this migrator"
            )
    cursor.execute(
        f"INSERT INTO schema_meta (id, version) VALUES (1, {MYSQL_SCHEMA_VERSION}) "
        "ON DUPLICATE KEY UPDATE version = VALUES(version)"
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        f"UPDATE {MYSQL_MIGRATION_GUARD_TABLE} "
        "SET status = %s, completed_at = %s "
        "WHERE guard_name = %s AND status = %s "
        "AND source_schema_version = %s AND source_fingerprint = %s "
        "AND rollout_marker_hash = %s",
        (
            "complete",
            completed_at,
            MYSQL_MIGRATION_GUARD_NAME,
            "in_progress",
            stored["source_schema_version"],
            stored["source_fingerprint"],
            marker["marker_hash"],
        ),
    )
    rowcount = getattr(cursor, "rowcount", 1)
    if rowcount != 1:
        raise MySqlBootstrapContractError(
            "MySQL migration guard completion compare-and-set failed"
        )


def ensure_mysql_schema(
    connection: Any,
    *,
    decision_quality_rollout_marker: dict[str, Any] | None = None,
    migration_guard: dict[str, Any] | None = None,
    defer_activation: bool = False,
    commit: bool = True,
) -> None:
    cursor = connection.cursor()
    with _mysql_schema_named_lock(cursor):
        _ensure_mysql_schema_locked(
            connection,
            cursor,
            decision_quality_rollout_marker=decision_quality_rollout_marker,
            migration_guard=migration_guard,
            defer_activation=defer_activation,
            commit=commit,
        )


def _ensure_mysql_schema_locked(
    connection: Any,
    cursor: Any,
    *,
    decision_quality_rollout_marker: dict[str, Any] | None,
    migration_guard: dict[str, Any] | None,
    defer_activation: bool,
    commit: bool,
) -> None:
    cursor.execute(_MYSQL_MIGRATION_GUARD_DDL)
    if _cursor_supports_mysql_metadata(cursor):
        _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)
    _verify_mysql_migration_guard_access(
        cursor,
        migration_guard=migration_guard,
    )
    statements = [
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            id INT PRIMARY KEY,
            version INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            userRole VARCHAR(32) NOT NULL DEFAULT 'user',
            username VARCHAR(64) NOT NULL,
            userAccount VARCHAR(128) NOT NULL UNIQUE,
            passwordHash VARCHAR(255) NOT NULL,
            bio VARCHAR(500) NOT NULL DEFAULT '',
            avatarUrl VARCHAR(512) NOT NULL DEFAULT '',
            cloudbaseUid VARCHAR(64) NULL,
            createdAt VARCHAR(64) NOT NULL,
            updatedAt VARCHAR(64) NOT NULL,
            isDeleted TINYINT NOT NULL DEFAULT 0,
            deletedAt VARCHAR(64) NULL,
            INDEX idx_users_cloudbase (cloudbaseUid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS reports (
            id VARCHAR(64) PRIMARY KEY,
            created_at VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            userId BIGINT NOT NULL,
            INDEX idx_reports_user (userId, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_profiles (
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            fund_name VARCHAR(255) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_state (
            userId BIGINT PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_daily_snapshots (
            userId BIGINT NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, snapshot_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_intraday_curves (
            userId BIGINT NOT NULL,
            trade_date VARCHAR(16) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS investor_profile_state (
            userId BIGINT PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_prompt_state (
            userId BIGINT PRIMARY KEY,
            role_prompt LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_prompt_state (
            userId BIGINT PRIMARY KEY,
            role_prompt LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_transactions (
            id VARCHAR(64) PRIMARY KEY,
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NULL,
            fund_name VARCHAR(255) NOT NULL,
            direction VARCHAR(8) NOT NULL,
            amount_yuan DOUBLE NOT NULL,
            trade_time VARCHAR(32) NOT NULL,
            confirm_date VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            shares_delta DOUBLE NULL,
            nav_on_confirm DOUBLE NULL,
            confirmed_shares DOUBLE NULL,
            fee_yuan DOUBLE NULL,
            shares_source VARCHAR(32) NULL,
            in_progress TINYINT NOT NULL DEFAULT 0,
            confirmed_at VARCHAR(64) NULL,
            dedup_key VARCHAR(255) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_fund_tx_dedup (userId, dedup_key),
            INDEX idx_fund_tx_fund (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS sector_mappings (
            userId BIGINT NOT NULL,
            sector_label VARCHAR(255) NOT NULL,
            source_type VARCHAR(64) NOT NULL,
            source_code VARCHAR(64) NULL,
            source_name VARCHAR(255) NOT NULL,
            confidence VARCHAR(32) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, sector_label)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_primary_sectors (
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            sector_name VARCHAR(255) NOT NULL,
            intraday_index_name VARCHAR(255) NULL,
            source VARCHAR(64) NOT NULL,
            confidence DOUBLE NULL,
            detail LONGTEXT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_primary_sectors_global (
            fund_code VARCHAR(16) NOT NULL PRIMARY KEY,
            sector_name VARCHAR(255) NOT NULL,
            intraday_index_name VARCHAR(255) NULL,
            source VARCHAR(64) NOT NULL,
            confidence DOUBLE NULL,
            detail LONGTEXT NULL,
            resolved_at VARCHAR(64) NOT NULL,
            INDEX idx_fund_primary_sectors_global_resolved (resolved_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ocr_text_cache (
            userId BIGINT NOT NULL,
            cache_key VARCHAR(255) NOT NULL,
            raw_text LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, cache_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS report_chat_messages (
            id VARCHAR(64) PRIMARY KEY,
            report_id VARCHAR(64) NOT NULL,
            role VARCHAR(32) NOT NULL,
            content LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            INDEX idx_chat_report (report_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(32) NOT NULL,
            request_payload LONGTEXT NOT NULL,
            report_id VARCHAR(64) NULL,
            error LONGTEXT NULL,
            stage VARCHAR(64) NULL,
            stage_label VARCHAR(255) NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_discovery_reports (
            id VARCHAR(64) PRIMARY KEY,
            created_at VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            INDEX idx_discovery_user_created (userId, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_jobs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(32) NOT NULL,
            request_payload LONGTEXT NOT NULL,
            discovery_report_id VARCHAR(64) NULL,
            error LONGTEXT NULL,
            stage VARCHAR(64) NULL,
            stage_label VARCHAR(255) NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_chat_messages (
            id VARCHAR(64) PRIMARY KEY,
            discovery_report_id VARCHAR(64) NOT NULL,
            role VARCHAR(32) NOT NULL,
            content LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            INDEX idx_discovery_chat_report (discovery_report_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS swing_alert_fired (
            userId BIGINT NOT NULL,
            trade_date VARCHAR(16) NOT NULL,
            alert_key VARCHAR(255) NOT NULL,
            payload LONGTEXT NOT NULL,
            fired_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, trade_date, alert_key),
            INDEX idx_swing_alert_user_date (userId, trade_date, fired_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id VARCHAR(64) PRIMARY KEY,
            userId BIGINT NOT NULL,
            tokenHash VARCHAR(255) NOT NULL,
            expiresAt VARCHAR(64) NOT NULL,
            createdAt VARCHAR(64) NOT NULL,
            revokedAt VARCHAR(64) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS news_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS sector_spot_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_snapshots (
            snapshot_id VARCHAR(64) PRIMARY KEY,
            schema_version INT NOT NULL,
            run_date VARCHAR(16) NOT NULL,
            generated_at VARCHAR(64) NOT NULL,
            published_at VARCHAR(64) NOT NULL,
            source_commit VARCHAR(64) NOT NULL,
            source_run_id VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            INDEX idx_factor_ic_generated (generated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_snapshots (
            snapshot_id VARCHAR(64) PRIMARY KEY,
            schema_version INT NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            available_at VARCHAR(64) NOT NULL,
            captured_at VARCHAR(64) NOT NULL,
            published_at VARCHAR(64) NOT NULL,
            source VARCHAR(64) NOT NULL,
            source_share_count INT NOT NULL,
            deduped_fund_count INT NOT NULL,
            sampled_fund_count INT NOT NULL,
            sample_target INT NOT NULL,
            fund_type_count INT NOT NULL,
            source_commit VARCHAR(64) NOT NULL,
            source_run_id VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            INDEX idx_factor_ic_universe_date (snapshot_date, available_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_members (
            snapshot_id VARCHAR(64) NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            fund_name VARCHAR(255) NOT NULL,
            fund_type VARCHAR(32) NOT NULL,
            share_class VARCHAR(16) NULL,
            canonical_portfolio_key VARCHAR(64) NOT NULL,
            inception_date VARCHAR(16) NULL,
            available_at VARCHAR(64) NOT NULL,
            source_rank INT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (snapshot_id, fund_code),
            INDEX idx_factor_ic_universe_member_code (fund_code, snapshot_id),
            INDEX idx_factor_ic_universe_member_type (snapshot_id, fund_type),
            INDEX idx_factor_ic_universe_member_portfolio (canonical_portfolio_key, snapshot_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_holdings_snapshots (
            id VARCHAR(128) PRIMARY KEY,
            fund_master_key VARCHAR(128) NOT NULL,
            fund_code VARCHAR(32) NOT NULL,
            report_period VARCHAR(16) NULL,
            as_of_date VARCHAR(16) NULL,
            available_at VARCHAR(64) NULL,
            first_observed_at VARCHAR(64) NOT NULL,
            source_hash CHAR(64) NOT NULL,
            snapshot_hash CHAR(64) NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            payload_json LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_fund_holdings_snapshot_hash (snapshot_hash),
            INDEX idx_fund_holdings_snapshots_code_pit
                (fund_code, available_at, status, first_observed_at),
            INDEX idx_fund_holdings_snapshots_master_pit
                (fund_master_key, available_at, status, first_observed_at),
            INDEX idx_fund_holdings_snapshots_period
                (fund_master_key, report_period, available_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_portfolio_snapshots (
            userId BIGINT NOT NULL,
            snapshot_id VARCHAR(64) NOT NULL,
            account_id VARCHAR(128) NOT NULL DEFAULT 'default',
            snapshot_at VARCHAR(64) NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            source_type VARCHAR(64) NOT NULL,
            truth_status VARCHAR(32) NOT NULL,
            ledger_version VARCHAR(128) NULL,
            cash_yuan DOUBLE NULL,
            total_market_value_yuan DOUBLE NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, snapshot_id),
            INDEX idx_decision_snapshots_user_date (userId, snapshot_date, snapshot_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_events (
            userId BIGINT NOT NULL,
            event_id VARCHAR(255) NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            source_type VARCHAR(32) NOT NULL,
            source_report_id VARCHAR(64) NULL,
            decision_at VARCHAR(64) NOT NULL,
            decision_date VARCHAR(16) NOT NULL,
            fund_code VARCHAR(32) NULL,
            fund_name VARCHAR(255) NULL,
            proposed_action VARCHAR(255) NULL,
            final_action VARCHAR(255) NOT NULL,
            action_category VARCHAR(32) NOT NULL,
            eligible TINYINT NOT NULL DEFAULT 0,
            amount_yuan DOUBLE NULL,
            portfolio_snapshot_id VARCHAR(64) NULL,
            benchmark_mapping_id VARCHAR(64) NULL,
            fee_model VARCHAR(64) NULL,
            is_backfilled TINYINT NOT NULL DEFAULT 0,
            metric_eligible TINYINT NOT NULL DEFAULT 1,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, event_id),
            INDEX idx_decision_events_user_report
                (userId, source_type, source_report_id),
            INDEX idx_decision_events_user_date
                (userId, decision_date, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observations (
            userId BIGINT NOT NULL,
            observation_id VARCHAR(255) NOT NULL,
            decision_event_id VARCHAR(255) NOT NULL,
            horizon_trading_days INT NOT NULL,
            target_date VARCHAR(16) NULL,
            status VARCHAR(32) NOT NULL,
            is_terminal TINYINT NOT NULL DEFAULT 0,
            revision_no INT NOT NULL DEFAULT 1,
            observed_at VARCHAR(64) NOT NULL,
            finalized_at VARCHAR(64) NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, observation_id),
            UNIQUE KEY uq_outcome_event_horizon
                (userId, decision_event_id, horizon_trading_days),
            INDEX idx_outcome_observations_pending (userId, status, target_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observation_revisions (
            userId BIGINT NOT NULL,
            observation_id VARCHAR(255) NOT NULL,
            revision_no INT NOT NULL,
            status VARCHAR(32) NOT NULL,
            is_terminal TINYINT NOT NULL,
            observed_at VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, observation_id, revision_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_benchmark_mappings (
            userId BIGINT NOT NULL,
            mapping_id VARCHAR(64) NOT NULL,
            fund_code VARCHAR(32) NOT NULL,
            benchmark_kind VARCHAR(32) NOT NULL,
            completeness VARCHAR(32) NOT NULL,
            benchmark_name VARCHAR(500) NOT NULL,
            benchmark_code VARCHAR(64) NULL,
            valid_from VARCHAR(16) NOT NULL,
            valid_to VARCHAR(16) NULL,
            source VARCHAR(64) NOT NULL,
            source_ref VARCHAR(512) NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, mapping_id),
            INDEX idx_fund_benchmark_effective
                (userId, fund_code, valid_from, valid_to, benchmark_kind)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_events (
            event_revision_id VARCHAR(64) PRIMARY KEY,
            logical_event_id VARCHAR(255) NOT NULL,
            userId BIGINT NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            revision_no INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            fund_code VARCHAR(32) NULL,
            effective_at VARCHAR(64) NOT NULL,
            recorded_at VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            source VARCHAR(64) NOT NULL,
            source_ref VARCHAR(255) NULL,
            event_hash VARCHAR(64) NOT NULL,
            previous_hash VARCHAR(64) NULL,
            payload_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_portfolio_ledger_logical_revision
                (userId, account_id, logical_event_id, revision_no),
            UNIQUE KEY uq_portfolio_ledger_source_ref
                (userId, account_id, source, source_ref),
            INDEX idx_portfolio_ledger_effective
                (userId, account_id, effective_at, recorded_at, event_revision_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_heads (
            userId BIGINT NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            revision BIGINT NOT NULL,
            chain_hash VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, account_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_input_artifacts (
            userId BIGINT NOT NULL,
            artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            artifact_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
            artifact_schema_version VARCHAR(64) NOT NULL,
            logical_key VARCHAR(255) COLLATE utf8mb4_bin NULL,
            source_type VARCHAR(64) NOT NULL,
            source_report_id VARCHAR(128) NULL,
            decision_event_id VARCHAR(255) NULL,
            decision_at VARCHAR(64) NULL,
            available_at VARCHAR(64) NOT NULL,
            recorded_at VARCHAR(64) NOT NULL,
            store_authority VARCHAR(32) NOT NULL,
            audit_eligible TINYINT NOT NULL DEFAULT 0,
            content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, artifact_id),
            UNIQUE KEY uq_decision_quality_artifact_content
                (userId, artifact_type, content_hash),
            UNIQUE KEY uq_decision_quality_artifact_logical_key
                (userId, artifact_type, logical_key),
            INDEX idx_decision_quality_artifacts_report
                (userId, artifact_type, source_report_id, recorded_at),
            INDEX idx_decision_quality_artifacts_event
                (userId, decision_event_id, artifact_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_artifact_receipts (
            userId BIGINT NOT NULL,
            artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            receipt_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            receipt_policy VARCHAR(64) NOT NULL,
            artifact_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
            artifact_content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            source_row_created_at VARCHAR(64) NOT NULL,
            source_visible_at VARCHAR(64) NOT NULL,
            store_authority VARCHAR(32) NOT NULL,
            content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, artifact_id),
            UNIQUE KEY uq_decision_quality_artifact_receipt_id
                (userId, receipt_id),
            UNIQUE KEY uq_decision_quality_artifact_receipt_content
                (userId, content_hash),
            INDEX idx_decision_quality_artifact_receipts_visibility
                (userId, source_visible_at, artifact_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_provider_receipts (
            receipt_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            provider VARCHAR(128) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            capture_mode VARCHAR(64) NOT NULL,
            request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            adapter_output_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            adapter_output_bytes BIGINT NOT NULL,
            normalized_payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            origin_fetched_at VARCHAR(64) NOT NULL,
            completed_at VARCHAR(64) NOT NULL,
            content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (receipt_id),
            UNIQUE KEY uq_decision_quality_provider_receipt_content
                (content_hash),
            INDEX idx_decision_quality_provider_receipts_lookup
                (provider, operation, completed_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_evaluation_snapshots (
            userId BIGINT NOT NULL,
            snapshot_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            evaluation_as_of VARCHAR(64) NOT NULL,
            evaluator_schema_version VARCHAR(64) NOT NULL,
            evaluator_version VARCHAR(128) NOT NULL,
            status VARCHAR(32) NOT NULL,
            evaluation_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            input_manifest_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            config_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            readiness_status VARCHAR(64) NOT NULL,
            human_review_status VARCHAR(64) NOT NULL,
            automatic_promotion_allowed TINYINT NOT NULL DEFAULT 0,
            store_authority VARCHAR(32) NOT NULL,
            audit_eligible TINYINT NOT NULL DEFAULT 1,
            content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, snapshot_id),
            UNIQUE KEY uq_decision_quality_snapshot_content
                (userId, content_hash),
            INDEX idx_decision_quality_snapshots_cutoff
                (userId, evaluation_as_of, created_at),
            INDEX idx_decision_quality_snapshots_status
                (userId, status, evaluation_as_of),
            INDEX idx_decision_quality_snapshots_review
                (userId, readiness_status, human_review_status, evaluation_as_of)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_quality_contract_rollouts (
            contract_name VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            required_from VARCHAR(64) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            hash_algorithm VARCHAR(16) NOT NULL,
            canonicalization VARCHAR(64) NOT NULL,
            marker_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            PRIMARY KEY (contract_name),
            UNIQUE KEY uq_decision_quality_rollout_hash (marker_hash)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS prompt_shadow_runs (
            userId BIGINT NOT NULL,
            run_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            policy_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            policy_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            decision_at VARCHAR(64) NOT NULL,
            registration_artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            champion_attempt_artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NULL,
            champion_output_artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NULL,
            champion_report_id VARCHAR(128) NULL,
            challenger_attempt_artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NULL,
            challenger_output_artifact_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NULL,
            status VARCHAR(64) NOT NULL,
            state_version BIGINT NOT NULL DEFAULT 0,
            challenger_deadline_at VARCHAR(64) NULL,
            lease_owner_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
            lease_token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
            lease_acquired_at VARCHAR(64) NULL,
            lease_expires_at VARCHAR(64) NULL,
            champion_network_started_at VARCHAR(64) NULL,
            challenger_network_started_at VARCHAR(64) NULL,
            budget_scope_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,
            budget_date_local VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL,
            budget_reserved_at VARCHAR(64) NULL,
            terminal_reason VARCHAR(128) NULL,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, run_id),
            UNIQUE KEY uq_prompt_shadow_registration
                (userId, registration_artifact_id),
            UNIQUE KEY uq_prompt_shadow_champion_attempt
                (userId, champion_attempt_artifact_id),
            UNIQUE KEY uq_prompt_shadow_champion_output
                (userId, champion_output_artifact_id),
            UNIQUE KEY uq_prompt_shadow_challenger_attempt
                (userId, challenger_attempt_artifact_id),
            UNIQUE KEY uq_prompt_shadow_challenger_output
                (userId, challenger_output_artifact_id),
            INDEX idx_prompt_shadow_runs_worker
                (status, lease_expires_at, challenger_deadline_at, created_at),
            INDEX idx_prompt_shadow_runs_decision
                (userId, decision_at, run_id),
            INDEX idx_prompt_shadow_runs_report
                (userId, champion_report_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS prompt_shadow_budget_counters (
            scope_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            budget_date_local VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            policy_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            policy_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            max_calls INT NOT NULL,
            reserved_calls INT NOT NULL,
            started_calls INT NOT NULL,
            completed_calls INT NOT NULL,
            failed_calls INT NOT NULL,
            state_version BIGINT NOT NULL DEFAULT 0,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (scope_key, budget_date_local),
            INDEX idx_prompt_shadow_budget_policy
                (policy_hash, budget_date_local)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]
    for statement in statements:
        cursor.execute(statement)

    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        # CREATE TABLE IF NOT EXISTS preserves an existing table's engine.  The
        # five quality ledgers rely on atomic writes and commit visibility, so
        # accepting a legacy MyISAM table would invalidate the receipt and
        # rollout contracts even if every column, index, and trigger matched.
        # Keep this before all trigger/marker/version work so an invalid engine
        # cannot be mistaken for a successfully bootstrapped v15 database.
        _ensure_decision_quality_storage_engine_mysql_contract(
            cursor,
            fetchall,
        )

    try:
        _ensure_decision_quality_rollout_mysql_triggers(cursor)
        _ensure_decision_quality_append_only_mysql_triggers(cursor)
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert privilege/DDL errors
        raise MySqlBootstrapContractError(
            "MySQL cannot enforce the immutable decision-quality ledger; "
            "grant TRIGGER on the application schema and retry bootstrap"
        ) from exc

    # Existing MySQL installations need additive repair because CREATE TABLE IF
    # NOT EXISTS does not add columns introduced by later application versions.
    fetchone = getattr(cursor, "fetchone", None)
    stored_schema_version: int | None = None
    if callable(fetchone):
        cursor.execute("SELECT version FROM schema_meta WHERE id = 1")
        schema_row = fetchone()
        if schema_row is not None:
            raw_version = (
                schema_row.get("version")
                if isinstance(schema_row, dict)
                else schema_row[0]
            )
            stored_schema_version = int(raw_version)
    if stored_schema_version is not None and stored_schema_version > MYSQL_SCHEMA_VERSION:
        raise MySqlBootstrapContractError(
            f"MySQL schema v{stored_schema_version} is newer than this application "
            f"(v{MYSQL_SCHEMA_VERSION})"
        )
    if callable(fetchone):
        transaction_columns = {
            "confirmed_shares": "DOUBLE NULL",
            "fee_yuan": "DOUBLE NULL",
            "shares_source": "VARCHAR(32) NULL",
            "in_progress": "TINYINT NOT NULL DEFAULT 0",
            "confirmed_at": "VARCHAR(64) NULL",
        }
        for column, definition in transaction_columns.items():
            cursor.execute(
                f"""
                SELECT 1 FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'fund_transactions'
                  AND COLUMN_NAME = '{column}'
                """
            )
            if fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE fund_transactions ADD COLUMN {column} {definition}"
                )
        _ensure_decision_quality_logical_key_mysql_contract(cursor, fetchone)
        # Ledger versions are content-addressed strings (for example
        # ``pl1:4:abc123``), not counters.  Early v10 DDL declared BIGINT and
        # would reject every real snapshot on MySQL.
        cursor.execute(
            """
            SELECT DATA_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'decision_portfolio_snapshots'
              AND COLUMN_NAME = 'ledger_version'
            """
        )
        ledger_version_column = fetchone()
        if ledger_version_column is not None:
            if isinstance(ledger_version_column, dict):
                ledger_type = str(ledger_version_column.get("DATA_TYPE") or "").lower()
            else:
                ledger_type = str(ledger_version_column[0] or "").lower()
            if ledger_type not in {"varchar", "char", "text", "mediumtext", "longtext"}:
                cursor.execute(
                    "ALTER TABLE decision_portfolio_snapshots "
                    "MODIFY COLUMN ledger_version VARCHAR(128) NULL"
                )
    if callable(fetchall):
        _repair_legacy_decision_quality_mysql_contracts(cursor, fetchall)
        _ensure_decision_quality_mysql_contracts(cursor, fetchall)
        _ensure_prompt_shadow_mysql_contracts(cursor, fetchall)

    expected_marker = (
        normalize_decision_quality_rollout_marker(
            decision_quality_rollout_marker
        )
        if decision_quality_rollout_marker is not None
        else None
    )
    if defer_activation:
        if migration_guard is None:
            raise MySqlBootstrapContractError(
                "deferred MySQL activation requires a persistent migration guard"
            )
        guard_marker = normalize_decision_quality_rollout_marker(
            migration_guard.get("rollout_marker") or {}
        )
        if expected_marker is not None and guard_marker != expected_marker:
            raise MySqlBootstrapContractError(
                "migration guard rollout marker conflicts with the source marker"
            )
        if callable(fetchall):
            _read_mysql_rollout_singleton(
                cursor,
                fetchall,
                expected_marker=guard_marker,
                allow_missing=True,
            )
        return

    if stored_schema_version is None or stored_schema_version < 14:
        marker = expected_marker or build_decision_quality_rollout_marker(
            datetime.now(timezone.utc).isoformat()
        )
        _insert_mysql_rollout_marker(cursor, marker)
    else:
        marker = expected_marker
    if callable(fetchall):
        _read_mysql_rollout_singleton(
            cursor,
            fetchall,
            expected_marker=marker,
            allow_missing=False,
        )
    cursor.execute(
        f"""
        INSERT INTO schema_meta (id, version) VALUES (1, {MYSQL_SCHEMA_VERSION})
        ON DUPLICATE KEY UPDATE version = VALUES(version)
        """
    )
    if commit:
        connection.commit()


def _ensure_decision_quality_storage_engine_mysql_contract(
    cursor: Any,
    fetchall: Any,
) -> None:
    """Require every decision-quality ledger to use transactional InnoDB.

    A single grouped metadata read is intentionally checked as an exact set:
    missing or duplicate rows are contract failures, not evidence that a
    ``CREATE TABLE IF NOT EXISTS`` statement repaired the installation.
    """

    table_names = ", ".join(
        f"'{table}'" for table in _DECISION_QUALITY_TRANSACTIONAL_TABLES
    )
    try:
        cursor.execute(
            f"""
            SELECT TABLE_NAME, ENGINE
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ({table_names})
            ORDER BY TABLE_NAME
            """
        )
        observed: list[tuple[str, str]] = []
        for row in fetchall():
            if isinstance(row, dict):
                table = row.get("TABLE_NAME") or row.get("table_name")
                engine = row.get("ENGINE") or row.get("engine")
            else:
                table, engine = row[:2]
            observed.append((str(table or ""), str(engine or "")))

        observed_names = [table for table, _engine in observed]
        expected_names = set(_DECISION_QUALITY_TRANSACTIONAL_TABLES)
        if len(observed_names) != len(set(observed_names)):
            raise MySqlBootstrapContractError(
                "MySQL decision-quality storage engine metadata contains "
                "duplicate table rows"
            )
        missing = expected_names.difference(observed_names)
        unexpected = set(observed_names).difference(expected_names)
        if missing or unexpected or len(observed_names) != len(expected_names):
            raise MySqlBootstrapContractError(
                "MySQL decision-quality storage engine metadata does not "
                "cover the exact required table set"
            )
        invalid = sorted(
            table
            for table, engine in observed
            if engine.lower() != "innodb"
        )
        if invalid:
            raise MySqlBootstrapContractError(
                "MySQL decision-quality tables must use InnoDB: "
                + ", ".join(invalid)
            )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - metadata is a release contract
        raise MySqlBootstrapContractError(
            "MySQL decision-quality storage engine metadata cannot be verified"
        ) from exc


def _ensure_decision_quality_rollout_mysql_triggers(cursor: Any) -> None:
    """Create and verify the database-owned immutable rollout boundary.

    Application-level hash validation cannot prevent a writer from moving the
    boundary and recomputing its hash.  The MySQL table therefore mirrors the
    SQLite UPDATE/DELETE guards.  Existing triggers are validated instead of
    trusted by name so a no-op replacement fails bootstrap closed.
    """

    fetchone = getattr(cursor, "fetchone", None)
    for trigger_name, event in _ROLLOUT_IMMUTABLE_TRIGGERS:
        create_statement = f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {event} ON decision_quality_contract_rollouts
            FOR EACH ROW
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = '{_ROLLOUT_IMMUTABLE_TRIGGER_MESSAGE}'
        """
        if not callable(fetchone):
            # Lightweight DDL-capture test cursors do not expose result reads.
            # Real DB-API MySQL cursors always take the metadata path below.
            cursor.execute(create_statement)
            continue

        row = _read_mysql_trigger_row(cursor, fetchone, trigger_name)
        if row is None:
            try:
                cursor.execute(create_statement)
            except Exception as exc:  # noqa: BLE001 - recheck concurrent DDL
                row = _read_mysql_trigger_row(cursor, fetchone, trigger_name)
                if not _valid_rollout_immutable_trigger_row(row, event=event):
                    raise MySqlBootstrapContractError(
                        f"MySQL rollout immutability trigger {trigger_name} "
                        "cannot be enforced"
                    ) from exc
            continue
        if not _valid_rollout_immutable_trigger_row(row, event=event):
            raise MySqlBootstrapContractError(
                f"MySQL rollout immutability trigger {trigger_name} conflicts "
                "with the required contract"
            )


def _ensure_decision_quality_append_only_mysql_triggers(cursor: Any) -> None:
    """Make the content-addressed quality ledger physically append-only."""

    fetchone = getattr(cursor, "fetchone", None)
    for table, prefix in _APPEND_ONLY_TABLES:
        message = f"{table} is append-only"
        for event in ("UPDATE", "DELETE"):
            trigger_name = f"trg_{prefix}_no_{event.lower()}"
            create_statement = f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {event} ON {table}
                FOR EACH ROW
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = '{message}'
            """
            if not callable(fetchone):
                cursor.execute(create_statement)
                continue
            row = _read_mysql_trigger_row(cursor, fetchone, trigger_name)
            if row is None:
                try:
                    cursor.execute(create_statement)
                except Exception as exc:  # noqa: BLE001 - concurrent DDL
                    row = _read_mysql_trigger_row(
                        cursor,
                        fetchone,
                        trigger_name,
                    )
                    if not _valid_immutable_trigger_row(
                        row,
                        event=event,
                        table=table,
                        message=message,
                    ):
                        raise MySqlBootstrapContractError(
                            f"MySQL append-only trigger {trigger_name} "
                            "cannot be enforced"
                        ) from exc
                continue
            if not _valid_immutable_trigger_row(
                row,
                event=event,
                table=table,
                message=message,
            ):
                raise MySqlBootstrapContractError(
                    f"MySQL append-only trigger {trigger_name} conflicts "
                    "with the required contract"
                )


def _valid_rollout_immutable_trigger_row(row: Any, *, event: str) -> bool:
    return _valid_immutable_trigger_row(
        row,
        event=event,
        table="decision_quality_contract_rollouts",
        message=_ROLLOUT_IMMUTABLE_TRIGGER_MESSAGE,
    )


def _read_mysql_trigger_row(cursor: Any, fetchone: Any, trigger_name: str) -> Any:
    cursor.execute(
        f"""
        SELECT ACTION_TIMING, EVENT_MANIPULATION, EVENT_OBJECT_TABLE,
               ACTION_STATEMENT
        FROM information_schema.TRIGGERS
        WHERE TRIGGER_SCHEMA = DATABASE()
          AND TRIGGER_NAME = '{trigger_name}'
        """
    )
    return fetchone()


def _valid_immutable_trigger_row(
    row: Any,
    *,
    event: str,
    table: str,
    message: str,
) -> bool:
    expected_table = table
    if isinstance(row, dict):
        timing = row.get("ACTION_TIMING") or row.get("action_timing")
        manipulation = row.get("EVENT_MANIPULATION") or row.get(
            "event_manipulation"
        )
        observed_table = row.get("EVENT_OBJECT_TABLE") or row.get(
            "event_object_table"
        )
        action = row.get("ACTION_STATEMENT") or row.get("action_statement")
    else:
        try:
            timing, manipulation, observed_table, action = row[:4]
        except (TypeError, ValueError):
            return False
    normalized_action = " ".join(str(action or "").lower().split()).rstrip(
        ";"
    ).strip()
    expected_actions = {
        "signal sqlstate '45000' set message_text = "
        f"'{message}'",
        "signal sqlstate value '45000' set message_text = "
        f"'{message}'",
    }
    return (
        str(timing or "").upper() == "BEFORE"
        and str(manipulation or "").upper() == event
        and str(observed_table or "").lower() == expected_table
        and normalized_action in expected_actions
    )


def _ensure_decision_quality_logical_key_mysql_contract(
    cursor: Any,
    fetchone: Any,
) -> None:
    """Install the candidate terminal-identity contract without unsafe fallback.

    MySQL DDL is implicitly committed and multiple application workers may run
    bootstrap concurrently.  A losing worker can therefore observe a missing
    column/index and then receive a duplicate-object error after another worker
    installs it.  Re-read exact metadata after a failed DDL: accept only the
    completed canonical contract, otherwise surface a bootstrap contract error
    so ``connect_with_fallback`` cannot hide the defect behind SQLite.
    """

    try:
        column_row = _read_logical_artifact_column_row(cursor, fetchone)
        if column_row is None:
            column_ddl_error: Exception | None = None
            try:
                cursor.execute(
                    "ALTER TABLE decision_quality_input_artifacts "
                    "ADD COLUMN logical_key VARCHAR(255) "
                    "COLLATE utf8mb4_bin NULL"
                )
            except Exception as exc:  # noqa: BLE001 - recheck concurrent DDL
                column_ddl_error = exc
            column_row = _read_logical_artifact_column_row(cursor, fetchone)
            if not _valid_logical_artifact_column_row(column_row):
                raise MySqlBootstrapContractError(
                    "MySQL candidate logical identity column cannot be enforced"
                ) from column_ddl_error
        elif not _valid_logical_artifact_column_row(column_row):
            raise MySqlBootstrapContractError(
                "MySQL candidate logical identity column conflicts with contract"
            )

        index_row = _read_logical_artifact_index_row(cursor, fetchone)
        if index_row is None:
            index_ddl_error: Exception | None = None
            try:
                cursor.execute(
                    "CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key "
                    "ON decision_quality_input_artifacts "
                    "(userId, artifact_type, logical_key)"
                )
            except Exception as exc:  # noqa: BLE001 - recheck concurrent DDL
                index_ddl_error = exc
            index_row = _read_logical_artifact_index_row(cursor, fetchone)
            if not _valid_logical_artifact_index_row(index_row):
                raise MySqlBootstrapContractError(
                    "MySQL candidate logical identity index cannot be enforced"
                ) from index_ddl_error
        elif not _valid_logical_artifact_index_row(index_row):
            raise MySqlBootstrapContractError(
                "MySQL candidate logical identity index conflicts with contract"
            )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - metadata access is contractual
        raise MySqlBootstrapContractError(
            "MySQL candidate logical identity metadata cannot be verified"
        ) from exc


def _read_logical_artifact_column_row(cursor: Any, fetchone: Any) -> Any:
    cursor.execute(
        """
        SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'decision_quality_input_artifacts'
          AND COLUMN_NAME = 'logical_key'
        """
    )
    return fetchone()


def _read_logical_artifact_index_row(cursor: Any, fetchone: Any) -> Any:
    cursor.execute(
        """
        SELECT MIN(NON_UNIQUE) AS NON_UNIQUE,
               GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',')
                   AS INDEX_COLUMNS,
               MAX(CASE WHEN SUB_PART IS NULL THEN 0 ELSE 1 END) AS HAS_PREFIX
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'decision_quality_input_artifacts'
          AND INDEX_NAME = 'uq_decision_quality_artifact_logical_key'
        GROUP BY INDEX_NAME
        """
    )
    return fetchone()


def _valid_logical_artifact_column_row(row: Any) -> bool:
    if isinstance(row, dict):
        data_type = row.get("DATA_TYPE") or row.get("data_type")
        max_length = row.get("CHARACTER_MAXIMUM_LENGTH")
        if max_length is None:
            max_length = row.get("character_maximum_length")
        nullable = row.get("IS_NULLABLE") or row.get("is_nullable")
    else:
        try:
            data_type, max_length, nullable = row[:3]
        except (TypeError, ValueError):
            return False
    try:
        exact_length = int(max_length) == 255
    except (TypeError, ValueError):
        return False
    return (
        str(data_type or "").lower() == "varchar"
        and exact_length
        and str(nullable or "").upper() == "YES"
    )


def _valid_logical_artifact_index_row(row: Any) -> bool:
    if isinstance(row, dict):
        non_unique = row.get("NON_UNIQUE")
        if non_unique is None:
            non_unique = row.get("non_unique")
        columns = row.get("INDEX_COLUMNS") or row.get("index_columns")
        has_prefix = row.get("HAS_PREFIX")
        if has_prefix is None:
            has_prefix = row.get("has_prefix")
    else:
        try:
            non_unique, columns, has_prefix = row[:3]
        except (TypeError, ValueError):
            return False
    try:
        unique = int(non_unique) == 0
        full_columns = int(has_prefix) == 0
    except (TypeError, ValueError):
        return False
    return (
        unique
        and full_columns
        and str(columns or "") == "userId,artifact_type,logical_key"
    )


_MYSQL_DQ_COLUMN_CONTRACTS = {
    "decision_quality_input_artifacts": (
        ("userId", "bigint", None, "NO", None),
        ("artifact_id", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("artifact_type", "varchar", 64, "NO", "binary"),
        ("artifact_schema_version", "varchar", 64, "NO", None),
        ("logical_key", "varchar", 255, "YES", "binary"),
        ("source_type", "varchar", 64, "NO", None),
        ("source_report_id", "varchar", 128, "YES", None),
        ("decision_event_id", "varchar", 255, "YES", None),
        ("decision_at", "varchar", 64, "YES", None),
        ("available_at", "varchar", 64, "NO", None),
        ("recorded_at", "varchar", 64, "NO", None),
        ("store_authority", "varchar", 32, "NO", None),
        ("audit_eligible", "tinyint", None, "NO", None),
        ("content_hash", "char", 64, "NO", "binary"),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
    ),
    "decision_quality_artifact_receipts": (
        ("userId", "bigint", None, "NO", None),
        ("artifact_id", "varchar", 96, "NO", "binary"),
        ("receipt_id", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("receipt_policy", "varchar", 64, "NO", None),
        ("artifact_type", "varchar", 64, "NO", "binary"),
        ("artifact_content_hash", "char", 64, "NO", "binary"),
        ("source_row_created_at", "varchar", 64, "NO", None),
        ("source_visible_at", "varchar", 64, "NO", None),
        ("store_authority", "varchar", 32, "NO", None),
        ("content_hash", "char", 64, "NO", "binary"),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
    ),
    "decision_quality_provider_receipts": (
        ("receipt_id", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("provider", "varchar", 128, "NO", None),
        ("operation", "varchar", 128, "NO", None),
        ("capture_mode", "varchar", 64, "NO", None),
        ("request_hash", "char", 64, "NO", "binary"),
        ("adapter_output_sha256", "char", 64, "NO", "binary"),
        ("adapter_output_bytes", "bigint", None, "NO", None),
        ("normalized_payload_hash", "char", 64, "NO", "binary"),
        ("origin_fetched_at", "varchar", 64, "NO", None),
        ("completed_at", "varchar", 64, "NO", None),
        ("content_hash", "char", 64, "NO", "binary"),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
    ),
    "decision_quality_evaluation_snapshots": (
        ("userId", "bigint", None, "NO", None),
        ("snapshot_id", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("evaluation_as_of", "varchar", 64, "NO", None),
        ("evaluator_schema_version", "varchar", 64, "NO", None),
        ("evaluator_version", "varchar", 128, "NO", None),
        ("status", "varchar", 32, "NO", None),
        ("evaluation_hash", "char", 64, "NO", "binary"),
        ("input_manifest_hash", "char", 64, "NO", "binary"),
        ("config_hash", "char", 64, "NO", "binary"),
        ("readiness_status", "varchar", 64, "NO", None),
        ("human_review_status", "varchar", 64, "NO", None),
        ("automatic_promotion_allowed", "tinyint", None, "NO", None),
        ("store_authority", "varchar", 32, "NO", None),
        ("audit_eligible", "tinyint", None, "NO", None),
        ("content_hash", "char", 64, "NO", "binary"),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
    ),
    "decision_quality_contract_rollouts": (
        ("contract_name", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("contract_version", "varchar", 64, "NO", None),
        ("required_from", "varchar", 64, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
        ("hash_algorithm", "varchar", 16, "NO", None),
        ("canonicalization", "varchar", 64, "NO", None),
        ("marker_hash", "char", 64, "NO", "binary"),
    ),
}

# Two tables can exist in this shape when an early decision-quality build ran
# before the v16 exact binary-identity DDL was finalized.  Keep this allowlist
# deliberately narrow: it is an upgrade path, not a relaxed schema contract.
_MYSQL_DQ_LEGACY_COLUMN_CONTRACTS = {
    "decision_quality_input_artifacts": (
        ("userId", "bigint", None, "NO", None),
        ("artifact_id", "varchar", 96, "NO", None),
        ("schema_version", "varchar", 64, "NO", None),
        ("artifact_type", "varchar", 64, "NO", None),
        ("artifact_schema_version", "varchar", 64, "NO", None),
        ("source_type", "varchar", 64, "NO", None),
        ("source_report_id", "varchar", 128, "YES", None),
        ("decision_event_id", "varchar", 255, "YES", None),
        ("decision_at", "varchar", 64, "YES", None),
        ("available_at", "varchar", 64, "NO", None),
        ("recorded_at", "varchar", 64, "NO", None),
        ("store_authority", "varchar", 32, "NO", None),
        ("audit_eligible", "tinyint", None, "NO", None),
        ("content_hash", "varchar", 64, "NO", None),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
        ("logical_key", "varchar", 255, "YES", "binary"),
    ),
    "decision_quality_evaluation_snapshots": (
        ("userId", "bigint", None, "NO", None),
        ("snapshot_id", "varchar", 96, "NO", None),
        ("schema_version", "varchar", 64, "NO", None),
        ("evaluation_as_of", "varchar", 64, "NO", None),
        ("evaluator_schema_version", "varchar", 64, "NO", None),
        ("evaluator_version", "varchar", 128, "NO", None),
        ("status", "varchar", 32, "NO", None),
        ("evaluation_hash", "varchar", 64, "NO", None),
        ("input_manifest_hash", "varchar", 64, "NO", None),
        ("config_hash", "varchar", 64, "NO", None),
        ("readiness_status", "varchar", 64, "NO", None),
        ("human_review_status", "varchar", 64, "NO", None),
        ("automatic_promotion_allowed", "tinyint", None, "NO", None),
        ("store_authority", "varchar", 32, "NO", None),
        ("audit_eligible", "tinyint", None, "NO", None),
        ("content_hash", "varchar", 64, "NO", None),
        ("payload", "longtext", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
    ),
}

_MYSQL_DQ_LEGACY_REPAIR_DDLS = {
    "decision_quality_input_artifacts": """
        ALTER TABLE decision_quality_input_artifacts
            MODIFY COLUMN artifact_id VARCHAR(96)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            MODIFY COLUMN artifact_type VARCHAR(64)
                CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            MODIFY COLUMN logical_key VARCHAR(255)
                CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL
                AFTER artifact_schema_version,
            MODIFY COLUMN content_hash CHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    """,
    "decision_quality_evaluation_snapshots": """
        ALTER TABLE decision_quality_evaluation_snapshots
            MODIFY COLUMN snapshot_id VARCHAR(96)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            MODIFY COLUMN evaluation_hash CHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            MODIFY COLUMN input_manifest_hash CHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            MODIFY COLUMN config_hash CHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            MODIFY COLUMN content_hash CHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    """,
}

_MYSQL_DQ_INDEX_CONTRACTS = {
    "decision_quality_input_artifacts": {
        "PRIMARY": (0, ("userId", "artifact_id")),
        "uq_decision_quality_artifact_content": (
            0,
            ("userId", "artifact_type", "content_hash"),
        ),
        "uq_decision_quality_artifact_logical_key": (
            0,
            ("userId", "artifact_type", "logical_key"),
        ),
        "idx_decision_quality_artifacts_report": (
            1,
            ("userId", "artifact_type", "source_report_id", "recorded_at"),
        ),
        "idx_decision_quality_artifacts_event": (
            1,
            ("userId", "decision_event_id", "artifact_type"),
        ),
    },
    "decision_quality_artifact_receipts": {
        "PRIMARY": (0, ("userId", "artifact_id")),
        "uq_decision_quality_artifact_receipt_id": (
            0,
            ("userId", "receipt_id"),
        ),
        "uq_decision_quality_artifact_receipt_content": (
            0,
            ("userId", "content_hash"),
        ),
        "idx_decision_quality_artifact_receipts_visibility": (
            1,
            ("userId", "source_visible_at", "artifact_id"),
        ),
    },
    "decision_quality_provider_receipts": {
        "PRIMARY": (0, ("receipt_id",)),
        "uq_decision_quality_provider_receipt_content": (
            0,
            ("content_hash",),
        ),
        "idx_decision_quality_provider_receipts_lookup": (
            1,
            ("provider", "operation", "completed_at"),
        ),
    },
    "decision_quality_evaluation_snapshots": {
        "PRIMARY": (0, ("userId", "snapshot_id")),
        "uq_decision_quality_snapshot_content": (0, ("userId", "content_hash")),
        "idx_decision_quality_snapshots_cutoff": (
            1,
            ("userId", "evaluation_as_of", "created_at"),
        ),
        "idx_decision_quality_snapshots_status": (
            1,
            ("userId", "status", "evaluation_as_of"),
        ),
        "idx_decision_quality_snapshots_review": (
            1,
            ("userId", "readiness_status", "human_review_status", "evaluation_as_of"),
        ),
    },
    "decision_quality_contract_rollouts": {
        "PRIMARY": (0, ("contract_name",)),
        "uq_decision_quality_rollout_hash": (0, ("marker_hash",)),
    },
}

_MYSQL_PROMPT_SHADOW_COLUMN_CONTRACTS = {
    "prompt_shadow_runs": (
        ("userId", "bigint", None, "NO", None),
        ("run_id", "varchar", 96, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("policy_id", "varchar", 96, "NO", "binary"),
        ("policy_hash", "char", 64, "NO", "binary"),
        ("decision_at", "varchar", 64, "NO", None),
        ("registration_artifact_id", "varchar", 96, "NO", "binary"),
        ("champion_attempt_artifact_id", "varchar", 96, "YES", "binary"),
        ("champion_output_artifact_id", "varchar", 96, "YES", "binary"),
        ("champion_report_id", "varchar", 128, "YES", None),
        ("challenger_attempt_artifact_id", "varchar", 96, "YES", "binary"),
        ("challenger_output_artifact_id", "varchar", 96, "YES", "binary"),
        ("status", "varchar", 64, "NO", None),
        ("state_version", "bigint", None, "NO", None),
        ("challenger_deadline_at", "varchar", 64, "YES", None),
        ("lease_owner_hash", "char", 64, "YES", "binary"),
        ("lease_token_hash", "char", 64, "YES", "binary"),
        ("lease_acquired_at", "varchar", 64, "YES", None),
        ("lease_expires_at", "varchar", 64, "YES", None),
        ("champion_network_started_at", "varchar", 64, "YES", None),
        ("challenger_network_started_at", "varchar", 64, "YES", None),
        ("budget_scope_key", "varchar", 128, "YES", "binary"),
        ("budget_date_local", "varchar", 10, "YES", "binary"),
        ("budget_reserved_at", "varchar", 64, "YES", None),
        ("terminal_reason", "varchar", 128, "YES", None),
        ("created_at", "varchar", 64, "NO", None),
        ("updated_at", "varchar", 64, "NO", None),
    ),
    "prompt_shadow_budget_counters": (
        ("scope_key", "varchar", 128, "NO", "binary"),
        ("budget_date_local", "varchar", 10, "NO", "binary"),
        ("schema_version", "varchar", 64, "NO", None),
        ("policy_id", "varchar", 96, "NO", "binary"),
        ("policy_hash", "char", 64, "NO", "binary"),
        ("max_calls", "int", None, "NO", None),
        ("reserved_calls", "int", None, "NO", None),
        ("started_calls", "int", None, "NO", None),
        ("completed_calls", "int", None, "NO", None),
        ("failed_calls", "int", None, "NO", None),
        ("state_version", "bigint", None, "NO", None),
        ("created_at", "varchar", 64, "NO", None),
        ("updated_at", "varchar", 64, "NO", None),
    ),
}

_MYSQL_PROMPT_SHADOW_INDEX_CONTRACTS = {
    "prompt_shadow_runs": {
        "PRIMARY": (0, ("userId", "run_id")),
        "uq_prompt_shadow_registration": (0, ("userId", "registration_artifact_id")),
        "uq_prompt_shadow_champion_attempt": (
            0,
            ("userId", "champion_attempt_artifact_id"),
        ),
        "uq_prompt_shadow_champion_output": (
            0,
            ("userId", "champion_output_artifact_id"),
        ),
        "uq_prompt_shadow_challenger_attempt": (
            0,
            ("userId", "challenger_attempt_artifact_id"),
        ),
        "uq_prompt_shadow_challenger_output": (
            0,
            ("userId", "challenger_output_artifact_id"),
        ),
        "idx_prompt_shadow_runs_worker": (
            1,
            ("status", "lease_expires_at", "challenger_deadline_at", "created_at"),
        ),
        "idx_prompt_shadow_runs_decision": (1, ("userId", "decision_at", "run_id")),
        "idx_prompt_shadow_runs_report": (1, ("userId", "champion_report_id")),
    },
    "prompt_shadow_budget_counters": {
        "PRIMARY": (0, ("scope_key", "budget_date_local")),
        "idx_prompt_shadow_budget_policy": (1, ("policy_hash", "budget_date_local")),
    },
}


def _ensure_prompt_shadow_mysql_contracts(cursor: Any, fetchall: Any) -> None:
    """Verify both mutable v16 operational tables exactly."""

    try:
        for table, expected_columns in _MYSQL_PROMPT_SHADOW_COLUMN_CONTRACTS.items():
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "IS_NULLABLE, ORDINAL_POSITION, COLLATION_NAME "
                "FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION"
            )
            observed_columns = tuple(_mysql_dq_column_contract(row) for row in fetchall())
            if len(observed_columns) != len(expected_columns) or any(
                not _valid_mysql_dq_column(
                    observed_columns[position - 1],
                    expected=expected,
                    position=position,
                )
                for position, expected in enumerate(expected_columns, start=1)
            ):
                raise MySqlBootstrapContractError(
                    f"MySQL table {table} columns conflict with prompt-shadow contract"
                )
            cursor.execute(
                "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART "
                "FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
            )
            observed_indexes: dict[str, list[tuple[int, int, str, Any]]] = {}
            for row in fetchall():
                name, non_unique, sequence, column, sub_part = (
                    _mysql_receipt_index_part(row)
                )
                observed_indexes.setdefault(name, []).append(
                    (non_unique, sequence, column, sub_part)
                )
            expected_indexes = _MYSQL_PROMPT_SHADOW_INDEX_CONTRACTS[table]
            if set(observed_indexes) != set(expected_indexes):
                raise MySqlBootstrapContractError(
                    f"MySQL table {table} indexes conflict with prompt-shadow contract"
                )
            for name, (non_unique, columns) in expected_indexes.items():
                expected_parts = [
                    (non_unique, position, column, None)
                    for position, column in enumerate(columns, start=1)
                ]
                if observed_indexes.get(name) != expected_parts:
                    raise MySqlBootstrapContractError(
                        f"MySQL index {name} conflicts with prompt-shadow contract"
                    )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - metadata is a release contract
        raise MySqlBootstrapContractError(
            "MySQL prompt-shadow metadata cannot be verified"
        ) from exc


def _ensure_decision_quality_mysql_contracts(
    cursor: Any,
    fetchall: Any,
) -> None:
    """Verify all five v15 quality ledgers and binary identities exactly."""

    try:
        for table, expected_columns in _MYSQL_DQ_COLUMN_CONTRACTS.items():
            cursor.execute(
                f"""
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                       IS_NULLABLE, ORDINAL_POSITION, COLLATION_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = '{table}'
                ORDER BY ORDINAL_POSITION
                """
            )
            observed_columns = tuple(_mysql_dq_column_contract(row) for row in fetchall())
            if len(observed_columns) != len(expected_columns) or any(
                not _valid_mysql_dq_column(
                    observed_columns[position - 1],
                    expected=expected,
                    position=position,
                )
                for position, expected in enumerate(expected_columns, start=1)
            ):
                raise MySqlBootstrapContractError(
                    f"MySQL table {table} columns conflict with quality contract"
                )

            cursor.execute(
                f"""
                SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = '{table}'
                ORDER BY INDEX_NAME, SEQ_IN_INDEX
                """
            )
            observed_indexes: dict[str, list[tuple[int, int, str, Any]]] = {}
            for row in fetchall():
                name, non_unique, sequence, column, sub_part = (
                    _mysql_receipt_index_part(row)
                )
                observed_indexes.setdefault(name, []).append(
                    (non_unique, sequence, column, sub_part)
                )
            expected_indexes = _MYSQL_DQ_INDEX_CONTRACTS[table]
            if set(observed_indexes) != set(expected_indexes):
                raise MySqlBootstrapContractError(
                    f"MySQL table {table} indexes conflict with quality contract"
                )
            for name, (non_unique, columns) in expected_indexes.items():
                parts = observed_indexes.get(name)
                expected_parts = [
                    (non_unique, position, column, None)
                    for position, column in enumerate(columns, start=1)
                ]
                if parts != expected_parts:
                    raise MySqlBootstrapContractError(
                        f"MySQL index {name} conflicts with quality contract"
                    )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - metadata is a release contract
        raise MySqlBootstrapContractError(
            "MySQL decision-quality metadata cannot be verified"
        ) from exc


def _repair_legacy_decision_quality_mysql_contracts(
    cursor: Any,
    fetchall: Any,
) -> None:
    """Upgrade only the explicitly recognized early-v16 table shapes."""

    try:
        for table, legacy_columns in _MYSQL_DQ_LEGACY_COLUMN_CONTRACTS.items():
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "IS_NULLABLE, ORDINAL_POSITION, COLLATION_NAME "
                "FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION"
            )
            observed = tuple(_mysql_dq_column_contract(row) for row in fetchall())
            canonical_columns = _MYSQL_DQ_COLUMN_CONTRACTS[table]
            if len(observed) == len(canonical_columns) and all(
                _valid_mysql_dq_column(
                    observed[position - 1],
                    expected=expected,
                    position=position,
                )
                for position, expected in enumerate(canonical_columns, start=1)
            ):
                continue
            if len(observed) != len(legacy_columns) or any(
                not _valid_mysql_dq_column(
                    observed[position - 1],
                    expected=expected,
                    position=position,
                )
                for position, expected in enumerate(legacy_columns, start=1)
            ):
                continue
            cursor.execute(
                "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART "
                "FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
            )
            observed_indexes: dict[str, list[tuple[int, int, str, Any]]] = {}
            for row in fetchall():
                name, non_unique, sequence, column, sub_part = (
                    _mysql_receipt_index_part(row)
                )
                observed_indexes.setdefault(name, []).append(
                    (non_unique, sequence, column, sub_part)
                )
            expected_indexes = _MYSQL_DQ_INDEX_CONTRACTS[table]
            if set(observed_indexes) != set(expected_indexes) or any(
                observed_indexes.get(name)
                != [
                    (non_unique, position, column, None)
                    for position, column in enumerate(columns, start=1)
                ]
                for name, (non_unique, columns) in expected_indexes.items()
            ):
                continue
            cursor.execute(_MYSQL_DQ_LEGACY_REPAIR_DDLS[table])
    except Exception as exc:  # noqa: BLE001 - repair must fail closed
        raise MySqlBootstrapContractError(
            "MySQL legacy decision-quality contract cannot be upgraded safely"
        ) from exc


def _mysql_dq_column_contract(
    row: Any,
) -> tuple[str, str, int | None, str, int, str | None]:
    if isinstance(row, dict):
        values = (
            row.get("COLUMN_NAME") or row.get("column_name"),
            row.get("DATA_TYPE") or row.get("data_type"),
            row.get("CHARACTER_MAXIMUM_LENGTH")
            if "CHARACTER_MAXIMUM_LENGTH" in row
            else row.get("character_maximum_length"),
            row.get("IS_NULLABLE") or row.get("is_nullable"),
            row.get("ORDINAL_POSITION") or row.get("ordinal_position"),
            row.get("COLLATION_NAME")
            if "COLLATION_NAME" in row
            else row.get("collation_name"),
        )
    else:
        values = row[:6]
    name, data_type, length, nullable, position, collation = values
    normalized_length = int(length) if length is not None else None
    if str(data_type or "").lower() not in {"varchar", "char"}:
        normalized_length = None
    return (
        str(name or ""),
        str(data_type or "").lower(),
        normalized_length,
        str(nullable or "").upper(),
        int(position),
        None if collation is None else str(collation).lower(),
    )


def _valid_mysql_dq_column(
    observed: tuple[str, str, int | None, str, int, str | None],
    *,
    expected: tuple[str, str, int | None, str, str | None],
    position: int,
) -> bool:
    name, data_type, length, nullable, observed_position, collation = observed
    expected_name, expected_type, expected_length, expected_nullable, collation_rule = expected
    if (
        (name, data_type, length, nullable, observed_position)
        != (expected_name, expected_type, expected_length, expected_nullable, position)
    ):
        return False
    if collation_rule == "binary":
        return bool(collation and collation.endswith("_bin"))
    return True


def _ensure_decision_quality_receipt_mysql_contracts(
    cursor: Any,
    fetchall: Any,
) -> None:
    """Backward-compatible focused verifier used by receipt-only tests."""

    tables = {
        table: _MYSQL_DQ_COLUMN_CONTRACTS[table]
        for table in (
            "decision_quality_artifact_receipts",
            "decision_quality_provider_receipts",
        )
    }
    try:
        for table, expected_columns in tables.items():
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "IS_NULLABLE, ORDINAL_POSITION FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION"
            )
            observed = []
            for row in fetchall():
                name, data_type, length, nullable, position = row[:5]
                normalized_type = str(data_type).lower()
                observed.append(
                    (
                        str(name),
                        normalized_type,
                        int(length)
                        if length is not None and normalized_type in {"varchar", "char"}
                        else None,
                        str(nullable).upper(),
                        int(position),
                    )
                )
            expected_legacy = [
                (
                    name,
                    "varchar" if data_type == "char" else data_type,
                    length,
                    nullable,
                    position,
                )
                for position, (name, data_type, length, nullable, _collation) in enumerate(
                    expected_columns,
                    start=1,
                )
            ]
            if observed != expected_legacy:
                raise MySqlBootstrapContractError(
                    f"MySQL table {table} columns conflict with receipt contract"
                )
            cursor.execute(
                "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART "
                "FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
            )
            observed_indexes: dict[str, list[tuple[int, int, str, Any]]] = {}
            for row in fetchall():
                name, non_unique, sequence, column, sub_part = _mysql_receipt_index_part(
                    row
                )
                observed_indexes.setdefault(name, []).append(
                    (non_unique, sequence, column, sub_part)
                )
            expected_indexes = _MYSQL_DQ_INDEX_CONTRACTS[table]
            for name, (non_unique, columns) in expected_indexes.items():
                expected_parts = [
                    (non_unique, position, column, None)
                    for position, column in enumerate(columns, start=1)
                ]
                if observed_indexes.get(name) != expected_parts:
                    raise MySqlBootstrapContractError(
                        f"MySQL index {name} conflicts with receipt contract"
                    )
    except MySqlBootstrapContractError:
        raise
    except Exception as exc:
        raise MySqlBootstrapContractError(
            "MySQL decision-quality receipt metadata cannot be verified"
        ) from exc


def _mysql_receipt_index_part(
    row: Any,
) -> tuple[str, int, int, str, int | None]:
    if isinstance(row, dict):
        values = (
            row.get("INDEX_NAME") or row.get("index_name"),
            row.get("NON_UNIQUE")
            if "NON_UNIQUE" in row
            else row.get("non_unique"),
            row.get("SEQ_IN_INDEX") or row.get("seq_in_index"),
            row.get("COLUMN_NAME") or row.get("column_name"),
            row.get("SUB_PART") if "SUB_PART" in row else row.get("sub_part"),
        )
    else:
        values = row[:5]
    name, non_unique, sequence, column, sub_part = values
    return (
        str(name or ""),
        int(non_unique),
        int(sequence),
        str(column or ""),
        int(sub_part) if sub_part is not None else None,
    )
