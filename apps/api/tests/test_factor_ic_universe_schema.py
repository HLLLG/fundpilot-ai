from __future__ import annotations

import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.mysql_bootstrap import MYSQL_SCHEMA_VERSION, ensure_mysql_schema


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SPEC = spec_from_file_location(
    "fundpilot_root_migrate_sqlite_to_mysql",
    _PROJECT_ROOT / "scripts" / "migrate_sqlite_to_mysql.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)
IMMUTABLE_TABLES = _MIGRATION.IMMUTABLE_TABLES
TABLES = _MIGRATION.TABLES
ImmutableMigrationConflict = _MIGRATION.ImmutableMigrationConflict
_insert_immutable_row = _MIGRATION._insert_immutable_row


def test_schema_v11_creates_and_self_heals_pit_universe_tables_and_indexes() -> None:
    assert SCHEMA_VERSION == 11
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)
    assert connection.execute("SELECT version FROM schema_meta WHERE id=1").fetchone()[0] == 11
    for table in ("factor_ic_universe_snapshots", "factor_ic_universe_members"):
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert {
        "idx_factor_ic_universe_date",
        "idx_factor_ic_universe_member_code",
        "idx_factor_ic_universe_member_type",
        "idx_factor_ic_universe_member_portfolio",
    } <= indexes

    connection.execute("DROP TABLE factor_ic_universe_members")
    run_migrations(connection)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='factor_ic_universe_members'"
    ).fetchone()


def test_mysql_bootstrap_and_sqlite_migration_keep_pit_tables_immutable() -> None:
    assert MYSQL_SCHEMA_VERSION == 11
    statements: list[str] = []

    class Cursor:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            return None

    ensure_mysql_schema(Connection())
    ddl = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS factor_ic_universe_snapshots" in ddl
    assert "CREATE TABLE IF NOT EXISTS factor_ic_universe_members" in ddl
    assert "idx_factor_ic_universe_member_portfolio" in ddl

    tables = dict(TABLES)
    assert "content_hash" in tables["factor_ic_universe_snapshots"]
    assert "content_hash" in tables["factor_ic_universe_members"]
    assert IMMUTABLE_TABLES["factor_ic_universe_snapshots"] == (
        ("snapshot_id",),
        ("content_hash",),
    )
    assert IMMUTABLE_TABLES["factor_ic_universe_members"] == (
        ("snapshot_id", "fund_code"),
        ("content_hash",),
    )


def test_sqlite_to_mysql_migration_rejects_pit_identity_hash_conflict() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str, _values) -> None:
            self.statements.append(statement)

        def fetchone(self):
            return {"content_hash": "destination-hash"}

    cursor = Cursor()
    with pytest.raises(ImmutableMigrationConflict, match="不可变表"):
        _insert_immutable_row(
            cursor,
            table="factor_ic_universe_snapshots",
            columns=("snapshot_id", "content_hash"),
            values=("snapshot-1", "source-hash"),
        )
    assert len(cursor.statements) == 1
    assert cursor.statements[0].startswith("SELECT content_hash")
