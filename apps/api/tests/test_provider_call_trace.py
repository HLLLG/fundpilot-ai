from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.config import refresh_settings
from app.services.deepseek_http import ProviderOutputError
from app.services.deepseek_streaming import stream_chat_completion
from app.services.discovery_client import DiscoveryClient
from app.services.provider_call_trace import (
    ProviderCallTraceCollector,
    ProviderCallTraceError,
    attach_provider_call_trace,
    normalize_provider_call_trace,
    provider_request_hash,
)


_FAKE_KEY = "sk-test-provider-trace-key-123456789"
_VALID_REPORT_TEXT = (
    '{"title":"t","summary":"s","recommendations":[],"caveats":[]}'
)


def _clock() -> datetime:
    return datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


def test_collector_normalizes_hashes_metadata_and_never_retains_request_id() -> None:
    body = {
        "model": "requested-model",
        "messages": [{"role": "user", "content": "secret user material"}],
        "stream": False,
    }
    envelope = b'{"id":"provider-secret-id","choices":[]}'
    content = "模型结果"
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)

    collector.start_request(body)
    collector.mark_response_started(
        http_status=200,
        provider_request_id="provider-secret-id",
    )
    collector.observe_sync_envelope(envelope)
    collector.observe_metadata(
        actual_model="actual-model",
        finish_reason="stop",
        usage={
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        },
    )
    collector.observe_content(content)
    trace = collector.finish_success()

    assert trace == normalize_provider_call_trace(trace)
    assert trace["request_hash"] == provider_request_hash(body)
    assert trace["transport_envelope_sha256"] == hashlib.sha256(envelope).hexdigest()
    assert trace["transport_envelope_bytes"] == len(envelope)
    assert trace["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert trace["content_bytes"] == len(content.encode())
    assert trace["requested_model"] == "requested-model"
    assert trace["actual_model"] == "actual-model"
    assert trace["finish_reason"] == "stop"
    assert trace["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "prompt_cache_hit_tokens": None,
        "prompt_cache_miss_tokens": None,
    }
    assert trace["provider_request_id_hash"] == hashlib.sha256(
        b"provider-secret-id"
    ).hexdigest()
    assert "provider-secret-id" not in repr(trace)
    assert "secret user material" not in repr(trace)


def test_trace_hash_tamper_is_rejected_and_large_content_is_only_counted() -> None:
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)
    collector.start_request({"model": "m", "stream": False})
    collector.mark_response_started(http_status=200)
    collector.observe_sync_envelope(b"{}")
    large = "x" * (4 * 1024 * 1024 + 1)
    collector.observe_content(large)
    trace = collector.finish_success()

    assert trace["content_bytes"] == 4 * 1024 * 1024 + 1
    assert large not in repr(trace)
    tampered = dict(trace)
    tampered["actual_model"] = "tampered"
    with pytest.raises(ProviderCallTraceError, match="trace_hash mismatch"):
        normalize_provider_call_trace(tampered)


def test_collector_requires_stream_flag_to_match_actual_body() -> None:
    collector = ProviderCallTraceCollector(transport="stream", clock=_clock)
    with pytest.raises(ProviderCallTraceError, match="stream conflicts"):
        collector.start_request({"model": "m", "stream": False})


def test_validated_trace_can_be_attached_without_provider_bodies() -> None:
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)
    collector.start_request({"model": "m", "stream": False})
    collector.finish_error(outcome="timeout", error_category="connect_timeout")
    facts = {"pipeline": {"provider": "offline-fallback"}}

    attach_provider_call_trace(facts, collector.require_trace())

    trace = facts["pipeline"]["provider_call_trace"]
    assert trace["error_category"] == "connect_timeout"
    assert trace["outcome"] == "timeout"
    assert "messages" not in repr(trace)


class _SyncResponse:
    def __init__(self, payload: object, *, raw: bytes | None = None) -> None:
        self._payload = payload
        self.content = raw if raw is not None else json.dumps(payload).encode()
        self.status_code = 200
        self.headers = {"x-request-id": "sync-request-id"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _SyncClient:
    response: _SyncResponse
    captured: dict[str, Any]

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _SyncClient:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def post(self, _url: str, *, headers: dict, json: dict) -> _SyncResponse:
        self.captured["headers"] = headers
        self.captured["body"] = json
        return self.response


def test_discovery_sync_optional_trace_keeps_return_contract(monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    payload = {
        "id": "body-request-id",
        "model": "actual-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": _VALID_REPORT_TEXT},
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    _SyncClient.response = _SyncResponse(payload, raw=raw)
    _SyncClient.captured = {}
    monkeypatch.setattr(
        "app.services.discovery_client.get_deepseek_http_client",
        lambda _settings: _SyncClient(),
    )
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)

    result = DiscoveryClient()._call_model(
        "system",
        {"today": "2026-07-15"},
        "requested-model",
        trace_collector=collector,
    )
    trace = collector.require_trace()

    assert isinstance(result, dict)
    assert result["title"] == "t"
    assert trace["request_hash"] == provider_request_hash(_SyncClient.captured["body"])
    assert trace["transport_envelope_sha256"] == hashlib.sha256(raw).hexdigest()
    assert trace["content_sha256"] == hashlib.sha256(
        _VALID_REPORT_TEXT.encode()
    ).hexdigest()
    assert trace["actual_model"] == "actual-model"
    assert trace["finish_reason"] == "stop"
    assert trace["outcome"] == "success"
    assert "Authorization" not in repr(trace)
    refresh_settings()


@pytest.mark.parametrize(
    ("payload", "category"),
    [
        ([], "invalid_envelope"),
        ({"choices": [{"message": {"content": ""}}]}, "empty_content"),
        ({"choices": [{"message": {"content": "{}"}}]}, "invalid_json"),
    ],
)
def test_discovery_sync_trace_records_provider_output_failures(
    monkeypatch,
    payload: object,
    category: str,
) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    _SyncClient.response = _SyncResponse(payload)
    _SyncClient.captured = {}
    monkeypatch.setattr(
        "app.services.discovery_client.get_deepseek_http_client",
        lambda _settings: _SyncClient(),
    )
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)

    with pytest.raises(ProviderOutputError):
        DiscoveryClient()._call_model(
            "system",
            {},
            "m",
            trace_collector=collector,
        )

    trace = collector.require_trace()
    assert trace["outcome"] == "provider_error"
    assert trace["error_category"] == category
    refresh_settings()


def test_discovery_sync_trace_records_timeout_without_error_text(monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()

    class TimeoutClient(_SyncClient):
        def post(self, *_args: Any, **_kwargs: Any) -> _SyncResponse:
            raise httpx.ReadTimeout("secret provider response text")

    monkeypatch.setattr(
        "app.services.discovery_client.get_deepseek_http_client",
        lambda _settings: TimeoutClient(),
    )
    collector = ProviderCallTraceCollector(transport="sync", clock=_clock)

    with pytest.raises(httpx.ReadTimeout):
        DiscoveryClient()._call_model(
            "system", {}, "m", trace_collector=collector
        )

    trace = collector.require_trace()
    assert trace["outcome"] == "timeout"
    assert trace["error_category"] == "read_timeout"
    assert "secret provider response text" not in repr(trace)
    refresh_settings()


class _StreamResponse:
    def __init__(
        self,
        lines: list[str],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.lines = lines
        self.failure = failure
        self.status_code = 200
        self.headers = {"x-request-id": "stream-request-id"}

    def __enter__(self) -> _StreamResponse:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for line in self.lines:
            yield line
        if self.failure is not None:
            raise self.failure


def _patch_stream(monkeypatch, response: _StreamResponse) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_stream(_method: str, _url: str, **kwargs: Any) -> _StreamResponse:
        captured.update(kwargs)
        return response

    class FakeStreamClient:
        stream = staticmethod(fake_stream)

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.deepseek_streaming.get_deepseek_http_client",
        lambda _settings: FakeStreamClient(),
    )
    return captured


def test_stream_trace_hashes_actual_lines_content_and_metadata(monkeypatch) -> None:
    lines = [
        'data: {"id":"stream-body-id","model":"actual-model","choices":'
        '[{"delta":{"content":"你"},"finish_reason":null}]}',
        'data: {"model":"actual-model","choices":'
        '[{"delta":{"content":"好"},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
        "data: [DONE]",
    ]
    captured = _patch_stream(monkeypatch, _StreamResponse(lines))
    collector = ProviderCallTraceCollector(transport="stream", clock=_clock)

    chunks = list(
        stream_chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            model="requested-model",
            max_tokens=100,
            trace_collector=collector,
        )
    )
    trace = collector.require_trace()
    envelope = "".join(f"{line}\n" for line in lines).encode()

    assert chunks == ["你", "好"]
    assert captured["json"]["stream"] is True
    assert trace["request_hash"] == provider_request_hash(captured["json"])
    assert trace["transport_envelope_sha256"] == hashlib.sha256(envelope).hexdigest()
    assert trace["transport_envelope_bytes"] == len(envelope)
    assert trace["content_sha256"] == hashlib.sha256("你好".encode()).hexdigest()
    assert trace["chunk_count"] == 2
    assert trace["actual_model"] == "actual-model"
    assert trace["finish_reason"] == "stop"
    assert trace["usage"]["total_tokens"] == 6
    assert trace["outcome"] == "success"
    refresh_settings()


@pytest.mark.parametrize(
    ("lines", "expected_category"),
    [
        (["data: null", "data: [DONE]"], "invalid_envelope"),
        (["data: [DONE]"], "empty_content"),
    ],
)
def test_stream_trace_records_invalid_or_empty_envelope(
    monkeypatch,
    lines: list[str],
    expected_category: str,
) -> None:
    _patch_stream(monkeypatch, _StreamResponse(lines))
    collector = ProviderCallTraceCollector(transport="stream", clock=_clock)

    assert list(
        stream_chat_completion(
            messages=[],
            model="m",
            max_tokens=10,
            trace_collector=collector,
        )
    ) == []
    trace = collector.require_trace()
    assert trace["outcome"] == "provider_error"
    assert trace["error_category"] == expected_category
    assert trace["actual_model"] is None
    assert trace["finish_reason"] is None
    assert all(value is None for value in trace["usage"].values())
    refresh_settings()


def test_stream_success_allows_missing_optional_provider_metadata(monkeypatch) -> None:
    line = 'data: {"choices":[{"delta":{"content":"ok"}}]}'
    _patch_stream(monkeypatch, _StreamResponse([line, "data: [DONE]"]))
    collector = ProviderCallTraceCollector(transport="stream", clock=_clock)

    assert list(
        stream_chat_completion(
            messages=[],
            model="m",
            max_tokens=10,
            trace_collector=collector,
        )
    ) == ["ok"]
    trace = collector.require_trace()
    assert trace["outcome"] == "success"
    assert trace["actual_model"] is None
    assert trace["finish_reason"] is None
    assert all(value is None for value in trace["usage"].values())
    refresh_settings()


@pytest.mark.parametrize(
    ("failure", "outcome", "category"),
    [
        (httpx.ReadTimeout("private timeout"), "timeout", "read_timeout"),
        (
            httpx.StreamError("private interrupted payload"),
            "interrupted",
            "stream_interrupted",
        ),
    ],
)
def test_stream_trace_records_timeout_and_interruption(
    monkeypatch,
    failure: BaseException,
    outcome: str,
    category: str,
) -> None:
    line = 'data: {"choices":[{"delta":{"content":"partial"}}]}'
    _patch_stream(monkeypatch, _StreamResponse([line], failure=failure))
    collector = ProviderCallTraceCollector(transport="stream", clock=_clock)

    with pytest.raises(type(failure)):
        list(
            stream_chat_completion(
                messages=[],
                model="m",
                max_tokens=10,
                trace_collector=collector,
            )
        )
    trace = collector.require_trace()
    assert trace["outcome"] == outcome
    assert trace["error_category"] == category
    assert "private" not in repr(trace)
    refresh_settings()


def test_stream_default_api_remains_iterator_of_text(monkeypatch) -> None:
    line = 'data: {"choices":[{"delta":{"content":"ok"}}]}'
    _patch_stream(monkeypatch, _StreamResponse([line, "data: [DONE]"]))

    result = stream_chat_completion(messages=[], model="m", max_tokens=10)

    assert list(result) == ["ok"]
    refresh_settings()
