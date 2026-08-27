from __future__ import annotations

from datetime import datetime, timezone

from app.database import (
    get_fund_manager_roster_meta,
    list_fund_manager_roster,
    list_fund_manager_roster_by_codes,
    replace_fund_manager_roster,
    replace_fund_research_profiles,
)
from app.services.discovery_candidate_pool import _overlay_catalogue_on_identity_rows
from app.services.fund_discovery_data_cache import fetch_fund_research_profiles_cached
from app.services.fund_manager_roster import (
    _reset_roster_memory_for_tests,
    apply_manager_roster_to_row,
    explode_eastmoney_manager_rows,
    format_career_tenure,
    run_fund_manager_roster_refresh,
)
from app.services.fund_research_profile_store import (
    _reset_profile_memory_for_tests,
    overlay_research_on_universe_rows,
)


def setup_function() -> None:
    _reset_roster_memory_for_tests()
    _reset_profile_memory_for_tests()


def teardown_function() -> None:
    _reset_roster_memory_for_tests()
    _reset_profile_memory_for_tests()


def _aibangni_row() -> list[str]:
    return [
        "30777698",
        "艾邦妮",
        "80000222",
        "华夏基金",
        "001924,010692,010693",
        "华夏国企改革混合,华夏核心价值混合A,华夏核心价值混合C",
        "1456",
        "38.06%",
        "001924",
        "华夏国企改革混合",
        "2.93亿元",
        "38.06%",
    ]


def test_explode_keeps_manager_id_and_career_days() -> None:
    rows = explode_eastmoney_manager_rows([_aibangni_row()])

    assert [row["fund_code"] for row in rows] == ["001924", "010692", "010693"]
    assert {row["manager_id"] for row in rows} == {"30777698"}
    assert {row["career_days"] for row in rows} == {1456}
    assert {row["current_best_tenure_return_percent"] for row in rows} == {38.06}
    assert all("career_annual_return_percent" not in row for row in rows)
    assert format_career_tenure(1456) == "3年又361天"


def test_replace_roster_is_atomic_and_keyed_by_fund_and_manager() -> None:
    first = replace_fund_manager_roster(
        explode_eastmoney_manager_rows([_aibangni_row()]),
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney.fund_manager_em",
    )
    second = replace_fund_manager_roster(
        [
            {
                "fund_code": "000001",
                "manager_id": "30000001",
                "manager_name": "张三",
                "career_days": 2000,
                "current_best_tenure_return_percent": 12.5,
            },
            {
                "fund_code": "000001",
                "manager_id": "30000002",
                "manager_name": "李四",
                "career_days": 400,
            },
        ],
        snapshot_available_at="2026-08-27T16:00:00+00:00",
        source="eastmoney.fund_manager_em",
    )

    assert first == 3
    assert second == 2
    assert [row["manager_name"] for row in list_fund_manager_roster()] == ["张三", "李四"]
    found = list_fund_manager_roster_by_codes(["1", "000001", "001924"])
    assert set(found) == {"000001"}
    assert found["000001"][0]["career_days"] == 2000
    assert get_fund_manager_roster_meta() == {
        "snapshot_available_at": "2026-08-27T16:00:00+00:00",
        "source": "eastmoney.fund_manager_em",
        "row_count": 2,
    }


def test_apply_roster_uses_max_career_days_among_current_managers() -> None:
    row = apply_manager_roster_to_row(
        {"fund_code": "000001", "fund_manager": "张三/李四"},
        [
            {
                "manager_id": "1",
                "manager_name": "张三",
                "career_days": 2000,
                "current_best_tenure_return_percent": 12.5,
            },
            {
                "manager_id": "2",
                "manager_name": "李四",
                "career_days": 400,
                "current_best_tenure_return_percent": 4.0,
            },
        ],
    )

    assert row["manager_career_days"] == 2000
    assert row["manager_career_tenure"] == "5年又175天"
    assert row["manager_best_tenure_return_percent"] == 12.5
    assert "career_annual_return_percent" not in row
    assert all("career_annual_return_percent" not in item for item in row["fund_managers"])
    assert [item["manager_name"] for item in row["fund_managers"]] == ["张三", "李四"]


def test_overlay_and_cached_profile_join_roster_without_fetch(monkeypatch) -> None:
    replace_fund_research_profiles(
        [
            {
                "fund_code": "001924",
                "fund_name": "华夏国企改革混合",
                "fund_category": "混合型",
                "fund_scale_yi": 1.36,
                "fund_scale_basis": "quarterly_net_assets",
                "established_date": "2015-06-02",
                "fund_manager": "艾邦妮",
            }
        ],
        snapshot_available_at=datetime.now(timezone.utc).isoformat(),
        source="sina.fund_scale_open_sina",
    )
    replace_fund_manager_roster(
        explode_eastmoney_manager_rows([_aibangni_row()]),
        snapshot_available_at="2026-08-27T16:00:00+00:00",
        source="eastmoney.fund_manager_em",
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_eastmoney_fund_manager_roster",
        lambda **_kwargs: fetched.append("roster") or [],
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_scale_universe",
        lambda **_kwargs: fetched.append("sina") or [],
    )

    overlaid = overlay_research_on_universe_rows(
        [{"fund_code": "001924", "fund_name": "华夏国企改革混合"}]
    )
    cached = fetch_fund_research_profiles_cached(["001924"])
    identity = _overlay_catalogue_on_identity_rows(
        [
            {
                "fund_code": "001924",
                "sector_name": "国企改革",
                "source": "precompute_holdings",
                "identity_status": "verified",
            }
        ]
    )

    assert fetched == []
    assert overlaid[0]["manager_career_days"] == 1456
    assert overlaid[0]["manager_career_tenure"] == "3年又361天"
    assert overlaid[0]["manager_best_tenure_return_percent"] == 38.06
    assert cached["001924"]["manager_career_days"] == 1456
    assert identity[0]["manager_career_days"] == 1456


def test_roster_refresh_keeps_snapshot_when_source_empty(monkeypatch) -> None:
    replace_fund_manager_roster(
        explode_eastmoney_manager_rows([_aibangni_row()]),
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney.fund_manager_em",
    )
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_eastmoney_fund_manager_roster",
        lambda **_kwargs: [],
    )

    kept = run_fund_manager_roster_refresh(force=True)

    assert kept["ok"] is True
    assert kept["row_count"] == 3
    assert list_fund_manager_roster()[0]["manager_name"] == "艾邦妮"


def test_roster_refresh_writes_exploded_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_eastmoney_fund_manager_roster",
        lambda **_kwargs: [_aibangni_row()],
    )

    summary = run_fund_manager_roster_refresh(force=True)

    assert summary["ok"] is True
    assert summary["written"] == 3
    assert summary["source"] == "eastmoney.fund_manager_em"
    assert list_fund_manager_roster_by_codes(["010692"])["010692"][0]["career_days"] == 1456
