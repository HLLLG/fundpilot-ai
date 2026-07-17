from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services import deepseek_http


class _FakeTransport:
    def __init__(self, *, retries: int, proxy: str | None) -> None:
        self.retries = retries
        self.proxy = proxy


class _FakeClient:
    created: list["_FakeClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.is_closed = False
        self.created.append(self)

    def close(self) -> None:
        self.is_closed = True


def test_shared_client_reuses_pool_and_configures_connection_only_retries(
    monkeypatch,
) -> None:
    deepseek_http.close_deepseek_http_clients()
    _FakeClient.created = []
    monkeypatch.setattr(deepseek_http.httpx, "HTTPTransport", _FakeTransport)
    monkeypatch.setattr(deepseek_http.httpx, "Client", _FakeClient)
    settings = Settings(
        _env_file=None,
        deepseek_timeout_seconds=123,
        deepseek_connection_retries=2,
    )

    first = deepseek_http.get_deepseek_http_client(settings)
    second = deepseek_http.get_deepseek_http_client(settings)

    assert first is second
    assert len(_FakeClient.created) == 1
    assert first.kwargs["transport"].retries == 2
    assert first.kwargs["timeout"].read == 123
    assert "headers" not in first.kwargs

    deepseek_http.close_deepseek_http_clients()
    assert first.is_closed is True


def test_shared_client_separates_different_transport_contracts(monkeypatch) -> None:
    deepseek_http.close_deepseek_http_clients()
    _FakeClient.created = []
    monkeypatch.setattr(deepseek_http.httpx, "HTTPTransport", _FakeTransport)
    monkeypatch.setattr(deepseek_http.httpx, "Client", _FakeClient)

    first = deepseek_http.get_deepseek_http_client(
        Settings(_env_file=None, deepseek_connection_retries=1)
    )
    second = deepseek_http.get_deepseek_http_client(
        Settings(_env_file=None, deepseek_connection_retries=2)
    )

    assert first is not second
    assert [client.kwargs["transport"].retries for client in _FakeClient.created] == [
        1,
        2,
    ]
    deepseek_http.close_deepseek_http_clients()


def test_shared_client_preserves_https_proxy_environment(monkeypatch) -> None:
    deepseek_http.close_deepseek_http_clients()
    _FakeClient.created = []
    monkeypatch.setattr(deepseek_http.httpx, "HTTPTransport", _FakeTransport)
    monkeypatch.setattr(deepseek_http.httpx, "Client", _FakeClient)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("NO_PROXY", raising=False)

    client = deepseek_http.get_deepseek_http_client(Settings(_env_file=None))

    assert client.kwargs["transport"].proxy == "http://127.0.0.1:7890"
    deepseek_http.close_deepseek_http_clients()
