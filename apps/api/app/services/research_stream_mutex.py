"""Process-local mutex so daily-report and discovery SSE cannot overlap.

The 3.6G Lighthouse host cannot finish both pipelines at once. With a single
Uvicorn worker this lock is host-wide for those two endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

ResearchKind = Literal["analyze", "discovery"]

ANALYZE_BLOCKS_DISCOVERY = "日报正在生成，完成后即可扫描。"
DISCOVERY_BLOCKS_ANALYZE = "发现基金正在扫描，完成后即可生成日报。"
ANALYZE_ALREADY_RUNNING = "已有日报正在生成，请稍后再试。"
DISCOVERY_ALREADY_RUNNING = "已有发现基金扫描正在进行，请稍后再试。"

_lock = Lock()
_holder: ResearchKind | None = None


@dataclass
class ResearchStreamSlot:
    kind: ResearchKind
    _released: bool = field(default=False, init=False)

    def release(self) -> None:
        global _holder
        if self._released:
            return
        self._released = True
        with _lock:
            if _holder == self.kind:
                _holder = None


def try_acquire_research_stream(kind: ResearchKind) -> tuple[ResearchStreamSlot | None, str | None]:
    """Return ``(slot, None)`` or ``(None, user-facing conflict detail)``."""

    global _holder
    with _lock:
        if _holder is None:
            _holder = kind
            return ResearchStreamSlot(kind=kind), None
        return None, _conflict_detail(_holder, kind)


def active_research_stream() -> ResearchKind | None:
    with _lock:
        return _holder


def reset_research_stream_mutex_for_tests() -> None:
    global _holder
    with _lock:
        _holder = None


def _conflict_detail(holder: ResearchKind, requested: ResearchKind) -> str:
    if holder == "analyze" and requested == "discovery":
        return ANALYZE_BLOCKS_DISCOVERY
    if holder == "discovery" and requested == "analyze":
        return DISCOVERY_BLOCKS_ANALYZE
    if requested == "analyze":
        return ANALYZE_ALREADY_RUNNING
    return DISCOVERY_ALREADY_RUNNING
