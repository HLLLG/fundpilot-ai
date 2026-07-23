from __future__ import annotations

import threading
import time

import httpx
import pytest

from app.config import Settings, refresh_settings
from app.services.deepseek_http import (
    DeepSeekBudgetExceeded,
    deepseek_timeout,
)
from app.services.deepseek_streaming import stream_chat_completion
from app.services.streaming_heartbeat import StreamCancelled


def test_deadline_bounds_every_httpx_timeout_phase() -> None:
    settings = Settings(
        _env_file=None,
        deepseek_timeout_seconds=300,
        deepseek_first_byte_timeout_seconds=60,
    )
    timeout = deepseek_timeout(
        settings,
        deadline_monotonic=time.monotonic() + 2,
        first_byte_watchdog=True,
    )

    assert timeout.connect is not None and timeout.connect <= 2
    assert timeout.read is not None and timeout.read <= 2
    assert timeout.write is not None and timeout.write <= 2
    assert timeout.pool is not None and timeout.pool <= 2


def test_expired_deadline_fails_before_opening_provider_connection() -> None:
    with pytest.raises(DeepSeekBudgetExceeded):
        deepseek_timeout(
            Settings(_env_file=None),
            deadline_monotonic=time.monotonic() - 1,
        )


def test_stream_wall_clock_budget_closes_blocked_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = threading.Event()

    class BlockingResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.set()

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            closed.wait(2)
            raise httpx.ReadError("closed by watchdog")
            yield ""  # pragma: no cover

        def close(self) -> None:
            closed.set()

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return BlockingResponse()

        def close(self) -> None:
            closed.set()

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "x" * 32)
    monkeypatch.setenv("FUND_AI_DEEPSEEK_REQUEST_BUDGET_SECONDS", "0.15")
    monkeypatch.setenv("FUND_AI_DEEPSEEK_FIRST_BYTE_TIMEOUT_SECONDS", "1")
    refresh_settings()
    monkeypatch.setattr(
        "app.services.deepseek_streaming.create_interruptible_deepseek_http_client",
        lambda _settings: FakeClient(),
    )

    started = time.monotonic()
    with pytest.raises(DeepSeekBudgetExceeded):
        list(
            stream_chat_completion(
                messages=[],
                model="test",
                max_tokens=10,
            )
        )

    assert closed.is_set()
    assert time.monotonic() - started < 1
    refresh_settings()


def test_disconnect_interrupts_request_before_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = threading.Event()

    class BlockingOpen:
        def __enter__(self):
            closed.wait(2)
            raise httpx.ReadError("client closed before headers")

        def __exit__(self, *_args):
            return False

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return BlockingOpen()

        def close(self) -> None:
            closed.set()

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "x" * 32)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.deepseek_streaming.create_interruptible_deepseek_http_client",
        lambda _settings: FakeClient(),
    )
    stop_event = threading.Event()
    timer = threading.Timer(0.1, stop_event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(StreamCancelled):
            list(
                stream_chat_completion(
                    messages=[],
                    model="test",
                    max_tokens=10,
                    stop_event=stop_event,
                )
            )
    finally:
        timer.cancel()
        refresh_settings()

    assert closed.is_set()
    assert time.monotonic() - started < 1
