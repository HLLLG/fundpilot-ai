from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.database import _connect

InboxEventKind = Literal["ocr_ready", "schedule_reminder"]
InboxEventStatus = Literal["pending", "consumed", "failed"]


def _ensure_inbox_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox_events (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            file_name TEXT,
            file_path TEXT,
            payload TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_inbox_event(
    *,
    kind: InboxEventKind,
    file_name: str | None = None,
    file_path: str | None = None,
    payload: dict[str, Any],
    status: InboxEventStatus = "pending",
    error: str | None = None,
) -> dict[str, Any]:
    event_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        _ensure_inbox_table(connection)
        connection.execute(
            """
            INSERT INTO inbox_events (
                id, kind, status, file_name, file_path, payload, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                kind,
                status,
                file_name,
                file_path,
                json.dumps(payload, ensure_ascii=False),
                error,
                now,
                now,
            ),
        )
        connection.commit()
    event = get_inbox_event(event_id)
    assert event is not None
    return event


def list_inbox_events(
    *,
    status: InboxEventStatus | None = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    with _connect() as connection:
        _ensure_inbox_table(connection)
        if status is None:
            rows = connection.execute(
                """
                SELECT * FROM inbox_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM inbox_events
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
    return [_row_to_event(row) for row in rows]


def get_inbox_event(event_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        _ensure_inbox_table(connection)
        row = connection.execute(
            "SELECT * FROM inbox_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_event(row)


def update_inbox_event_status(
    event_id: str,
    status: InboxEventStatus,
    *,
    error: str | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        _ensure_inbox_table(connection)
        connection.execute(
            """
            UPDATE inbox_events
            SET status = ?, error = COALESCE(?, error), updated_at = ?
            WHERE id = ?
            """,
            (status, error, now, event_id),
        )
        connection.commit()
    return get_inbox_event(event_id)


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "file_name": row["file_name"],
        "file_path": row["file_path"],
        "payload": json.loads(row["payload"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
