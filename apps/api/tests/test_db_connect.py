from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from app.config import refresh_settings
from app import db_connect


def test_sqlite_fallback_uses_sqlite_placeholders(monkeypatch):
    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    raw = sqlite3.connect(":memory:")
    connection = db_connect.DbConnection(raw, "sqlite")

    connection.execute("CREATE TABLE smoke (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO smoke (id) VALUES (?)", ("ok",))
    row = connection.execute("SELECT id FROM smoke WHERE id = ?", ("ok",)).fetchone()

    assert row[0] == "ok"


class _FakeMySqlConnection:
    def __init__(self) -> None:
        self.closed = False

    def ping(self, reconnect: bool = True) -> None:
        del reconnect

    def close(self) -> None:
        self.closed = True


def test_mysql_schema_bootstrap_is_single_flight_across_threads(monkeypatch):
    from app import mysql_bootstrap

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_single_flight",
    )
    refresh_settings()
    db_connect.reset_mysql_bootstrap_cache()

    connected: list[_FakeMySqlConnection] = []
    connected_lock = threading.Lock()

    def fake_connect(**_kwargs):
        connection = _FakeMySqlConnection()
        with connected_lock:
            connected.append(connection)
        return connection

    bootstrap_started = threading.Event()
    release_bootstrap = threading.Event()
    bootstrap_calls = 0
    bootstrap_calls_lock = threading.Lock()

    def fake_bootstrap(_connection) -> None:
        nonlocal bootstrap_calls
        with bootstrap_calls_lock:
            bootstrap_calls += 1
        bootstrap_started.set()
        assert release_bootstrap.wait(timeout=5)

    monkeypatch.setattr("pymysql.connect", fake_connect)
    monkeypatch.setattr(mysql_bootstrap, "ensure_mysql_schema", fake_bootstrap)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(db_connect._open_mysql)
            assert bootstrap_started.wait(timeout=2)
            remaining = [executor.submit(db_connect._open_mysql) for _ in range(7)]
            release_bootstrap.set()
            connections = [first.result(timeout=5), *[item.result(timeout=5) for item in remaining]]

        assert bootstrap_calls == 1
        assert len(connections) == 8
        assert all(connection.dialect == "mysql" for connection in connections)
        assert len(connected) == 8
    finally:
        db_connect.reset_mysql_bootstrap_cache()
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()


def test_mysql_schema_bootstrap_failure_is_retryable(monkeypatch):
    from app import mysql_bootstrap

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_bootstrap_retry",
    )
    refresh_settings()
    db_connect.reset_mysql_bootstrap_cache()

    connected: list[_FakeMySqlConnection] = []

    def fake_connect(**_kwargs):
        connection = _FakeMySqlConnection()
        connected.append(connection)
        return connection

    attempts = 0

    def flaky_bootstrap(_connection) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise mysql_bootstrap.MySqlBootstrapContractError("injected bootstrap failure")

    monkeypatch.setattr("pymysql.connect", fake_connect)
    monkeypatch.setattr(mysql_bootstrap, "ensure_mysql_schema", flaky_bootstrap)

    try:
        try:
            db_connect._open_mysql()
        except mysql_bootstrap.MySqlBootstrapContractError:
            pass
        else:
            raise AssertionError("first bootstrap attempt should fail")

        connection = db_connect._open_mysql()

        assert connection.dialect == "mysql"
        assert attempts == 2
        assert connected[0].closed is True
    finally:
        db_connect.reset_mysql_bootstrap_cache()
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()
