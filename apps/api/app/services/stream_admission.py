from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


_lock = Lock()
_active_streams = 0


@dataclass
class StreamSlot:
    counted: bool
    _released: bool = field(default=False, init=False)

    def release(self) -> None:
        global _active_streams
        if self._released:
            return
        self._released = True
        if not self.counted:
            return
        with _lock:
            _active_streams = max(0, _active_streams - 1)


def try_acquire_stream_slot(limit: int) -> StreamSlot | None:
    """Acquire one process-local stream slot without queueing request threads."""

    global _active_streams
    resolved_limit = max(0, int(limit))
    if resolved_limit == 0:
        return StreamSlot(counted=False)
    with _lock:
        if _active_streams >= resolved_limit:
            return None
        _active_streams += 1
    return StreamSlot(counted=True)


def active_stream_count() -> int:
    with _lock:
        return _active_streams
