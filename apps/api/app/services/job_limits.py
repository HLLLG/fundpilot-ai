from __future__ import annotations

import hashlib
import json
from threading import BoundedSemaphore
from typing import Any


class JobQueueFull(RuntimeError):
    """Raised before persistence when a process-local async queue is full."""


class JobCapacity:
    def __init__(self, *, workers: int, queued: int) -> None:
        self._semaphore = BoundedSemaphore(
            max(1, int(workers)) + max(0, int(queued))
        )

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


def canonical_job_dedup_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
