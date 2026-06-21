from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from app.config import get_settings

logger = logging.getLogger(__name__)


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
    if not uses_mysql():
        return _open_sqlite()
    try:
        return _open_mysql()
    except Exception as exc:
        if not sqlite_fallback_enabled():
            raise
        logger.warning(
            "MySQL unavailable (%s); falling back to SQLite at %s",
            exc,
            _db_path(),
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

    def __init__(self, raw: Any, dialect: str) -> None:
        self._raw = raw
        self.dialect = dialect

    def execute(self, sql: str, params: tuple | list = ()) -> Any:
        statement = adapt_sql(sql)
        if self.dialect == "mysql":
            import pymysql

            cursor = self._raw.cursor(pymysql.cursors.DictCursor)
            cursor.execute(statement, params or ())
            return cursor
        return self._raw.execute(statement, params or ())

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> DbConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
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
    import pymysql

    from app.mysql_bootstrap import ensure_mysql_schema

    settings = get_settings()
    assert settings.database_url
    conn = pymysql.connect(
        **(_parse_mysql_url(settings.database_url) | {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}),
    )
    ensure_mysql_schema(conn)
    return DbConnection(conn, "mysql")


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
