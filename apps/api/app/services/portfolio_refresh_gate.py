"""Coalesce live cache refreshes so a manual click cannot duplicate an in-flight job."""

from __future__ import annotations

import threading

_SPOT_LOCK = threading.Lock()
_SPOT_BUSY = False
_SPOT_IDLE = threading.Event()
_SPOT_IDLE.set()

_SECTOR_JOB_LOCK = threading.Lock()
_SECTOR_JOB_BUSY = False
_SECTOR_JOB_IDLE = threading.Event()
_SECTOR_JOB_IDLE.set()

_NAV_LOCK = threading.Lock()
_NAV_BUSY: set[int] = set()
_NAV_IDLE: dict[int, threading.Event] = {}


def _wait(event: threading.Event, timeout: float) -> bool:
    return event.wait(timeout=max(0.0, timeout))


def shared_spot_refresh_in_flight() -> bool:
    with _SPOT_LOCK:
        return _SPOT_BUSY


def begin_shared_spot_refresh() -> bool:
    global _SPOT_BUSY
    with _SPOT_LOCK:
        if _SPOT_BUSY:
            return False
        _SPOT_BUSY = True
        _SPOT_IDLE.clear()
        return True


def end_shared_spot_refresh() -> None:
    global _SPOT_BUSY
    with _SPOT_LOCK:
        _SPOT_BUSY = False
        _SPOT_IDLE.set()


def wait_shared_spot_refresh(*, timeout: float) -> bool:
    return _wait(_SPOT_IDLE, timeout)


def background_sector_refresh_in_flight() -> bool:
    with _SECTOR_JOB_LOCK:
        return _SECTOR_JOB_BUSY


def begin_background_sector_refresh() -> bool:
    global _SECTOR_JOB_BUSY
    with _SECTOR_JOB_LOCK:
        if _SECTOR_JOB_BUSY:
            return False
        _SECTOR_JOB_BUSY = True
        _SECTOR_JOB_IDLE.clear()
        return True


def end_background_sector_refresh() -> None:
    global _SECTOR_JOB_BUSY
    with _SECTOR_JOB_LOCK:
        _SECTOR_JOB_BUSY = False
        _SECTOR_JOB_IDLE.set()


def wait_background_sector_refresh(*, timeout: float) -> bool:
    return _wait(_SECTOR_JOB_IDLE, timeout)


def nav_work_in_flight(user_id: int | None) -> bool:
    if user_id is None:
        return False
    with _NAV_LOCK:
        return user_id in _NAV_BUSY


def begin_nav_work(user_id: int) -> bool:
    with _NAV_LOCK:
        if user_id in _NAV_BUSY:
            return False
        _NAV_BUSY.add(user_id)
        idle = _NAV_IDLE.get(user_id)
        if idle is None:
            idle = threading.Event()
            _NAV_IDLE[user_id] = idle
        idle.clear()
        return True


def end_nav_work(user_id: int) -> None:
    with _NAV_LOCK:
        _NAV_BUSY.discard(user_id)
        idle = _NAV_IDLE.get(user_id)
        if idle is not None:
            idle.set()


def wait_nav_work(user_id: int | None, *, timeout: float) -> bool:
    if user_id is None:
        return True
    with _NAV_LOCK:
        if user_id not in _NAV_BUSY:
            return True
        idle = _NAV_IDLE[user_id]
    return _wait(idle, timeout)


def join_timeout_seconds(timeout_seconds: float | None) -> float:
    if timeout_seconds is None:
        return 30.0
    return max(1.0, float(timeout_seconds))
