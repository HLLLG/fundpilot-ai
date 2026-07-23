"""通用工具：为阻塞的同步迭代器（如 LLM 流式响应）插入心跳事件。

背景：云托管等网关通常会在 SSE 响应连接持续空闲超过一定时长（腾讯云开发
CloudBase 约 60s）后主动中断连接，客户端表现为 ``ERR_ABORT_HANDLER`` /
``net/http: abort Handler``。深度推理模型在返回首个 token 前可能有较长的
思考耗时，一旦超过网关空闲阈值，即使后端仍在正常工作，连接也会被强制
断开，导致「AI 分析中…」阶段偶发卡死/报错。

``iter_with_heartbeat`` 在后台线程中驱动底层迭代器，主线程按固定间隔从
队列中取值；若长时间未取到新元素，则先产出一个心跳事件（不消耗底层迭代
器），从而保证 SSE 连接持续有字节流出，避免被判定为空闲。
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")

_SENTINEL = object()


class Heartbeat(Generic[_T]):
    """包裹心跳事件的产出值，调用方用 ``isinstance`` 区分心跳与真实元素。"""

    __slots__ = ("value",)

    def __init__(self, value: _T) -> None:
        self.value = value


class StreamCancelled(RuntimeError):
    """Internal cooperative-cancellation signal; never exposed as an SSE error."""


def raise_if_stream_cancelled(stop_event: threading.Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise StreamCancelled


def iter_with_heartbeat(
    iterator: Iterator[_T],
    *,
    heartbeat_seconds: float,
    heartbeat_factory: Callable[[], _T],
    stop_event: threading.Event | None = None,
) -> Iterator[_T | Heartbeat[_T]]:
    """驱动 ``iterator``；若超过 ``heartbeat_seconds`` 未产出新元素，先
    yield 一个 ``Heartbeat(heartbeat_factory())``，再继续等待。

    底层迭代器在独立守护线程中运行，因此本函数本身可安全地在同步生成器
    （如已运行在后台线程中的 SSE 生成器）中调用。
    """

    q: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=32)

    def _put(kind: str, payload: Any) -> bool:
        while stop_event is None or not stop_event.is_set():
            try:
                q.put((kind, payload), timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _drain() -> None:
        try:
            for item in iterator:
                if stop_event is not None and stop_event.is_set():
                    return
                if not _put("item", item):
                    return
        except BaseException as exc:  # noqa: BLE001 — 转发给消费方重新抛出
            if stop_event is None or not stop_event.is_set():
                _put("error", exc)
            return
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except (RuntimeError, ValueError):
                    pass
        _put("done", _SENTINEL)

    thread = threading.Thread(target=_drain, name="iter-with-heartbeat", daemon=True)
    thread.start()

    next_heartbeat = time.monotonic() + heartbeat_seconds
    completed = False
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            remaining = max(0.0, next_heartbeat - time.monotonic())
            try:
                kind, payload = q.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if time.monotonic() >= next_heartbeat:
                    yield Heartbeat(heartbeat_factory())
                    next_heartbeat = time.monotonic() + heartbeat_seconds
                continue
            if kind == "done":
                completed = True
                return
            if kind == "error":
                raise payload
            yield payload
    finally:
        if not completed and stop_event is not None:
            stop_event.set()
