from __future__ import annotations

from datetime import date, timedelta

from app.services.factor_ic_backtest import NavPoint
from app.services.factor_ic_pit import (
    _nav_on_or_after,
    benjamini_hochberg,
    build_anchor_cohorts,
    compute_point_in_time_segmented_ic,
    expanding_walk_forward,
    normalize_universe_snapshots,
    nav_information_window,
    select_asof_snapshot,
)


def test_asof_snapshot_never_uses_a_late_publication_or_stale_snapshot() -> None:
    snapshots = [
        {
            "snapshot_id": "observable",
            "snapshot_date": "2026-01-08",
            "available_at": "2026-01-08T08:00:00+00:00",
            "members": [{"fund_code": "000001", "fund_type": "gp"}],
        },
        {
            # Backdated, but not observable until after the anchor.
            "snapshot_id": "future-publication",
            "snapshot_date": "2026-01-09",
            "available_at": "2026-01-20T08:00:00+00:00",
            "members": [{"fund_code": "999999", "fund_type": "gp"}],
        },
    ]

    selected = select_asof_snapshot(snapshots, "2026-01-10", max_age_days=7)
    assert selected is not None and selected.snapshot_id == "observable"
    assert select_asof_snapshot(snapshots, "2026-01-16", max_age_days=7) is None


def test_member_learned_after_snapshot_is_excluded() -> None:
    [snapshot] = normalize_universe_snapshots(
        [
            {
                "snapshot_id": "s1",
                "snapshot_date": "2026-01-10",
                "available_at": "2026-01-10T10:00:00+00:00",
                "members": [
                    {"fund_code": "000001", "available_at": "2026-01-10T09:00:00+00:00"},
                    {"fund_code": "000002", "available_at": "2026-01-11T09:00:00+00:00"},
                ],
            }
        ]
    )
    assert [member.fund_code for member in snapshot.members] == ["000001"]


def test_anchor_coverage_reports_missing_snapshot_without_borrowing_future() -> None:
    cohorts, coverage = build_anchor_cohorts(
        anchors=["2026-01-01", "2026-01-08", "2026-01-15"],
        snapshots=[
            {
                "snapshot_id": "s1",
                "snapshot_date": "2026-01-08",
                "available_at": "2026-01-08T00:00:00+00:00",
                "members": ["000001"],
            }
        ],
    )
    assert set(cohorts) == {"2026-01-08", "2026-01-15"}
    assert coverage["anchor_coverage_rate"] == 0.6667
    assert coverage["future_snapshot_violations"] == 0


def test_expanding_walk_forward_has_five_folds_and_twenty_day_embargo() -> None:
    start = date(2025, 1, 1)
    observations = [
        ((start + timedelta(days=index)).isoformat(), 0.03 + index * 0.0001)
        for index in range(120)
    ]
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(120)]
    result = expanding_walk_forward(
        observations,
        folds=5,
        embargo_days=20,
        trading_calendar=calendar,
    )
    assert result["fold_count"] == 5
    assert result["valid_fold_count"] == 5
    assert result["same_direction_folds"] == 5
    for fold in result["folds"]:
        train_end = date.fromisoformat(fold["train_end"])
        test_start = date.fromisoformat(fold["test_start"])
        assert (test_start - train_end).days > 20


def test_benjamini_hochberg_is_monotone_and_retains_missing_values() -> None:
    adjusted = benjamini_hochberg({"a": 0.01, "b": 0.02, "c": 0.20, "d": None})
    assert adjusted == {"a": 0.03, "b": 0.03, "c": 0.2, "d": None}


def test_nav_information_window_lags_qdii_two_days_and_enters_next_day() -> None:
    calendar = [f"2026-07-{day:02d}" for day in range(1, 11)]

    qdii = nav_information_window(calendar, 5, fund_type="qdii", horizon=2)
    ordinary = nav_information_window(calendar, 5, fund_type="gp", horizon=2)

    assert qdii == {
        "publication_lag_trading_days": 2,
        "factor_as_of": "2026-07-04",
        "entry_target_date": "2026-07-07",
        "exit_target_date": "2026-07-09",
        "entry_offset_trading_days": 1,
        "holding_horizon_trading_days": 2,
    }
    assert ordinary is not None
    assert ordinary["factor_as_of"] == "2026-07-05"
    assert ordinary["entry_target_date"] == "2026-07-07"


def test_execution_nav_never_falls_back_to_pre_entry_nav() -> None:
    dates = ["2026-07-01", "2026-07-03", "2026-07-06"]
    navs = [1.0, 1.1, 1.2]

    assert _nav_on_or_after(dates, navs, "2026-07-02", max_delay_days=3) == 1.1
    assert _nav_on_or_after(dates, navs, "2026-07-04", max_delay_days=3) == 1.2
    assert _nav_on_or_after(dates, navs, "2026-07-07", max_delay_days=3) is None


def test_qdii_factor_history_excludes_anchor_and_previous_day_nav(monkeypatch) -> None:
    import app.services.factor_ic_pit as pit

    start = date(2026, 1, 1)
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(10)]
    panel = {
        f"{index:06d}": [
            NavPoint(day, 1.0 + day_index * 0.01 + index * 0.001)
            for day_index, day in enumerate(calendar)
        ]
        for index in range(2)
    }
    seen_last_navs: list[float] = []

    def capture(navs):
        seen_last_navs.append(float(navs[-1]))
        return {"momentum": navs[-1], "risk_adjusted": navs[-1], "drawdown": navs[-1]}

    monkeypatch.setattr(pit, "_raw_factors_at", capture)
    compute_point_in_time_segmented_ic(
        nav_panel=panel,
        snapshots=[
            {
                "snapshot_id": "qdii-anchor",
                "snapshot_date": calendar[4],
                "available_at": f"{calendar[4]}T00:00:00+00:00",
                "members": [
                    {"fund_code": code, "fund_type": "qdii"} for code in panel
                ],
            }
        ],
        rebalance_step=20,
        forward_horizons=(1,),
        factor_lookback=3,
        min_cross_section=2,
    )

    # 唯一锚点为 index=4；QDII 因子只能看到 index=2 的 NAV。
    assert seen_last_navs
    assert set(round(value, 3) for value in seen_last_navs) == {1.02, 1.021}


def test_segmented_pit_ic_uses_only_frozen_anchor_memberships() -> None:
    start = date(2024, 1, 1)
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(620)]
    panel: dict[str, list[NavPoint]] = {}
    members = []
    for index in range(24):
        code = f"{index:06d}"
        slope = 0.00005 * (index + 1)
        panel[code] = [
            NavPoint(day, (1 + slope) ** offset)
            for offset, day in enumerate(calendar)
        ]
        members.append({"fund_code": code, "fund_type": "gp"})
    panel["short"] = [NavPoint(calendar[-2], 1.0), NavPoint(calendar[-1], 1.01)]
    members.append({"fund_code": "short", "fund_type": "gp"})
    snapshots = [
        {
            "snapshot_id": f"s-{offset}",
            "snapshot_date": calendar[offset],
            "available_at": f"{calendar[offset]}T00:00:00+00:00",
            "members": members,
        }
        for offset in range(245, 620, 7)
    ]

    segments, coverage = compute_point_in_time_segmented_ic(
        nav_panel=panel,
        snapshots=snapshots,
        rebalance_step=10,
        forward_horizons=(20, 60),
        factor_lookback=250,
    )

    assert coverage["effective_anchor_count"] >= 30
    assert coverage["anchor_coverage_rate"] >= 0.9
    assert coverage["cohort_nav_coverage_rate"] == 0.96
    assert coverage["ready"] is True
    assert coverage["primary_maturity_horizon"] == 20
    assert coverage["horizon_ready"]["20"] is True
    assert (
        coverage["mature_anchor_count_by_horizon"]["20"]
        > coverage["mature_anchor_count_by_horizon"]["60"]
    )
    horizon = segments["gp"]["horizons"]["20"]
    assert horizon["maturity"] == {
        "mature_anchor_count": coverage["mature_anchor_count_by_horizon"]["20"],
        "mature_anchor_coverage_rate": coverage[
            "mature_anchor_coverage_rate_by_horizon"
        ]["20"],
        "ready": True,
    }
    assert horizon["universe_size"] == 24
    assert {
        "momentum", "risk_adjusted", "drawdown", "composite"
    }.issubset({row["factor"] for row in horizon["factors"]})
    assert {
        row["factor"] for row in horizon["factors"]
        if row["factor_family"] == "fund_type_specific"
    } == {
        "medium_momentum",
        "momentum_acceleration",
        "return_consistency",
        "downside_resilience",
        "drawdown_recovery",
    }
    assert all("q_value" in row and "walk_forward" in row for row in horizon["factors"])
    assert all(
        row["economic_significance"]["schema_version"]
        == "factor_economic_significance.v1"
        for row in horizon["factors"]
    )
    assert all(
        row["walk_forward"]["embargo_trading_days"] == 60
        for row in segments["gp"]["horizons"]["60"]["factors"]
    )


def test_point_in_time_ready_requires_primary_horizon_to_be_mature() -> None:
    start = date(2025, 1, 1)
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(140)]
    panel: dict[str, list[NavPoint]] = {}
    members = []
    for index in range(20):
        code = f"{index:06d}"
        panel[code] = [
            NavPoint(day, (1 + 0.0001 * (index + 1)) ** offset)
            for offset, day in enumerate(calendar)
        ]
        members.append({"fund_code": code, "fund_type": "gp"})
    snapshots = [
        {
            "snapshot_id": f"s-{offset}",
            "snapshot_date": day,
            "available_at": f"{day}T00:00:00+00:00",
            "members": members,
        }
        for offset, day in enumerate(calendar)
    ]

    segments, coverage = compute_point_in_time_segmented_ic(
        nav_panel=panel,
        snapshots=snapshots,
        rebalance_step=3,
        forward_horizons=(60,),
        factor_lookback=20,
    )

    assert coverage["effective_anchor_count"] >= 24
    assert coverage["mature_anchor_count_by_horizon"]["60"] < 24
    assert coverage["horizon_ready"]["60"] is False
    assert coverage["ready"] is False
    assert segments["gp"]["horizons"]["60"]["maturity"]["ready"] is False
