from __future__ import annotations

import hashlib
import json
from threading import BoundedSemaphore, Lock
from typing import Any


class JobQueueFull(RuntimeError):
    """Raised before persistence when a process-local async queue is full."""


class JobCapacity:
    def __init__(self, *, workers: int, queued: int) -> None:
        self._workers = max(1, int(workers))
        self._queued = max(0, int(queued))
        self._limit = self._workers + self._queued
        self._semaphore = BoundedSemaphore(self._limit)
        self._lock = Lock()
        self._in_use = 0

    def try_acquire(self) -> bool:
        acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._in_use += 1
        return acquired

    def release(self) -> None:
        with self._lock:
            self._in_use = max(0, self._in_use - 1)
        self._semaphore.release()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            in_use = self._in_use
        return {
            "workers": self._workers,
            "queue_capacity": self._queued,
            "capacity": self._limit,
            "in_use": in_use,
            "available": max(0, self._limit - in_use),
        }


def canonical_job_dedup_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
