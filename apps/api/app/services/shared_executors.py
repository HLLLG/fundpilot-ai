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


#: 一份日报会并发提交的增强项数量（`analysis_facts.build_analysis_facts`：signal_backtest /
#: guard_policy / intraday / stock_connect_flow / sector_opportunity / market_breadth）。
#:
#: 这是上下文池的**下界**，不是建议值。原因是每个增强项都有自己的超时预算，而
#: `_enhancement_result` 的 deadline 从"开始等待"起算——任务还在队列里排队时，它的预算就已经
#: 在流失。池子比任务数小意味着后提交的任务用一部分（甚至全部）预算在排队，那些超时数字就
#: 不再表示"给数据源多少时间"。
#:
#: 2026-08-11 14:30 实测到的后果：池子只有 2 个 worker、任务有 6 个，而 `sector_opportunity`
#: 是第 5 个提交的。更糟的是 `future.cancel()` 对**已在运行**的任务无效，被放弃的超时任务仍然
#: 占着 worker 跑完自己的预算——两个这样的任务就能让方向层压根没机会启动，外层只能拿到
#: `held={}`，随后 6 只持仓被数据门禁全部降为观察。
#:
#: 池子是**进程级单例**，所以还要留出并发报告的余量：这些线程绝大部分时间阻塞在
#: `shared_io`（默认 48 workers）上等 provider，线程本身几乎不占资源，宁可宽一点。
ANALYSIS_ENHANCEMENT_TASK_COUNT = 6
_ANALYSIS_CONTEXT_CONCURRENT_REPORTS = 2


def analysis_context_worker_floor() -> int:
    """上下文池不得低于「单份日报任务数 × 预期并发报告数」。"""
    return ANALYSIS_ENHANCEMENT_TASK_COUNT * _ANALYSIS_CONTEXT_CONCURRENT_REPORTS


def get_analysis_context_executor() -> ThreadPoolExecutor:
    """Return the bounded analysis context/judge executor."""

    global _analysis_context_executor
    with _lock:
        if _analysis_context_executor is None:
            _analysis_context_executor = InstrumentedThreadPoolExecutor(
                # 配置只能往上调，不能把池子压到下界之下——压下去等于让各增强项的超时预算
                # 变成"排队时间 + 数据源时间"的混合物。
                max_workers=max(
                    analysis_context_worker_floor(),
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
