import asyncio
import contextvars
import threading
import time

from app.services.async_sse import sse_from_sync_iterator


def test_sse_from_sync_iterator_emits_json_events():
    async def collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in sse_from_sync_iterator(iter([{"type": "stage"}, {"type": "done"}])):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert len(chunks) == 2
    assert '"type": "stage"' in chunks[0]
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")


def test_sse_disconnect_stops_sync_producer_promptly():
    stop_event = threading.Event()
    producer_started = threading.Event()
    producer_stopped = threading.Event()

    def produce():
        try:
            index = 0
            while not stop_event.is_set():
                producer_started.set()
                yield {"type": "stage", "index": index}
                index += 1
                time.sleep(0.01)
        finally:
            producer_stopped.set()

    async def collect_until_disconnect() -> list[str]:
        checks = 0

        async def is_disconnected() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 2

        chunks: list[str] = []
        async for chunk in sse_from_sync_iterator(
            produce(),
            stop_event=stop_event,
            is_disconnected=is_disconnected,
            disconnect_poll_seconds=0.02,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_until_disconnect())

    assert producer_started.wait(1)
    assert chunks
    assert stop_event.is_set()
    assert producer_stopped.wait(1)


def test_sync_producer_inherits_request_context() -> None:
    marker = contextvars.ContextVar("marker", default="missing")
    token = marker.set("request-user")

    def items():
        yield {"value": marker.get()}

    async def collect() -> list[str]:
        return [chunk async for chunk in sse_from_sync_iterator(items())]

    try:
        chunks = asyncio.run(collect())
    finally:
        marker.reset(token)

    assert '"value": "request-user"' in chunks[0]
