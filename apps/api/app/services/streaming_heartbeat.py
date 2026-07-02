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
from collections.abc import Callable, Iterator
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")

_SENTINEL = object()


class Heartbeat(Generic[_T]):
    """包裹心跳事件的产出值，调用方用 ``isinstance`` 区分心跳与真实元素。"""

    __slots__ = ("value",)

    def __init__(self, value: _T) -> None:
        self.value = value


def iter_with_heartbeat(
    iterator: Iterator[_T],
    *,
    heartbeat_seconds: float,
    heartbeat_factory: Callable[[], _T],
) -> Iterator[_T | Heartbeat[_T]]:
    """驱动 ``iterator``；若超过 ``heartbeat_seconds`` 未产出新元素，先
    yield 一个 ``Heartbeat(heartbeat_factory())``，再继续等待。

    底层迭代器在独立守护线程中运行，因此本函数本身可安全地在同步生成器
    （如已运行在后台线程中的 SSE 生成器）中调用。
    """

    q: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def _drain() -> None:
        try:
            for item in iterator:
                q.put(("item", item))
        except BaseException as exc:  # noqa: BLE001 — 转发给消费方重新抛出
            q.put(("error", exc))
            return
        q.put(("done", _SENTINEL))

    thread = threading.Thread(target=_drain, name="iter-with-heartbeat", daemon=True)
    thread.start()

    while True:
        try:
            kind, payload = q.get(timeout=heartbeat_seconds)
        except queue.Empty:
            yield Heartbeat(heartbeat_factory())
            continue
        if kind == "done":
            return
        if kind == "error":
            raise payload
        yield payload
