from __future__ import annotations

from contextlib import contextmanager
from threading import RLock, local
from typing import Iterator
from weakref import WeakValueDictionary

from app.config import get_settings
from app.request_context import get_request_user_id
from app.services.cross_process_lock import (
    CrossProcessLockError,
    CrossProcessLockTimeout,
    cross_process_lock,
)


_LOCKS: WeakValueDictionary[int, RLock] = WeakValueDictionary()
_LOCKS_GUARD = RLock()
_THREAD_STATE = local()


class PortfolioMutationLockError(RuntimeError):
    """The portfolio write could not obtain its required account lock."""


class PortfolioMutationLockTimeout(PortfolioMutationLockError):
    """Another worker is still mutating the same account."""


def _lock_for_current_user() -> RLock:
    user_id = int(get_request_user_id())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(user_id)
        if lock is None:
            lock = RLock()
            _LOCKS[user_id] = lock
        return lock


def _depths() -> dict[int, int]:
    depths = getattr(_THREAD_STATE, "portfolio_lock_depths", None)
    if depths is None:
        depths = {}
        _THREAD_STATE.portfolio_lock_depths = depths
    return depths


@contextmanager
def portfolio_mutation_guard() -> Iterator[None]:
    """Serialize portfolio read-modify-write operations for one account.

    The local RLock avoids avoidable database contention between threads in one
    worker. The MySQL named lock (or local SQLite file lock) is the actual
    cross-worker correctness boundary. Nested sync calls are reentrant in the
    current thread and therefore acquire the database lock exactly once.
    """
    user_id = int(get_request_user_id())
    depths = _depths()
    current_depth = depths.get(user_id, 0)
    if current_depth:
        depths[user_id] = current_depth + 1
        try:
            yield
        finally:
            next_depth = depths[user_id] - 1
            if next_depth:
                depths[user_id] = next_depth
            else:
                depths.pop(user_id, None)
        return

    lock = _lock_for_current_user()
    timeout_seconds = float(
        max(0.0, get_settings().portfolio_mutation_lock_timeout_seconds)
    )
    with lock:
        try:
            with cross_process_lock(
                f"portfolio-mutation:user:{user_id}",
                timeout_seconds=timeout_seconds,
            ):
                depths[user_id] = 1
                try:
                    yield
                finally:
                    depths.pop(user_id, None)
        except CrossProcessLockTimeout as exc:
            raise PortfolioMutationLockTimeout(
                "持仓正在被另一项操作更新，请稍后重试"
            ) from exc
        except CrossProcessLockError as exc:
            raise PortfolioMutationLockError(
                "暂时无法取得持仓写锁，请稍后重试"
            ) from exc
