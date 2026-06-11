from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

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
    assert missing.status_code == 404

    found = client.get(
        "/api/investor-profile",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert found.status_code == 200
    assert found.json()["style"] == "稳健"
