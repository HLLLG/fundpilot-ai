from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from app import database


@pytest.fixture(autouse=True)
def _reset_schema_init_cache() -> None:
    database._clear_sqlite_schema_init_cache()
    yield
    database._clear_sqlite_schema_init_cache()


def _use_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(path))


def _open_and_check_schema() -> None:
    with database._connect() as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
        ).fetchone()
    assert row is not None


def _count_bootstraps(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    original = database._bootstrap_sqlite_schema

    def counted(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA database_list").fetchone()
        calls.append(Path(str(row[2])))
        original(connection)

    monkeypatch.setattr(database, "_bootstrap_sqlite_schema", counted)
    return calls


def test_sequential_connections_bootstrap_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sequential.db"
    _use_database(monkeypatch, path)
    calls = _count_bootstraps(monkeypatch)

    _open_and_check_schema()
    _open_and_check_schema()

    assert calls == [path]


def test_concurrent_first_connections_bootstrap_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "concurrent.db"
    _use_database(monkeypatch, path)
    calls = _count_bootstraps(monkeypatch)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _index: _open_and_check_schema(), range(8)))

    assert calls == [path]


def test_schema_cache_is_scoped_by_database_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    calls = _count_bootstraps(monkeypatch)

    _use_database(monkeypatch, first)
    _open_and_check_schema()
    _use_database(monkeypatch, second)
    _open_and_check_schema()
    _use_database(monkeypatch, first)
    _open_and_check_schema()

    assert calls == [first, second]


def test_deleted_database_is_bootstrapped_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "deleted.db"
    _use_database(monkeypatch, path)
    calls = _count_bootstraps(monkeypatch)
    _open_and_check_schema()

    path.unlink()
    _open_and_check_schema()

    assert calls == [path, path]


def test_replaced_database_is_bootstrapped_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "target.db"
    replacement = tmp_path / "replacement.db"
    _use_database(monkeypatch, path)
    calls = _count_bootstraps(monkeypatch)
    _open_and_check_schema()

    with closing(sqlite3.connect(replacement)) as connection:
        connection.execute("CREATE TABLE replacement_marker (id INTEGER PRIMARY KEY)")
        connection.commit()
    os.replace(replacement, path)

    with database._connect() as connection:
        marker = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'replacement_marker'"
        ).fetchone()
        reports = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'reports'"
        ).fetchone()

    assert marker is not None
    assert reports is not None
    assert calls == [path, path]


def test_database_import_invalidates_same_inode_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    _use_database(monkeypatch, target)
    calls = _count_bootstraps(monkeypatch)
    _open_and_check_schema()
    before = target.stat()

    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE imported_marker (id INTEGER PRIMARY KEY)")
        connection.commit()

    database.import_database_file(source, backup_current=False)
    after = target.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert database._sqlite_path_cache_key(target) not in database._SQLITE_SCHEMA_INIT_CACHE

    with database._connect() as connection:
        marker = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'imported_marker'"
        ).fetchone()
        reports = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'reports'"
        ).fetchone()

    assert marker is not None
    assert reports is not None
    assert calls == [target, target]


def test_failed_bootstrap_is_not_cached_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "retry.db"
    _use_database(monkeypatch, path)
    original = database._bootstrap_sqlite_schema
    attempts = 0

    def flaky(connection: sqlite3.Connection) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            connection.execute("CREATE TABLE partial_marker (id INTEGER PRIMARY KEY)")
            raise RuntimeError("bootstrap interrupted")
        original(connection)

    monkeypatch.setattr(database, "_bootstrap_sqlite_schema", flaky)

    with pytest.raises(RuntimeError, match="bootstrap interrupted"):
        database._connect()
    assert not database._SQLITE_SCHEMA_INIT_CACHE

    _open_and_check_schema()
    assert attempts == 2


def test_schema_version_or_external_ddl_invalidates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "schema-change.db"
    _use_database(monkeypatch, path)
    calls = _count_bootstraps(monkeypatch)
    _open_and_check_schema()

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE external_marker (id INTEGER PRIMARY KEY)")
        connection.commit()
    _open_and_check_schema()

    monkeypatch.setattr(database, "SCHEMA_VERSION", database.SCHEMA_VERSION + 1)
    _open_and_check_schema()

    assert calls == [path, path, path]


def test_schema_init_cache_is_bounded_lru(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def minimal_bootstrap(_connection: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(database, "_bootstrap_sqlite_schema", minimal_bootstrap)
    paths = [tmp_path / f"cache-{index}.db" for index in range(33)]
    for path in paths:
        _use_database(monkeypatch, path)
        with database._connect():
            pass

    first_key = database._sqlite_path_cache_key(paths[0])
    assert len(database._SQLITE_SCHEMA_INIT_CACHE) == 32
    assert first_key not in database._SQLITE_SCHEMA_INIT_CACHE

    _use_database(monkeypatch, paths[0])
    with database._connect():
        pass

    assert calls == 34
    assert len(database._SQLITE_SCHEMA_INIT_CACHE) == 32
    assert first_key in database._SQLITE_SCHEMA_INIT_CACHE
