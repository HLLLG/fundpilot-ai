from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

logger = logging.getLogger(__name__)


async def sse_from_sync_iterator(
    items: Iterator[dict[str, Any]],
    *,
    stop_event: threading.Event | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    disconnect_poll_seconds: float = 0.25,
) -> AsyncIterator[str]:
    """Bridge a blocking generator to SSE with cooperative disconnect cleanup.

    The producer used to wait up to 600 seconds on a full asyncio queue after
    the client had gone away. The shared stop event now propagates cancellation
    into the sync pipeline, while short bounded waits keep the daemon producer
    responsive even under backpressure.
    """

    stop = stop_event or threading.Event()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)
    producer_done = threading.Event()

    def put_on_event_loop(payload: dict[str, Any] | None) -> bool:
        try:
            pending = asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
        except RuntimeError:
            return False
        while not stop.is_set():
            try:
                pending.result(timeout=disconnect_poll_seconds)
                return True
            except concurrent.futures.TimeoutError:
                continue
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                return False
        pending.cancel()
        return False

    def producer() -> None:
        try:
            for payload in items:
                if stop.is_set() or not put_on_event_loop(payload):
                    break
        except Exception:  # noqa: BLE001 — producer failures terminate this SSE
            if not stop.is_set():
                logger.exception("sync SSE producer failed")
        finally:
            close = getattr(items, "close", None)
            if callable(close):
                try:
                    close()
                except (RuntimeError, ValueError):
                    # A generator can still be unwinding after a provider
                    # response was closed from the cancellation watcher.
                    pass
            if not stop.is_set():
                put_on_event_loop(None)
            producer_done.set()

    producer_context = contextvars.copy_context()
    producer_thread = threading.Thread(
        target=lambda: producer_context.run(producer),
        name="sse-producer",
        daemon=True,
    )
    producer_thread.start()
    poll_seconds = max(0.05, float(disconnect_poll_seconds))
    try:
        while True:
            if is_disconnected is not None and await is_disconnected():
                stop.set()
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=poll_seconds)
            except TimeoutError:
                if producer_done.is_set():
                    break
                continue
            if payload is None:
                break
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        stop.set()
        raise
    finally:
        stop.set()
        if producer_thread.is_alive():
            await asyncio.to_thread(producer_thread.join, 1.0)
