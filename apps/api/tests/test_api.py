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
