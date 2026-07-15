from __future__ import annotations

import time

import pytest

from app import db_connect
from app.mysql_bootstrap import (
    MySqlBootstrapContractError,
    _ensure_decision_quality_logical_key_mysql_contract,
    _ensure_decision_quality_receipt_mysql_contracts,
    _ensure_decision_quality_storage_engine_mysql_contract,
    ensure_mysql_schema,
)


_QUALITY_ENGINE_ROWS = [
    ("decision_quality_artifact_receipts", "InnoDB"),
    ("decision_quality_contract_rollouts", "InnoDB"),
    ("decision_quality_evaluation_snapshots", "InnoDB"),
    ("decision_quality_input_artifacts", "InnoDB"),
    ("decision_quality_provider_receipts", "InnoDB"),
    ("prompt_shadow_budget_counters", "InnoDB"),
    ("prompt_shadow_runs", "InnoDB"),
]


class _StorageEngineContractCursor:
    def __init__(self, rows=None):
        self.rows = list(_QUALITY_ENGINE_ROWS if rows is None else rows)
        self.last_statement = ""
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.last_statement = " ".join(statement.split())
        self.statements.append(self.last_statement)

    def fetchall(self):
        assert "information_schema.TABLES" in self.last_statement
        return list(self.rows)


def test_mysql_storage_engine_contract_accepts_exact_innodb_set() -> None:
    rows = [
        (table, "INNODB" if index % 2 else "innodb")
        for index, (table, _engine) in enumerate(_QUALITY_ENGINE_ROWS)
    ]
    cursor = _StorageEngineContractCursor(rows)

    _ensure_decision_quality_storage_engine_mysql_contract(
        cursor,
        cursor.fetchall,
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                (table, "MyISAM" if index == 2 else engine)
                for index, (table, engine) in enumerate(_QUALITY_ENGINE_ROWS)
            ],
            "must use InnoDB",
        ),
        (_QUALITY_ENGINE_ROWS[:-1], "exact required table set"),
        ([*_QUALITY_ENGINE_ROWS, _QUALITY_ENGINE_ROWS[0]], "duplicate table rows"),
    ],
)
def test_mysql_storage_engine_contract_rejects_drift(
    rows,
    message: str,
) -> None:
    cursor = _StorageEngineContractCursor(rows)

    with pytest.raises(MySqlBootstrapContractError, match=message):
        _ensure_decision_quality_storage_engine_mysql_contract(
            cursor,
            cursor.fetchall,
        )


def test_mysql_bootstrap_checks_engines_before_quality_contract_writes() -> None:
    rows = [
        (table, "MyISAM" if index == 0 else engine)
        for index, (table, engine) in enumerate(_QUALITY_ENGINE_ROWS)
    ]
    cursor = _StorageEngineContractCursor(rows)

    class Connection:
        def cursor(self):
            return cursor

        def commit(self) -> None:
            pytest.fail("invalid storage engine must fail before commit")

    with pytest.raises(MySqlBootstrapContractError, match="must use InnoDB"):
        ensure_mysql_schema(Connection())

    assert not any(
        statement.startswith("CREATE TRIGGER")
        or "INSERT IGNORE INTO decision_quality_contract_rollouts" in statement
        or "INSERT INTO schema_meta" in statement
        for statement in cursor.statements
    )


class _LogicalKeyContractCursor:
    def __init__(self, *, failing_ddl: str, concurrent_winner: bool = False):
        self.failing_ddl = failing_ddl
        self.concurrent_winner = concurrent_winner
        self.last_statement = ""
        self.column_row = (
            ("varchar", 255, "YES") if failing_ddl == "index" else None
        )
        self.index_row = None

    def execute(self, statement: str) -> None:
        self.last_statement = " ".join(statement.split())
        if (
            self.last_statement.startswith(
                "ALTER TABLE decision_quality_input_artifacts ADD COLUMN"
            )
        ):
            if self.failing_ddl == "column":
                if self.concurrent_winner:
                    self.column_row = ("varchar", 255, "YES")
                raise RuntimeError("simulated ALTER failure")
            self.column_row = ("varchar", 255, "YES")
        if (
            self.last_statement.startswith(
                "CREATE UNIQUE INDEX uq_decision_quality_artifact_logical_key"
            )
        ):
            if self.failing_ddl == "index":
                if self.concurrent_winner:
                    self.index_row = (
                        0,
                        "userId,artifact_type,logical_key",
                        0,
                    )
                raise RuntimeError("simulated CREATE INDEX failure")
            self.index_row = (
                0,
                "userId,artifact_type,logical_key",
                0,
            )

    def fetchone(self):
        if "information_schema.COLUMNS" in self.last_statement:
            return self.column_row
        if "information_schema.STATISTICS" in self.last_statement:
            return self.index_row
        return None


class _ReceiptContractCursor:
    def __init__(self, *, bad_column: bool = False, prefix_index: bool = False):
        self.last_statement = ""
        self.bad_column = bad_column
        self.prefix_index = prefix_index

    def execute(self, statement: str) -> None:
        self.last_statement = " ".join(statement.split())

    def fetchall(self):
        artifact_columns = [
            ("userId", "bigint", None, "NO", 1),
            ("artifact_id", "varchar", 96, "NO", 2),
            ("receipt_id", "varchar", 96, "NO", 3),
            ("schema_version", "varchar", 64, "NO", 4),
            ("receipt_policy", "varchar", 64, "NO", 5),
            ("artifact_type", "varchar", 64, "NO", 6),
            ("artifact_content_hash", "varchar", 64, "NO", 7),
            ("source_row_created_at", "varchar", 64, "NO", 8),
            ("source_visible_at", "varchar", 64, "NO", 9),
            ("store_authority", "varchar", 32, "NO", 10),
            ("content_hash", "varchar", 64, "NO", 11),
            ("payload", "longtext", 4294967295, "NO", 12),
            ("created_at", "varchar", 64, "NO", 13),
        ]
        provider_columns = [
            ("receipt_id", "varchar", 96, "NO", 1),
            ("schema_version", "varchar", 64, "NO", 2),
            ("provider", "varchar", 128, "NO", 3),
            ("operation", "varchar", 128, "NO", 4),
            ("capture_mode", "varchar", 64, "NO", 5),
            ("request_hash", "varchar", 64, "NO", 6),
            ("adapter_output_sha256", "varchar", 64, "NO", 7),
            ("adapter_output_bytes", "bigint", None, "NO", 8),
            ("normalized_payload_hash", "varchar", 64, "NO", 9),
            ("origin_fetched_at", "varchar", 64, "NO", 10),
            ("completed_at", "varchar", 64, "NO", 11),
            ("content_hash", "varchar", 64, "NO", 12),
            ("payload", "longtext", 4294967295, "NO", 13),
            ("created_at", "varchar", 64, "NO", 14),
        ]
        artifact_indexes = [
            ("PRIMARY", 0, 1, "userId", None),
            ("PRIMARY", 0, 2, "artifact_id", None),
            (
                "idx_decision_quality_artifact_receipts_visibility",
                1,
                1,
                "userId",
                None,
            ),
            (
                "idx_decision_quality_artifact_receipts_visibility",
                1,
                2,
                "source_visible_at",
                None,
            ),
            (
                "idx_decision_quality_artifact_receipts_visibility",
                1,
                3,
                "artifact_id",
                None,
            ),
            ("uq_decision_quality_artifact_receipt_content", 0, 1, "userId", None),
            (
                "uq_decision_quality_artifact_receipt_content",
                0,
                2,
                "content_hash",
                None,
            ),
            ("uq_decision_quality_artifact_receipt_id", 0, 1, "userId", None),
            ("uq_decision_quality_artifact_receipt_id", 0, 2, "receipt_id", None),
        ]
        provider_indexes = [
            ("PRIMARY", 0, 1, "receipt_id", None),
            (
                "idx_decision_quality_provider_receipts_lookup",
                1,
                1,
                "provider",
                None,
            ),
            (
                "idx_decision_quality_provider_receipts_lookup",
                1,
                2,
                "operation",
                None,
            ),
            (
                "idx_decision_quality_provider_receipts_lookup",
                1,
                3,
                "completed_at",
                None,
            ),
            ("uq_decision_quality_provider_receipt_content", 0, 1, "content_hash", None),
        ]
        provider = "decision_quality_provider_receipts" in self.last_statement
        if "information_schema.COLUMNS" in self.last_statement:
            rows = provider_columns if provider else artifact_columns
            if self.bad_column and not provider:
                rows = [*rows]
                rows[1] = ("artifact_id", "varchar", 95, "NO", 2)
            return rows
        rows = provider_indexes if provider else artifact_indexes
        if self.prefix_index and not provider:
            rows = [*rows]
            rows[-1] = (*rows[-1][:-1], 32)
        return rows


def test_mysql_receipt_contract_accepts_exact_columns_and_indexes() -> None:
    cursor = _ReceiptContractCursor()
    _ensure_decision_quality_receipt_mysql_contracts(cursor, cursor.fetchall)


@pytest.mark.parametrize(
    "cursor",
    [
        _ReceiptContractCursor(bad_column=True),
        _ReceiptContractCursor(prefix_index=True),
    ],
)
def test_mysql_receipt_contract_rejects_column_or_prefix_drift(cursor) -> None:
    with pytest.raises(MySqlBootstrapContractError, match="receipt contract"):
        _ensure_decision_quality_receipt_mysql_contracts(cursor, cursor.fetchall)


def test_mysql_fallback_cache_skips_repeated_timeouts(monkeypatch):
    calls = {"mysql": 0}

    def _open_mysql():
        calls["mysql"] += 1
        raise ConnectionError("simulated mysql down")

    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(db_connect, "_open_mysql", _open_mysql)
    monkeypatch.setattr(db_connect, "_open_sqlite", lambda: db_connect.DbConnection(object(), "sqlite"))
    monkeypatch.setattr(db_connect, "_mysql_fallback_cooldown_seconds", lambda: 60.0)
    db_connect.reset_mysql_fallback_cache()

    db_connect.connect_with_fallback()
    db_connect.connect_with_fallback()

    assert calls["mysql"] == 1


def test_mysql_fallback_cache_resets_after_success(monkeypatch):
    calls = {"mysql": 0}

    def _open_mysql():
        calls["mysql"] += 1
        if calls["mysql"] == 1:
            raise ConnectionError("simulated mysql down")
        return db_connect.DbConnection(object(), "mysql")

    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(db_connect, "_open_mysql", _open_mysql)
    monkeypatch.setattr(db_connect, "_open_sqlite", lambda: db_connect.DbConnection(object(), "sqlite"))
    monkeypatch.setattr(db_connect, "_mysql_fallback_cooldown_seconds", lambda: 60.0)
    db_connect.reset_mysql_fallback_cache()

    db_connect.connect_with_fallback()
    db_connect.reset_mysql_fallback_cache()
    db_connect.connect_with_fallback()

    assert calls["mysql"] == 2


def test_mysql_schema_contract_error_never_falls_back_to_sqlite(monkeypatch):
    sqlite_calls = 0

    def _open_mysql():
        raise MySqlBootstrapContractError("TRIGGER privilege is required")

    def _open_sqlite():
        nonlocal sqlite_calls
        sqlite_calls += 1
        return db_connect.DbConnection(object(), "sqlite")

    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(db_connect, "_open_mysql", _open_mysql)
    monkeypatch.setattr(db_connect, "_open_sqlite", _open_sqlite)
    db_connect.reset_mysql_fallback_cache()

    with pytest.raises(MySqlBootstrapContractError, match="TRIGGER privilege"):
        db_connect.connect_with_fallback()

    assert sqlite_calls == 0


def test_mysql_engine_contract_error_never_falls_back_or_starts_cooldown(
    monkeypatch,
) -> None:
    sqlite_calls = 0

    def _open_mysql():
        rows = [
            (table, "MyISAM" if index == 4 else engine)
            for index, (table, engine) in enumerate(_QUALITY_ENGINE_ROWS)
        ]
        cursor = _StorageEngineContractCursor(rows)
        _ensure_decision_quality_storage_engine_mysql_contract(
            cursor,
            cursor.fetchall,
        )
        pytest.fail("a nontransactional quality ledger must not connect")

    def _open_sqlite():
        nonlocal sqlite_calls
        sqlite_calls += 1
        return db_connect.DbConnection(object(), "sqlite")

    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(db_connect, "_open_mysql", _open_mysql)
    monkeypatch.setattr(db_connect, "_open_sqlite", _open_sqlite)
    db_connect.reset_mysql_fallback_cache()

    with pytest.raises(MySqlBootstrapContractError, match="must use InnoDB"):
        db_connect.connect_with_fallback()

    assert sqlite_calls == 0
    assert db_connect._mysql_unreachable_until == 0.0


@pytest.mark.parametrize("failing_ddl", ["column", "index"])
def test_logical_identity_ddl_failure_never_falls_back_or_starts_cooldown(
    monkeypatch,
    failing_ddl: str,
) -> None:
    sqlite_calls = 0

    def _open_mysql():
        cursor = _LogicalKeyContractCursor(failing_ddl=failing_ddl)
        _ensure_decision_quality_logical_key_mysql_contract(
            cursor,
            cursor.fetchone,
        )
        pytest.fail("a missing logical identity contract must not connect")

    def _open_sqlite():
        nonlocal sqlite_calls
        sqlite_calls += 1
        return db_connect.DbConnection(object(), "sqlite")

    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(db_connect, "_open_mysql", _open_mysql)
    monkeypatch.setattr(db_connect, "_open_sqlite", _open_sqlite)
    db_connect.reset_mysql_fallback_cache()

    with pytest.raises(
        MySqlBootstrapContractError,
        match="logical identity (column|index) cannot be enforced",
    ):
        db_connect.connect_with_fallback()

    assert sqlite_calls == 0
    assert db_connect._mysql_unreachable_until == 0.0


@pytest.mark.parametrize("failing_ddl", ["column", "index"])
def test_logical_identity_duplicate_ddl_is_idempotent_after_exact_recheck(
    failing_ddl: str,
) -> None:
    cursor = _LogicalKeyContractCursor(
        failing_ddl=failing_ddl,
        concurrent_winner=True,
    )

    _ensure_decision_quality_logical_key_mysql_contract(
        cursor,
        cursor.fetchone,
    )
