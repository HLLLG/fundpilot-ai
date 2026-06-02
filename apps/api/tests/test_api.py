from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app


client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_allocate_penetration_daily_endpoint():
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
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
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


def test_ocr_endpoint_accepts_text_fallback():
    response = client.post(
        "/api/ocr",
        data={"raw_text": "测试基金A\n000001\n持有金额 1000\n持有收益率 -1.5%"},
    )

    assert response.status_code == 200
    assert response.json()["holdings"][0]["fund_code"] == "000001"


def test_ocr_endpoint_resolves_holdings_with_saved_profiles(tmp_path, monkeypatch):
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    profile = parse_profile_from_text(
        "华夏中证电网设备主题ETF联接A\n025856\n持有金额\n15,075.46\n10,645.76\n52.76%"
    )
    assert profile is not None
    FundProfileService().save_profile(profile)

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

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
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


def test_ocr_endpoint_returns_clear_error_when_local_ocr_is_missing(monkeypatch):
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


def test_analyze_unknown_code_keeps_yangjibao_snapshot_and_rich_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
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
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
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
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
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


def test_fund_profiles_export_import(tmp_path, monkeypatch):
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    profile = parse_profile_from_text("测试基金\n015608\n持有金额\n1000\n1.2%")
    assert profile is not None
    FundProfileService().save_profile(profile)

    exported = client.get("/api/fund-profiles/export").json()
    assert exported["count"] >= 1

    client.post("/api/fund-profiles/import", json={"profiles": exported["profiles"]})
    listed = client.get("/api/fund-profiles").json()
    assert any(item["fund_code"] == "015608" for item in listed)
