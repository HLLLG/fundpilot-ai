"""持仓详情按用户缓存 + 板块分时后台预热。"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import Holding
from app.services import holding_detail_cache as cache_module
from app.services.holding_detail_cache import (
    bump_holding_detail_cache_generation,
    get_cached_holding_detail,
    holding_detail_fingerprint,
    save_cached_holding_detail,
)
from app.services.holding_intraday_warmup import collect_intraday_queries


@pytest.fixture(autouse=True)
def _reset_holding_detail_cache():
    bump_holding_detail_cache_generation()
    yield
    bump_holding_detail_cache_generation()


def test_holding_detail_cache_hit_and_miss(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_cache.get_request_user_id",
        lambda: 42,
    )
    bump_holding_detail_cache_generation()

    payload = {"holding": {"fund_code": "008586", "fund_name": "测试"}}
    fp = holding_detail_fingerprint(fund_code="008586", holding_amount=1000.0)
    assert get_cached_holding_detail("008586", fp) is None

    save_cached_holding_detail("008586", fp, payload)
    assert get_cached_holding_detail("008586", fp) == payload

    bump_holding_detail_cache_generation()
    assert get_cached_holding_detail("008586", fp) is None


def test_holding_detail_cache_removes_expired_entry_on_read(monkeypatch):
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    monkeypatch.setattr(cache_module, "_cache_ttl_seconds", lambda: 5.0)
    now = {"value": 100.0}
    monkeypatch.setattr(cache_module, "_now_timestamp", lambda: now["value"])

    save_cached_holding_detail("008586", "fp", {"value": 1})
    assert len(cache_module._MEMORY) == 1

    now["value"] = 106.0
    assert get_cached_holding_detail("008586", "fp") is None
    assert not cache_module._MEMORY


def test_holding_detail_cache_removes_stale_generation_on_read(monkeypatch):
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    key = cache_module._cache_key("008586", "fp")
    with cache_module._LOCK:
        cache_module._MEMORY[key] = (
            cache_module._GENERATION - 1,
            cache_module._now_timestamp(),
            {"value": 1},
        )

    assert get_cached_holding_detail("008586", "fp") is None
    assert key not in cache_module._MEMORY


def test_holding_detail_cache_generation_bump_releases_all_entries(monkeypatch):
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    save_cached_holding_detail("008586", "a", {"value": 1})
    save_cached_holding_detail("519674", "b", {"value": 2})
    assert len(cache_module._MEMORY) == 2

    bump_holding_detail_cache_generation()

    assert not cache_module._MEMORY


def test_holding_detail_cache_evicts_least_recently_used_entry(monkeypatch):
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    monkeypatch.setattr(cache_module, "_MAX_ENTRIES", 2)

    save_cached_holding_detail("A", "fp-a", {"value": "a"})
    save_cached_holding_detail("B", "fp-b", {"value": "b"})
    assert get_cached_holding_detail("A", "fp-a") == {"value": "a"}

    save_cached_holding_detail("C", "fp-c", {"value": "c"})

    assert get_cached_holding_detail("B", "fp-b") is None
    assert get_cached_holding_detail("A", "fp-a") == {"value": "a"}
    assert get_cached_holding_detail("C", "fp-c") == {"value": "c"}


def test_holding_detail_cache_stays_bounded_under_concurrent_access(monkeypatch):
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    monkeypatch.setattr(cache_module, "_MAX_ENTRIES", 16)

    def write_and_read(index: int) -> None:
        fund_code = f"{index:06d}"
        fingerprint = f"fp-{index}"
        save_cached_holding_detail(fund_code, fingerprint, {"value": index})
        get_cached_holding_detail(fund_code, fingerprint)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_and_read, range(200)))

    with cache_module._LOCK:
        assert len(cache_module._MEMORY) <= 16


def test_collect_intraday_queries_dedupes_by_sector(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_fund_profiles",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup._resolve_intraday_for_holding",
        lambda holding, _profile: ("index", holding.sector_name or ""),
    )
    holdings = [
        Holding(fund_code="008586", fund_name="A", sector_name="人工智能", holding_amount=1),
        Holding(fund_code="025857", fund_name="B", sector_name="人工智能", holding_amount=2),
        Holding(fund_code="519674", fund_name="C", sector_name="半导体", holding_amount=3),
    ]
    queries = collect_intraday_queries(holdings)
    assert queries == [("index", "人工智能"), ("index", "半导体")]
