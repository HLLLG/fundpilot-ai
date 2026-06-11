"""Shared test constants and auth fixtures."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app

PYTEST_VALID_DEEPSEEK_KEY = "fundpilot-pytest-only-not-a-real-api-key-ok"
PYTEST_PLACEHOLDER_DEEPSEEK_KEY = "replace-me-not-a-real-deepseek-key"
PYTEST_JWT_SECRET = "pytest-jwt-secret-key-32-chars-minimum!!"


@pytest.fixture(autouse=True)
def _clear_trade_calendar_cache():
    from app.services.trade_calendar_cache import get_trade_date_set

    get_trade_date_set.cache_clear()
    yield
    get_trade_date_set.cache_clear()


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "pytest.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    refresh_settings()
    yield


@pytest.fixture(autouse=True)
def _default_user_context(_auth_env):
    from app.database import _connect
    from app.request_context import reset_request_user_id, set_request_user_id

    _connect()
    token = set_request_user_id(1)
    yield
    reset_request_user_id(token)


def auth_client_for_db(monkeypatch, db_path) -> TestClient:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    refresh_settings()
    client = TestClient(app)
    token = register_and_login(client)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def register_and_login(
    client: TestClient,
    *,
    email: str | None = None,
    password: str = "Test1234!",
    username: str = "测试用户",
) -> str:
    account = email or f"user-{uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "userAccount": account,
            "password": password,
            "username": username,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def authenticated_test_client() -> TestClient:
    client = TestClient(app)
    token = register_and_login(client)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def auth_client() -> TestClient:
    return authenticated_test_client()


@pytest.fixture
def client(auth_client: TestClient) -> TestClient:
    return auth_client
