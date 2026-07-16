"""POST /api/analyze/stream SSE 端点集成测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_client_for_db


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_analyze_stream_endpoint_emits_sse(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_modes: list[str] = []

    def fake_stream_analysis(request, *, user_id: int):
        captured_modes.append(request.analysis_mode)
        yield {"type": "session", "session_id": "sess-1"}
        yield {"type": "stage", "stage": "fund_data", "label": "拉取净值"}
        yield {"type": "skeleton", "fund_codes": ["519674"], "fund_names": ["银河"]}
        yield {"type": "done", "report_id": "r1", "report": {"id": "r1", "title": "t"}}

    monkeypatch.setattr(
        "app.main.stream_analysis",
        fake_stream_analysis,
    )
    client = auth_client_for_db(monkeypatch, tmp_path / "stream.db")
    payload = {
        "holdings": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "holding_amount": 10000,
            }
        ],
        "profile": {
            "decision_style": "conservative",
            "max_drawdown_percent": 15,
            "concentration_limit_percent": 30,
            "expected_investment_amount": 100000,
        },
        "analysis_mode": "fast",
    }
    with client.stream("POST", "/api/analyze/stream", json=payload) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    types = [e["type"] for e in events]
    assert types == ["session", "stage", "skeleton", "done"]
    assert events[-1]["report_id"] == "r1"
    assert captured_modes == ["deep"]
