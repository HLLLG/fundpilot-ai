import json

from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.database import list_report_chat_messages
from tests.conftest import auth_client_for_db, authenticated_test_client


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


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_report_chat_history_empty(tmp_path, monkeypatch):
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.get(f"/api/reports/{report_id}/chat")
    assert response.status_code == 200
    assert response.json()["messages"] == []


def test_report_chat_stream_offline_persists_messages(tmp_path, monkeypatch):
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.post(
        f"/api/reports/{report_id}/chat",
        json={"message": "015608 今天适合加仓吗？"},
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert any(event["type"] == "user_message" for event in events)
    assert any(event["type"] == "token" for event in events)
    done_events = [event for event in events if event["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["message"]["role"] == "assistant"
    assert "API Key" in done_events[0]["message"]["content"]

    from app.request_context import reset_request_user_id, set_request_user_id

    user_id = client.get("/api/auth/me").json()["id"]
    ctx = set_request_user_id(user_id)
    try:
        stored = list_report_chat_messages(report_id)
    finally:
        reset_request_user_id(ctx)
    assert len(stored) == 2
    assert stored[0]["role"] == "user"
    assert stored[0]["content"] == "015608 今天适合加仓吗？"
    assert stored[1]["role"] == "assistant"


def test_report_chat_markdown_export(tmp_path, monkeypatch):
    client, report_id = _create_report(tmp_path, monkeypatch)
    client.post(
        f"/api/reports/{report_id}/chat",
        json={"message": "测试问题", "chat_mode": "fast"},
    )
    response = client.get(f"/api/reports/{report_id}/chat/markdown")
    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert "报告追问记录" in markdown
    assert "测试问题" in markdown


def test_report_chat_deep_mode_offline(tmp_path, monkeypatch):
    client, report_id = _create_report(tmp_path, monkeypatch)
    response = client.post(
        f"/api/reports/{report_id}/chat",
        json={"message": "查一下半导体最新新闻", "chat_mode": "deep"},
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert any(event.get("type") == "status" for event in events)


def test_report_chat_unknown_report_returns_404(tmp_path, monkeypatch):
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    response = client.post(
        "/api/reports/missing-id/chat",
        json={"message": "hello"},
    )
    assert response.status_code == 404
