from __future__ import annotations

import sqlite3

from app.db_migrations import SCHEMA_VERSION, run_migrations


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
