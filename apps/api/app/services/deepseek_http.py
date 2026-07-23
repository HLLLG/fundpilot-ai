from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
import time
from urllib.parse import urlsplit
from urllib.request import getproxies_environment, proxy_bypass_environment

import httpx

from app.config import Settings, get_settings


def deepseek_chat_url(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    return f"{resolved.deepseek_base_url.rstrip('/')}/chat/completions"


def deepseek_request_headers(settings: Settings | None = None) -> dict[str, str]:
    resolved = settings or get_settings()
    api_key = resolved.deepseek_api_key
    if not api_key:
        raise RuntimeError("DeepSeek API key is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


class DeepSeekBudgetExceeded(httpx.TimeoutException):
    """The bounded provider wall-clock budget was exhausted."""


def deepseek_request_deadline(settings: Settings | None = None) -> float | None:
    resolved = settings or get_settings()
    seconds = max(0.0, float(resolved.deepseek_request_budget_seconds))
    return None if seconds == 0 else time.monotonic() + seconds


def deepseek_budget_remaining(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        return None
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise DeepSeekBudgetExceeded("DeepSeek request budget exhausted")
    return remaining


def deepseek_timeout(
    settings: Settings | None = None,
    *,
    deadline_monotonic: float | None = None,
    first_byte_watchdog: bool = False,
) -> httpx.Timeout:
    resolved = settings or get_settings()
    remaining = deepseek_budget_remaining(deadline_monotonic)
    read_seconds = float(resolved.deepseek_timeout_seconds)
    if first_byte_watchdog:
        watchdog = max(0.0, float(resolved.deepseek_first_byte_timeout_seconds))
        if watchdog > 0:
            read_seconds = min(read_seconds, watchdog)
    if remaining is not None:
        read_seconds = min(read_seconds, remaining)

    def bounded(value: float) -> float:
        if remaining is None:
            return value
        return max(0.001, min(value, remaining))

    return httpx.Timeout(
        connect=bounded(10),
        read=max(0.001, read_seconds),
        write=bounded(30),
        pool=bounded(10),
    )


_CLIENTS_LOCK = Lock()
_SHARED_CLIENTS: dict[tuple[float, int, str | None], httpx.Client] = {}


def _environment_proxy_for(url: str) -> str | None:
    parsed = urlsplit(url)
    host = parsed.hostname
    proxies = getproxies_environment()
    if not host or proxy_bypass_environment(host, proxies):
        return None
    return proxies.get(parsed.scheme.lower()) or proxies.get("all")


def get_deepseek_http_client(settings: Settings | None = None) -> httpx.Client:
    """Return a process-wide client with connection-only retries.

    Authorization remains request-scoped, so rotating a key never leaves the
    previous credential in a pooled client. HTTPX documents ``Client`` as
    shareable between threads and ``HTTPTransport(retries=...)`` as retrying
    only ConnectError/ConnectTimeout.
    """

    resolved = settings or get_settings()
    signature = (
        float(resolved.deepseek_timeout_seconds),
        max(0, int(resolved.deepseek_connection_retries)),
        _environment_proxy_for(resolved.deepseek_base_url),
    )
    with _CLIENTS_LOCK:
        existing = _SHARED_CLIENTS.get(signature)
        if existing is not None and not existing.is_closed:
            return existing
        client = httpx.Client(
            timeout=deepseek_timeout(resolved),
            transport=httpx.HTTPTransport(
                retries=signature[1],
                proxy=signature[2],
            ),
        )
        _SHARED_CLIENTS[signature] = client
        return client


def create_interruptible_deepseek_http_client(
    settings: Settings | None = None,
) -> httpx.Client:
    """Create a request-owned client that can be closed on SSE disconnect.

    A shared client cannot be closed to interrupt one request without
    disrupting unrelated reports.  Streaming calls therefore own this small
    client while non-streaming provider calls continue to use the pooled
    process-wide client above.
    """

    resolved = settings or get_settings()
    return httpx.Client(
        timeout=deepseek_timeout(resolved),
        transport=httpx.HTTPTransport(
            retries=max(0, int(resolved.deepseek_connection_retries)),
            proxy=_environment_proxy_for(resolved.deepseek_base_url),
        ),
    )


def close_deepseek_http_clients() -> None:
    """Close pooled provider connections during application shutdown/tests."""

    with _CLIENTS_LOCK:
        clients = list(_SHARED_CLIENTS.values())
        _SHARED_CLIENTS.clear()
    for client in clients:
        client.close()


@dataclass(frozen=True)
class ProviderFailure:
    """Sanitized failure metadata safe to persist and return to clients."""

    category: str
    message: str
    retryable: bool
    status_code: int | None = None
    detail_category: str | None = None
    retry_after_seconds: int | None = None

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    raw = str(response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        seconds = max(0, int(float(raw)))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = max(
                0,
                int(
                    (
                        retry_at.astimezone(timezone.utc)
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )
        except (TypeError, ValueError, OverflowError):
            return None
    return min(seconds, 300)


class ProviderOutputError(RuntimeError):
    """A provider responded, but its content cannot satisfy the report schema."""

    def __init__(self, category: str) -> None:
        if category not in {"empty_content", "invalid_json"}:
            raise ValueError(f"unsupported provider output category: {category}")
        self.category = category
        super().__init__(category)


def classify_deepseek_failure(exc: BaseException) -> ProviderFailure:
    """Map transport/output errors to a stable, redacted public category.

    Response bodies, request headers, URLs and exception strings are excluded on
    purpose: upstream payloads can echo credentials or user input.
    """

    if isinstance(exc, ProviderOutputError):
        if exc.category == "empty_content":
            return ProviderFailure(
                category="empty_content",
                message="模型返回空内容，已切换为不可执行的离线观察报告。",
                retryable=True,
            )
        return ProviderFailure(
            category="invalid_json",
            message="模型返回内容未通过 JSON 合同校验，已切换为不可执行的离线观察报告。",
            retryable=True,
        )
    if isinstance(exc, httpx.TimeoutException):
        detail_category = "timeout"
        if isinstance(exc, DeepSeekBudgetExceeded):
            detail_category = "request_budget"
        elif isinstance(exc, httpx.ConnectTimeout):
            detail_category = "connect_timeout"
        elif isinstance(exc, httpx.ReadTimeout):
            detail_category = "read_timeout"
        elif isinstance(exc, httpx.WriteTimeout):
            detail_category = "write_timeout"
        elif isinstance(exc, httpx.PoolTimeout):
            detail_category = "pool_timeout"
        return ProviderFailure(
            category="timeout",
            message="模型调用超时，已切换为不可执行的离线观察报告。",
            retryable=True,
            detail_category=detail_category,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return ProviderFailure(
                category="authentication",
                message="模型服务认证失败，已切换为不可执行的离线观察报告。",
                retryable=False,
                status_code=status,
            )
        if status == 402:
            return ProviderFailure(
                category="account_balance",
                message="模型服务账户不可用，已切换为不可执行的离线观察报告。",
                retryable=False,
                status_code=status,
            )
        if status == 429:
            return ProviderFailure(
                category="rate_limited",
                message="模型服务触发限流，已切换为不可执行的离线观察报告。",
                retryable=True,
                status_code=status,
                retry_after_seconds=_retry_after_seconds(exc.response),
            )
        if 500 <= status <= 599:
            return ProviderFailure(
                category="provider_5xx",
                message="模型服务暂时异常，已切换为不可执行的离线观察报告。",
                retryable=True,
                status_code=status,
            )
        return ProviderFailure(
            category="provider_4xx",
            message="模型请求未被服务接受，已切换为不可执行的离线观察报告。",
            retryable=False,
            status_code=status,
        )
    if isinstance(exc, httpx.ConnectError):
        return ProviderFailure(
            category="connection",
            message="无法连接模型服务，已切换为不可执行的离线观察报告。",
            retryable=True,
            detail_category="connect_error",
        )
    if isinstance(exc, httpx.StreamError):
        return ProviderFailure(
            category="stream_error",
            message="模型流式传输中断，已切换为不可执行的离线观察报告。",
            retryable=True,
        )
    if isinstance(exc, httpx.HTTPError):
        return ProviderFailure(
            category="transport_error",
            message="模型网络请求失败，已切换为不可执行的离线观察报告。",
            retryable=True,
        )
    return ProviderFailure(
        category="unknown",
        message="模型调用失败，已切换为不可执行的离线观察报告。",
        retryable=False,
    )


def format_deepseek_http_error(exc: BaseException) -> str:
    """Backward-compatible public message with no upstream response body."""

    return classify_deepseek_failure(exc).message
