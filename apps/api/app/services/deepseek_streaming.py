"""DeepSeek chat completion 流式调用（阶段 2 报告生成）。"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from app.config import get_settings
from app.services.deepseek_client import _build_chat_payload
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
)
from app.services.report_chat import _parse_stream_line

# 流式路径：chunk 间最大空闲 30s（总耗时不受此限）
_STREAM_READ_TIMEOUT_SECONDS = 30.0


def stream_chat_completion(
    *,
    messages: list[dict],
    model: str,
    max_tokens: int,
    response_format: dict | None = None,
) -> Iterator[str]:
    """逐 chunk yield content 文本。"""
    settings = get_settings()
    payload = _build_chat_payload(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        tools=None,
        response_format=response_format,
    )
    payload["stream"] = True

    timeout = httpx.Timeout(
        connect=10.0,
        read=_STREAM_READ_TIMEOUT_SECONDS,
        write=10.0,
        pool=10.0,
    )

    with httpx.stream(
        "POST",
        deepseek_chat_url(settings),
        headers=deepseek_request_headers(settings),
        json=payload,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = _parse_stream_line(line)
            if chunk:
                yield chunk
