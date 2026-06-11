from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import PYTEST_JWT_SECRET, register_and_login


def test_wechat_login_dev_mode_creates_user(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_AUTH_DEV_MODE", "true")
    from app.config import refresh_settings

    refresh_settings()
    client = TestClient(app)
    uid = "cloudbase-test-uid-001"
    response = client.post(
        "/api/auth/wechat-login",
        json={"cloudbaseUid": uid, "username": "小程序用户"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"]
    assert body["user"]["username"] == "小程序用户"

    holdings = client.get(
        "/api/portfolio/holdings",
        headers={"Authorization": f"Bearer {body['accessToken']}"},
    )
    assert holdings.status_code == 200

    again = client.post("/api/auth/wechat-login", json={"cloudbaseUid": uid})
    assert again.status_code == 200
    assert again.json()["user"]["id"] == body["user"]["id"]


def test_bind_wechat_links_email_account(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_AUTH_DEV_MODE", "true")
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    from app.config import refresh_settings

    refresh_settings()
    client = TestClient(app)
    email_token = register_and_login(client)
    wx_uid = "cloudbase-bind-uid-002"

    bind = client.post(
        "/api/auth/bind-wechat",
        headers={"Authorization": f"Bearer {email_token}"},
        json={"cloudbaseUid": wx_uid},
    )
    assert bind.status_code == 200
    assert bind.json()["wechatBound"] is True

    wx_login = client.post("/api/auth/wechat-login", json={"cloudbaseUid": wx_uid})
    assert wx_login.status_code == 200
    assert wx_login.json()["user"]["userAccount"] == bind.json()["userAccount"]
