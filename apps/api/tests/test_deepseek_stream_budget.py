from __future__ import annotations

import threading
import time

from app.services import deepseek_http, deepseek_streaming


def test_stream_request_budget_starts_after_slot_admission(monkeypatch) -> None:
    deepseek_http.reset_deepseek_admission_for_tests()
    settings = type(
        "Settings",
        (),
        {
            "deepseek_max_concurrent_streams": 1,
            "deepseek_max_concurrent_requests": 2,
            "deepseek_stream_acquire_timeout_seconds": 2,
            "deepseek_acquire_timeout_seconds": 0.2,
            "deepseek_request_budget_seconds": 180,
        },
    )()
    monkeypatch.setattr(deepseek_http, "get_settings", lambda: settings)
    monkeypatch.setattr(deepseek_streaming, "get_settings", lambda: settings)

    budget_started_at: list[float] = []

    def start_budget(_settings=None) -> float:
        now = time.monotonic()
        budget_started_at.append(now)
        return now + 180

    monkeypatch.setattr(deepseek_streaming, "deepseek_request_deadline", start_budget)

    def admitted(**_kwargs):
        yield "ok"

    monkeypatch.setattr(
        deepseek_streaming,
        "_stream_chat_completion_admitted",
        admitted,
    )

    first_inside = threading.Event()
    release = threading.Event()

    def hold_slot() -> None:
        with deepseek_http.deepseek_stream_slot():
            first_inside.set()
            release.wait(2)

    holder = threading.Thread(target=hold_slot)
    holder.start()
    assert first_inside.wait(1)

    errors: list[BaseException] = []

    def wait_then_stream() -> None:
        try:
            list(
                deepseek_streaming.stream_chat_completion(
                    messages=[{"role": "user", "content": "x"}],
                    model="test-model",
                    max_tokens=16,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    waiter = threading.Thread(target=wait_then_stream)
    waiter.start()
    time.sleep(0.08)
    released_at = time.monotonic()
    release.set()
    waiter.join(2)
    holder.join(2)
    deepseek_http.reset_deepseek_admission_for_tests()

    assert not errors
    assert budget_started_at
    assert budget_started_at[0] >= released_at - 0.02
