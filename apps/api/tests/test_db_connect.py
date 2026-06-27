from __future__ import annotations

import sqlite3

from app import db_connect


def test_sqlite_fallback_uses_sqlite_placeholders(monkeypatch):
    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    raw = sqlite3.connect(":memory:")
    connection = db_connect.DbConnection(raw, "sqlite")

    connection.execute("CREATE TABLE smoke (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO smoke (id) VALUES (?)", ("ok",))
    row = connection.execute("SELECT id FROM smoke WHERE id = ?", ("ok",)).fetchone()

    assert row[0] == "ok"
