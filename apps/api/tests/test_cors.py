import asyncio

from starlette.requests import Request

from app.config import refresh_settings
from app.main import unhandled_exception_handler


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


def test_unhandled_error_response_keeps_local_cors_origin():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/discovery-prompt",
            "raw_path": b"/api/discovery-prompt",
            "query_string": b"",
            "headers": [(b"origin", b"http://127.0.0.1:3001")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
    )

    response = asyncio.run(
        unhandled_exception_handler(request, RuntimeError("injected failure"))
    )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:3001"
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert response.headers.get("vary") == "Origin"


def test_unhandled_error_response_does_not_echo_untrusted_origin():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/discovery-prompt",
            "raw_path": b"/api/discovery-prompt",
            "query_string": b"",
            "headers": [(b"origin", b"https://attacker.example")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
    )

    response = asyncio.run(
        unhandled_exception_handler(request, RuntimeError("injected failure"))
    )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is None
