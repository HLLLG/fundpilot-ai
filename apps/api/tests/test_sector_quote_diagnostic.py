from app.services import sector_quote_diagnostic as diagnostic


def test_run_sector_quote_diagnostic_mocks_providers(monkeypatch):
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {f"板块{i}": float(i) for i in range(10)}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_quote_by_secid",
        lambda secid, **_: ("商业航天", 1.23),
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_boards_via_relay",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {f"ak{i}": 0.1 for i in range(10)}, "industry": {}, "index": {}},
    )

    class Settings:
        sector_quotes_relay_url = None
        sector_quotes_browser_enabled = False
        sector_quotes_browser_command = None

    monkeypatch.setattr(diagnostic, "get_settings", lambda: Settings())

    result = diagnostic.run_sector_quote_diagnostic(timeout_seconds=5.0)

    assert result["ok"] is True
    assert "eastmoney_batch" in result["ok_paths"]
    assert result["recommendation"].startswith("eastmoney_batch_ok")


def test_diagnostic_api_returns_json():
    from tests.conftest import authenticated_test_client

    client = authenticated_test_client()
    response = client.get("/api/sector-quotes/diagnostic?timeout_seconds=1")
    assert response.status_code == 200
    payload = response.json()
    assert "probes" in payload
    assert "recommendation" in payload
