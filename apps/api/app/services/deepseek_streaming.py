"""DeepSeek chat completion 流式调用（阶段 2 报告生成）。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy

import httpx

from app.config import get_settings
from app.services.deepseek_client import _build_chat_payload
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
    get_deepseek_http_client,
)
from app.services.provider_call_trace import (
    ProviderCallTraceCollector,
    provider_request_id_from_headers,
)
from app.services.report_chat import _parse_stream_line

def stream_chat_completion(
    *,
    messages: list[dict],
    model: str,
    max_tokens: int,
    response_format: dict | None = None,
    trace_collector: ProviderCallTraceCollector | None = None,
    exact_provider_payload: Mapping[str, object] | None = None,
) -> Iterator[str]:
    """逐 chunk yield content 文本。"""
    settings = get_settings()
    if exact_provider_payload is not None:
        payload = deepcopy(dict(exact_provider_payload))
        if (
            payload.get("stream") is not True
            or payload.get("messages") != messages
            or payload.get("model") != model
            or payload.get("max_tokens") != max_tokens
        ):
            raise ValueError("exact streaming payload conflicts with call arguments")
    else:
        payload = _build_chat_payload(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            tools=None,
            response_format=response_format,
        )
        payload["stream"] = True
    if trace_collector is not None:
        trace_collector.start_request(payload)

    try:
        client = get_deepseek_http_client(settings)
        with client.stream(
            "POST",
            deepseek_chat_url(settings),
            headers=deepseek_request_headers(settings),
            json=payload,
            timeout=deepseek_timeout(settings),
        ) as response:
            if trace_collector is not None:
                trace_collector.mark_response_started(
                    http_status=getattr(response, "status_code", None),
                    provider_request_id=provider_request_id_from_headers(
                        getattr(response, "headers", None)
                    ),
                )
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                if trace_collector is not None:
                    trace_collector.observe_stream_line(line)
                chunk = _parse_stream_line(line)
                if chunk:
                    if trace_collector is not None:
                        trace_collector.observe_content(chunk)
                    yield chunk
        if trace_collector is not None:
            trace_collector.finish_success()
    except GeneratorExit:
        if trace_collector is not None and not trace_collector.finalized:
            trace_collector.finish_error(
                outcome="interrupted",
                error_category="consumer_cancelled",
            )
        raise
    except Exception as exc:
        if trace_collector is not None and not trace_collector.finalized:
            outcome, category = _stream_trace_failure(exc)
            trace_collector.finish_error(
                outcome=outcome,
                error_category=category,
            )
        raise


def _stream_trace_failure(exc: BaseException) -> tuple[str, str]:
    """Classify transport failures without persisting exception text."""

    if isinstance(exc, httpx.ConnectTimeout):
        return "timeout", "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "timeout", "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "timeout", "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "timeout", "pool_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_error", "http_status"
    if isinstance(exc, httpx.StreamError):
        return "interrupted", "stream_interrupted"
    if isinstance(exc, httpx.ConnectError):
        return "transport_error", "connection_error"
    if isinstance(exc, httpx.HTTPError):
        return "transport_error", "transport_error"
    return "provider_error", "unknown_provider_error"
