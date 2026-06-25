"""内存中的流式分析会话（断线即丢弃，不持久化）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

PRE_LLM_FOLLOWUP_STAGES = frozenset({"fund_data", "news_prefetch", "news_summarize"})

_lock = Lock()
_sessions: dict[str, "StreamSession"] = {}


@dataclass
class StreamSession:
    session_id: str
    stage: str = "fund_data"
    operator_notes: list[str] = field(default_factory=list)


def create_stream_session() -> StreamSession:
    session = StreamSession(session_id=uuid4().hex)
    with _lock:
        _sessions[session.session_id] = session
    return session


def get_stream_session(session_id: str) -> StreamSession | None:
    with _lock:
        return _sessions.get(session_id)


def delete_stream_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)


def set_stream_session_stage(session_id: str, stage: str) -> None:
    with _lock:
        session = _sessions.get(session_id)
        if session is not None:
            session.stage = stage


def append_stream_followup(session_id: str, message: str) -> tuple[bool, str, int]:
    """返回 (ok, error_message, status_code)。"""
    text = message.strip()
    if not text:
        return False, "message 不能为空", 400
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return False, "会话不存在或已结束", 404
        if session.stage not in PRE_LLM_FOLLOWUP_STAGES:
            return False, "已进入生成阶段，无法追加说明", 409
        session.operator_notes.append(text)
    return True, "", 200
