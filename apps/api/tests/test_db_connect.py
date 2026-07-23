from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_sqlite_fallback_active_reports_the_effective_store(monkeypatch) -> None:
    monkeypatch.setattr(db_connect, "uses_mysql", lambda: True)
    monkeypatch.setattr(db_connect, "sqlite_fallback_enabled", lambda: True)
    monkeypatch.setattr(
        db_connect,
        "_mysql_unreachable_until",
        time.time() + 60,
    )

    assert db_connect.sqlite_fallback_active() is True

    monkeypatch.setattr(db_connect, "_mysql_unreachable_until", time.time() - 1)
    assert db_connect.sqlite_fallback_active() is False


class _FakeMySqlConnection:
    def __init__(self) -> None:
        self.closed = False
        self.session_statements: list[tuple[str, tuple]] = []

    class _Cursor:
        def __init__(self, owner) -> None:
            self._owner = owner

        def execute(self, statement: str, params: tuple = ()) -> None:
            self._owner.session_statements.append((statement, params))

        def close(self) -> None:
            return None

    def cursor(self):
        return self._Cursor(self)

    def ping(self, reconnect: bool = True) -> None:
        del reconnect

    def close(self) -> None:
        self.closed = True


class _ResultCursor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.rowcount = len(self.rows)
        self.rownumber = 0
        self.lastrowid = None
        self.description = (("value",),)
        self.closed = False

    def execute(self, _statement: str, _params: tuple = ()) -> None:
        return None

    def fetchone(self):
        if self.rownumber >= len(self.rows):
            return None
        row = self.rows[self.rownumber]
        self.rownumber += 1
        return row

    def fetchmany(self, size: int = 1):
        rows = self.rows[self.rownumber : self.rownumber + size]
        self.rownumber += len(rows)
        return rows

    def fetchall(self):
        rows = self.rows[self.rownumber :]
        self.rownumber = len(self.rows)
        return rows

    def close(self) -> None:
        self.closed = True


def test_managed_mysql_cursor_closes_after_results_are_consumed() -> None:
    cursor = _ResultCursor([{"value": 1}, {"value": 2}])
    managed = db_connect.ManagedMySqlCursor(cursor)

    assert managed.fetchone() == {"value": 1}
    assert cursor.closed is False
    assert managed.fetchone() == {"value": 2}
    assert cursor.closed is True

    cursor = _ResultCursor([{"value": 3}])
    assert db_connect.ManagedMySqlCursor(cursor).fetchall() == [{"value": 3}]
    assert cursor.closed is True


def test_managed_mysql_cursor_closes_immediately_for_non_result_statement() -> None:
    cursor = _ResultCursor()
    cursor.description = None

    managed = db_connect.ManagedMySqlCursor(cursor)

    assert cursor.closed is True
    managed.close()  # idempotent


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


def test_mysql_thread_local_connection_retires_after_reuse_limit(monkeypatch) -> None:
    from app import mysql_bootstrap

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_reuse_limit",
    )
    monkeypatch.setenv("FUND_AI_MYSQL_CONNECTION_MAX_REUSE_COUNT", "2")
    monkeypatch.setenv("FUND_AI_MYSQL_CONNECTION_MAX_LIFETIME_SECONDS", "600")
    refresh_settings()
    db_connect.reset_mysql_bootstrap_cache()
    connected: list[_FakeMySqlConnection] = []

    def fake_connect(**_kwargs):
        connection = _FakeMySqlConnection()
        connected.append(connection)
        return connection

    monkeypatch.setattr("pymysql.connect", fake_connect)
    monkeypatch.setattr(mysql_bootstrap, "ensure_mysql_schema", lambda _connection: None)

    try:
        first = db_connect._open_mysql()
        second = db_connect._open_mysql()
        third = db_connect._open_mysql()

        assert first._raw is second._raw
        assert third._raw is not first._raw
        assert connected[0].closed is True
        assert len(connected) == 2
        assert connected[0].session_statements == [
            ("SET SESSION wait_timeout = %s", (2100,))
        ]
    finally:
        db_connect.reset_mysql_bootstrap_cache()
        monkeypatch.delenv("FUND_AI_MYSQL_CONNECTION_MAX_REUSE_COUNT")
        monkeypatch.delenv("FUND_AI_MYSQL_CONNECTION_MAX_LIFETIME_SECONDS")
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()


def test_mysql_thread_local_connection_retires_after_max_lifetime(monkeypatch) -> None:
    from app import mysql_bootstrap

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_lifetime",
    )
    monkeypatch.setenv("FUND_AI_MYSQL_CONNECTION_MAX_LIFETIME_SECONDS", "60")
    refresh_settings()
    db_connect.reset_mysql_bootstrap_cache()
    connected: list[_FakeMySqlConnection] = []

    def fake_connect(**_kwargs):
        connection = _FakeMySqlConnection()
        connected.append(connection)
        return connection

    monkeypatch.setattr("pymysql.connect", fake_connect)
    monkeypatch.setattr(mysql_bootstrap, "ensure_mysql_schema", lambda _connection: None)

    try:
        first = db_connect._open_mysql()
        db_connect._thread_local.mysql_connection_created_monotonic = (
            time.monotonic() - 61
        )
        second = db_connect._open_mysql()

        assert first._raw is not second._raw
        assert connected[0].closed is True
    finally:
        db_connect.reset_mysql_bootstrap_cache()
        monkeypatch.delenv("FUND_AI_MYSQL_CONNECTION_MAX_LIFETIME_SECONDS")
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()


class _DedicatedConnection:
    def __init__(self) -> None:
        self.closed = False
        self.pings: list[bool] = []
        self.statements: list[str] = []

    class _Cursor:
        def __init__(self, owner: "_DedicatedConnection") -> None:
            self.owner = owner
            self.closed = False

        def execute(self, statement: str) -> None:
            self.owner.statements.append(statement)

        def fetchone(self) -> tuple[int]:
            return (0,)

        def close(self) -> None:
            self.closed = True

    def cursor(self):
        return self._Cursor(self)

    def ping(self, reconnect: bool = True) -> None:
        self.pings.append(reconnect)

    def close(self) -> None:
        self.closed = True


def test_dedicated_mysql_pool_reuses_only_sanitized_sessions() -> None:
    created: list[_DedicatedConnection] = []

    def create() -> _DedicatedConnection:
        connection = _DedicatedConnection()
        created.append(connection)
        return connection

    pool = db_connect._DedicatedMySqlSessionPool(max_size=1, create=create)
    first = pool.acquire(0.1)
    pool.release(first)
    second = pool.acquire(0.1)

    assert first is second
    assert first.statements == ["SELECT RELEASE_ALL_LOCKS()"]
    assert first.pings == [False, False]
    with pytest.raises(TimeoutError):
        pool.acquire(0.1)

    pool.release(second)
    pool.close()
    assert first.closed is True
