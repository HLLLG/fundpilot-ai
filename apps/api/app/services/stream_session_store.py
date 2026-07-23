"""Short-lived, cross-worker stream follow-up sessions.

The database is deliberately the authority: an SSE request and its follow-up
POST may be routed to different Uvicorn workers.  Rows contain only operator
notes and stage metadata, are isolated by ``userId``, and expire quickly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.database import _connect
from app.request_context import get_request_user_id

PRE_LLM_FOLLOWUP_STAGES = frozenset(
    {"fund_data", "news_prefetch", "news_summarize"}
)


@dataclass(frozen=True)
class StreamSession:
    session_id: str
    user_id: int
    stage: str
    operator_notes: list[str]
    created_at: str
    updated_at: str
    expires_at: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_notes(raw: Any) -> list[str]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _row_to_session(row: Any) -> StreamSession:
    return StreamSession(
        session_id=str(row["session_id"]),
        user_id=int(row["userId"]),
        stage=str(row["stage"]),
        operator_notes=_decode_notes(row["operator_notes"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]),
    )


def create_stream_session() -> StreamSession:
    user_id = get_request_user_id()
    now = _now()
    expires = now + timedelta(
        seconds=max(60, int(get_settings().stream_session_ttl_seconds))
    )
    session = StreamSession(
        session_id=uuid4().hex,
        user_id=user_id,
        stage="fund_data",
        operator_notes=[],
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )
    with _connect() as connection:
        connection.execute(
            """
            DELETE FROM stream_sessions
            WHERE userId = ? AND expires_at <= ?
            """,
            (user_id, now.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO stream_sessions (
                session_id, userId, stage, operator_notes,
                created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.user_id,
                session.stage,
                "[]",
                session.created_at,
                session.updated_at,
                session.expires_at,
            ),
        )
    return session


def cleanup_expired_stream_sessions() -> int:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM stream_sessions WHERE expires_at <= ?",
            (_now().isoformat(),),
        )
        return max(0, int(cursor.rowcount or 0))


def get_stream_session(session_id: str) -> StreamSession | None:
    user_id = get_request_user_id()
    now = _now().isoformat()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM stream_sessions
            WHERE session_id = ? AND userId = ? AND expires_at > ?
            """,
            (session_id, user_id, now),
        ).fetchone()
    return _row_to_session(row) if row is not None else None


def delete_stream_session(session_id: str) -> None:
    user_id = get_request_user_id()
    with _connect() as connection:
        connection.execute(
            "DELETE FROM stream_sessions WHERE session_id = ? AND userId = ?",
            (session_id, user_id),
        )


def set_stream_session_stage(session_id: str, stage: str) -> None:
    user_id = get_request_user_id()
    now = _now().isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE stream_sessions
            SET stage = ?, updated_at = ?
            WHERE session_id = ? AND userId = ? AND expires_at > ?
            """,
            (stage, now, session_id, user_id, now),
        )


def append_stream_followup(
    session_id: str,
    message: str,
) -> tuple[bool, str, int]:
    """Return ``(ok, error_message, status_code)``."""

    text = message.strip()
    if not text:
        return False, "message 不能为空", 400
    user_id = get_request_user_id()
    now = _now().isoformat()
    with _connect() as connection:
        if str(getattr(connection, "dialect", "sqlite")) == "sqlite":
            connection.execute("BEGIN IMMEDIATE")
            lock_clause = ""
        else:
            lock_clause = " FOR UPDATE"
        row = connection.execute(
            f"""
            SELECT stage, operator_notes, expires_at
            FROM stream_sessions
            WHERE session_id = ? AND userId = ?{lock_clause}
            """,
            (session_id, user_id),
        ).fetchone()
        if row is None or str(row["expires_at"]) <= now:
            return False, "会话不存在、已过期或已结束", 404
        if str(row["stage"]) not in PRE_LLM_FOLLOWUP_STAGES:
            return False, "已进入生成阶段，无法追加说明", 409
        notes = _decode_notes(row["operator_notes"])
        notes.append(text)
        connection.execute(
            """
            UPDATE stream_sessions
            SET operator_notes = ?, updated_at = ?
            WHERE session_id = ? AND userId = ?
            """,
            (
                json.dumps(notes, ensure_ascii=False),
                now,
                session_id,
                user_id,
            ),
        )
    return True, "", 200
