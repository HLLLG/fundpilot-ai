from __future__ import annotations

import json
from typing import Any

import pytest

from app.mysql_bootstrap import (
    MYSQL_MIGRATION_GUARD_NAME,
    MYSQL_MIGRATION_GUARD_TABLE,
    MySqlBootstrapContractError,
    _MYSQL_DQ_COLUMN_CONTRACTS,
    _MYSQL_DQ_INDEX_CONTRACTS,
    _MYSQL_MIGRATION_GUARD_COLUMN_CONTRACT,
    _ROLLOUT_COLUMNS,
    _ensure_decision_quality_mysql_contracts,
    _ensure_mysql_migration_guard_contract,
    _normalize_mysql_migration_guard,
    _read_mysql_migration_guard,
    _read_mysql_rollout_singleton,
    _repair_legacy_decision_quality_mysql_contracts,
    _verify_mysql_migration_guard_access,
    prepare_mysql_migration_guard,
)
from app.services.decision_quality_rollout import (
    build_decision_quality_rollout_marker,
)


def _guard_row(
    *,
    status: str = "in_progress",
    fingerprint: str = "a" * 64,
    started_at: str = "2026-07-15T00:00:00+00:00",
    completed_at: str | None = None,
) -> tuple[Any, ...]:
    marker = build_decision_quality_rollout_marker(
        "2026-07-14T00:00:00+00:00"
    )
    return (
        MYSQL_MIGRATION_GUARD_NAME,
        status,
        15,
        fingerprint,
        marker["marker_hash"],
        json.dumps(
            marker,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        started_at,
        completed_at,
    )


class _GuardCursor:
    def __init__(
        self,
        *,
        guard_rows: list[tuple[Any, ...]] | None = None,
        engine: str = "InnoDB",
        payload_collation: str = "utf8mb4_bin",
        column_drift: tuple[int, tuple[Any, ...]] | None = None,
        index_rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.guard_rows = list(guard_rows or [])
        self.engine = engine
        self.last_statement = ""
        self.last_params: tuple[Any, ...] = ()
        self.columns = [
            (
                name,
                data_type,
                length,
                nullable,
                position,
                payload_collation
                if name == "rollout_marker_payload"
                else collation,
            )
            for position, (
                name,
                data_type,
                length,
                nullable,
                collation,
            ) in enumerate(_MYSQL_MIGRATION_GUARD_COLUMN_CONTRACT, start=1)
        ]
        if column_drift is not None:
            position, row = column_drift
            self.columns[position] = row
        self.index_rows = (
            [("PRIMARY", 0, 1, "guard_name", None)]
            if index_rows is None
            else list(index_rows)
        )
        self.rowcount = 1

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        self.last_statement = " ".join(statement.split())
        self.last_params = tuple(params)
        if self.last_statement.startswith(
            f"INSERT INTO {MYSQL_MIGRATION_GUARD_TABLE}"
        ):
            self.guard_rows = [(*self.last_params, None)]
        elif self.last_statement.startswith(
            f"ALTER TABLE {MYSQL_MIGRATION_GUARD_TABLE} MODIFY COLUMN rollout_marker_payload"
        ):
            self.columns[5] = (*self.columns[5][:5], "utf8mb4_bin")
        elif self.last_statement.startswith(
            f"UPDATE {MYSQL_MIGRATION_GUARD_TABLE} SET status = %s, completed_at = NULL"
        ):
            row = self.guard_rows[0]
            self.guard_rows = [(row[0], "in_progress", *row[2:7], None)]

    def fetchone(self):
        if "GET_LOCK(" in self.last_statement or "RELEASE_LOCK(" in self.last_statement:
            return (1,)
        return None

    def fetchall(self):
        if "information_schema.TABLES" in self.last_statement:
            return [(MYSQL_MIGRATION_GUARD_TABLE, self.engine)]
        if "information_schema.COLUMNS" in self.last_statement:
            return list(self.columns)
        if "information_schema.STATISTICS" in self.last_statement:
            return list(self.index_rows)
        if (
            f"FROM {MYSQL_MIGRATION_GUARD_TABLE}" in self.last_statement
            and "information_schema" not in self.last_statement
        ):
            return list(self.guard_rows)
        return []


class _GuardConnection:
    def __init__(self, cursor: _GuardCursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def cursor(self) -> _GuardCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def test_guard_exact_metadata_contract_accepts_canonical_table() -> None:
    cursor = _GuardCursor()

    _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)


def test_guard_repairs_legacy_server_default_payload_collation() -> None:
    cursor = _GuardCursor(payload_collation="utf8mb4_0900_ai_ci")

    _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)

    assert cursor.columns[5][5] == "utf8mb4_bin"


@pytest.mark.parametrize(
    "cursor",
    [
        _GuardCursor(engine="MyISAM"),
        _GuardCursor(
            column_drift=(
                1,
                ("status", "varchar", 16, "NO", 2, "utf8mb4_general_ci"),
            )
        ),
        _GuardCursor(index_rows=[]),
        _GuardCursor(
            index_rows=[("PRIMARY", 0, 1, "guard_name", 8)]
        ),
    ],
)
def test_guard_exact_metadata_contract_rejects_drift(cursor: _GuardCursor) -> None:
    with pytest.raises(MySqlBootstrapContractError, match="migration guard"):
        _ensure_mysql_migration_guard_contract(cursor, cursor.fetchall)


def test_guard_reader_rejects_multiple_rows_and_normal_bootstrap_is_blocked() -> None:
    duplicate_cursor = _GuardCursor(guard_rows=[_guard_row(), _guard_row()])
    with pytest.raises(MySqlBootstrapContractError, match="at most one row"):
        _read_mysql_migration_guard(duplicate_cursor)

    cursor = _GuardCursor(guard_rows=[_guard_row()])
    with pytest.raises(MySqlBootstrapContractError, match="bootstrap is blocked"):
        _verify_mysql_migration_guard_access(cursor, migration_guard=None)


@pytest.mark.parametrize(
    "row",
    [
        _guard_row(started_at="2026-07-15T08:00:00+08:00"),
        _guard_row(
            status="complete",
            started_at="2026-07-15T00:00:01+00:00",
            completed_at="2026-07-15T00:00:00+00:00",
        ),
    ],
)
def test_guard_rejects_noncanonical_or_reversed_timestamps(row) -> None:
    with pytest.raises(MySqlBootstrapContractError, match="migration guard"):
        _normalize_mysql_migration_guard(row)


def test_pre_v14_guard_reuses_one_stable_generated_marker() -> None:
    cursor = _GuardCursor()
    connection = _GuardConnection(cursor)

    first = prepare_mysql_migration_guard(
        connection,
        source_schema_version=13,
        source_fingerprint="b" * 64,
        source_rollout_marker=None,
    )
    second = prepare_mysql_migration_guard(
        connection,
        source_schema_version=13,
        source_fingerprint="b" * 64,
        source_rollout_marker=None,
    )

    assert first["rollout_marker"] == second["rollout_marker"]
    assert connection.commits == 1
    with pytest.raises(MySqlBootstrapContractError, match="different source snapshot"):
        prepare_mysql_migration_guard(
            connection,
            source_schema_version=13,
            source_fingerprint="c" * 64,
            source_rollout_marker=None,
        )


class _QualityMetadataCursor:
    def __init__(
        self,
        *,
        mutation: str | None = None,
        legacy: bool = False,
    ) -> None:
        self.last_statement = ""
        self.mutation = mutation
        self.legacy = legacy
        self.repaired_tables: set[str] = set()

    def execute(self, statement: str) -> None:
        self.last_statement = " ".join(statement.split())
        if self.last_statement.startswith("ALTER TABLE "):
            self.repaired_tables.add(self.last_statement.split()[2])

    def _table(self) -> str:
        return self.last_statement.split("TABLE_NAME = '", 1)[1].split("'", 1)[0]

    def fetchall(self):
        table = self._table()
        if "information_schema.COLUMNS" in self.last_statement:
            rows = [
                (
                    name,
                    data_type,
                    length,
                    nullable,
                    position,
                    "ascii_bin" if collation_rule == "binary" else None,
                )
                for position, (
                    name,
                    data_type,
                    length,
                    nullable,
                    collation_rule,
                ) in enumerate(_MYSQL_DQ_COLUMN_CONTRACTS[table], start=1)
            ]
            if self.legacy and table not in self.repaired_tables:
                if table == "decision_quality_input_artifacts":
                    logical_key = rows.pop(5)
                    rows[1] = (*rows[1][:5], "utf8mb4_0900_ai_ci")
                    rows[3] = (*rows[3][:5], "utf8mb4_0900_ai_ci")
                    rows[13] = (
                        "content_hash",
                        "varchar",
                        64,
                        "NO",
                        14,
                        "utf8mb4_0900_ai_ci",
                    )
                    rows.append((*logical_key[:4], 17, logical_key[5]))
                    rows = [
                        (*row[:4], position, row[5])
                        for position, row in enumerate(rows, 1)
                    ]
                elif table == "decision_quality_evaluation_snapshots":
                    rows[1] = (*rows[1][:5], "utf8mb4_0900_ai_ci")
                    for index in (7, 8, 9, 15):
                        rows[index] = (
                            rows[index][0],
                            "varchar",
                            64,
                            rows[index][3],
                            rows[index][4],
                            "utf8mb4_0900_ai_ci",
                        )
            if self.mutation == "identity_collation" and table == "decision_quality_input_artifacts":
                row = rows[1]
                rows[1] = (*row[:5], "utf8mb4_general_ci")
            return rows
        rows = [
            (name, non_unique, position, column, None)
            for name, (non_unique, columns) in _MYSQL_DQ_INDEX_CONTRACTS[table].items()
            for position, column in enumerate(columns, start=1)
        ]
        if self.mutation == "missing_primary" and table == "decision_quality_provider_receipts":
            rows = [row for row in rows if row[0] != "PRIMARY"]
        if self.mutation == "prefix_hash" and table == "decision_quality_contract_rollouts":
            rows = [(*row[:4], 8) if row[0] == "uq_decision_quality_rollout_hash" else row for row in rows]
        return sorted(rows, key=lambda row: (row[0], row[2]))


def test_five_ledger_metadata_contract_accepts_exact_binary_identities() -> None:
    cursor = _QualityMetadataCursor()
    _ensure_decision_quality_mysql_contracts(cursor, cursor.fetchall)


def test_early_v16_quality_tables_are_repaired_before_exact_validation() -> None:
    cursor = _QualityMetadataCursor(legacy=True)

    _repair_legacy_decision_quality_mysql_contracts(cursor, cursor.fetchall)
    _ensure_decision_quality_mysql_contracts(cursor, cursor.fetchall)

    assert cursor.repaired_tables == {
        "decision_quality_input_artifacts",
        "decision_quality_evaluation_snapshots",
    }


@pytest.mark.parametrize(
    "mutation",
    ["identity_collation", "missing_primary", "prefix_hash"],
)
def test_five_ledger_metadata_contract_rejects_attacks(mutation: str) -> None:
    cursor = _QualityMetadataCursor(mutation=mutation)
    with pytest.raises(MySqlBootstrapContractError, match="quality contract"):
        _ensure_decision_quality_mysql_contracts(cursor, cursor.fetchall)


def test_rollout_singleton_rejects_missing_duplicate_and_source_mismatch() -> None:
    marker = build_decision_quality_rollout_marker(
        "2026-07-14T00:00:00+00:00"
    )

    class Cursor:
        def execute(self, _statement: str) -> None:
            return None

    cursor = Cursor()
    with pytest.raises(MySqlBootstrapContractError, match="marker is missing"):
        _read_mysql_rollout_singleton(
            cursor,
            lambda: [],
            expected_marker=marker,
            allow_missing=False,
        )
    row = tuple(marker[column] for column in _ROLLOUT_COLUMNS)
    with pytest.raises(MySqlBootstrapContractError, match="exactly one"):
        _read_mysql_rollout_singleton(
            cursor,
            lambda: [row, row],
            expected_marker=marker,
            allow_missing=False,
        )
    other = build_decision_quality_rollout_marker(
        "2026-07-14T00:00:01+00:00"
    )
    with pytest.raises(MySqlBootstrapContractError, match="conflicts with source"):
        _read_mysql_rollout_singleton(
            cursor,
            lambda: [row],
            expected_marker=other,
            allow_missing=False,
        )
