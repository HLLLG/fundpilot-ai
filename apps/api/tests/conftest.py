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
        "app.services.akshare_spot_client.fetch_boards_via_akshare",
        lambda **_kwargs: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_boards_via_akshare",
        lambda **_kwargs: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_akshare_board_records",
        lambda _board_type: [],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_board_records",
        lambda _board_type: [],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_clist_theme_metrics_by_code",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_kline_close_percent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot._load_theme_spot_changes",
        lambda: {},
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_canonical_daily_kline_series",
        lambda *_args, **_kwargs: [],
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
        "app.services.fund_nav_service.fetch_fund_nav_history",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_rank",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.dip_drop_scanner.build_dip_pool_for_sectors",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "sector_label": "半导体",
                "dip_drop_percent": -5.2,
                "rebound_score": 72.0,
                "rebound_signals": [{"id": "two_day_reversal_up", "label": "近两日先跌后涨"}],
            },
            {
                "fund_code": "015945",
                "fund_name": "易方达国防军工",
                "sector_label": "国防军工",
                "dip_drop_percent": -4.1,
                "rebound_score": 65.0,
                "rebound_signals": [],
            },
        ],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_board_daily_kline_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history._fetch_flow_history_via_httpx",
        lambda *_args, **_kwargs: [],
    )
    # API routes import this symbol at module load; patching the service alone is not enough.
    monkeypatch.setattr(
        "app.main.build_sector_heat_ranking",
        lambda: list(_STUB_SECTOR_HEAT),
    )
    monkeypatch.setattr(
        "app.main.build_sector_heat_ranking_for_ui",
        lambda: list(_STUB_SECTOR_HEAT),
    )
    monkeypatch.setattr(
        "app.main.get_theme_board_snapshot",
        lambda **_kwargs: {
            "trade_date": "2026-06-17",
            "session_kind": "trading_day_intraday",
            "available": True,
            "from_cache": True,
            "stale": False,
            "refreshed_at": "2026-06-17T06:00:00+00:00",
            "message": None,
            "sort": _kwargs.get("sort", "change"),
            "items": [
                {
                    "sector_label": "商业航天",
                    "board_kind": "concept",
                    "change_1d_percent": 2.78,
                    "held_fund_count": 0,
                    "in_portfolio": False,
                    "rank": 1,
                },
                {
                    "sector_label": "电子",
                    "board_kind": "industry",
                    "change_1d_percent": 1.21,
                    "held_fund_count": 0,
                    "in_portfolio": False,
                    "rank": 2,
                },
            ],
        },
    )

    # 美股概览：避免子进程拉取真实期货 / 外汇源（需求 7 stub，任务 7.1）。
    monkeypatch.setattr(
        "app.services.us_futures_client.fetch_us_index_futures",
        lambda: [
            {
                "symbol": "NASDAQ_FUT",
                "display_name": "纳斯达克",
                "last_price": 19850.5,
                "change_percent": 0.62,
                "quote_time": "2026-06-17T08:12:00-04:00",
            },
            {
                "symbol": "SP500_FUT",
                "display_name": "标普500",
                "last_price": 5510.25,
                "change_percent": 0.41,
                "quote_time": "2026-06-17T08:12:00-04:00",
            },
            {
                "symbol": "DOW_FUT",
                "display_name": "道琼斯",
                "last_price": 40120.0,
                "change_percent": 0.28,
                "quote_time": "2026-06-17T08:12:00-04:00",
            },
        ],
    )
    monkeypatch.setattr("app.services.us_market_service.fetch_us_index_spot", lambda: None)
    monkeypatch.setattr(
        "app.services.us_market_service.fetch_fund_estimates_for_codes",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "app.services.us_market_service.fetch_stock_changes_for_holdings",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "app.services.us_market_service.load_qdii_holdings_batch",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "app.services.us_forex_client.fetch_usd_cny",
        lambda: {
            "last_price": 6.8096,
            "change_percent": -0.02,
            "quote_time": "2026-06-17",
            "source": "currency_boc_safe",
            "stale": False,
            "frequency": "daily",
        },
    )
    # API 路由在模块加载时导入该符号；提供确定性 snapshot stub 供 smoke 测试。
    from app.models import (
        UsdCnyQuote,
        UsFuturesQuote,
        UsMarketSnapshot,
    )

    def _stub_us_market_snapshot(**_kwargs):
        return UsMarketSnapshot(
            session_kind="pre_market",
            session_label="盘前交易中",
            et_date="2026-06-17",
            updated_at="2026-06-17T08:12:30-04:00",
            futures=[
                UsFuturesQuote(
                    symbol="NASDAQ_FUT",
                    display_name="纳斯达克",
                    last_price=19850.5,
                    change_percent=0.62,
                    quote_time="2026-06-17T08:12:00-04:00",
                    status="ok",
                ),
                UsFuturesQuote(
                    symbol="SP500_FUT",
                    display_name="标普500",
                    last_price=5510.25,
                    change_percent=0.41,
                    quote_time="2026-06-17T08:12:00-04:00",
                    status="ok",
                ),
                UsFuturesQuote(
                    symbol="DOW_FUT",
                    display_name="道琼斯",
                    last_price=40120.0,
                    change_percent=0.28,
                    quote_time="2026-06-17T08:12:00-04:00",
                    status="ok",
                ),
            ],
            usd_cny=UsdCnyQuote(
                last_price=6.8096,
                change_percent=-0.02,
                quote_time="2026-06-17",
                status="ok",
            ),
            qdii=[],
            qdii_status="unavailable",
            futures_status="ok",
            forex_status="ok",
            available=True,
            from_cache=False,
            stale=False,
            message=None,
        )

    monkeypatch.setattr("app.main.get_us_market_snapshot", _stub_us_market_snapshot)

    def _stub_dip_radar_build(**_kwargs):
        return {
            "refreshed_at": "2026-06-17T06:00:00+00:00",
            "trade_date": "2026-06-17",
            "lookback_days": _kwargs.get("lookback_days", 5),
            "fee_break_even_percent": 2.5,
            "items": [
                {
                    "fund_code": "519674",
                    "fund_name": "银河创新成长",
                    "sector_label": "半导体",
                    "dip_drop_percent": -5.2,
                    "rebound_score": 72.0,
                    "rebound_signals": [{"id": "two_day_reversal_up", "label": "近两日先跌后涨"}],
                    "rank": 1,
                }
            ],
            "sector_dip_leaders": [
                {
                    "sector_label": "半导体",
                    "avg_dip_drop_percent": -5.2,
                    "fund_count": 1,
                    "min_dip_drop_percent": -5.2,
                }
            ],
            "available": True,
            "from_cache": False,
            "stale": False,
            "session_kind": "trading_day_intraday",
            "message": None,
        }

    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.build_dip_radar_snapshot",
        _stub_dip_radar_build,
    )


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "pytest.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    monkeypatch.setenv("FUND_AI_OCR_PRELOAD", "false")
    monkeypatch.setenv("FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_THEME_BOARD_REFRESH_ENABLED", "false")
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
