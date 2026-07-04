"""GET /api/diagnostics/shadow-escalation-digest 端点集成测试（M6.3）。"""

from __future__ import annotations

import pytest

from tests.conftest import auth_client_for_db


def test_shadow_escalation_digest_endpoint_returns_service_payload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_payload = {
        "available": True,
        "lookback_days": 7,
        "trigger_count": 2,
        "by_sector": {"半导体": 2},
        "summary": "近 7 天共触发 2 次灰度升级判定。",
    }
    monkeypatch.setattr(
        "app.main.build_shadow_escalation_digest", lambda **_kwargs: fake_payload
    )

    client = auth_client_for_db(monkeypatch, tmp_path / "shadow-digest.db")
    response = client.get("/api/diagnostics/shadow-escalation-digest")

    assert response.status_code == 200
    assert response.json() == fake_payload


def test_shadow_escalation_digest_endpoint_requires_auth(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.config import refresh_settings
    from app.main import app
    from tests.conftest import PYTEST_JWT_SECRET

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "shadow-digest-noauth.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    refresh_settings()

    unauthenticated_client = TestClient(app)
    response = unauthenticated_client.get("/api/diagnostics/shadow-escalation-digest")
    assert response.status_code == 401


def test_shadow_escalation_digest_endpoint_clamps_days_param(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """days 参数应被夹在 [1, 30] 区间内（防御性：避免恶意/误传超大窗口触发全表扫描）。"""
    captured: dict = {}

    def _fake_build(*, lookback_days: int):
        captured["lookback_days"] = lookback_days
        return {"available": True, "lookback_days": lookback_days, "trigger_count": 0}

    monkeypatch.setattr("app.main.build_shadow_escalation_digest", _fake_build)

    client = auth_client_for_db(monkeypatch, tmp_path / "shadow-digest-clamp.db")
    response = client.get("/api/diagnostics/shadow-escalation-digest?days=999")

    assert response.status_code == 200
    assert captured["lookback_days"] == 30
