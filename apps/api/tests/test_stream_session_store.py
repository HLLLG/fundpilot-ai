"""流式会话 store 与 followup 端点测试。"""

from __future__ import annotations

import pytest

from app.services.stream_session_store import (
    PRE_LLM_FOLLOWUP_STAGES,
    append_stream_followup,
    create_stream_session,
    delete_stream_session,
    set_stream_session_stage,
)


def test_append_followup_only_before_generating():
    session = create_stream_session()
    try:
        for stage in PRE_LLM_FOLLOWUP_STAGES:
            set_stream_session_stage(session.session_id, stage)
            ok, message, status = append_stream_followup(session.session_id, "关注电网设备")
            assert ok, message
            assert status == 200

        set_stream_session_stage(session.session_id, "generating")
        ok, message, status = append_stream_followup(session.session_id, "太晚了")
        assert not ok
        assert status == 409
        assert "生成" in message
    finally:
        delete_stream_session(session.session_id)


def test_append_followup_unknown_session():
    ok, message, status = append_stream_followup("missing", "hi")
    assert not ok
    assert status == 404
