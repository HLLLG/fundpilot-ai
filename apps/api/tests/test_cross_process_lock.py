from __future__ import annotations

import multiprocessing
import os
from contextlib import contextmanager
from queue import Empty
from types import SimpleNamespace

import pytest

from app.services import cross_process_lock as lock_service
from app.services import portfolio_holdings_cache
from app.services import portfolio_mutation_guard as mutation_guard


def _sqlite_lock_worker(
    db_path: str,
    attempted,
    acquired,
    release,
) -> None:
    os.environ["FUND_AI_DATABASE_URL"] = ""
    os.environ["FUND_AI_DB_PATH"] = db_path
    from app.config import refresh_settings
    from app.services.cross_process_lock import cross_process_lock

    refresh_settings()
    attempted.set()
    with cross_process_lock("portfolio-mutation:user:42", timeout_seconds=5):
        acquired.put(os.getpid())
        release.wait(5)


class _Result:
    def __init__(self, row: dict) -> None:
        self._row = row

    def fetchone(self) -> dict:
        return self._row


class _FakeMySqlConnection:
    def __init__(self, acquire_result: int | None = 1) -> None:
        self.acquire_result = acquire_result
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _Result:
        self.statements.append((sql, tuple(params)))
        if "GET_LOCK" in sql:
            return _Result({"acquired": self.acquire_result})
        return _Result({"released": 1})


def test_mysql_lock_name_is_stable_secret_free_and_within_mysql_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        lock_service,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="mysql://writer:super-secret@db:3306/fundpilot",
        ),
    )

    first = lock_service.mysql_lock_name("portfolio-mutation:user:123")
    second = lock_service.mysql_lock_name("portfolio-mutation:user:123")

    assert first == second
    assert len(first) <= 64
    assert "super-secret" not in first
    assert "123" not in first


def test_mysql_named_lock_uses_dedicated_session_and_releases(monkeypatch) -> None:
    connection = _FakeMySqlConnection()

    @contextmanager
    def session(**_kwargs):
        yield connection

    monkeypatch.setattr(lock_service, "open_dedicated_mysql_session", session)
    monkeypatch.setattr(
        lock_service,
        "mysql_lock_name",
        lambda _resource: "fp:test:portfolio",
    )

    with lock_service._mysql_lock("portfolio", timeout_seconds=3):
        pass

    assert "GET_LOCK" in connection.statements[0][0]
    assert connection.statements[0][1] == ("fp:test:portfolio", 3.0)
    assert "RELEASE_LOCK" in connection.statements[1][0]


def test_mysql_named_lock_timeout_is_distinct(monkeypatch) -> None:
    connection = _FakeMySqlConnection(acquire_result=0)

    @contextmanager
    def session(**_kwargs):
        yield connection

    monkeypatch.setattr(lock_service, "open_dedicated_mysql_session", session)
    monkeypatch.setattr(lock_service, "mysql_lock_name", lambda _resource: "fp:test")

    with pytest.raises(lock_service.CrossProcessLockTimeout):
        with lock_service._mysql_lock("portfolio", timeout_seconds=0):
            raise AssertionError("lock body must not run")


def test_lock_does_not_wrap_exceptions_from_protected_body(monkeypatch) -> None:
    connection = _FakeMySqlConnection()

    @contextmanager
    def session(**_kwargs):
        yield connection

    monkeypatch.setattr(lock_service, "open_dedicated_mysql_session", session)
    monkeypatch.setattr(lock_service, "mysql_lock_name", lambda _resource: "fp:test")

    with pytest.raises(ValueError, match="business failure"):
        with lock_service._mysql_lock("portfolio", timeout_seconds=1):
            raise ValueError("business failure")


def test_portfolio_guard_is_reentrant_without_reacquiring_database_lock(monkeypatch) -> None:
    calls = 0

    @contextmanager
    def fake_cross_process_lock(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield

    monkeypatch.setattr(mutation_guard, "cross_process_lock", fake_cross_process_lock)
    monkeypatch.setattr(
        mutation_guard,
        "get_settings",
        lambda: SimpleNamespace(portfolio_mutation_lock_timeout_seconds=1),
    )

    with mutation_guard.portfolio_mutation_guard():
        with mutation_guard.portfolio_mutation_guard():
            pass

    assert calls == 1


def test_sqlite_file_lock_serializes_separate_processes(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Queue()
    attempted_first = context.Event()
    attempted_second = context.Event()
    release_first = context.Event()
    release_second = context.Event()
    db_path = str(tmp_path / "multi-worker.db")

    first = context.Process(
        target=_sqlite_lock_worker,
        args=(db_path, attempted_first, acquired, release_first),
    )
    second = context.Process(
        target=_sqlite_lock_worker,
        args=(db_path, attempted_second, acquired, release_second),
    )
    first.start()
    try:
        assert attempted_first.wait(5)
        first_pid = acquired.get(timeout=5)
        second.start()
        assert attempted_second.wait(5)
        with pytest.raises(Empty):
            acquired.get(timeout=0.3)

        release_first.set()
        second_pid = acquired.get(timeout=5)
        assert second_pid != first_pid
        release_second.set()
    finally:
        release_first.set()
        release_second.set()
        first.join(timeout=8)
        if second.pid is not None:
            second.join(timeout=8)
        if first.is_alive():
            first.terminate()
        if second.pid is not None and second.is_alive():
            second.terminate()

    assert first.exitcode == 0
    assert second.exitcode == 0


def test_sqlite_file_lock_is_released_when_owner_process_crashes(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Queue()
    attempted_first = context.Event()
    attempted_second = context.Event()
    never_release = context.Event()
    release_second = context.Event()
    db_path = str(tmp_path / "crash-release.db")

    first = context.Process(
        target=_sqlite_lock_worker,
        args=(db_path, attempted_first, acquired, never_release),
    )
    second = context.Process(
        target=_sqlite_lock_worker,
        args=(db_path, attempted_second, acquired, release_second),
    )
    first.start()
    try:
        assert attempted_first.wait(5)
        first_pid = acquired.get(timeout=5)
        second.start()
        assert attempted_second.wait(5)
        with pytest.raises(Empty):
            acquired.get(timeout=0.3)

        first.terminate()
        first.join(timeout=5)
        second_pid = acquired.get(timeout=5)
        assert second_pid != first_pid
        release_second.set()
    finally:
        release_second.set()
        if first.is_alive():
            first.terminate()
        if second.pid is not None:
            second.join(timeout=8)
        if second.pid is not None and second.is_alive():
            second.terminate()

    assert first.exitcode is not None and first.exitcode != 0
    assert second.exitcode == 0


def test_mysql_mode_disables_process_local_holdings_response_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        portfolio_holdings_cache,
        "get_settings",
        lambda: SimpleNamespace(resolved_holdings_memory_cache_enabled=False),
    )

    assert portfolio_holdings_cache.save_cached_holdings_response({"holdings": [1]}) is True
    assert portfolio_holdings_cache.get_cached_holdings_response() is None
