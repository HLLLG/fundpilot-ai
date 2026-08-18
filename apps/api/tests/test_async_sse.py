import asyncio
import contextvars
import threading
import time

from app.services.async_sse import (
    SSE_HTTP2_PADDING,
    format_sse_event,
    sse_connected_prelude,
    sse_from_sync_iterator,
)


def test_sse_connected_prelude_forces_an_early_flush() -> None:
    event, padding = sse_connected_prelude("已连接服务端，正在启动扫描…")
    assert event.startswith("data: ")
    assert '"stage": "connected"' in event
    assert padding == SSE_HTTP2_PADDING
    assert padding.startswith(": ")
    assert padding.endswith("\n\n")
    assert len(padding) > 2048
    assert format_sse_event({"type": "done"}) == 'data: {"type": "done"}\n\n'


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


def test_two_sse_producers_emit_independently_on_one_loop() -> None:
    """日报与荐基各占一条 SSE：同一事件循环上两条生产者必须都能出完。"""

    def produce(name: str, count: int = 3):
        for index in range(count):
            time.sleep(0.01)
            yield {"type": "stage", "name": name, "index": index}
        yield {"type": "done", "name": name}

    async def collect_both() -> tuple[list[str], list[str]]:
        left: list[str] = []
        right: list[str] = []

        async def drain(items, bucket: list[str]) -> None:
            async for chunk in sse_from_sync_iterator(items):
                bucket.append(chunk)

        await asyncio.gather(
            drain(produce("analyze"), left),
            drain(produce("discovery"), right),
        )
        return left, right

    left, right = asyncio.run(collect_both())
    assert any('"name": "analyze"' in chunk for chunk in left)
    assert any('"type": "done"' in chunk and '"name": "analyze"' in chunk for chunk in left)
    assert any('"name": "discovery"' in chunk for chunk in right)
    assert any('"type": "done"' in chunk and '"name": "discovery"' in chunk for chunk in right)
    assert len(left) == 4
    assert len(right) == 4
