"""``iter_with_heartbeat`` 单测：验证心跳插入、异常转发与正常透传。"""

from __future__ import annotations

import time

import pytest

from app.services.streaming_heartbeat import Heartbeat, iter_with_heartbeat


def test_iter_with_heartbeat_passes_through_fast_items_without_heartbeat():
    entries = list(
        iter_with_heartbeat(
            iter(["a", "b", "c"]),
            heartbeat_seconds=1.0,
            heartbeat_factory=lambda: "hb",
        )
    )
    assert entries == ["a", "b", "c"]
    assert not any(isinstance(entry, Heartbeat) for entry in entries)


def test_iter_with_heartbeat_emits_heartbeat_when_upstream_is_slow():
    def slow_source():
        time.sleep(0.08)
        yield "first"
        yield "second"

    entries = list(
        iter_with_heartbeat(
            slow_source(),
            heartbeat_seconds=0.02,
            heartbeat_factory=lambda: "hb",
        )
    )

    heartbeats = [entry for entry in entries if isinstance(entry, Heartbeat)]
    items = [entry for entry in entries if not isinstance(entry, Heartbeat)]

    assert len(heartbeats) >= 2, "预期在慢速首个元素到达前产生多次心跳"
    assert all(hb.value == "hb" for hb in heartbeats)
    assert items == ["first", "second"]


def test_iter_with_heartbeat_reraises_upstream_exception():
    def failing_source():
        yield "ok"
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        list(
            iter_with_heartbeat(
                failing_source(),
                heartbeat_seconds=1.0,
                heartbeat_factory=lambda: "hb",
            )
        )


def test_iter_with_heartbeat_calls_factory_lazily_each_time():
    calls: list[int] = []

    def factory():
        calls.append(len(calls))
        return calls[-1]

    def slow_source():
        time.sleep(0.07)
        yield "done"

    entries = list(
        iter_with_heartbeat(
            slow_source(),
            heartbeat_seconds=0.02,
            heartbeat_factory=factory,
        )
    )
    heartbeat_values = [entry.value for entry in entries if isinstance(entry, Heartbeat)]
    assert heartbeat_values == list(range(len(heartbeat_values)))
