from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app
from tests.conftest import register_and_login


def test_register_and_login_flow():
    client = TestClient(app)
    email = f"auth-{uuid4().hex[:8]}@example.com"
    password = "SecurePass1!"

    register = client.post(
        "/api/auth/register",
        json={"userAccount": email, "password": password, "username": "投研A"},
    )
    assert register.status_code == 200
    body = register.json()
    assert body["accessToken"]
    assert body["user"]["userAccount"] == email
    assert body["user"]["username"] == "投研A"

    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['accessToken']}"},
    )
    assert me.status_code == 200
    assert me.json()["userAccount"] == email

    login = client.post(
        "/api/auth/login",
        json={"userAccount": email, "password": password},
    )
    assert login.status_code == 200
    assert login.json()["accessToken"]


def test_duplicate_register_rejected():
    client = TestClient(app)
    email = f"dup-{uuid4().hex[:8]}@example.com"
    payload = {"userAccount": email, "password": "SecurePass1!", "username": "U"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    dup = client.post("/api/auth/register", json=payload)
    assert dup.status_code == 400


def test_portfolio_holdings_requires_auth():
    client = TestClient(app)
    response = client.get("/api/portfolio/holdings")
    assert response.status_code == 401


def test_user_data_isolation():
    client = TestClient(app)
    token_a = register_and_login(client, email=f"a-{uuid4().hex[:8]}@example.com")
    token_b = register_and_login(client, email=f"b-{uuid4().hex[:8]}@example.com")

    save = client.put(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "style": "稳健",
            "horizon": "半年",
            "max_drawdown_percent": 8,
            "concentration_limit_percent": 35,
            "prefer_dca": True,
            "avoid_chasing": True,
        },
    )
    assert save.status_code == 200

    missing = client.get(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert missing.status_code == 200
    assert missing.json()["style"] == "稳健"

    found = client.get(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert found.status_code == 200
    assert found.json()["style"] == "稳健"


def test_wechat_login_with_callcontainer_openid_header(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_ENV_ID", "fundpilot-ai-test-env")
    refresh_settings()
    client = TestClient(app)
    response = client.post(
        "/api/auth/wechat-login",
        json={},
        headers={
            "X-Wx-Openid": "oPytest-openid-001",
            "X-Wx-Env-Id": "fundpilot-ai-test-env",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"]
    assert body["user"]["wechatBound"] is True

    second = client.post(
        "/api/auth/wechat-login",
        json={},
        headers={
            "X-Wx-Openid": "oPytest-openid-001",
            "X-Wx-Env-Id": "fundpilot-ai-test-env",
        },
    )
    assert second.status_code == 200
    assert second.json()["user"]["id"] == body["user"]["id"]

def _enable_wechat_dev_mode(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_AUTH_DEV_MODE", "true")
    refresh_settings()


def test_link_email_merges_wechat_into_email_account(monkeypatch):
    _enable_wechat_dev_mode(monkeypatch)
    client = TestClient(app)
    email = f"link-{uuid4().hex[:8]}@example.com"
    password = "SecurePass1!"

    register = client.post(
        "/api/auth/register",
        json={"userAccount": email, "password": password, "username": "邮箱用户"},
    )
    assert register.status_code == 200
    token_email = register.json()["accessToken"]

    # 邮箱账号下放一份可识别的风控画像，用于验证关联后数据可见
    profile_payload = {
        "style": "稳健",
        "horizon": "半年",
        "max_drawdown_percent": 12,
        "concentration_limit_percent": 35,
        "prefer_dca": True,
        "avoid_chasing": True,
    }
    saved = client.put(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_email}"},
        json=profile_payload,
    )
    assert saved.status_code == 200

    # 微信登录 → 新建空占位账号
    openid = f"openid-{uuid4().hex[:10]}"
    wx_login = client.post("/api/auth/wechat-login", json={"cloudbaseUid": openid})
    assert wx_login.status_code == 200, wx_login.text
    token_wx = wx_login.json()["accessToken"]

    wx_me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token_wx}"}
    )
    assert wx_me.status_code == 200
    assert wx_me.json()["userAccount"].endswith("@wechat.fundpilot")

    # 占位账号看不到邮箱账号的画像（默认 8，而非 12）
    wx_profile = client.get(
        "/api/investor-profile", headers={"Authorization": f"Bearer {token_wx}"}
    )
    assert wx_profile.status_code == 200
    assert wx_profile.json()["max_drawdown_percent"] != 12

    # 关联邮箱账号
    link = client.post(
        "/api/auth/link-email",
        headers={"Authorization": f"Bearer {token_wx}"},
        json={"userAccount": email, "password": password},
    )
    assert link.status_code == 200, link.text
    linked = link.json()
    assert linked["user"]["userAccount"] == email
    assert linked["user"]["wechatBound"] is True
    token_linked = linked["accessToken"]

    # 关联后用新令牌可见邮箱账号的画像
    linked_profile = client.get(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_linked}"},
    )
    assert linked_profile.status_code == 200
    assert linked_profile.json()["max_drawdown_percent"] == 12

    # 再次微信登录（同一 openid）应命中邮箱账号，而不是新建占位账号
    wx_login2 = client.post("/api/auth/wechat-login", json={"cloudbaseUid": openid})
    assert wx_login2.status_code == 200, wx_login2.text
    assert wx_login2.json()["user"]["userAccount"] == email

    relogin_profile = client.get(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {wx_login2.json()['accessToken']}"},
    )
    assert relogin_profile.status_code == 200
    assert relogin_profile.json()["max_drawdown_percent"] == 12


def test_link_email_rejects_wrong_password(monkeypatch):
    _enable_wechat_dev_mode(monkeypatch)
    client = TestClient(app)
    email = f"link-wrong-{uuid4().hex[:8]}@example.com"
    client.post(
        "/api/auth/register",
        json={"userAccount": email, "password": "SecurePass1!", "username": "U"},
    )
    openid = f"openid-{uuid4().hex[:10]}"
    token_wx = client.post(
        "/api/auth/wechat-login", json={"cloudbaseUid": openid}
    ).json()["accessToken"]

    bad = client.post(
        "/api/auth/link-email",
        headers={"Authorization": f"Bearer {token_wx}"},
        json={"userAccount": email, "password": "WrongPass9!"},
    )
    assert bad.status_code == 400


def test_link_email_rejected_for_non_wechat_account(monkeypatch):
    _enable_wechat_dev_mode(monkeypatch)
    client = TestClient(app)
    email_a = f"link-a-{uuid4().hex[:8]}@example.com"
    email_b = f"link-b-{uuid4().hex[:8]}@example.com"
    token_a = register_and_login(client, email=email_a, password="SecurePass1!")
    client.post(
        "/api/auth/register",
        json={"userAccount": email_b, "password": "SecurePass1!", "username": "B"},
    )

    # 邮箱登录态不允许调用 link-email（只有微信占位账号可关联）
    rejected = client.post(
        "/api/auth/link-email",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"userAccount": email_b, "password": "SecurePass1!"},
    )
    assert rejected.status_code == 400
