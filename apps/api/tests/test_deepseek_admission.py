from __future__ import annotations

import threading
import time

import httpx
import pytest

from app.services import deepseek_http
from app.services.streaming_heartbeat import StreamCancelled


@pytest.fixture(autouse=True)
def _reset_admission():
    deepseek_http.reset_deepseek_admission_for_tests()
    yield
    deepseek_http.reset_deepseek_admission_for_tests()


def _settings(**overrides):
    values = {
        "deepseek_max_concurrent_streams": 1,
        "deepseek_max_concurrent_requests": 2,
        "deepseek_stream_acquire_timeout_seconds": 0.2,
        "deepseek_acquire_timeout_seconds": 0.2,
    }
    values.update(overrides)
    return type("Settings", (), values)()


def test_stream_slot_serializes_second_stream(monkeypatch):
    monkeypatch.setattr(deepseek_http, "get_settings", lambda: _settings())
    first_inside = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    errors: list[BaseException] = []

    def hold_stream() -> None:
        try:
            with deepseek_http.deepseek_stream_slot():
                first_inside.set()
                release.wait(2)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def wait_stream() -> None:
        second_started.set()
        try:
            with deepseek_http.deepseek_stream_slot():
                pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    holder = threading.Thread(target=hold_stream)
    waiter = threading.Thread(target=wait_stream)
    holder.start()
    assert first_inside.wait(1)
    waiter.start()
    assert second_started.wait(1)
    time.sleep(0.05)
    assert waiter.is_alive()
    release.set()
    holder.join(2)
    waiter.join(2)
    assert not errors


def test_stream_slot_times_out_instead_of_stacking(monkeypatch):
    monkeypatch.setattr(deepseek_http, "get_settings", lambda: _settings())
    release = threading.Event()
    inside = threading.Event()

    def hold_stream() -> None:
        with deepseek_http.deepseek_stream_slot():
            inside.set()
            release.wait(2)

    holder = threading.Thread(target=hold_stream)
    holder.start()
    assert inside.wait(1)
    with pytest.raises(httpx.PoolTimeout):
        with deepseek_http.deepseek_stream_slot():
            raise AssertionError("second stream must not be admitted")
    release.set()
    holder.join(2)


def test_request_slot_is_independent_of_stream_slot(monkeypatch):
    monkeypatch.setattr(deepseek_http, "get_settings", lambda: _settings())
    release = threading.Event()
    inside = threading.Event()

    def hold_stream() -> None:
        with deepseek_http.deepseek_stream_slot():
            inside.set()
            release.wait(2)

    holder = threading.Thread(target=hold_stream)
    holder.start()
    assert inside.wait(1)
    with deepseek_http.deepseek_request_slot():
        pass
    release.set()
    holder.join(2)


def test_zero_limits_disable_gates(monkeypatch):
    monkeypatch.setattr(
        deepseek_http,
        "get_settings",
        lambda: _settings(
            deepseek_max_concurrent_streams=0,
            deepseek_max_concurrent_requests=0,
        ),
    )
    with deepseek_http.deepseek_stream_slot():
        with deepseek_http.deepseek_stream_slot():
            with deepseek_http.deepseek_request_slot():
                with deepseek_http.deepseek_request_slot():
                    pass


def test_stream_slot_respects_cancel(monkeypatch):
    monkeypatch.setattr(
        deepseek_http,
        "get_settings",
        lambda: _settings(deepseek_stream_acquire_timeout_seconds=2),
    )
    release = threading.Event()
    inside = threading.Event()
    cancelled = threading.Event()
    cancelled.set()

    def hold_stream() -> None:
        with deepseek_http.deepseek_stream_slot():
            inside.set()
            release.wait(2)

    holder = threading.Thread(target=hold_stream)
    holder.start()
    assert inside.wait(1)
    with pytest.raises(StreamCancelled):
        with deepseek_http.deepseek_stream_slot(is_cancelled=cancelled.is_set):
            raise AssertionError("cancelled waiter must not enter")
    release.set()
    holder.join(2)
