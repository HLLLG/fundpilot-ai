from fastapi.testclient import TestClient

from app.config import refresh_settings
from tests.conftest import auth_client_for_db


def test_health_endpoint_returns_ok():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_allocate_penetration_daily_endpoint(client: TestClient):
    response = client.post(
        "/api/holdings/allocate-penetration-daily",
        json={
            "holdings": [
                {
                    "fund_code": "008586",
                    "fund_name": "华夏人工智能",
                    "holding_amount": 8000,
                    "return_percent": -3,
                    "sector_return_percent": 2.5,
                },
                {
                    "fund_code": "015945",
                    "fund_name": "国防军工",
                    "holding_amount": 2000,
                    "return_percent": -5,
                    "sector_return_percent": 0.5,
                },
            ],
            "account_daily_profit": 369.84,
            "account_daily_profit_source": "penetration_estimate",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allocated_total"] == 369.84
    assert len(body["holdings"]) == 2
    assert all(item.get("daily_profit") is not None for item in body["holdings"])


def _mock_news_search(monkeypatch):
    from app.models import NewsItem
    from app.services.news_service import NewsService

    def fake_prefetch_for_holdings(self, holdings, max_topics=None):
        del max_topics
        items: list[NewsItem] = []
        for holding in holdings:
            topic = (holding.sector_name or holding.fund_name or "市场").strip()
            items.append(
                NewsItem(
                    topic=topic,
                    title=f"{topic}相关新闻",
                    published_at="2026-05-30 09:00:00",
                    source="eastmoney",
                    url=f"http://example.com/{topic}",
                    snippet="测试摘要",
                )
            )
        return items

    monkeypatch.setattr(NewsService, "prefetch_for_holdings", fake_prefetch_for_holdings)


def test_analyze_manual_holdings_returns_persisted_report(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)
    payload = {
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "华夏中证电网设备主题ETF发起式联接A",
                "holding_amount": 5280.66,
                "return_percent": -3.25,
            }
        ],
        "profile": {
            "style": "稳健",
            "horizon": "半年到一年",
            "max_drawdown_percent": 8,
            "concentration_limit_percent": 35,
            "prefer_dca": True,
            "avoid_chasing": True,
        },
    }

    response = client.post("/api/analyze", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["risk"]["level"] == "medium"
    assert body["holdings"][0]["fund_code"] == "015608"
    assert body["recommendations"]
    assert body["market_news"]
    assert any("相关新闻" in item["title"] for item in body["market_news"])

    reports_response = client.get("/api/reports")
    assert reports_response.status_code == 200
    assert any(report["id"] == body["id"] for report in reports_response.json())


def test_ocr_preview_skips_sector_refresh(client: TestClient, monkeypatch):
    from pathlib import Path

    fixture = (
        Path(__file__).parent / "fixtures" / "alipay_holdings_list_ocr.txt"
    ).read_text(encoding="utf-8")
    called = {"count": 0}

    def fake_refresh(*args, **kwargs):
        called["count"] += 1
        return {"ok": True, "holdings": [], "items": [], "summary": {}}

    monkeypatch.setattr(
        "app.services.overview_pipeline.refresh_holdings_sector_quotes",
        fake_refresh,
    )
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: [
            ("519674", "银河创新成长混合A"),
            ("008586", "华夏人工智能ETF联接C"),
            ("025856", "华夏中证电网设备主题ETF联接A"),
            ("015945", "易方达国防军工混合C"),
        ],
    )

    response = client.post(
        "/api/ocr",
        data={"raw_text": fixture, "preview": "true"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["preview"] is True
    assert body["sector_refresh"]["skipped"] is True
    assert called["count"] == 0
    assert len(body["holdings"]) == 4
    assert body["holdings"][0]["fund_code"] == "519674"


def test_ocr_endpoint_accepts_text_fallback(client: TestClient):
    from pathlib import Path

    text = (
        Path(__file__).parent / "fixtures" / "alipay_holdings_list_ocr.txt"
    ).read_text(encoding="utf-8")
    response = client.post(
        "/api/ocr",
        data={"raw_text": text},
    )

    assert response.status_code == 200
    holdings = response.json()["holdings"]
    assert len(holdings) == 4
    assert any("电网设备" in item["fund_name"] for item in holdings)


def test_ocr_endpoint_resolves_holdings_with_saved_profiles(tmp_path, monkeypatch):
    from pathlib import Path

    from app.models import FundProfile
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_profile import FundProfileService

    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: [("025856", "华夏中证电网设备主题ETF联接A")],
    )

    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    user_id = client.get("/api/auth/me").json()["id"]
    token = set_request_user_id(user_id)
    try:
        FundProfileService().save_profile(
            FundProfile(
                fund_code="025856",
                fund_name="华夏中证电网设备主题ETF联接A",
                holding_amount=15075.46,
                holding_shares=10645.76,
                position_percent=52.76,
            )
        )
    finally:
        reset_request_user_id(token)

    text = (
        Path(__file__).parent / "fixtures" / "alipay_holdings_list_ocr.txt"
    ).read_text(encoding="utf-8")
    response = client.post("/api/ocr", data={"raw_text": text})

    body = response.json()
    assert response.status_code == 200
    grid = next(item for item in body["holdings"] if "电网设备" in item["fund_name"])
    assert grid["fund_code"] == "025856"
    assert grid["fund_name"] == "华夏中证电网设备主题ETF联接A"


def test_ocr_endpoint_caches_image_text(tmp_path, monkeypatch):
    from app.services.ocr_engine import OcrEngine

    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    calls = {"count": 0}

    def fake_extract(self, image_path):
        calls["count"] += 1
        return "测试基金A\n000001\n持有金额 1000\n持有收益率 1.5%"

    monkeypatch.setattr(OcrEngine, "extract_text", fake_extract)

    for _ in range(2):
        response = client.post(
            "/api/ocr",
            files={"file": ("same.png", b"same-image-bytes", "image/png")},
        )
        assert response.status_code == 200

    assert calls["count"] == 1


def test_ocr_endpoint_returns_clear_error_when_local_ocr_is_missing(
    client: TestClient,
    monkeypatch,
):
    from app.services.ocr_engine import OcrEngine

    def raise_missing_ocr(self, image_path):
        raise TypeError("PaddleOCR.predict() got an unexpected keyword argument 'cls'")

    monkeypatch.setattr(OcrEngine, "extract_text", raise_missing_ocr)

    response = client.post(
        "/api/ocr",
        files={"file": ("fund.png", b"not-a-real-image", "image/png")},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["holdings"] == []
    assert "OCR 识别失败" in body["error"]


def test_analyze_unknown_code_keeps_yangjibao_snapshot_and_rich_recommendations(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)

    response = client.post(
        "/api/analyze",
        json={
            "holdings": [
                {
                    "fund_code": "000000",
                    "fund_name": "华夏中证电网设备...",
                    "holding_amount": 15161.69,
                    "return_percent": 0.87,
                    "daily_profit": 488.03,
                    "sector_name": "中证电网设备",
                    "sector_return_percent": 3.33,
                },
                {
                    "fund_code": "000000",
                    "fund_name": "银河创新成长混合A",
                    "holding_amount": 4458.63,
                    "return_percent": 5.2,
                    "daily_profit": 299.18,
                    "sector_name": "半导体",
                    "sector_return_percent": 7.19,
                },
            ]
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["snapshots"][0]["source"] == "yangjibao-ocr"
    assert "补全代码" in body["snapshots"][0]["note"]
    assert body["market_context"] == []
    assert body["market_news"]
    assert len(body["fund_recommendations"]) >= 2
    assert any(
        "电网" in item["fund_name"] or "电网" in " ".join(item.get("points", []))
        for item in body["fund_recommendations"]
    )
    assert any(
        "半导体" in item["fund_name"]
        or any("半导体" in point for point in item.get("points", []))
        for item in body["fund_recommendations"]
    )


def test_delete_report_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)

    create = client.post(
        "/api/analyze",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "测试基金",
                    "holding_amount": 1000,
                    "return_percent": 1.0,
                }
            ]
        },
    )
    report_id = create.json()["id"]

    delete = client.delete(f"/api/reports/{report_id}")
    assert delete.status_code == 200
    assert delete.json()["ok"] is True

    missing = client.delete(f"/api/reports/{report_id}")
    assert missing.status_code == 404

    reports = client.get("/api/reports").json()
    assert not any(item["id"] == report_id for item in reports)


def test_report_diff_and_markdown_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)

    first = client.post(
        "/api/analyze",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "基金A",
                    "holding_amount": 1000,
                    "return_percent": 1.0,
                }
            ]
        },
    ).json()
    second = client.post(
        "/api/analyze",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "基金A",
                    "holding_amount": 1200,
                    "return_percent": -1.0,
                }
            ],
            "analysis_mode": "fast",
        },
    ).json()

    diff = client.get(f"/api/reports/{second['id']}/diff")
    assert diff.status_code == 200
    body = diff.json()
    assert body["has_previous"] is True
    assert body["diff"]["previous_report_id"] == first["id"]

    markdown = client.get(f"/api/reports/{second['id']}/markdown")
    assert markdown.status_code == 200
    assert "# " in markdown.json()["markdown"]


def test_fund_profiles_list_after_save(tmp_path, monkeypatch):
    from app.models import FundProfile
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_profile import FundProfileService

    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    user_id = client.get("/api/auth/me").json()["id"]
    token = set_request_user_id(user_id)
    try:
        FundProfileService().save_profile(
            FundProfile(
                fund_code="015608",
                fund_name="测试基金",
                holding_amount=1000.0,
                holding_return_percent=1.2,
            )
        )
    finally:
        reset_request_user_id(token)

    listed = client.get("/api/fund-profiles").json()
    assert any(item["fund_code"] == "015608" for item in listed)


def test_investor_profile_persistence(tmp_path, monkeypatch):
    client = auth_client_for_db(monkeypatch, tmp_path / "investor_profile.db")

    missing = client.get("/api/investor-profile")
    assert missing.status_code == 200
    assert missing.json()["style"] == "稳健"
    assert missing.json()["max_drawdown_percent"] == 8

    payload = {
        "style": "进取",
        "horizon": "一年以上",
        "max_drawdown_percent": 12,
        "concentration_limit_percent": 40,
        "expected_investment_amount": 45000,
        "prefer_dca": False,
        "avoid_chasing": True,
    }
    saved = client.put("/api/investor-profile", json=payload)
    assert saved.status_code == 200
    assert saved.json()["expected_investment_amount"] == 45000

    loaded = client.get("/api/investor-profile")
    assert loaded.status_code == 200
    assert loaded.json()["style"] == "进取"
    assert loaded.json()["max_drawdown_percent"] == 12


def test_async_job_returns_stage(tmp_path, monkeypatch):
    from app.models import FundSnapshot
    from app.services.fund_data import FundDataService

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    _mock_news_search(monkeypatch)

    def fake_snapshots(self, holdings, **kwargs):
        snapshots = [
            FundSnapshot(fund_code=holding.fund_code, fund_name=holding.fund_name, source="test")
            for holding in holdings
        ]
        return snapshots, {}

    monkeypatch.setattr(FundDataService, "get_snapshots_with_nav_trends", fake_snapshots)

    started = client.post(
        "/api/analyze/async",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "测试",
                    "holding_amount": 1000,
                    "return_percent": 1,
                }
            ],
            "profile": {
                "style": "稳健",
                "horizon": "半年到一年",
                "max_drawdown_percent": 8,
                "concentration_limit_percent": 35,
                "prefer_dca": True,
                "avoid_chasing": True,
            },
            "analysis_mode": "fast",
        },
    )
    job_id = started.json()["job_id"]

    import time

    for _ in range(60):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            assert job.get("stage_label")
            assert job.get("analysis_mode") == "fast"
            return
        time.sleep(0.1)

    raise AssertionError(
        f"job did not finish in time (last status={job.get('status')}, stage={job.get('stage')})"
    )


def test_fund_discovery_sectors(client):
    response = client.get("/api/fund-discovery/sectors")
    assert response.status_code == 200
    body = response.json()
    assert "sectors" in body


def test_market_theme_boards(client):
    response = client.get("/api/market/theme-boards?sort=change")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["sort"] == "change"
    assert isinstance(body["items"], list)
    first = body["items"][0]
    assert first["sector_label"] == "商业航天"
    assert first["board_kind"] in {"industry", "concept", "index"}
    assert "linked_fund_count" not in first


def test_market_theme_boards_invalid_sort(client):
    response = client.get("/api/market/theme-boards?sort=invalid")
    assert response.status_code == 400


def test_market_theme_boards_sort_inflow(client):
    response = client.get("/api/market/theme-boards?sort=inflow")
    assert response.status_code == 200
    assert response.json()["sort"] == "inflow"


def test_fund_discovery_async_offline(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_sector_heat_ranking",
        lambda: [{"sector_label": "半导体", "heat_score": 1.0, "change_1d_percent": 1.0}],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_candidate_pool",
        lambda *args, **kwargs: [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "sector_label": "半导体",
                "return_1y_percent": 10.0,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.enrich_candidates",
        lambda pool: pool,
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.NewsService",
        lambda: type(
            "NS",
            (),
            {"prefetch_topics": staticmethod(lambda topics: [])},
        )(),
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.summarize_all_topics",
        lambda news, settings=None: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_discovery_facts",
        lambda **kwargs: {
            "readonly": True,
            "instruction": "test",
            "portfolio_gap": {
                "holding_count": 0,
                "available_budget_yuan": 30000.0,
                "target_sectors": kwargs.get("target_sectors", []),
            },
            "sector_heat": kwargs.get("sector_heat", []),
            "market_flow": {"available": False},
            "signal_backtest": {"enabled": False, "has_data": False},
            "news": {"has_data": False},
            "candidate_pool": kwargs.get("candidate_pool", []),
            "selection_strategy": kwargs.get("selection_strategy", "balanced"),
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_client.get_settings",
        lambda: type("S", (), {"deepseek_api_key": None})(),
    )

    started = client.post(
        "/api/fund-discovery/async",
        json={
            "profile": {
                "style": "稳健",
                "horizon": "半年到一年",
                "max_drawdown_percent": 8,
                "concentration_limit_percent": 35,
                "prefer_dca": True,
                "avoid_chasing": True,
            },
            "focus_sectors": ["半导体"],
            "holdings": [],
        },
    )
    assert started.status_code == 200
    job_id = started.json()["job_id"]

    import time

    job: dict = {}
    for _ in range(150):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "completed":
            assert job.get("job_kind") == "discovery"
            assert job.get("discovery_report")
            report_id = job["discovery_report"]["id"]
            detail = client.get(f"/api/fund-discovery/reports/{report_id}")
            assert detail.status_code == 200
            return
        if job["status"] == "failed":
            raise AssertionError(job.get("error"))
        time.sleep(0.1)

    raise AssertionError(
        f"discovery job timeout (last status={job.get('status')}, stage={job.get('stage')})"
    )


def test_market_us_overview_smoke(client: TestClient):
    response = client.get("/api/market/us-overview")
    assert response.status_code == 200
    body = response.json()

    # 时段字段
    assert body["session_kind"] == "pre_market"
    assert body["session_label"] == "盘前交易中"

    # 期货：3 个品种 + 数值字段齐全
    assert isinstance(body["futures"], list)
    assert len(body["futures"]) == 3
    symbols = {item["symbol"] for item in body["futures"]}
    assert {"NASDAQ_FUT", "SP500_FUT", "DOW_FUT"} <= symbols
    assert body["futures"][0]["last_price"] is not None

    # USD/CNY 汇率
    assert body["usd_cny"]["last_price"] == 6.8096
    assert body["usd_cny"]["status"] == "ok"

    # 方案 A：QDII 列表默认关闭
    assert isinstance(body["qdii"], list)
    assert body["qdii"] == []
    assert body["qdii_status"] == "unavailable"
    assert body["futures_status"] == "ok"
    assert body["forex_status"] == "ok"
    assert body["available"] is True


def test_market_us_overview_force_refresh(client: TestClient):
    response = client.get("/api/market/us-overview?force_refresh=true")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["session_kind"] == "pre_market"
    assert len(body["futures"]) == 3
    assert body["usd_cny"]["last_price"] == 6.8096
    assert body["qdii"] == []


def _stub_transaction_nav(monkeypatch, nav: float = 2.0) -> None:
    from app.services import holding_amount_sync, transaction_ledger

    monkeypatch.setattr(transaction_ledger, "get_unit_nav_on_date", lambda _c, _d: nav)
    monkeypatch.setattr(holding_amount_sync, "fetch_fund_estimate_quotes", lambda *_a, **_k: {})
    monkeypatch.setattr(holding_amount_sync, "get_latest_unit_nav", lambda _c: nav)
    monkeypatch.setattr(holding_amount_sync, "get_official_nav_return", lambda _c, _d: None)


def test_transactions_apply_then_list_fund_transactions(tmp_path, monkeypatch):
    _stub_transaction_nav(monkeypatch, nav=2.0)
    client = auth_client_for_db(monkeypatch, tmp_path / "tx.db")

    body = {
        "transactions": [
            {
                "direction": "buy",
                "fund_name": "测试基金",
                "fund_code": "110011",
                "amount_yuan": 1500.0,
                "trade_time": "2026-06-09 10:00:00",
                "confirm_date": "2026-06-09",
            }
        ]
    }
    apply_resp = client.post("/api/transactions/apply", json=body)
    assert apply_resp.status_code == 200, apply_resp.text
    data = apply_resp.json()
    assert data["inserted"] == 1
    assert data["skipped"] == 0
    assert data["pending"] == 0

    listed = client.get("/api/funds/110011/transactions")
    assert listed.status_code == 200
    transactions = listed.json()["transactions"]
    assert len(transactions) == 1
    assert transactions[0]["status"] == "confirmed"
    assert transactions[0]["shares_delta"] == 750.0


def test_transactions_apply_updates_holding_amount_via_override(tmp_path, monkeypatch):
    from app.database import save_fund_profile
    from app.models import FundProfile
    from app.request_context import reset_request_user_id, set_request_user_id

    _stub_transaction_nav(monkeypatch, nav=2.0)
    client = auth_client_for_db(monkeypatch, tmp_path / "tx_override.db")

    user_id = client.get("/api/auth/me").json()["id"]
    token = set_request_user_id(user_id)
    try:
        save_fund_profile(
            FundProfile(
                fund_code="110011",
                fund_name="华夏成长混合",
                holding_amount=2000.0,
                holding_shares=1000.0,
                shares_baseline_date="2026-06-01",
            )
        )
    finally:
        reset_request_user_id(token)

    body = {
        "transactions": [
            {
                "direction": "buy",
                "fund_name": "华夏成长混合",
                "fund_code": "110011",
                "amount_yuan": 1500.0,
                "trade_time": "2026-06-09 10:00:00",
                "confirm_date": "2026-06-09",
            }
        ]
    }
    resp = client.post("/api/transactions/apply", json=body)
    assert resp.status_code == 200, resp.text
    holdings = {h["fund_code"]: h for h in resp.json()["holdings"]}
    assert "110011" in holdings
    # baseline 1000 + delta 750 = 1750 shares × nav 2.0 = 3500.0
    assert holdings["110011"]["holding_amount"] == 3500.0


def test_transactions_ocr_parses_text_without_persisting(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: [("110011", "测试基金")],
    )
    text = "\n".join(
        [
            "交易分析",
            "买入",
            "测试基金",
            "1,500.00元",
            "2026-06-09 10:00:00",
            "卖出",
            "测试基金",
            "500.00元",
            "2026-06-09 09:00:00",
        ]
    )
    resp = client.post("/api/transactions/ocr", data={"raw_text": text})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ocr_source"] == "alipay_transactions"
    assert len(body["transactions"]) == 2
    first = body["transactions"][0]
    assert first["direction"] == "buy"
    assert first["confirm_date"] == "2026-06-09"
    assert first["fund_code"] == "110011"
