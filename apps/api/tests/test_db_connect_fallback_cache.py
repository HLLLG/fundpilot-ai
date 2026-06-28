from __future__ import annotations

import time

from app import db_connect


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
