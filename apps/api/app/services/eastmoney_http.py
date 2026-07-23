from __future__ import annotations

import contextvars
import functools
import random
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from queue import Empty, LifoQueue
from threading import Condition, Lock
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
import requests

from app.config import get_settings


_T = TypeVar("_T")
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "eastmoney_deadline",
    default=None,
)

_gate = Condition(Lock())
_active_requests = 0
_circuit_lock = Lock()
_circuit_failures: dict[str, int] = {}
_circuit_open_until: dict[str, float] = {}

_httpx_client: httpx.Client | None = None
_httpx_client_lock = Lock()

_requests_pool: LifoQueue[requests.Session] = LifoQueue()
_requests_pool_lock = Lock()
_requests_sessions_created = 0


class EastmoneyDeadlineExceeded(TimeoutError):
    pass


class EastmoneyCircuitOpen(RuntimeError):
    pass


def _remaining_seconds() -> float | None:
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EastmoneyDeadlineExceeded("Eastmoney call deadline exhausted")
    return remaining


def eastmoney_budgeted(func: Callable[..., _T]) -> Callable[..., _T]:
    """Apply one wall-clock budget across nested host/parameter fallbacks."""

    @functools.wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> _T:
        existing = _DEADLINE.get()
        token = None
        if existing is None:
            seconds = max(
                0.0,
                float(get_settings().eastmoney_call_deadline_seconds),
            )
            if seconds > 0:
                token = _DEADLINE.set(time.monotonic() + seconds)
        try:
            return func(*args, **kwargs)
        finally:
            if token is not None:
                _DEADLINE.reset(token)

    return wrapped


def eastmoney_backoff(attempt: int, *, base_seconds: float) -> None:
    delay = max(0.0, base_seconds) * max(1, attempt + 1)
    delay *= random.uniform(0.8, 1.2)
    remaining = _remaining_seconds()
    if remaining is not None:
        delay = min(delay, remaining)
    if delay > 0:
        time.sleep(delay)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _check_circuit(url: str) -> None:
    host = _host(url)
    if not host:
        return
    now = time.monotonic()
    with _circuit_lock:
        open_until = _circuit_open_until.get(host, 0.0)
        if open_until > now:
            raise EastmoneyCircuitOpen(f"Eastmoney host circuit open: {host}")
        if open_until:
            _circuit_open_until.pop(host, None)
            _circuit_failures[host] = 0


def _record_result(
    url: str,
    *,
    status_code: int | None = None,
    failed: bool = False,
) -> None:
    host = _host(url)
    if not host:
        return
    is_failure = failed or status_code == 429 or (
        status_code is not None and status_code >= 500
    )
    with _circuit_lock:
        if not is_failure:
            _circuit_failures[host] = 0
            _circuit_open_until.pop(host, None)
            return
        failures = _circuit_failures.get(host, 0) + 1
        _circuit_failures[host] = failures
        settings = get_settings()
        if failures >= max(1, int(settings.eastmoney_circuit_failure_threshold)):
            _circuit_open_until[host] = (
                time.monotonic()
                + max(1.0, float(settings.eastmoney_circuit_cooldown_seconds))
            )


@contextmanager
def _request_slot(url: str) -> Iterator[None]:
    global _active_requests
    _check_circuit(url)
    settings = get_settings()
    limit = max(0, int(settings.eastmoney_max_concurrency))
    wait_seconds = max(0.01, float(settings.eastmoney_acquire_timeout_seconds))
    remaining = _remaining_seconds()
    if remaining is not None:
        wait_seconds = min(wait_seconds, remaining)
    wait_until = time.monotonic() + wait_seconds

    counted = limit > 0
    if counted:
        with _gate:
            while _active_requests >= limit:
                remaining_wait = wait_until - time.monotonic()
                if remaining_wait <= 0:
                    raise httpx.PoolTimeout(
                        "Eastmoney provider concurrency budget exhausted"
                    )
                _gate.wait(timeout=remaining_wait)
            _active_requests += 1
    try:
        yield
    finally:
        if counted:
            with _gate:
                _active_requests = max(0, _active_requests - 1)
                _gate.notify()


def _bounded_timeout(value: Any) -> Any:
    remaining = _remaining_seconds()
    if remaining is None:
        return value
    if value is None:
        return remaining
    if isinstance(value, (int, float)):
        return max(0.001, min(float(value), remaining))
    if isinstance(value, tuple):
        return tuple(
            max(0.001, min(float(item), remaining))
            for item in value
        )
    return value


def _clean_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() != "connection"
    }


class EastmoneyHttpxClient:
    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: Any = None,
    ) -> None:
        self.headers = _clean_headers(headers)
        self.timeout = timeout

    def __enter__(self) -> EastmoneyHttpxClient:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        request_headers = dict(self.headers)
        request_headers.update(_clean_headers(kwargs.pop("headers", None)))
        timeout = _bounded_timeout(kwargs.pop("timeout", self.timeout))
        try:
            with _request_slot(url):
                response = _shared_httpx_client().get(
                    url,
                    headers=request_headers,
                    timeout=timeout,
                    **kwargs,
                )
        except (EastmoneyCircuitOpen, httpx.PoolTimeout):
            # Local shedding is not a fresh provider failure and must not keep
            # extending an already-open circuit under sustained traffic.
            raise
        except Exception:
            _record_result(url, failed=True)
            raise
        _record_result(url, status_code=response.status_code)
        return response


def eastmoney_httpx_client(
    *,
    headers: Mapping[str, str] | None = None,
    timeout: Any = None,
    **_kwargs: Any,
) -> EastmoneyHttpxClient:
    return EastmoneyHttpxClient(headers=headers, timeout=timeout)


def _shared_httpx_client() -> httpx.Client:
    global _httpx_client
    with _httpx_client_lock:
        if _httpx_client is None or _httpx_client.is_closed:
            _httpx_client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=32,
                    max_keepalive_connections=16,
                    keepalive_expiry=30.0,
                ),
                trust_env=False,
                follow_redirects=True,
                http2=False,
            )
        return _httpx_client


def _new_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=4,
        pool_maxsize=4,
        max_retries=0,
        pool_block=True,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _borrow_requests_session() -> requests.Session:
    global _requests_sessions_created
    try:
        return _requests_pool.get_nowait()
    except Empty:
        pass
    limit = max(1, int(get_settings().eastmoney_max_concurrency or 8))
    with _requests_pool_lock:
        if _requests_sessions_created < limit:
            _requests_sessions_created += 1
            return _new_requests_session()
    wait = max(0.01, float(get_settings().eastmoney_acquire_timeout_seconds))
    remaining = _remaining_seconds()
    if remaining is not None:
        wait = min(wait, remaining)
    try:
        return _requests_pool.get(timeout=wait)
    except Empty as exc:
        raise requests.Timeout(
            "Eastmoney requests session pool exhausted"
        ) from exc


class EastmoneyRequestsClient:
    def __init__(self, headers: Mapping[str, str] | None = None) -> None:
        self.headers = requests.structures.CaseInsensitiveDict(
            _clean_headers(headers)
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        request_headers = dict(self.headers)
        request_headers.update(_clean_headers(kwargs.pop("headers", None)))
        kwargs["timeout"] = _bounded_timeout(kwargs.get("timeout"))
        session = _borrow_requests_session()
        try:
            with _request_slot(url):
                response = session.get(
                    url,
                    headers=request_headers,
                    **kwargs,
                )
        except (EastmoneyCircuitOpen, httpx.PoolTimeout):
            raise
        except Exception:
            _record_result(url, failed=True)
            raise
        finally:
            _requests_pool.put(session)
        _record_result(url, status_code=response.status_code)
        return response


def eastmoney_requests_client(
    headers: Mapping[str, str] | None = None,
) -> EastmoneyRequestsClient:
    return EastmoneyRequestsClient(headers)


def close_eastmoney_http_clients() -> None:
    global _httpx_client, _requests_sessions_created
    with _httpx_client_lock:
        client = _httpx_client
        _httpx_client = None
    if client is not None:
        client.close()
    while True:
        try:
            session = _requests_pool.get_nowait()
        except Empty:
            break
        session.close()
    with _requests_pool_lock:
        # Lifespan shutdown is also exercised repeatedly by TestClient.  Reset
        # the accounting alongside the drained pool so the next lifespan can
        # lazily create sessions instead of waiting on an empty queue.
        _requests_sessions_created = 0
