"""GET /api/diagnostics/market-breadth 端点集成测试（M5 前端接入前置）。"""

from __future__ import annotations

import pytest

from tests.conftest import auth_client_for_db


def test_market_breadth_endpoint_returns_service_payload(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_payload = {
        "available": True,
        "trade_date": "2026-07-02",
        "sentiment_level": "中性",
        "sentiment_level_change": 0,
        "limit_up_count": 32,
        "limit_down_count": 41,
        "interpretation": "市场情绪中性。",
    }
    monkeypatch.setattr("app.main.build_market_breadth_signal", lambda: fake_payload)

    client = auth_client_for_db(monkeypatch, tmp_path / "market-breadth.db")
    response = client.get("/api/diagnostics/market-breadth")

    assert response.status_code == 200
    assert response.json() == fake_payload


def test_market_breadth_endpoint_requires_auth(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """该端点未加入 middleware 的公开路径白名单，未带 JWT 应返回 401（与其余诊断端点一致）。"""
    from fastapi.testclient import TestClient

    from app.config import refresh_settings
    from app.main import app
    from tests.conftest import PYTEST_JWT_SECRET

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "market-breadth-noauth.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    refresh_settings()

    unauthenticated_client = TestClient(app)
    response = unauthenticated_client.get("/api/diagnostics/market-breadth")
    assert response.status_code == 401



