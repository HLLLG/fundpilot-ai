from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import (
    get_fund_daily_catalogue_meta,
    replace_fund_daily_catalogue,
)
from app.services import fund_discovery_data_cache as cache
from app.services.fund_discovery_data_cache import (
    _build_universe_snapshot,
    _reset_universe_memory_for_tests,
    _universe_rows_with_snapshot_contract,
    fetch_discovery_fund_universe_cache_only,
    fetch_discovery_fund_universe_cached,
)
from app.services.fund_research_profile_store import _reset_profile_memory_for_tests


@pytest.fixture(autouse=True)
def _clear_universe_memory() -> None:
    _reset_universe_memory_for_tests()
    _reset_profile_memory_for_tests()
    yield
    _reset_universe_memory_for_tests()
    _reset_profile_memory_for_tests()


def _raw_row(code: str = "000001") -> dict:
    return {
        "fund_code": code,
        "fund_name": "测试基金",
        "fund_type": "gp",
        "return_3m_percent": 4.2,
        "return_3y_percent": 18.5,
        "fund_scale_yi": 12.5,
    }


def test_universe_snapshot_is_stamped_once_at_write() -> None:
    snapshot = _build_universe_snapshot([_raw_row(), _raw_row("000002")])
    first = snapshot["rows"][0]

    assert first["membership_available_at"] == snapshot["snapshot_available_at"]
    assert first["return_3m_percent_available_at"] == snapshot["snapshot_available_at"]
    assert first["return_3y_percent_available_at"] == snapshot["snapshot_available_at"]
    assert first["fund_scale_yi_available_at"] == snapshot["snapshot_available_at"]


def test_stamped_universe_rows_are_shared_not_copied() -> None:
    snapshot = _build_universe_snapshot([_raw_row()])
    first = _universe_rows_with_snapshot_contract(snapshot)
    second = _universe_rows_with_snapshot_contract(snapshot)

    assert first[0] is snapshot["rows"][0]
    assert second[0] is first[0]


def test_legacy_unstamped_rows_still_receive_contract_fields() -> None:
    payload = {
        "snapshot_available_at": "2026-08-07T02:00:00+00:00",
        "source": "legacy",
        "rows": [_raw_row()],
    }

    rows = _universe_rows_with_snapshot_contract(payload)

    assert rows[0] is not payload["rows"][0]
    assert rows[0]["membership_available_at"] == "2026-08-07T02:00:00+00:00"
    assert rows[0]["return_3m_percent_available_at"] == "2026-08-07T02:00:00+00:00"


def test_cached_universe_reads_daily_catalogue_table(monkeypatch) -> None:
    _reset_universe_memory_for_tests()
    replace_fund_daily_catalogue(
        [_raw_row("000311")],
        snapshot_available_at=datetime.now(timezone.utc).isoformat(),
        source="test_table",
    )
    fetched: list[object] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_universe",
        lambda **_kwargs: fetched.append(1) or [],
    )

    rows = fetch_discovery_fund_universe_cached()

    assert [row["fund_code"] for row in rows] == ["000311"]
    assert rows[0]["membership_available_at"]
    assert rows[0]["return_3y_percent"] == 18.5
    assert fetched == []


def test_cache_only_does_not_fetch_when_table_and_blob_are_empty(monkeypatch) -> None:
    _reset_universe_memory_for_tests()
    monkeypatch.setattr(cache, "get_spot_snapshot_any_age", lambda _key: None)
    fetched: list[object] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_universe",
        lambda **_kwargs: fetched.append(1) or [{"fund_code": "000999", "fund_name": "不该拉"}],
    )

    assert fetch_discovery_fund_universe_cache_only() == []
    assert fetched == []
    assert get_fund_daily_catalogue_meta() is None


def test_cache_only_imports_legacy_blob_without_fetch(monkeypatch) -> None:
    _reset_universe_memory_for_tests()
    blob = _build_universe_snapshot([_raw_row("000222")])
    monkeypatch.setattr(
        cache,
        "get_spot_snapshot_any_age",
        lambda key: blob if key == cache._UNIVERSE_CACHE_KEY else None,
    )
    fetched: list[object] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_universe",
        lambda **_kwargs: fetched.append(1) or [],
    )

    rows = fetch_discovery_fund_universe_cache_only()

    assert [row["fund_code"] for row in rows] == ["000222"]
    assert fetched == []
    meta = get_fund_daily_catalogue_meta()
    assert meta is not None
    assert meta["row_count"] == 1


def test_refresh_writes_catalogue_table_not_json_blob(monkeypatch) -> None:
    written_keys: list[str] = []
    monkeypatch.setattr(
        cache,
        "save_spot_snapshot",
        lambda key, _payload: written_keys.append(key),
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_universe",
        lambda **_kwargs: [_raw_row("000888")],
    )

    rows = cache._refresh_discovery_universe_under_lock(limit=20, force=True)

    assert [row["fund_code"] for row in rows] == ["000888"]
    assert cache._UNIVERSE_CACHE_KEY not in written_keys
    meta = get_fund_daily_catalogue_meta()
    assert meta is not None
    assert meta["row_count"] == 1
    assert meta["source"] == cache._UNIVERSE_SNAPSHOT_SOURCE
