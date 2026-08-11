"""内存层必须周期性向持久行复验，否则 api 容器当天永远看不到 worker 的刷新。

回归背景（2026-08-11 线上实测）：刷新线程只跑在 worker 容器（``runtime_role=worker``），
api 容器自己从不刷新板块快照。``get_spot_snapshot_any_age`` 命中进程内存后直接返回、
永不回源，于是两个 uvicorn worker 分别把 13:17 和 14:38 的快照供到了收盘之后，而 MySQL
里已经是 14:59 的数据。13:30 / 13:45 / 14:31 三次扫描因此拿到完全相同的板块涨跌。
"""

from __future__ import annotations

import json

import pytest

from app.services import sector_quote_cache as cache


@pytest.fixture(autouse=True)
def _clean_memory():
    cache._MEMORY.clear()
    cache._MEMORY_REVALIDATED_AT.clear()
    yield
    cache._MEMORY.clear()
    cache._MEMORY_REVALIDATED_AT.clear()


def _write_durable(monkeypatch, *, payload: dict, updated_at: str) -> None:
    """伪造持久行，模拟 worker 容器刚写完库。"""

    class _Row(dict):
        pass

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, _sql, _params=None):
            class _Cursor:
                @staticmethod
                def fetchone():
                    return _Row(payload=json.dumps(payload), updated_at=updated_at)

            return _Cursor()

    monkeypatch.setattr(cache, "_connect", lambda: _Conn())
    monkeypatch.setattr(cache, "_ensure_cache_table", lambda _c: None)


def test_any_age_keeps_serving_frozen_memory(monkeypatch):
    """旧读法的行为（保留）：命中内存就返回，不回源。"""
    _write_durable(
        monkeypatch,
        payload={"v": "worker-14:59"},
        updated_at="2026-08-11T06:59:40+00:00",
    )
    frozen_at = cache._updated_at_timestamp("2026-08-11T05:17:09+00:00")
    cache._save_memory_snapshot("k", frozen_at, {"v": "frozen-13:17"})

    assert cache.get_spot_snapshot_any_age("k") == {"v": "frozen-13:17"}


def test_revalidated_read_picks_up_newer_durable_row(monkeypatch):
    """新读法：内存超出复验窗口后读持久行，换到 worker 写的新值。"""
    _write_durable(
        monkeypatch,
        payload={"v": "worker-14:59"},
        updated_at="2026-08-11T06:59:40+00:00",
    )
    frozen_at = cache._updated_at_timestamp("2026-08-11T05:17:09+00:00")
    cache._save_memory_snapshot("k", frozen_at, {"v": "frozen-13:17"})

    got = cache.get_spot_snapshot_revalidated("k", memory_ttl_seconds=30.0)
    assert got == {"v": "worker-14:59"}


def test_revalidated_read_serves_memory_inside_window(monkeypatch):
    """复验窗口内不查库：刚写入的内存值直接命中。"""
    calls = {"n": 0}

    class _Conn:
        def __enter__(self):
            calls["n"] += 1
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, _sql, _params=None):
            raise AssertionError("复验窗口内不应该查库")

    monkeypatch.setattr(cache, "_connect", lambda: _Conn())
    monkeypatch.setattr(cache, "_ensure_cache_table", lambda _c: None)

    cache.save_spot_snapshot  # noqa: B018 - 只是表明不走写路径
    now = cache.datetime.now(cache.timezone.utc).timestamp()
    cache._save_memory_snapshot("k", now, {"v": "fresh"})

    assert cache.get_spot_snapshot_revalidated("k", memory_ttl_seconds=30.0) == {"v": "fresh"}
    assert calls["n"] == 0


def test_revalidated_read_preserves_durable_capture_time(monkeypatch):
    """晋升持久行时必须保留持久层捕获时刻，不能用读取时刻。

    否则后续 TTL-aware 的 ``get_spot_snapshot`` 会把一次纯读取误当成新鲜的 provider 拉取。
    """
    updated_at = "2026-08-11T06:59:40+00:00"
    _write_durable(monkeypatch, payload={"v": "worker"}, updated_at=updated_at)
    frozen_at = cache._updated_at_timestamp("2026-08-11T05:17:09+00:00")
    cache._save_memory_snapshot("k", frozen_at, {"v": "frozen"})

    cache.get_spot_snapshot_revalidated("k", memory_ttl_seconds=30.0)

    cached_at, _payload = cache._MEMORY["k"]
    assert cached_at == pytest.approx(cache._updated_at_timestamp(updated_at))


def test_revalidated_read_keeps_memory_when_durable_row_vanishes(monkeypatch):
    """持久行不存在时不能把内存里的可用值丢掉。"""

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, _sql, _params=None):
            class _Cursor:
                @staticmethod
                def fetchone():
                    return None

            return _Cursor()

    monkeypatch.setattr(cache, "_connect", lambda: _Conn())
    monkeypatch.setattr(cache, "_ensure_cache_table", lambda _c: None)

    frozen_at = cache._updated_at_timestamp("2026-08-11T05:17:09+00:00")
    cache._save_memory_snapshot("k", frozen_at, {"v": "frozen"})

    assert cache.get_spot_snapshot_revalidated("k", memory_ttl_seconds=30.0) == {"v": "frozen"}


def test_ttl_read_evicts_the_expired_memory_entry(monkeypatch):
    """TTL 读判定过期时必须淘汰内存条目——好几处调用点隐式依赖这个行为。

    `sector_quote_provider.load_spot_boards_from_cache_only` 与
    `us_market_service` 的 stale 分支都是"先 TTL 读、紧接着 any_age 读同一个 key"。
    它们之所以没有跟 `theme:boards` 一起冻结，靠的正是前面那次 TTL 读把过期内存摘掉，
    让紧随其后的 any_age 必须回持久行。谁把前置的 TTL 读删掉、或把 TTL 调得很长
    （例如原来 `stale_day` 那个 24 小时），冻结就会立刻复现。
    """
    _write_durable(
        monkeypatch,
        payload={"v": "durable"},
        updated_at="2026-08-11T06:59:40+00:00",
    )
    stale_at = cache.datetime.now(cache.timezone.utc).timestamp() - 600
    cache._save_memory_snapshot("k", stale_at, {"v": "stale-memory"})

    # TTL 读判过期 → 返回 None 且把内存条目摘掉。
    assert cache.get_spot_snapshot("k", ttl_seconds=60.0) is None
    assert "k" not in cache._MEMORY

    # 因此紧随其后的 any_age 会回持久行，而不是又拿到那份陈旧内存。
    assert cache.get_spot_snapshot_any_age("k") == {"v": "durable"}


def test_long_ttl_read_does_not_evict_and_therefore_freezes(monkeypatch):
    """反证上一条：TTL 长到覆盖住陈旧年龄时，内存条目不会被摘，值就是冻的。"""
    _write_durable(
        monkeypatch,
        payload={"v": "durable"},
        updated_at="2026-08-11T06:59:40+00:00",
    )
    stale_at = cache.datetime.now(cache.timezone.utc).timestamp() - 600
    cache._save_memory_snapshot("k", stale_at, {"v": "stale-memory"})

    assert cache.get_spot_snapshot("k", ttl_seconds=24 * 3600) == {"v": "stale-memory"}
    assert "k" in cache._MEMORY
