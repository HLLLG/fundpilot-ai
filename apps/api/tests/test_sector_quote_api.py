from app.models import Holding
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def test_refresh_sector_quotes_endpoint(monkeypatch):
    captured = {}

    def fake_refresh(holdings, force_refresh=False, timeout_seconds=None):
        captured["timeout_seconds"] = timeout_seconds
        return {
            "ok": True,
            "holdings": [h.model_dump() for h in holdings],
            "items": [],
            "summary": {"matched": 1, "unresolved": 0, "needs_mapping": 0},
        }

    monkeypatch.setattr("app.main.refresh_holdings_sector_quotes", fake_refresh)

    from tests.conftest import authenticated_test_client

    client = authenticated_test_client()
    response = client.post(
        "/api/holdings/refresh-sector-quotes",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "测试",
                    "holding_amount": 1000,
                    "return_percent": 1,
                    "sector_name": "半导体",
                    "sector_return_percent": 1,
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["timeout_seconds"] == 8.0


def test_refresh_sector_quotes_accurate_budget_uses_no_timeout(monkeypatch):
    captured = {}

    def fake_refresh(holdings, force_refresh=False, timeout_seconds=None):
        captured["timeout_seconds"] = timeout_seconds
        return {
            "ok": True,
            "holdings": [h.model_dump() for h in holdings],
            "items": [],
            "summary": {"matched": 1, "unresolved": 0, "needs_mapping": 0},
        }

    monkeypatch.setattr("app.main.refresh_holdings_sector_quotes", fake_refresh)

    from tests.conftest import authenticated_test_client

    client = authenticated_test_client()
    response = client.post(
        "/api/holdings/refresh-sector-quotes",
        json={
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "测试",
                    "holding_amount": 1000,
                    "return_percent": 1,
                    "sector_name": "半导体",
                    "sector_return_percent": 1,
                }
            ],
            "budget": "accurate",
        },
    )
    assert response.status_code == 200
    assert captured["timeout_seconds"] is None


def test_sector_quotes_status_endpoint():
    from app.config import get_settings
    from tests.conftest import authenticated_test_client

    client = authenticated_test_client()
    response = client.get("/api/sector-quotes/status")
    assert response.status_code == 200
    body = response.json()
    assert "auto_interval_seconds" in body
    assert body["auto_interval_seconds"] == get_settings().sector_quotes_auto_interval_seconds
