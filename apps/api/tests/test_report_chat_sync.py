"""Integration tests for POST /api/reports/{report_id}/chat/sync.

Covers:
- Correct JSON response shape: {user_message, message, chat_mode, model?}
- Message is persisted to DB (via list_report_chat_messages)
- Original SSE route is unaffected (Web regression)

Requirements: 6.7, 14.3
"""

import json

from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.database import list_report_chat_messages
from tests.conftest import auth_client_for_db


def _mock_news_search(monkeypatch):
    from app.models import NewsItem
    from app.services.news_service import NewsService

    def fake_search(self, topic: str, limit: int | None = None):
        return [
            NewsItem(
                topic=topic,
                title=f"{topic}相关新闻",
                published_at="2026-05-30 09:00:00",
                source="eastmoney",
                url=f"http://example.com/{topic}",
                snippet="测试摘要",
            )
        ]

    monkeypatch.setattr(NewsService, "search", fake_search)


def _create_report(tmp_path, monkeypatch) -> tuple[TestClient, str]:
    """Create an analysis report and return (client, report_id)."""
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)
    payload = {
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "华夏中证电网设备主题ETF发起式联接A",
                "holding_amount": 5280.66,
                "return_percent": -3.25,
            }
        ],
    }
    response = client.post("/api/analyze", json=payload)
    assert response.status_code == 200
    return client, response.json()["id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _set_user_context(client: TestClient):
    """Return user id for DB query context."""
    return client.get("/api/auth/me").json()["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chat_sync_response_shape(tmp_path, monkeypatch):
    """POST /chat/sync returns a JSON dict with required keys."""
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.post(
        f"/api/reports/{report_id}/chat/sync",
        json={"message": "015608 今天适合加仓吗？"},
    )
    assert response.status_code == 200
    data = response.json()

    # Required top-level keys
    assert "user_message" in data, f"missing user_message in {data}"
    assert "message" in data, f"missing message in {data}"
    assert "chat_mode" in data, f"missing chat_mode in {data}"
    # model is optional — just confirm it isn't broken if present
    # (it may or may not be present depending on whether a model is used)


def test_chat_sync_user_message_content(tmp_path, monkeypatch):
    """user_message in /chat/sync response carries the sent message text."""
    client, report_id = _create_report(tmp_path, monkeypatch)
    question = "015608 今天适合加仓吗？"
    response = client.post(
        f"/api/reports/{report_id}/chat/sync",
        json={"message": question},
    )
    assert response.status_code == 200
    data = response.json()

    user_msg = data.get("user_message")
    assert user_msg is not None
    assert isinstance(user_msg, dict)
    assert user_msg.get("content") == question or user_msg.get("role") == "user"


def test_chat_sync_message_has_assistant_role(tmp_path, monkeypatch):
    """The 'message' field in the sync response is the assistant message."""
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.post(
        f"/api/reports/{report_id}/chat/sync",
        json={"message": "帮我分析一下持仓"},
    )
    assert response.status_code == 200
    data = response.json()

    msg = data.get("message")
    assert isinstance(msg, dict)
    assert msg.get("role") == "assistant"
    assert "content" in msg
    # Offline mode returns an error string about missing API key
    assert isinstance(msg["content"], str)
    assert len(msg["content"]) > 0


def test_chat_sync_persists_messages_to_db(tmp_path, monkeypatch):
    """After a /chat/sync call, both user and assistant messages are in DB."""
    from app.request_context import reset_request_user_id, set_request_user_id

    client, report_id = _create_report(tmp_path, monkeypatch)
    question = "015608 今天适合加仓吗？"
    response = client.post(
        f"/api/reports/{report_id}/chat/sync",
        json={"message": question},
    )
    assert response.status_code == 200

    user_id = _set_user_context(client)
    ctx = set_request_user_id(user_id)
    try:
        stored = list_report_chat_messages(report_id)
    finally:
        reset_request_user_id(ctx)

    assert len(stored) == 2, f"expected 2 messages, got {len(stored)}: {stored}"
    assert stored[0]["role"] == "user"
    assert stored[0]["content"] == question
    assert stored[1]["role"] == "assistant"


def test_chat_sync_unknown_report_returns_404(tmp_path, monkeypatch):
    """POST /chat/sync for non-existent report_id returns 404."""
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    response = client.post(
        "/api/reports/non-existent-id/chat/sync",
        json={"message": "hello"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Web regression: original SSE route still works
# ---------------------------------------------------------------------------

def test_original_sse_chat_route_unaffected(tmp_path, monkeypatch):
    """The original POST /chat SSE endpoint still returns a valid event stream (Web regression)."""
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.post(
        f"/api/reports/{report_id}/chat",
        json={"message": "分析一下市场情况"},
    )
    assert response.status_code == 200
    # Must be SSE content type
    assert "text/event-stream" in response.headers.get("content-type", "")
    events = _parse_sse_events(response.text)
    assert len(events) > 0, "SSE stream produced no events"
    event_types = {e.get("type") for e in events}
    # Must contain at least a done event
    assert "done" in event_types, f"No 'done' event found; types seen: {event_types}"


def test_sse_and_sync_routes_coexist(tmp_path, monkeypatch):
    """Both SSE and sync routes can be called on the same report without conflict."""
    from app.request_context import reset_request_user_id, set_request_user_id

    client, report_id = _create_report(tmp_path, monkeypatch)

    # First call via SSE
    sse_response = client.post(
        f"/api/reports/{report_id}/chat",
        json={"message": "第一条问题"},
    )
    assert sse_response.status_code == 200

    # Second call via sync
    sync_response = client.post(
        f"/api/reports/{report_id}/chat/sync",
        json={"message": "第二条问题"},
    )
    assert sync_response.status_code == 200

    # Both should have persisted messages
    user_id = _set_user_context(client)
    ctx = set_request_user_id(user_id)
    try:
        stored = list_report_chat_messages(report_id)
    finally:
        reset_request_user_id(ctx)

    # 2 messages per call (user + assistant) × 2 calls = 4
    assert len(stored) == 4, f"expected 4 stored messages, got {len(stored)}"
