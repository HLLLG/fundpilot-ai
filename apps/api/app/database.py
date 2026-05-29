from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import Report


def _db_path() -> Path:
    override = os.getenv("FUND_AI_DB_PATH")
    if override:
        return Path(override)
    return get_settings().db_path


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def save_report(report: Report) -> Report:
    payload = report.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO reports (id, created_at, payload)
            VALUES (?, ?, ?)
            """,
            (report.id, report.created_at.isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        connection.commit()
    return report


def list_reports() -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM reports ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def get_report(report_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])
