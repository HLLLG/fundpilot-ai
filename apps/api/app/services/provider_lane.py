"""Mark which pipeline is using shared providers.

Eastmoney and DeepSeek are process-wide. Without a lane, one SSE (or job)
can occupy every slot and the peer stream fails locally even though the
host is healthy. Callers set this contextvar at the pipeline boundary;
thread-pool workers inherit it via ``copy_context``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator

LANE_ANALYSIS = "analysis"
LANE_DISCOVERY = "discovery"
LANE_OTHER = "other"

_LANE: ContextVar[str] = ContextVar("provider_lane", default=LANE_OTHER)


def current_provider_lane() -> str:
    lane = str(_LANE.get() or LANE_OTHER).strip().lower()
    if lane in {LANE_ANALYSIS, LANE_DISCOVERY, LANE_OTHER}:
        return lane
    return LANE_OTHER


def use_provider_lane(lane: str) -> Token[str]:
    resolved = str(lane or LANE_OTHER).strip().lower()
    if resolved not in {LANE_ANALYSIS, LANE_DISCOVERY, LANE_OTHER}:
        resolved = LANE_OTHER
    return _LANE.set(resolved)


def reset_provider_lane(token: Token[str]) -> None:
    _LANE.reset(token)


@contextmanager
def provider_lane(lane: str) -> Iterator[None]:
    token = use_provider_lane(lane)
    try:
        yield
    finally:
        reset_provider_lane(token)
