from __future__ import annotations

import time

import httpx
import pytest
import requests

from app.services import eastmoney_http


class _HttpxResponse:
    status_code = 200


class _HttpxClient:
    is_closed = False

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _HttpxResponse()

    def close(self) -> None:
        self.closed = True
        self.is_closed = True


class _RequestsResponse:
    status_code = 200


class _RequestsSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _RequestsResponse()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_provider_state(monkeypatch):
    eastmoney_http.close_eastmoney_http_clients()
    eastmoney_http._circuit_failures.clear()
    eastmoney_http._circuit_open_until.clear()
    monkeypatch.setattr(eastmoney_http.random, "uniform", lambda _a, _b: 1.0)
    yield
    eastmoney_http.close_eastmoney_http_clients()
    eastmoney_http._circuit_failures.clear()
    eastmoney_http._circuit_open_until.clear()


def test_httpx_wrapper_reuses_client_and_strips_connection_header(monkeypatch):
    shared = _HttpxClient()
    monkeypatch.setattr(eastmoney_http, "_shared_httpx_client", lambda: shared)

    first = eastmoney_http.eastmoney_httpx_client(
        headers={"Connection": "close", "X-Test": "base"},
        timeout=9,
    )
    second = eastmoney_http.eastmoney_httpx_client()
    first.get("https://push2.eastmoney.com/a", headers={"Connection": "close"})
    second.get("https://push2.eastmoney.com/b", timeout=4)

    assert len(shared.calls) == 2
    assert shared.calls[0]["headers"] == {"X-Test": "base"}
    assert shared.calls[0]["timeout"] == 9
    assert shared.calls[1]["timeout"] == 4


def test_requests_wrapper_returns_session_to_pool(monkeypatch):
    session = _RequestsSession()
    monkeypatch.setattr(
        eastmoney_http,
        "_borrow_requests_session",
        lambda: session,
    )
    returned: list[object] = []
    monkeypatch.setattr(eastmoney_http._requests_pool, "put", returned.append)

    client = eastmoney_http.eastmoney_requests_client(
        {"Connection": "close", "X-Test": "base"}
    )
    client.get("https://push2.eastmoney.com/a", timeout=3)

    assert returned == [session]
    assert session.calls[0]["headers"] == {"X-Test": "base"}
    assert session.calls[0]["timeout"] == 3


def test_budget_caps_nested_request_timeout(monkeypatch):
    shared = _HttpxClient()
    monkeypatch.setattr(eastmoney_http, "_shared_httpx_client", lambda: shared)
    deadline = time.monotonic() + 0.05
    token = eastmoney_http._DEADLINE.set(deadline)
    try:
        eastmoney_http.eastmoney_httpx_client(timeout=30).get(
            "https://push2.eastmoney.com/a"
        )
    finally:
        eastmoney_http._DEADLINE.reset(token)

    assert 0 < shared.calls[0]["timeout"] <= 0.05


def test_circuit_opens_after_configured_failures(monkeypatch):
    class _FailingClient:
        is_closed = False

        def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(
        eastmoney_http,
        "_shared_httpx_client",
        lambda: _FailingClient(),
    )
    monkeypatch.setattr(
        eastmoney_http,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "eastmoney_max_concurrency": 8,
                "eastmoney_acquire_timeout_seconds": 1,
                "eastmoney_circuit_failure_threshold": 2,
                "eastmoney_circuit_cooldown_seconds": 15,
            },
        )(),
    )
    client = eastmoney_http.eastmoney_httpx_client(timeout=1)

    with pytest.raises(httpx.ConnectError):
        client.get("https://push2.eastmoney.com/a")
    with pytest.raises(httpx.ConnectError):
        client.get("https://push2.eastmoney.com/a")
    with pytest.raises(eastmoney_http.EastmoneyCircuitOpen):
        client.get("https://push2.eastmoney.com/a")

    open_until = eastmoney_http._circuit_open_until["push2.eastmoney.com"]
    with pytest.raises(eastmoney_http.EastmoneyCircuitOpen):
        client.get("https://push2.eastmoney.com/a")
    assert (
        eastmoney_http._circuit_open_until["push2.eastmoney.com"]
        == open_until
    )


def test_close_resets_requests_pool_accounting(monkeypatch):
    session = _RequestsSession()
    eastmoney_http._requests_sessions_created = 1
    eastmoney_http._requests_pool.put(session)  # type: ignore[arg-type]

    eastmoney_http.close_eastmoney_http_clients()

    assert session.closed is True
    assert eastmoney_http._requests_sessions_created == 0
