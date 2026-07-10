#!/usr/bin/env python3
"""将 CloudBase MySQL（或任意 MySQL）数据同步到本地 SQLite，供开发/测试使用。"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

# 体量较大、且可自动重建的共享缓存表；默认跳过以加快同步
CACHE_TABLES = frozenset({"news_cache", "sector_spot_cache"})


def parse_mysql_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
        "charset": "utf8mb4",
    }


def load_env_database_url() -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return os.getenv("FUND_AI_DATABASE_URL")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("FUND_AI_DATABASE_URL=") and not line.startswith("#"):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return os.getenv("FUND_AI_DATABASE_URL")


def ensure_sqlite_schema(db_path: Path) -> None:
    os.environ["FUND_AI_DATABASE_URL"] = ""
    os.environ["FUND_AI_DB_PATH"] = str(db_path)
    from app.config import refresh_settings
    from app.database import _connect
    from app.services.job_store import _ensure_jobs_table
    from app.services.news_cache import _ensure_cache_table as _ensure_news_cache_table
    from app.services.sector_quote_cache import _ensure_cache_table as _ensure_sector_cache_table

    refresh_settings()
    conn = _connect()
    if hasattr(conn, "close"):
        conn.close()

    sqlite_conn = sqlite3.connect(db_path)
    try:
        _ensure_jobs_table(sqlite_conn)
        _ensure_news_cache_table(sqlite_conn)
        _ensure_sector_cache_table(sqlite_conn)
        sqlite_conn.commit()
    finally:
        sqlite_conn.close()


def list_mysql_tables(cursor) -> list[str]:
    cursor.execute(
        """
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME
        """
    )
    return [row["TABLE_NAME"] for row in cursor.fetchall()]


def copy_table(mysql_cursor, sqlite_conn: sqlite3.Connection, table: str) -> int:
    mysql_cursor.execute(f"SELECT * FROM `{table}`")
    rows = mysql_cursor.fetchall()
    if not rows:
        return 0

    columns = list(rows[0].keys())
    sqlite_columns = {
        row[1]
        for row in sqlite_conn.execute(f"PRAGMA table_info(`{table}`)").fetchall()
    }
    missing = [column for column in columns if column not in sqlite_columns]
    if missing:
        print(f"  跳过 {table}：SQLite 缺少列 {missing}", file=sys.stderr)
        return 0

    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(f"`{column}`" for column in columns)
    payload = [tuple(row[column] for column in columns) for row in rows]
    sqlite_conn.execute(f"DELETE FROM `{table}`")
    sqlite_conn.executemany(
        f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})",
        payload,
    )
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MySQL → SQLite：把线上 fundpilot 数据复制到本地 SQLite"
    )
    parser.add_argument(
        "--mysql-url",
        default=None,
        help="mysql://user:pass@host:3306/db；默认读 .env 的 FUND_AI_DATABASE_URL",
    )
    parser.add_argument(
        "--sqlite",
        default=str(ROOT / "data" / "app-from-prod.db"),
        help="目标 SQLite 文件（默认 data/app-from-prod.db，不覆盖 data/app.db）",
    )
    parser.add_argument(
        "--include-cache",
        action="store_true",
        help="同步 news_cache / sector_spot_cache（默认跳过）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="目标文件已存在时覆盖",
    )
    args = parser.parse_args()

    mysql_url = (args.mysql_url or load_env_database_url() or "").strip()
    if not mysql_url.startswith("mysql"):
        print("请提供 --mysql-url 或在 .env 配置 FUND_AI_DATABASE_URL", file=sys.stderr)
        return 1

    target = Path(args.sqlite)
    if target.exists() and not args.force:
        print(
            f"目标已存在: {target}\n加 --force 覆盖，或换 --sqlite 路径",
            file=sys.stderr,
        )
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    print(f"初始化 SQLite schema → {target}")
    ensure_sqlite_schema(target)

    import pymysql

    mysql_conn = pymysql.connect(
        **parse_mysql_url(mysql_url),
        cursorclass=pymysql.cursors.DictCursor,
    )
    mysql_cursor = mysql_conn.cursor()
    sqlite_conn = sqlite3.connect(target)

    tables = list_mysql_tables(mysql_cursor)
    if not args.include_cache:
        tables = [table for table in tables if table not in CACHE_TABLES]

    total_rows = 0
    try:
        sqlite_conn.execute("PRAGMA foreign_keys = OFF")
        for table in tables:
            copied = copy_table(mysql_cursor, sqlite_conn, table)
            sqlite_conn.commit()
            print(f"  {table}: {copied} 行")
            total_rows += copied
    finally:
        sqlite_conn.close()
        mysql_conn.close()

    print(f"\n完成：{len(tables)} 张表，共 {total_rows} 行 → {target}")
    print("\n本地使用方式：")
    print("  FUND_AI_DATABASE_URL=")
    print(f"  FUND_AI_DB_PATH={target.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
