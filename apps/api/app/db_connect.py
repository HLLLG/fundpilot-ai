from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
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


def _connect_primary() -> DbConnection:
    if uses_mysql():
        return _open_mysql()
    return _open_sqlite()


def connect_with_fallback() -> DbConnection:
    global _mysql_unreachable_until
    if not uses_mysql():
        return _open_sqlite()
    if sqlite_fallback_enabled() and time.time() < _mysql_unreachable_until:
        return _open_sqlite()
    try:
        conn = _open_mysql()
        _mysql_unreachable_until = 0.0
        return conn
    except Exception as exc:
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
            import pymysql

            cursor = self._raw.cursor(pymysql.cursors.DictCursor)
            cursor.execute(statement, params or ())
            return cursor
        return self._raw.execute(sql, params or ())

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


def _open_mysql() -> DbConnection:
    existing = getattr(_thread_local, "mysql_conn", None)
    if existing is not None:
        try:
            existing.ping(reconnect=True)
            return DbConnection(existing, "mysql", pooled=True)
        except Exception:
            try:
                existing.close()
            except Exception:
                pass
            _thread_local.mysql_conn = None

    import pymysql

    from app.mysql_bootstrap import ensure_mysql_schema

    settings = get_settings()
    assert settings.database_url
    conn = pymysql.connect(
        **(_parse_mysql_url(settings.database_url) | {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}),
    )
    ensure_mysql_schema(conn)
    _thread_local.mysql_conn = conn
    return DbConnection(conn, "mysql", pooled=True)


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
