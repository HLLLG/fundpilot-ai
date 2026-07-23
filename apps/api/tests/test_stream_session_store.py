from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.database import _connect
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.stream_session_store import (
    append_stream_followup,
    create_stream_session,
    delete_stream_session,
    get_stream_session,
    set_stream_session_stage,
)


def test_followup_is_database_backed_and_stage_gated() -> None:
    session = create_stream_session()

    assert append_stream_followup(session.session_id, "优先关注回撤风险") == (
        True,
        "",
        200,
    )
    reloaded = get_stream_session(session.session_id)
    assert reloaded is not None
    assert reloaded.operator_notes == ["优先关注回撤风险"]

    set_stream_session_stage(session.session_id, "generating")
    ok, _message, status = append_stream_followup(
        session.session_id,
        "这条不应写入",
    )
    assert ok is False
    assert status == 409
    assert get_stream_session(session.session_id).operator_notes == [
        "优先关注回撤风险"
    ]


def test_session_is_isolated_by_user() -> None:
    session = create_stream_session()
    other_token = set_request_user_id(2)
    try:
        assert get_stream_session(session.session_id) is None
        ok, _message, status = append_stream_followup(
            session.session_id,
            "越权追加",
        )
        assert ok is False
        assert status == 404
        delete_stream_session(session.session_id)
    finally:
        reset_request_user_id(other_token)

    assert get_stream_session(session.session_id) is not None


def test_expired_session_fails_closed() -> None:
    session = create_stream_session()
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with _connect() as connection:
        connection.execute(
            "UPDATE stream_sessions SET expires_at = ? WHERE session_id = ?",
            (expired, session.session_id),
        )

    assert get_stream_session(session.session_id) is None
    ok, _message, status = append_stream_followup(session.session_id, "late")
    assert ok is False
    assert status == 404
