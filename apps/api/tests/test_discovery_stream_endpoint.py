"""POST /api/fund-discovery/stream SSE 端点集成测试。"""

from __future__ import annotations

import json
import logging

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


def test_discovery_stream_endpoint_emits_sse(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_modes: list[str] = []

    def fake_stream_discovery(request, *, user_id: int, stop_event=None):
        captured_modes.append(request.analysis_mode)
        yield {"type": "stage", "stage": "sector_heat", "label": "计算板块热度"}
        yield {"type": "skeleton", "fund_codes": ["161725"], "fund_names": ["白酒"]}
        yield {"type": "done", "report_id": "d1", "report": {"id": "d1", "title": "t"}}

    monkeypatch.setattr(
        "app.main.stream_discovery",
        fake_stream_discovery,
    )
    client = auth_client_for_db(monkeypatch, tmp_path / "disc_stream.db")
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
        "focus_sectors": ["半导体"],
    }
    with client.stream("POST", "/api/fund-discovery/stream", json=payload) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert response.headers.get("x-accel-buffering") == "no"
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    types = [e["type"] for e in events]
    assert types == ["stage", "stage", "skeleton", "done"]
    assert events[0] == {
        "type": "stage",
        "stage": "connected",
        "label": "已连接服务端，正在启动扫描…",
    }
    assert events[-1]["report_id"] == "d1"
    assert captured_modes == ["deep"]


def test_discovery_stream_endpoint_logs_missing_terminal_event(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_stream_discovery(request, *, user_id: int, stop_event=None):
        assert request.analysis_mode == "deep"
        assert user_id > 0
        yield {"type": "stage", "stage": "generating", "label": "AI 分析中…"}

    monkeypatch.setattr("app.main.stream_discovery", fake_stream_discovery)
    client = auth_client_for_db(monkeypatch, tmp_path / "disc_stream_cancelled.db")
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
    }

    with caplog.at_level(logging.WARNING, logger="app.main"):
        with client.stream("POST", "/api/fund-discovery/stream", json=payload) as response:
            assert response.status_code == 200
            assert _parse_sse_events("".join(response.iter_text())) == [
                {
                    "type": "stage",
                    "stage": "connected",
                    "label": "已连接服务端，正在启动扫描…",
                },
                {"type": "stage", "stage": "generating", "label": "AI 分析中…"},
            ]

    assert "fund_discovery_stream_ended_without_terminal" in caplog.text
    assert "stage=generating" in caplog.text


def test_discovery_stream_loads_empty_holdings_after_prelude(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls: list[bool] = []

    def fake_load(*, fetch_benchmark: bool = True):
        load_calls.append(fetch_benchmark)
        return ([], "none", None, None)

    def fake_stream_discovery(request, *, user_id: int, stop_event=None):
        assert request.holdings == []
        yield {"type": "done", "report_id": "d1", "report": {"id": "d1"}}

    monkeypatch.setattr("app.main.load_persisted_holdings", fake_load)
    monkeypatch.setattr("app.main.stream_discovery", fake_stream_discovery)
    client = auth_client_for_db(monkeypatch, tmp_path / "disc_stream_empty.db")
    payload = {
        "holdings": [],
        "profile": {
            "decision_style": "conservative",
            "max_drawdown_percent": 15,
            "concentration_limit_percent": 30,
            "expected_investment_amount": 100000,
        },
    }

    with client.stream("POST", "/api/fund-discovery/stream", json=payload) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert events[0]["stage"] == "connected"
    assert load_calls == [False]
