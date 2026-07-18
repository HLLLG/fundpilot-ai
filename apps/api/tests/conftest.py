"""Shared test constants and auth fixtures."""

from __future__ import annotations

import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, refresh_settings
from app.main import app

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    monkeypatch.setattr(
        "app.services.fund_lookthrough_context.resolve_fund_holdings_snapshot_at_decision",
        lambda _code, **kwargs: {
            "status": "unavailable",
            "qualified": False,
            "reason_codes": ["pytest_store_only_snapshot_missing"],
            "decision_at": kwargs.get("decision_at").isoformat()
            if hasattr(kwargs.get("decision_at"), "isoformat")
            else None,
            "source": "append_only_store",
            "snapshot": None,
            "record": None,
        },
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

    def _verified_tradeability(codes, *, decision_at=None, **_kwargs):
        checked_at = (
            decision_at.isoformat()
            if hasattr(decision_at, "isoformat")
            else "2026-06-10T10:00:00+08:00"
        )
        return {
            str(code).zfill(6): {
                "schema_version": "fund_tradeability.v1",
                "fund_code": str(code).zfill(6),
                "data_status": "complete",
                "freshness": "fresh",
                "can_purchase": True,
                "purchase_state": "open",
                "purchase_status": "开放申购",
                "redemption_state": "open",
                "redemption_status": "开放赎回",
                "currency": "CNY",
                "minimum_purchase_yuan": 10.0,
                "minimum_initial_purchase_yuan": 10.0,
                "minimum_additional_purchase_yuan": 10.0,
                "daily_purchase_limit_yuan": None,
                "daily_purchase_limit_unlimited": True,
                "daily_purchase_limit_scope": "pytest_unlimited",
                "revalidation_required": True,
                "standard_purchase_fee_tiers": [
                    {
                        "condition": "小于100万元",
                        "min_amount_yuan": None,
                        "max_amount_yuan": 1_000_000.0,
                        "min_inclusive": True,
                        "max_inclusive": False,
                        "fee_type": "percent",
                        "fee_percent": 0.0,
                        "flat_fee_yuan": None,
                        "source_rate": "standard_undiscounted",
                    }
                ],
                "redemption_fee_tiers": [
                    {
                        "condition": "大于等于7天",
                        "min_days": 7,
                        "max_days": None,
                        "fee_percent": 0.0,
                    }
                ],
                "sales_service_fee_annual_percent": 0.0,
                "share_class_fee_status": "standard_upper_bound_available",
                "source_conflict": False,
                "missing_fields": [],
                "source_ids": ["pytest.tradeability"],
                "source_urls": [],
                "checked_at": checked_at,
                "fee_checked_at": checked_at,
                "fee_freshness": "fresh",
                "effective_at": checked_at,
            }
            for code in codes
        }

    def _qualified_discovery_risk(candidate_rows, _holdings_slim, **_kwargs):
        codes = sorted(
            {
                str(row.get("fund_code") or "").zfill(6)
                for row in candidate_rows
                if isinstance(row, dict) and row.get("fund_code")
            }
        )
        return {
            "schema_version": "discovery_risk_context.v1",
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "max_drawdown_percent_by_code": {code: 10.0 for code in codes},
            "covariance_by_code": {
                code: {
                    other: (0.04 if code == other else 0.0)
                    for other in codes
                }
                for code in codes
            },
            "positive_correlation_penalty_to_current_holdings_by_code": {
                code: 0.0 for code in codes
            },
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
    # sector_flow_divergence_backtest.py does `from ... import fetch_canonical_daily_kline_series`
    # (direct import), so patching only the source module above doesn't reach its own bound
    # reference; without this, discovery_pipeline's divergence-map call (M1.4) would attempt
    # live network during unit tests. Kline check runs first and short-circuits before any
    # flow-history fetch, so this single stub is sufficient to keep the whole path offline.
    monkeypatch.setattr(
        "app.services.sector_flow_divergence_backtest.fetch_canonical_daily_kline_series",
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
        "app.services.discovery_candidate_pool.resolve_fund_tradeability_profiles",
        _verified_tradeability,
    )
    monkeypatch.setattr(
        "app.services.discovery_allocation_service.build_discovery_risk_context",
        _qualified_discovery_risk,
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.resolve_fund_tradeability_profiles",
        _verified_tradeability,
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
        "app.services.akshare_subprocess.fetch_board_daily_kline_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history._fetch_flow_history_via_httpx",
        lambda *_args, **_kwargs: [],
    )
    # 上面的 httpx stub 返回空列表后，fetch_board_flow_series 会继续走 requests 库的
    # 真实网络兜底（带重试 + time.sleep），这是 build_holding_sector_opportunity_context
    # (via board flow prefetch) 在单测里另一处意外联网+耗时来源，直接 stub 顶层函数更彻底。
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.fetch_board_flow_series",
        lambda *_args, **_kwargs: [],
    )
    # refresh_theme_board_snapshot 刷新后会 fire-and-forget 一个 daemon 线程调用
    # prefetch_board_flow_histories，对每个板块 code 串行 sleep(0.75s) 限速预热资金流
    # 历史缓存——线上是合理的节流设计，但在单测里这是一个游离的背景线程，即使
    # fetch/httpx 都已 stub 到位，仍会导致整个 pytest 进程在所有测试通过之后额外
    # 挂起数十秒（ThreadPoolExecutor 的 atexit join 钩子会等它跑完，观察到过 hang
    # 到进程被打断报 "cannot schedule new futures after interpreter shutdown"）。
    # 单测里这个预热本身没有验证意义，直接整体 stub 为空操作。
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.prefetch_board_flow_histories",
        lambda *_args, **_kwargs: 0,
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

    # 大盘情绪温度计（market_breadth_signal.py）与互联互通摘要（market_flow_client.py）
    # 都是 build_analysis_facts 非 budget_enhancements 路径末尾无条件调用、且没有超时
    # 包装的真实 AkShare 子进程请求（stock_a_high_low_statistics/涨跌停池/两融/南向资金），
    # 此前一直漏 stub，导致任何不传 budget_enhancements=True 的 build_analysis_facts
    # 单测（如 test_analysis_facts_dates.py）都会真的打网络，是本仓库测试套件里最大的
    # 几个耗时黑洞之一（单个测试 24~32s）。这里统一在子进程层兜底为空，走各自的
    # best-effort 降级分支（available=False），不影响断言。
    monkeypatch.setattr(
        "app.services.akshare_subprocess.run_akshare_json_script",
        lambda *_args, **_kwargs: None,
    )
    # market_breadth_signal.py 用 `from ... import run_akshare_json_script`（直接导入
    # 绑定），只 patch 上面的源模块符号够不到它自己模块内的引用，需要单独再 patch 一次
    # （同 sector_flow_divergence_backtest.fetch_canonical_daily_kline_series 的坑）。
    monkeypatch.setattr(
        "app.services.market_breadth_signal.run_akshare_json_script",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.market_flow_client._fetch_stock_connect_flow_summary_uncached",
        lambda _anchor: None,
    )


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "pytest.db"))
    monkeypatch.setenv("FUND_AI_JWT_SECRET", PYTEST_JWT_SECRET)
    monkeypatch.setenv("FUND_AI_OCR_PRELOAD", "false")
    monkeypatch.setenv("FUND_AI_RUNTIME_ROLE", "api")
    monkeypatch.setenv("FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_THEME_BOARD_REFRESH_ENABLED", "false")
    # M6：绝大多数既有测试（M2~M4 阶段编写）验证的是双向 guard 升级机制本身的正确性
    # （触发条件对不对、升级到哪一档对不对），这些断言隐含假设"升级判定会真正生效"。
    # 生产默认值是更保守的 shadow（见 config.py），但测试套件默认切到 enforced，
    # 让历史测试的原始意图（验证机制本身）保持不变；shadow 模式"只提示不生效"的
    # 行为由专门的 test_decision_escalation_mode.py 显式 monkeypatch 覆盖验证。
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
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
