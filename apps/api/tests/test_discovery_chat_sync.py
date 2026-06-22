"""Tests for the荐基 non-streaming /chat/sync endpoint and aggregate helper.

Covers:
  1. Unit: aggregate_chat_stream([token, token, done]) -> content == concatenated tokens,
     message == done's message.
  2. Unit: aggregate_chat_stream([error]) raises ValueError.
  3. Integration: Create a discovery report, POST to /chat/sync, assert JSON response
     shape {user_message, message, chat_mode}, assert messages stored in DB.
  4. Regression: Original SSE discovery chat route still returns streaming response.

Validates: Requirements 16.5
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.database import list_discovery_chat_messages, save_discovery_report
from app.models import FundDiscoveryReport
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.chat_aggregate import AggregatedChat, aggregate_chat_stream
from tests.conftest import auth_client_for_db, register_and_login

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discovery_report() -> FundDiscoveryReport:
    """Create a minimal but valid FundDiscoveryReport for test setup."""
    return FundDiscoveryReport(
        title="测试荐基报告",
        summary="自动化测试报告",
        candidate_pool=[
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长混合A",
                "sector_label": "半导体",
            }
        ],
        recommendations=[],
    )


def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE event-stream body into a list of event dicts."""
    events: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    return events


# ---------------------------------------------------------------------------
# 1. Unit: aggregate correctly concatenates tokens and captures done message
# ---------------------------------------------------------------------------


def test_aggregate_concatenates_tokens_and_captures_done_message():
    """aggregate_chat_stream([token, token, done]) content equals joined tokens,
    message equals the done event's message object."""
    tokens = ["今天", "适合", "加仓"]
    done_message = {"role": "assistant", "content": "今天适合加仓", "id": 1}

    events = [
        {"type": "token", "content": t} for t in tokens
    ]
    events.append(
        {"type": "done", "message": done_message, "chat_mode": "discovery", "model": "deepseek-chat"}
    )

    result = aggregate_chat_stream(events)

    assert isinstance(result, AggregatedChat)
    assert result.content == "".join(tokens)
    assert result.message == done_message
    assert result.chat_mode == "discovery"


# ---------------------------------------------------------------------------
# 2. Unit: aggregate raises ValueError on error event
# ---------------------------------------------------------------------------


def test_aggregate_raises_value_error_on_error_event():
    """aggregate_chat_stream([error]) raises ValueError with the error message."""
    error_message = "DeepSeek API 调用失败"
    events = [{"type": "error", "message": error_message}]

    with pytest.raises(ValueError) as exc_info:
        aggregate_chat_stream(events)

    assert str(exc_info.value) == error_message


# ---------------------------------------------------------------------------
# 3. Integration: create a discovery report and POST to /chat/sync
# ---------------------------------------------------------------------------


def test_discovery_chat_sync_returns_json_and_stores_messages(tmp_path, monkeypatch):
    """Integration: POST /api/fund-discovery/reports/{id}/chat/sync returns
    JSON with {user_message, message, chat_mode} and persists both messages to DB."""
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")

    # Get the user id for DB assertions
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    user_id = me.json()["id"]

    # Create a discovery report directly in the DB under the test user's context
    ctx = set_request_user_id(user_id)
    try:
        report = _make_discovery_report()
        save_discovery_report(report)
        report_id = report.id
    finally:
        reset_request_user_id(ctx)

    # POST to the sync endpoint
    response = client.post(
        f"/api/fund-discovery/reports/{report_id}/chat/sync",
        json={"message": "519674 今天适合加仓吗？", "chat_mode": "fast"},
    )

    assert response.status_code == 200
    body = response.json()

    # Assert response shape
    assert "user_message" in body
    assert "message" in body
    assert "chat_mode" in body

    # user_message should be set (the assistant always saves and echoes it)
    # or can be None on offline mode - just check message is present
    assistant_msg = body["message"]
    assert assistant_msg is not None
    assert assistant_msg.get("role") == "assistant"
    assert isinstance(assistant_msg.get("content"), str)
    assert len(assistant_msg.get("content", "")) > 0

    # Assert messages were stored in DB
    ctx = set_request_user_id(user_id)
    try:
        stored = list_discovery_chat_messages(report_id)
    finally:
        reset_request_user_id(ctx)

    assert len(stored) == 2
    assert stored[0]["role"] == "user"
    assert stored[0]["content"] == "519674 今天适合加仓吗？"
    assert stored[1]["role"] == "assistant"


def test_discovery_chat_sync_returns_404_for_missing_report(tmp_path, monkeypatch):
    """Integration: /chat/sync returns 404 when the discovery report does not exist."""
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")

    response = client.post(
        "/api/fund-discovery/reports/nonexistent-report-id/chat/sync",
        json={"message": "hello"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4. Regression: original SSE discovery chat route still returns streaming response
# ---------------------------------------------------------------------------


def test_discovery_chat_sse_route_still_streams(tmp_path, monkeypatch):
    """Regression: the original SSE route POST /api/fund-discovery/reports/{id}/chat
    still returns text/event-stream (Web regression - SSE must be unchanged)."""
    # Blank out the API key so the offline path is taken (no live network calls).
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    from app.config import refresh_settings
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    user_id = me.json()["id"]

    # Create a discovery report in the DB
    ctx = set_request_user_id(user_id)
    try:
        report = _make_discovery_report()
        save_discovery_report(report)
        report_id = report.id
    finally:
        reset_request_user_id(ctx)

    response = client.post(
        f"/api/fund-discovery/reports/{report_id}/chat",
        json={"message": "这个报告有什么亮点？", "chat_mode": "fast"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Parse SSE events and verify the stream structure
    events = _parse_sse_events(response.text)
    assert len(events) > 0

    # Must contain a done or error event
    event_types = {e.get("type") for e in events}
    assert event_types & {"done", "error"}, (
        f"Expected 'done' or 'error' event in SSE stream, got: {event_types}"
    )
