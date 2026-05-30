from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app


client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analyze_manual_holdings_returns_persisted_report(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
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
    assert body["market_context"]
    assert any(item["topic"] == "中证电网设备" for item in body["market_context"])
    assert len(body["recommendations"]) >= 3
    assert any("决策：" in item and "触发：" in item for item in body["recommendations"])
    assert any("中证电网设备" in item for item in body["recommendations"])
    assert any("半导体" in item for item in body["recommendations"])
