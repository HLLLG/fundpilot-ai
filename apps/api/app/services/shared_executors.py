from __future__ import annotations

import threading
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.config import get_settings

_lock = threading.Lock()
_io_executor: InstrumentedThreadPoolExecutor | None = None
_analysis_context_executor: InstrumentedThreadPoolExecutor | None = None
_discovery_context_executor: InstrumentedThreadPoolExecutor | None = None


class InstrumentedThreadPoolExecutor(ThreadPoolExecutor):
    """Bounded executor with context propagation and occupancy metrics."""

    def __init__(self, *args: Any, metric_name: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._metric_name = metric_name
        self._metric_lock = threading.Lock()
        self._submitted = 0
        self._started = 0
        self._completed = 0
        self._cancelled = 0
        self._active = 0

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ):
        context = copy_context()
        with self._metric_lock:
            self._submitted += 1

        def tracked() -> Any:
            with self._metric_lock:
                self._started += 1
                self._active += 1
            try:
                return context.run(fn, *args, **kwargs)
            finally:
                with self._metric_lock:
                    self._active = max(0, self._active - 1)
                    self._completed += 1

        try:
            future = super().submit(tracked)
        except Exception:
            with self._metric_lock:
                self._submitted = max(0, self._submitted - 1)
            raise

        def observe_cancel(completed_future) -> None:
            if completed_future.cancelled():
                with self._metric_lock:
                    self._cancelled += 1
                    self._completed += 1

        future.add_done_callback(observe_cancel)
        return future

    def snapshot(self) -> dict[str, int | str]:
        with self._metric_lock:
            return {
                "name": self._metric_name,
                "max_workers": self._max_workers,
                "active": self._active,
                "queued": self._work_queue.qsize(),
                "submitted": self._submitted,
                "started": self._started,
                "completed": self._completed,
                "cancelled": self._cancelled,
            }


def get_shared_io_executor() -> ThreadPoolExecutor:
    """Return the bounded process-wide pool for provider/database fan-out."""

    global _io_executor
    with _lock:
        if _io_executor is None:
            _io_executor = InstrumentedThreadPoolExecutor(
                max_workers=max(1, int(get_settings().sse_shared_io_workers)),
                thread_name_prefix="fund-ai-shared-io",
                metric_name="shared_io",
            )
        return _io_executor


def get_analysis_context_executor() -> ThreadPoolExecutor:
    """Return the bounded analysis context/judge executor."""

    global _analysis_context_executor
    with _lock:
        if _analysis_context_executor is None:
            _analysis_context_executor = InstrumentedThreadPoolExecutor(
                max_workers=max(
                    1,
                    int(get_settings().sse_analysis_context_workers),
                ),
                thread_name_prefix="fund-ai-analysis-context",
                metric_name="analysis_context",
            )
        return _analysis_context_executor


def get_discovery_context_executor() -> ThreadPoolExecutor:
    """Return the bounded discovery context/judge executor."""

    global _discovery_context_executor
    with _lock:
        if _discovery_context_executor is None:
            _discovery_context_executor = InstrumentedThreadPoolExecutor(
                max_workers=max(
                    1,
                    int(get_settings().sse_discovery_context_workers),
                ),
                thread_name_prefix="fund-ai-discovery-context",
                metric_name="discovery_context",
            )
        return _discovery_context_executor


def close_shared_executors() -> None:
    """Stop accepting queued work during application shutdown."""

    global _analysis_context_executor, _discovery_context_executor, _io_executor
    with _lock:
        executors = (
            _io_executor,
            _analysis_context_executor,
            _discovery_context_executor,
        )
        _io_executor = None
        _analysis_context_executor = None
        _discovery_context_executor = None
    for executor in executors:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def shared_executor_snapshot() -> list[dict[str, int | str]]:
    """Return occupancy without lazily creating any executor."""

    with _lock:
        executors = (
            _io_executor,
            _analysis_context_executor,
            _discovery_context_executor,
        )
        return [
            executor.snapshot()
            for executor in executors
            if executor is not None
        ]
