from __future__ import annotations

import hashlib
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from app.config import get_settings
from app.db_connect import open_dedicated_mysql_session


logger = logging.getLogger(__name__)
_FILE_LOCK_POLL_SECONDS = 0.05


class CrossProcessLockError(RuntimeError):
    """Base class for a required cross-process lock that was not obtained."""


class CrossProcessLockTimeout(CrossProcessLockError):
    """Another process kept the requested lock past the bounded wait."""


class CrossProcessLockUnavailable(CrossProcessLockError):
    """The configured coordination store could not provide the lock."""


def _mysql_database_identity() -> str:
    database_url = str(get_settings().database_url or "")
    parsed = urlparse(database_url)
    host = (parsed.hostname or "localhost").lower()
    port = parsed.port or 3306
    database = (parsed.path or "/").lstrip("/")
    return f"{host}:{port}/{database}"


def mysql_lock_name(resource: str) -> str:
    """Return a secret-free, server-global MySQL lock name within 64 chars."""
    database_digest = hashlib.sha256(
        _mysql_database_identity().encode("utf-8")
    ).hexdigest()[:12]
    resource_digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:32]
    return f"fp:{database_digest}:{resource_digest}"


def _lock_result(row: object, key: str) -> int | None:
    if row is None:
        return None
    if isinstance(row, dict):
        value = row.get(key)
    else:
        try:
            value = row[key]  # type: ignore[index]
        except (IndexError, KeyError, TypeError):
            try:
                value = row[0]  # type: ignore[index]
            except (IndexError, KeyError, TypeError):
                return None
    return None if value is None else int(value)


@contextmanager
def _mysql_lock(resource: str, *, timeout_seconds: float) -> Iterator[None]:
    lock_name = mysql_lock_name(resource)
    body_started = False
    try:
        with open_dedicated_mysql_session(
            read_timeout_seconds=max(10.0, timeout_seconds + 5.0),
        ) as connection:
            row = connection.execute(
                "SELECT GET_LOCK(?, ?) AS acquired",
                (lock_name, max(0.0, float(timeout_seconds))),
            ).fetchone()
            acquired = _lock_result(row, "acquired")
            if acquired == 0:
                raise CrossProcessLockTimeout(
                    f"timed out acquiring database lock for {resource}"
                )
            if acquired != 1:
                raise CrossProcessLockUnavailable(
                    f"database lock unavailable for {resource}"
                )
            body_started = True
            try:
                yield
            finally:
                try:
                    released_row = connection.execute(
                        "SELECT RELEASE_LOCK(?) AS released",
                        (lock_name,),
                    ).fetchone()
                    if _lock_result(released_row, "released") != 1:
                        logger.error(
                            "MySQL named lock release was not acknowledged resource=%s",
                            resource,
                        )
                except Exception:
                    # The dedicated session is closed immediately afterwards;
                    # MySQL releases session-owned locks on disconnect.
                    logger.exception(
                        "MySQL named lock release failed resource=%s",
                        resource,
                    )
    except CrossProcessLockError:
        raise
    except Exception as exc:
        if body_started:
            raise
        raise CrossProcessLockUnavailable(
            f"database lock unavailable for {resource}"
        ) from exc


def _sqlite_lock_path(resource: str) -> Path:
    settings = get_settings()
    db_path = settings.db_path.expanduser().resolve(strict=False)
    database_digest = hashlib.sha256(str(db_path).encode("utf-8")).hexdigest()[:12]
    resource_digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:32]
    return db_path.parent / ".fundpilot-locks" / f"{database_digest}-{resource_digest}.lock"


def _try_lock_file(descriptor: int) -> bool:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_file(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _sqlite_file_lock(resource: str, *, timeout_seconds: float) -> Iterator[None]:
    try:
        lock_path = _sqlite_lock_path(resource)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except Exception as exc:
        raise CrossProcessLockUnavailable(
            f"file lock unavailable for {resource}"
        ) from exc
    acquired = False
    body_started = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            if _try_lock_file(descriptor):
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise CrossProcessLockTimeout(
                    f"timed out acquiring file lock for {resource}"
                )
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(_FILE_LOCK_POLL_SECONDS, remaining))
        body_started = True
        yield
    except CrossProcessLockError:
        raise
    except Exception as exc:
        if body_started:
            raise
        raise CrossProcessLockUnavailable(
            f"file lock unavailable for {resource}"
        ) from exc
    finally:
        if acquired:
            try:
                _unlock_file(descriptor)
            except OSError:
                logger.exception("file lock release failed resource=%s", resource)
        os.close(descriptor)


@contextmanager
def cross_process_lock(
    resource: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    """Serialize a named operation across Uvicorn workers.

    Production MySQL uses connection-scoped advisory locks. Local SQLite uses
    an OS file lock beside the database, which also works across local worker
    processes and is automatically released when a process exits.
    """
    if get_settings().uses_mysql:
        with _mysql_lock(resource, timeout_seconds=timeout_seconds):
            yield
        return
    with _sqlite_file_lock(resource, timeout_seconds=timeout_seconds):
        yield
