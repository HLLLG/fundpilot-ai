"""Shared test constants and auth fixtures."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app

PYTEST_VALID_DEEPSEEK_KEY = "fundpilot-pytest-only-not-a-real-api-key-ok"
PYTEST_PLACEHOLDER_DEEPSEEK_KEY = "replace-me-not-a-real-deepseek-key"
PYTEST_JWT_SECRET = "pytest-jwt-secret-key-32-chars-minimum!!"

# Weekdays referenced across unit tests; avoids AkShare subprocess in CI.
PYTEST_TRADE_DATES = frozenset(
    {
        "2026-01-02",
        "2026-01-03",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-10",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
        "2026-06-08",
        "2026-06-09",
        "2026-06-10",
        "2026-06-11",
        "2026-06-12",
    }
)

_STUB_SECTOR_HEAT = [
    {"sector_label": "半导体", "heat_score": 1.0, "change_1d_percent": 1.0},
]


@pytest.fixture(autouse=True)
def _offline_external_data(monkeypatch):
    """Keep unit tests offline: no AkShare subprocess fetches for calendar or fund names."""
    monkeypatch.setattr(
        "app.services.trade_calendar_cache._fetch_dates_subprocess",
        lambda: PYTEST_TRADE_DATES,
    )
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        lambda: [],
    )


@pytest.fixture(autouse=True)
def _stub_market_data_fetches(monkeypatch):
    """Avoid live East Money / AkShare calls during API and pipeline tests."""

    def _empty_spot_boards(**_kwargs):
        return {}, "stub"

    def _passthrough_sector_refresh(holdings, **_kwargs):
        serialized = [
            holding.model_dump() if hasattr(holding, "model_dump") else holding
            for holding in holdings
        ]
        return {
            "ok": True,
            "message": "stub",
            "holdings": serialized,
            "items": [],
            "summary": {"matched": 0, "unresolved": 0, "needs_mapping": 0},
        }

    monkeypatch.setattr(
        "app.services.akshare_spot_client._fetch_board_kind_subprocess",
        lambda _kind: {},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_eastmoney_boards",
        _empty_spot_boards,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_akshare",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_relay",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_browser_command",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_diagnostic.fetch_eastmoney_boards",
        _empty_spot_boards,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_diagnostic.fetch_boards_via_akshare",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.sector_canonical.fetch_eastmoney_kline_close_percent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_canonical.fetch_eastmoney_quote_by_secid",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.fetch_eastmoney_intraday_trends",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_on_demand.fetch_eastmoney_sector_quote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.refresh_holdings_sector_quotes",
        _passthrough_sector_refresh,
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_nav_history",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_rank",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_board_daily_kline_series",
        lambda *_args, **_kwargs: [],
    )
    # API routes import this symbol at module load; patching the service alone is not enough.
    monkeypatch.setattr(
        "app.main.build_sector_heat_ranking",
        lambda: list(_STUB_SECTOR_HEAT),
    )


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "pytest.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    monkeypatch.setenv("FUND_AI_OCR_PRELOAD", "false")
    monkeypatch.setenv("FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", "false")
    refresh_settings()
    yield


@pytest.fixture(autouse=True)
def _default_user_context(_auth_env):
    from app.request_context import reset_request_user_id, set_request_user_id

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
