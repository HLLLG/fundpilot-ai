#!/usr/bin/env python3
"""将本地 SQLite 数据迁移到 MySQL（CloudBase MySQL 或自建）。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

TABLES = [
    ("users", ["id", "userRole", "username", "userAccount", "passwordHash", "bio", "avatarUrl", "cloudbaseUid", "createdAt", "updatedAt", "isDeleted", "deletedAt"]),
    ("reports", ["id", "created_at", "payload", "userId"]),
    ("fund_profiles", ["userId", "fund_code", "fund_name", "payload", "updated_at"]),
    ("portfolio_state", ["userId", "payload", "updated_at"]),
    ("portfolio_daily_snapshots", ["userId", "snapshot_date", "payload", "updated_at"]),
    ("portfolio_intraday_curves", ["userId", "trade_date", "payload", "updated_at"]),
    ("investor_profile_state", ["userId", "payload", "updated_at"]),
    ("sector_mappings", ["userId", "sector_label", "source_type", "source_code", "source_name", "confidence", "updated_at"]),
    ("ocr_text_cache", ["userId", "cache_key", "raw_text", "updated_at"]),
    ("report_chat_messages", ["id", "report_id", "role", "content", "created_at"]),
    ("analysis_jobs", ["id", "status", "request_payload", "report_id", "error", "stage", "stage_label", "userId", "created_at", "updated_at"]),
]


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


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    parser.add_argument("--sqlite", default=str(ROOT / "data" / "app.db"), help="SQLite 源文件")
    parser.add_argument("--mysql-url", required=True, help="mysql://user:pass@host:3306/dbname")
    args = parser.parse_args()

    import pymysql
    from app.mysql_bootstrap import ensure_mysql_schema

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        print(f"SQLite 文件不存在: {sqlite_path}", file=sys.stderr)
        return 1

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    dst = pymysql.connect(**parse_mysql_url(args.mysql_url))
    ensure_mysql_schema(dst)
    cursor = dst.cursor()

    for table, columns in TABLES:
        try:
            rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"跳过表 {table}（源库不存在）")
            continue
        if not rows:
            print(f"表 {table}: 0 行")
            continue
        placeholders = ", ".join(["%s"] * len(columns))
        col_list = ", ".join(columns)
        sql = f"REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        payload = [tuple(row[col] for col in columns) for row in rows]
        cursor.executemany(sql, payload)
        dst.commit()
        print(f"表 {table}: 迁移 {len(payload)} 行")

    dst.close()
    src.close()
    print("迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
