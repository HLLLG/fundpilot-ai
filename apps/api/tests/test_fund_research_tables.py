from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import (
    get_fund_research_profile_meta,
    list_fund_research_profiles,
    list_fund_research_profiles_by_codes,
    list_fund_risk_metrics,
    list_fund_risk_metrics_by_codes,
    replace_fund_daily_catalogue,
    replace_fund_research_profiles,
    upsert_fund_risk_metrics,
)
from app.services.discovery_candidate_pool import _overlay_catalogue_on_identity_rows
from app.services.fund_discovery_data_cache import (
    _build_universe_snapshot,
    _reset_universe_memory_for_tests,
    _universe_rows_with_snapshot_contract,
    fetch_discovery_fund_universe_cache_only,
    fetch_fund_research_profiles_cached,
)
from app.services.fund_manager_roster import _reset_roster_memory_for_tests
from app.services.fund_research_profile_store import (
    _reset_profile_memory_for_tests,
    ensure_fund_research_profiles,
    overlay_research_on_universe_rows,
    persist_computed_fund_scales,
    run_fund_research_profile_refresh,
)
from app.services.fund_sharpe import SHARPE_SCHEMA_VERSION


@pytest.fixture(autouse=True)
def _reset_research_memory() -> None:
    _reset_profile_memory_for_tests()
    _reset_roster_memory_for_tests()
    _reset_universe_memory_for_tests()
    yield
    _reset_profile_memory_for_tests()
    _reset_roster_memory_for_tests()
    _reset_universe_memory_for_tests()


def _profile_row(code: str, **extra: object) -> dict:
    row = {
        "fund_code": code,
        "fund_name": "测试基金",
        "fund_category": "股票型",
        "latest_nav": 1.23,
        "fund_shares_yi": 6.91,
        "fund_scale_yi": 8.5,
        "fund_scale_basis": "quarterly_net_assets",
        "established_date": "2018-01-01",
        "fund_manager": "测试经理",
        "profile_updated_at": "2026-08-25",
    }
    row.update(extra)
    return row


def test_replace_fund_research_profiles_is_atomic_snapshot() -> None:
    first = replace_fund_research_profiles(
        [_profile_row("000001"), _profile_row("000002")],
        snapshot_available_at="2026-08-25T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )
    second = replace_fund_research_profiles(
        [_profile_row("000003", fund_scale_yi=3.2)],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )

    assert first == 2
    assert second == 1
    rows = list_fund_research_profiles()
    assert [row["fund_code"] for row in rows] == ["000003"]
    assert rows[0]["fund_scale_yi"] == 3.2
    assert rows[0]["fund_scale_basis"] == "quarterly_net_assets"
    assert rows[0]["fund_shares_yi"] == 6.91
    meta = get_fund_research_profile_meta()
    assert meta == {
        "snapshot_available_at": "2026-08-26T16:00:00+00:00",
        "source": "sina.fund_scale_open_sina",
        "row_count": 1,
    }


def test_list_research_profiles_by_codes_skips_invalid() -> None:
    replace_fund_research_profiles(
        [_profile_row("000011"), _profile_row("000012")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )

    found = list_fund_research_profiles_by_codes(["11", "000012", "not-a-code", "000000"])

    assert set(found) == {"000011", "000012"}
    assert found["000011"]["fund_manager"] == "测试经理"


def test_persist_computed_fund_scales_overwrites_sql_immediately() -> None:
    replace_fund_research_profiles(
        [_profile_row("000001", fund_scale_yi=None, fund_scale_basis=None)],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )
    ensure_fund_research_profiles()

    written = persist_computed_fund_scales(
        [
            {
                "fund_code": "000001",
                "fund_scale_yi": 30.9652,
                "fund_scale_basis": "nav_times_latest_shares",
            }
        ]
    )

    assert written == 1
    stored = list_fund_research_profiles_by_codes(["000001"])["000001"]
    assert stored["fund_scale_yi"] == 30.9652
    assert stored["fund_scale_basis"] == "nav_times_latest_shares"
    assert get_fund_research_profile_meta()["snapshot_available_at"] == (
        "2026-08-26T16:00:00+00:00"
    )
    assert ensure_fund_research_profiles()["000001"]["fund_scale_yi"] == 30.9652


def test_upsert_risk_metrics_clamps_invalid_drawdown() -> None:
    written = upsert_fund_risk_metrics(
        [
            {
                "fund_code": "000021",
                "sharpe_1y": 0.74,
                "sharpe_3y": 0.49,
                "max_drawdown_1y_percent": -18.5,
                "nav_as_of": "2026-08-25",
                "nav_point_count": 243,
            },
            {
                "fund_code": "000022",
                "sharpe_1y": 0.1,
                "max_drawdown_1y_percent": 12.0,
                "max_drawdown_3y_percent": 8.0,
            },
        ],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
    )

    assert written == 2
    rows = list_fund_risk_metrics_by_codes(["000021", "000022"])
    assert rows["000021"]["max_drawdown_1y_percent"] == -18.5
    assert rows["000021"]["schema_version"] == SHARPE_SCHEMA_VERSION
    assert rows["000021"]["source"] == "computed_nav"
    assert rows["000022"]["max_drawdown_1y_percent"] is None
    assert rows["000022"]["max_drawdown_3y_percent"] is None
    assert list_fund_risk_metrics()[0]["fund_code"] == "000021"


def test_cached_profiles_read_table_and_skip_sina_when_complete(monkeypatch) -> None:
    replace_fund_research_profiles(
        [_profile_row("000311")],
        snapshot_available_at=datetime.now(timezone.utc).isoformat(),
        source="sina.fund_scale_open_sina",
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: fetched.append("sina_universe") or [],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: fetched.append("sina_filter") or [],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: fetched.append("xq") or [],
    )

    rows = fetch_fund_research_profiles_cached(["000311"])

    assert rows["000311"]["fund_scale_yi"] == 8.5
    assert rows["000311"]["fund_manager"] == "测试经理"
    assert rows["000311"]["profile_status"] == "complete"
    assert fetched == []


def test_discovery_read_path_never_fetches_sina_scale(monkeypatch) -> None:
    replace_fund_research_profiles(
        [],
        snapshot_available_at="2026-08-26T00:00:00+00:00",
        source="test_clear",
    )
    _reset_profile_memory_for_tests()
    monkeypatch.setattr(
        "app.services.fund_research_profile_store._import_legacy_profile_blob_if_needed",
        lambda: {},
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: fetched.append("sina_universe") or [
            _profile_row("000311")
        ],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: fetched.append("sina_filter") or [
            _profile_row("000311")
        ],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: fetched.append("xq") or [],
    )

    fetch_fund_research_profiles_cached(["000311"])

    assert "sina_universe" not in fetched
    assert "sina_filter" not in fetched


def test_ensure_profiles_does_not_pull_sina_when_empty(monkeypatch) -> None:
    replace_fund_research_profiles(
        [],
        snapshot_available_at="2026-08-26T00:00:00+00:00",
        source="test_clear",
    )
    _reset_profile_memory_for_tests()
    monkeypatch.setattr(
        "app.services.fund_research_profile_store._import_legacy_profile_blob_if_needed",
        lambda: {},
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: fetched.append("sina_universe") or [_profile_row("000001")],
    )

    assert ensure_fund_research_profiles(blocking_if_empty=True) == {}
    assert fetched == []


def test_run_refresh_writes_snapshot_and_reports_empty_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: [_profile_row("000401", fund_scale_yi=12.0)],
    )

    summary = run_fund_research_profile_refresh(force=True)

    assert summary["ok"] is True
    assert summary["written"] == 1
    assert summary["row_count"] == 1
    assert list_fund_research_profiles()[0]["fund_code"] == "000401"

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: [],
    )
    kept = run_fund_research_profile_refresh(force=True)
    assert kept["ok"] is True
    assert kept["row_count"] == 1


def test_research_overlay_copies_stamped_universe_rows() -> None:
    snapshot = _build_universe_snapshot(
        [
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "fund_type": "gp",
                "return_3m_percent": 4.2,
            }
        ]
    )
    replace_fund_research_profiles(
        [_profile_row("000001", fund_manager="张三")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )
    upsert_fund_risk_metrics(
        [{"fund_code": "000001", "max_drawdown_1y_percent": -12.3, "sharpe_1y": 0.5}],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
    )

    stamped = _universe_rows_with_snapshot_contract(snapshot)
    overlaid = overlay_research_on_universe_rows(stamped)

    assert overlaid[0] is not stamped[0]
    assert stamped[0].get("fund_manager") is None
    assert stamped[0].get("max_drawdown_1y_percent") is None
    assert overlaid[0]["fund_manager"] == "张三"
    assert overlaid[0]["fund_scale_yi"] == 8.5
    assert overlaid[0]["max_drawdown_1y_percent"] == -12.3


def test_cache_only_overlays_research_without_fetch(monkeypatch) -> None:
    replace_fund_daily_catalogue(
        [
            {
                "fund_code": "000222",
                "fund_name": "测试基金",
                "fund_type": "gp",
                "return_3m_percent": 4.2,
                "return_3y_percent": 18.5,
            }
        ],
        snapshot_available_at=datetime.now(timezone.utc).isoformat(),
        source="test_table",
    )
    replace_fund_research_profiles(
        [_profile_row("000222", fund_scale_yi=21.0)],
        snapshot_available_at=datetime.now(timezone.utc).isoformat(),
        source="sina.fund_scale_open_sina",
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_universe",
        lambda **_kwargs: fetched.append("universe") or [],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: fetched.append("scale") or [],
    )

    rows = fetch_discovery_fund_universe_cache_only()

    assert [row["fund_code"] for row in rows] == ["000222"]
    assert rows[0]["fund_scale_yi"] == 21.0
    assert fetched == []


def test_identity_overlay_uses_research_tables() -> None:
    replace_fund_research_profiles(
        [_profile_row("000021", fund_scale_yi=1.2, fund_manager="迷你经理")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )

    rows = _overlay_catalogue_on_identity_rows(
        [
            {
                "fund_code": "000021",
                "sector_name": "半导体",
                "source": "precompute_holdings",
                "identity_status": "verified",
            }
        ]
    )

    assert rows[0]["fund_scale_yi"] == 1.2
    assert rows[0]["fund_manager"] == "迷你经理"
