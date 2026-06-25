"""POST /api/analyze/stream/{session_id}/followup 集成测试。"""

from __future__ import annotations

import pytest

from app.services.stream_session_store import create_stream_session, delete_stream_session
from tests.conftest import auth_client_for_db


def test_stream_followup_endpoint(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = create_stream_session()
    try:
        client = auth_client_for_db(monkeypatch, tmp_path / "followup.db")
        response = client.post(
            f"/api/analyze/stream/{session.session_id}/followup",
            json={"message": "请重点看半导体"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert session.operator_notes == ["请重点看半导体"]
    finally:
        delete_stream_session(session.session_id)


def test_stream_followup_rejects_after_generating(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = create_stream_session()
    try:
        from app.services.stream_session_store import set_stream_session_stage

        set_stream_session_stage(session.session_id, "generating")
        client = auth_client_for_db(monkeypatch, tmp_path / "followup2.db")
        response = client.post(
            f"/api/analyze/stream/{session.session_id}/followup",
            json={"message": "晚了"},
        )
        assert response.status_code == 409
    finally:
        delete_stream_session(session.session_id)
