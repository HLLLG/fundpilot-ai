from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from app.config import get_settings

logger = logging.getLogger(__name__)

_mysql_unreachable_until: float = 0.0
# 每个线程复用一条 MySQL 连接，避免每次查询都重新握手 + 重跑 24 条 schema DDL
# （远程云数据库单次往返 ~150-1000ms，逐查询新建连接曾导致单个持仓请求耗时 20s+）。
_thread_local = threading.local()
_mysql_schema_bootstrap_lock = threading.Lock()
_mysql_schema_ready_key: tuple[str, int, str, str] | None = None
_dedicated_pool_lock = threading.Lock()
_dedicated_pool: _DedicatedMySqlSessionPool | None = None
_dedicated_pool_key: tuple[str, int, str, str, int] | None = None


class ManagedMySqlCursor:
    """DB-API cursor proxy that deterministically releases buffered results."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self._closed = False
        # PyMySQL keeps these attributes after close today, but callers should
        # not depend on that implementation detail.
        self.rowcount = getattr(cursor, "rowcount", -1)
        self.lastrowid = getattr(cursor, "lastrowid", None)
        self.description = getattr(cursor, "description", None)
        if self.description is None:
            self.close()

    def _consumed(self) -> bool:
        rowcount = int(getattr(self._cursor, "rowcount", -1) or -1)
        rownumber = int(getattr(self._cursor, "rownumber", 0) or 0)
        return rowcount >= 0 and rownumber >= rowcount

    def fetchone(self) -> Any:
        try:
            row = self._cursor.fetchone()
            if row is None or self._consumed():
                self.close()
            return row
        except Exception:
            self.close()
            raise

    def fetchmany(self, size: int | None = None) -> Any:
        try:
            rows = (
                self._cursor.fetchmany()
                if size is None
                else self._cursor.fetchmany(size)
            )
            if not rows or self._consumed():
                self.close()
            return rows
        except Exception:
            self.close()
            raise

    def fetchall(self) -> Any:
        try:
            return self._cursor.fetchall()
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cursor.close()

    def __iter__(self) -> Iterator[Any]:
        try:
            yield from self._cursor
        finally:
            self.close()

    def __enter__(self) -> ManagedMySqlCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def execute_mysql_statement(
    raw_connection: Any,
    statement: str,
    params: tuple | list = (),
) -> ManagedMySqlCursor:
    """Execute one MySQL statement with a cursor that cannot leak silently."""

    import pymysql
    from app.services.performance_metrics import record_db_query

    cursor = raw_connection.cursor(pymysql.cursors.DictCursor)
    started_at = time.perf_counter()
    error: BaseException | None = None
    try:
        cursor.execute(statement, params or ())
    except Exception as exc:
        error = exc
        cursor.close()
        raise
    finally:
        record_db_query(
            "mysql",
            statement,
            time.perf_counter() - started_at,
            error=error,
        )
    return ManagedMySqlCursor(cursor)


class _DedicatedMySqlSessionPool:
    """Small bounded pool for session-scoped locks, isolated from requests."""

    def __init__(self, *, max_size: int, create: Callable[[], Any]) -> None:
        self._max_size = max(1, int(max_size))
        self._create = create
        self._condition = threading.Condition()
        self._idle: list[Any] = []
        self._total = 0
        self._closed = False

    def acquire(self, timeout_seconds: float) -> Any:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while True:
            create_new = False
            with self._condition:
                if self._closed:
                    raise RuntimeError("MySQL dedicated session pool is closed")
                if self._idle:
                    raw = self._idle.pop()
                elif self._total < self._max_size:
                    self._total += 1
                    create_new = True
                    raw = None
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for a dedicated MySQL session"
                        )
                    self._condition.wait(remaining)
                    continue

            if create_new:
                try:
                    return self._create()
                except Exception:
                    with self._condition:
                        self._total -= 1
                        self._condition.notify()
                    raise

            try:
                raw.ping(reconnect=False)
                return raw
            except Exception:
                try:
                    raw.close()
                except Exception:
                    pass
                with self._condition:
                    self._total -= 1
                    self._condition.notify()

    def release(self, raw: Any) -> None:
        reusable = False
        try:
            cursor = raw.cursor()
            try:
                cursor.execute("SELECT RELEASE_ALL_LOCKS()")
                cursor.fetchone()
            finally:
                cursor.close()
            raw.ping(reconnect=False)
            reusable = True
        except Exception:
            logger.warning(
                "discarding unhealthy dedicated MySQL session",
                exc_info=True,
            )

        with self._condition:
            if reusable and not self._closed:
                self._idle.append(raw)
            else:
                self._total -= 1
                try:
                    raw.close()
                except Exception:
                    pass
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            idle, self._idle = self._idle, []
            self._total -= len(idle)
            self._condition.notify_all()
        for raw in idle:
            try:
                raw.close()
            except Exception:
                pass

    def snapshot(self) -> dict[str, int | bool]:
        with self._condition:
            idle = len(self._idle)
            total = max(0, self._total)
            return {
                "max_size": self._max_size,
                "total": total,
                "idle": idle,
                "in_use": max(0, total - idle),
                "closed": self._closed,
            }


def _mysql_fallback_cooldown_seconds() -> float:
    raw = os.getenv("FUND_AI_MYSQL_FALLBACK_COOLDOWN", "300").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 300.0


def reset_mysql_fallback_cache() -> None:
    """测试或运维：清除 MySQL 不可用缓存，强制下次重试主库。"""
    global _mysql_unreachable_until
    _mysql_unreachable_until = 0.0


def reset_mysql_bootstrap_cache() -> None:
    """Clear the per-process schema-ready marker.

    Runtime code normally never needs this: a new deployment starts a new
    process, while the connection key automatically detects a changed target.
    Tests and explicit operational reconfiguration can use it to force one
    fresh bootstrap.
    """

    global _dedicated_pool, _dedicated_pool_key, _mysql_schema_ready_key
    with _mysql_schema_bootstrap_lock:
        _mysql_schema_ready_key = None

    existing = getattr(_thread_local, "mysql_conn", None)
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass
    _thread_local.mysql_conn = None
    _thread_local.mysql_connection_key = None
    _thread_local.mysql_connection_created_monotonic = None
    _thread_local.mysql_connection_reuse_count = None

    with _dedicated_pool_lock:
        pool, _dedicated_pool = _dedicated_pool, None
        _dedicated_pool_key = None
    if pool is not None:
        pool.close()


def dedicated_mysql_session_pool_snapshot() -> dict[str, int | bool]:
    """Return pool occupancy without creating a pool or opening a socket."""

    with _dedicated_pool_lock:
        pool = _dedicated_pool
    if pool is None:
        return {
            "max_size": max(
                0,
                int(get_settings().mysql_dedicated_session_pool_size),
            ),
            "total": 0,
            "idle": 0,
            "in_use": 0,
            "closed": False,
        }
    return pool.snapshot()


def _db_path() -> Path:
    override = os.getenv("FUND_AI_DB_PATH")
    if override:
        return Path(override)
    return get_settings().db_path


def uses_mysql() -> bool:
    return get_settings().uses_mysql


def sqlite_fallback_enabled() -> bool:
    """MySQL 不可达时是否回退本地 SQLite（本地开发默认开启）。"""
    raw = os.getenv("FUND_AI_DB_FALLBACK_SQLITE", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def sqlite_fallback_active() -> bool:
    """Whether this process is currently routing database work to SQLite.

    A configured MySQL URL alone is not enough to choose a coordination
    backend: local development may have already entered the bounded SQLite
    fallback after a connectivity failure. Cross-process locks must follow
    that effective store or the worker will retry an unreachable MySQL lock
    forever while ordinary reads and writes are using SQLite.
    """
    return bool(
        uses_mysql()
        and sqlite_fallback_enabled()
        and time.time() < _mysql_unreachable_until
    )


def connect_with_fallback() -> DbConnection:
    global _mysql_unreachable_until
    if not uses_mysql():
        return _open_sqlite()
    if sqlite_fallback_active():
        return _open_sqlite()
    try:
        conn = _open_mysql()
        _mysql_unreachable_until = 0.0
        return conn
    except Exception as exc:
        # A reachable primary database that cannot enforce an immutable schema
        # contract is not a transient connectivity outage.  Falling back here
        # would hide a release/privilege defect behind a local SQLite success.
        from app.mysql_bootstrap import MySqlBootstrapContractError

        if isinstance(exc, MySqlBootstrapContractError):
            raise
        if not sqlite_fallback_enabled():
            raise
        _mysql_unreachable_until = time.time() + _mysql_fallback_cooldown_seconds()
        logger.warning(
            "MySQL unavailable (%s); falling back to SQLite at %s (cooldown %.0fs)",
            exc,
            _db_path(),
            _mysql_fallback_cooldown_seconds(),
        )
        return _open_sqlite()


def adapt_sql(sql: str) -> str:
    if not uses_mysql():
        return sql
    adapted = sql.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    adapted = adapted.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
    return adapted.replace("?", "%s")


class DbConnection:
    """统一 SQLite / MySQL 连接包装。"""

    def __init__(self, raw: Any, dialect: str, *, pooled: bool = False) -> None:
        self._raw = raw
        self.dialect = dialect
        self._pooled = pooled

    def execute(self, sql: str, params: tuple | list = ()) -> Any:
        if self.dialect == "mysql":
            statement = adapt_sql(sql)
            return execute_mysql_statement(self._raw, statement, params)
        from app.services.performance_metrics import record_db_query

        started_at = time.perf_counter()
        error: BaseException | None = None
        try:
            return self._raw.execute(sql, params or ())
        except Exception as exc:
            error = exc
            raise
        finally:
            record_db_query(
                "sqlite",
                sql,
                time.perf_counter() - started_at,
                error=error,
            )

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except Exception:  # noqa: BLE001 — 回滚失败不应掩盖原始异常
            pass

    def close(self) -> None:
        if self._pooled:
            # 保留在线程本地池中复用，不真正关闭底层 socket。
            return
        self._raw.close()

    def __enter__(self) -> DbConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def _parse_mysql_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
        "charset": "utf8mb4",
    }


def _mysql_connection_key(settings: Any) -> tuple[str, int, str, str]:
    """Return a password-free identity for the configured primary database."""

    assert settings.database_url
    parsed = _parse_mysql_url(settings.database_url)
    return (
        str(parsed["host"]),
        int(parsed["port"]),
        str(parsed["user"]),
        str(parsed["database"]),
    )


def _ensure_mysql_schema_once(connection: Any, connection_key: tuple[str, int, str, str]) -> None:
    """Run heavyweight schema bootstrap once per process and database target.

    FastAPI's thread pool creates several thread-local MySQL connections during
    the first page load. Without this single-flight guard every connection runs
    the full bootstrap and competes for the same MySQL named lock. Waiting
    threads now share the first result and skip repeated DDL/metadata checks.
    """

    global _mysql_schema_ready_key
    from app.mysql_bootstrap import ensure_mysql_schema

    with _mysql_schema_bootstrap_lock:
        if _mysql_schema_ready_key == connection_key:
            return
        ensure_mysql_schema(connection)
        _mysql_schema_ready_key = connection_key


def _open_mysql() -> DbConnection:
    settings = get_settings()
    assert settings.database_url
    connection_key = _mysql_connection_key(settings)
    existing = getattr(_thread_local, "mysql_conn", None)
    existing_key = getattr(_thread_local, "mysql_connection_key", None)
    created_at = getattr(
        _thread_local,
        "mysql_connection_created_monotonic",
        None,
    )
    reuse_count = int(
        getattr(_thread_local, "mysql_connection_reuse_count", 0) or 0
    )
    now = time.monotonic()
    max_lifetime = max(
        1,
        int(settings.mysql_connection_max_lifetime_seconds),
    )
    max_reuse_count = max(
        1,
        int(settings.mysql_connection_max_reuse_count),
    )
    if existing is not None and existing_key == connection_key:
        expired = (
            created_at is None
            or now - float(created_at) >= max_lifetime
            or reuse_count >= max_reuse_count
        )
        if not expired:
            try:
                # Reconnect would create a new server session while retaining
                # stale local lifetime metadata, so fail and replace instead.
                existing.ping(reconnect=False)
                _thread_local.mysql_connection_reuse_count = reuse_count + 1
                return DbConnection(existing, "mysql", pooled=True)
            except Exception:
                pass
        try:
            existing.close()
        except Exception:
            pass
        _thread_local.mysql_conn = None
        _thread_local.mysql_connection_key = None
        _thread_local.mysql_connection_created_monotonic = None
        _thread_local.mysql_connection_reuse_count = None
    elif existing is not None:
        try:
            existing.close()
        except Exception:
            pass
        _thread_local.mysql_conn = None
        _thread_local.mysql_connection_key = None
        _thread_local.mysql_connection_created_monotonic = None
        _thread_local.mysql_connection_reuse_count = None

    import pymysql

    conn = pymysql.connect(
        **(_parse_mysql_url(settings.database_url) | {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}),
    )
    try:
        session_wait_timeout = max(
            max_lifetime + 60,
            int(settings.mysql_session_wait_timeout_seconds),
        )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SET SESSION wait_timeout = %s",
                (session_wait_timeout,),
            )
        finally:
            cursor.close()
        _ensure_mysql_schema_once(conn, connection_key)
    except Exception:
        # Do not leak a connection after bootstrap failure; connection-scoped
        # MySQL named locks are also guaranteed to be released on close.
        try:
            conn.close()
        finally:
            _thread_local.mysql_conn = None
            _thread_local.mysql_connection_key = None
            _thread_local.mysql_connection_created_monotonic = None
            _thread_local.mysql_connection_reuse_count = None
        raise
    _thread_local.mysql_conn = conn
    _thread_local.mysql_connection_key = connection_key
    _thread_local.mysql_connection_created_monotonic = now
    _thread_local.mysql_connection_reuse_count = 1
    return DbConnection(conn, "mysql", pooled=True)


def _get_dedicated_pool(
    *,
    read_timeout_seconds: float,
) -> _DedicatedMySqlSessionPool | None:
    global _dedicated_pool, _dedicated_pool_key

    settings = get_settings()
    pool_size = max(0, int(settings.mysql_dedicated_session_pool_size))
    if pool_size == 0:
        return None
    assert settings.database_url
    connection_key = (*_mysql_connection_key(settings), pool_size)

    with _dedicated_pool_lock:
        if _dedicated_pool is not None and _dedicated_pool_key == connection_key:
            return _dedicated_pool
        previous = _dedicated_pool

        def create() -> Any:
            import pymysql

            return pymysql.connect(
                **(
                    _parse_mysql_url(settings.database_url or "")
                    | {
                        "connect_timeout": 10,
                        "read_timeout": max(
                            35,
                            int(math.ceil(read_timeout_seconds)),
                        ),
                        "write_timeout": 10,
                        "autocommit": True,
                    }
                ),
            )

        _dedicated_pool = _DedicatedMySqlSessionPool(
            max_size=pool_size,
            create=create,
        )
        _dedicated_pool_key = connection_key
    if previous is not None:
        previous.close()
    return _dedicated_pool


@contextmanager
def open_dedicated_mysql_session(
    *,
    read_timeout_seconds: float = 35.0,
) -> Iterator[DbConnection]:
    """Open a short-lived, non-pooled MySQL session for session-scoped state.

    MySQL named locks belong to a server session rather than a transaction.
    The normal thread-local connection pool must therefore not be used: a
    failed release could otherwise leak a lock into an unrelated later
    request. Closing this dedicated connection is the final release guarantee.

    This deliberately does not fall back to SQLite. A deployment configured
    for MySQL must fail closed instead of acquiring a lock in a different
    store from the one that will receive the protected writes.
    """
    settings = get_settings()
    if not settings.uses_mysql or not settings.database_url:
        raise RuntimeError("MySQL dedicated session requested without MySQL configured")

    pool = _get_dedicated_pool(read_timeout_seconds=read_timeout_seconds)
    if pool is None:
        import pymysql

        raw = pymysql.connect(
            **(
                _parse_mysql_url(settings.database_url)
                | {
                    "connect_timeout": 10,
                    "read_timeout": max(5, int(math.ceil(read_timeout_seconds))),
                    "write_timeout": 10,
                    "autocommit": True,
                }
            ),
        )
    else:
        raw = pool.acquire(
            settings.mysql_dedicated_session_acquire_timeout_seconds
        )
    connection = DbConnection(raw, "mysql", pooled=False)
    try:
        yield connection
    finally:
        if pool is None:
            # Closing the server session releases every GET_LOCK() it owns,
            # even when RELEASE_LOCK() failed because the connection failed.
            connection.close()
        else:
            pool.release(raw)


def initialize_database_connection() -> None:
    """Warm the configured store before request/background concurrency starts."""

    connection = connect_with_fallback()
    connection.close()


def _open_sqlite() -> DbConnection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return DbConnection(conn, "sqlite")


@contextmanager
def open_db() -> Iterator[DbConnection]:
    connection = connect_with_fallback()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def connect() -> DbConnection:
    """与历史 `_connect()` 兼容：调用方负责 commit/close。"""
    return connect_with_fallback()
