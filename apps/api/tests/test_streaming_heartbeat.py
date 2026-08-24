from __future__ import annotations

import threading
import time

import httpx

from app.services.streaming_heartbeat import iter_with_heartbeat


def test_closing_heartbeat_iterator_stops_source_at_its_next_yield() -> None:
    allow_next_yield = threading.Event()
    source_closed = threading.Event()
    stop_event = threading.Event()

    def source():
        try:
            yield "first"
            allow_next_yield.wait(timeout=1)
            yield "second"
            raise AssertionError("source continued after the consumer closed")
        finally:
            source_closed.set()

    wrapped = iter_with_heartbeat(
        source(),
        heartbeat_seconds=0.01,
        heartbeat_factory=lambda: "heartbeat",
        stop_event=stop_event,
    )
    assert next(wrapped) == "first"
    wrapped.close()
    assert stop_event.is_set()
    allow_next_yield.set()

    deadline = time.monotonic() + 1
    while not source_closed.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert source_closed.is_set()


def test_source_error_does_not_cancel_shared_pipeline_stop_event() -> None:
    """LLM/provider failures must stay recoverable.

    Daily and discovery catch PoolTimeout / HTTPError and then yield an
    offline ``done`` event on the same stop_event. Treating that failure as a
    client disconnect used to drop the fallback on the SSE bridge.
    """

    stop_event = threading.Event()

    def source():
        yield "partial"
        raise httpx.PoolTimeout("DeepSeek concurrency budget exhausted")

    events: list[object] = []
    try:
        for item in iter_with_heartbeat(
            source(),
            heartbeat_seconds=10,
            heartbeat_factory=lambda: "heartbeat",
            stop_event=stop_event,
        ):
            events.append(item)
    except httpx.PoolTimeout:
        events.append("recovered")

    assert events == ["partial", "recovered"]
    assert not stop_event.is_set()
