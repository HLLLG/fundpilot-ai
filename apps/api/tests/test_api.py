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

    def fake_search(self, topic: str, limit: int | None = None):
        return [
            NewsItem(
                topic=topic,
                title=f"{topic}相关新闻",
                published_at="2026-05-30 09:00:00",
                source="eastmoney",
                url=f"http://example.com/{topic}",
                snippet="测试摘要",
            )
        ]

    monkeypatch.setattr(NewsService, "search", fake_search)


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
    response = client.post(
        "/api/ocr",
        data={"raw_text": "测试基金A\n000001\n持有金额 1000\n持有收益率 -1.5%"},
    )

    assert response.status_code == 200
    assert response.json()["holdings"][0]["fund_code"] == "000001"


def test_ocr_endpoint_resolves_holdings_with_saved_profiles(tmp_path, monkeypatch):
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    user_id = client.get("/api/auth/me").json()["id"]
    profile = parse_profile_from_text(
        "华夏中证电网设备主题ETF联接A\n025856\n持有金额\n15,075.46\n10,645.76\n52.76%"
    )
    assert profile is not None
    token = set_request_user_id(user_id)
    try:
        FundProfileService().save_profile(profile)
    finally:
        reset_request_user_id(token)

    response = client.post(
        "/api/ocr",
        data={
            "raw_text": "华夏中证电网设备...\n0.87%\n+488.03\n￥15,161.69\n中证电网设备\n+3.33%"
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["holdings"][0]["fund_code"] == "025856"
    assert body["holdings"][0]["fund_name"] == "华夏中证电网设备主题ETF联接A"


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
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    user_id = client.get("/api/auth/me").json()["id"]
    profile = parse_profile_from_text("测试基金\n015608\n持有金额\n1000\n1.2%")
    assert profile is not None
    token = set_request_user_id(user_id)
    try:
        FundProfileService().save_profile(profile)
    finally:
        reset_request_user_id(token)

    listed = client.get("/api/fund-profiles").json()
    assert any(item["fund_code"] == "015608" for item in listed)


def test_trading_session_endpoint():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/trading-session")
    assert response.status_code == 200
    body = response.json()
    assert body["session_kind"]
    assert body["decision_window"]
    assert body["effective_trade_date"]


def test_investor_profile_persistence(tmp_path, monkeypatch):
    client = auth_client_for_db(monkeypatch, tmp_path / "investor_profile.db")

    missing = client.get("/api/investor-profile")
    assert missing.status_code == 404

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


def test_database_export_and_import(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")

    client.post(
        "/api/analyze",
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
        },
    )

    export = client.get("/api/database/export")
    assert export.status_code == 200
    assert export.content

    import_path = tmp_path / "imported.db"
    import_path.write_bytes(export.content)
    upload = client.post(
        "/api/database/import",
        files={"file": ("fundpilot-app.db", import_path.read_bytes(), "application/octet-stream")},
    )
    assert upload.status_code == 200
    assert upload.json()["ok"] is True


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
