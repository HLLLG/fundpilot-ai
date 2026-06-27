from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

logger = logging.getLogger(__name__)


async def sse_from_sync_iterator(items: Iterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Run a blocking sync generator on a daemon thread; yield SSE lines on the event loop."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)

    def producer() -> None:
        try:
            for payload in items:
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop).result(timeout=600)
        except Exception:  # noqa: BLE001 — stream errors surface via queue sentinel
            logger.exception("sync SSE producer failed")
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    threading.Thread(target=producer, name="sse-producer", daemon=True).start()
    while True:
        payload = await queue.get()
        if payload is None:
            break
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
