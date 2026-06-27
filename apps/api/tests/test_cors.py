from app.config import refresh_settings


def test_cors_origin_regex_auto_when_cloudbase_env_configured(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_ENV_ID", "fundpilot-ai-d1g1j23iof248e1ec")
    monkeypatch.delenv("FUND_AI_CORS_ORIGIN_REGEX", raising=False)
    settings = refresh_settings()

    assert settings.resolved_cors_origin_regex == r"https://[\w-]+\.webapps\.tcloudbase\.com"


def test_cors_origin_regex_explicit_overrides_cloudbase_default(monkeypatch):
    monkeypatch.setenv("FUND_AI_CLOUDBASE_ENV_ID", "fundpilot-ai-d1g1j23iof248e1ec")
    monkeypatch.setenv("FUND_AI_CORS_ORIGIN_REGEX", r"https://example\.com")
    settings = refresh_settings()

    assert settings.resolved_cors_origin_regex == r"https://example\.com"


def test_cors_preflight_allows_localhost_origin(auth_client):
    response = auth_client.options(
        "/api/portfolio/holdings",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3001"
