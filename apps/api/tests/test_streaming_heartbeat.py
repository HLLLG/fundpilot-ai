from __future__ import annotations

import threading
import time

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
