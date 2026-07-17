from __future__ import annotations

import httpx
import pytest

from app.services.deepseek_http import (
    ProviderOutputError,
    classify_deepseek_failure,
    format_deepseek_http_error,
)


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "authentication", False),
        (402, "account_balance", False),
        (429, "rate_limited", True),
        (500, "provider_5xx", True),
        (503, "provider_5xx", True),
        (422, "provider_4xx", False),
    ],
)
def test_http_failure_categories_are_stable_and_redacted(
    status: int,
    category: str,
    retryable: bool,
):
    request = httpx.Request("POST", "https://api.invalid/chat")
    response = httpx.Response(
        status,
        request=request,
        text="secret-api-key user-private-prompt",
    )
    exc = httpx.HTTPStatusError("raw secret", request=request, response=response)

    failure = classify_deepseek_failure(exc)

    assert failure.category == category
    assert failure.retryable is retryable
    assert failure.status_code == status
    assert "secret-api-key" not in failure.message
    assert "user-private-prompt" not in format_deepseek_http_error(exc)


@pytest.mark.parametrize(
    ("exc", "category", "detail_category"),
    [
        (httpx.ConnectTimeout("secret"), "timeout", "connect_timeout"),
        (httpx.ReadTimeout("secret"), "timeout", "read_timeout"),
        (httpx.WriteTimeout("secret"), "timeout", "write_timeout"),
        (httpx.PoolTimeout("secret"), "timeout", "pool_timeout"),
        (httpx.ConnectError("secret"), "connection", "connect_error"),
        (ProviderOutputError("empty_content"), "empty_content", None),
        (ProviderOutputError("invalid_json"), "invalid_json", None),
    ],
)
def test_non_status_failures_are_classified_without_exception_text(
    exc: BaseException,
    category: str,
    detail_category: str | None,
):
    failure = classify_deepseek_failure(exc)

    assert failure.category == category
    assert failure.detail_category == detail_category
    assert "secret" not in failure.message
