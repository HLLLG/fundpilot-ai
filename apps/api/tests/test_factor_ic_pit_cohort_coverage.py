"""PIT cohort 覆盖率口径：结构上算不出因子的成员不进分母。

背景：cohort_nav_coverage_rate 的门槛是 MIN_POINT_IN_TIME_COVERAGE=0.90，而生产实测
长期停在 0.888。原因不是抓取范围不足（run_factor_ic 早已把历史 cohort 成员并入 NAV
抓取），而是分母里混进了成立不足 factor_lookback 个交易日的基金——它们本来就被
len(history) 检查拒掉、从不进入因子横截面，却把覆盖率拉低成「样本有多年轻」。

这些用例锁住三件事：
  1. 成立日证明历史不足时，该成员不计入分母；
  2. 成立日缺失时仍计入分母（证据缺失不等于不合格，fail-open）；
  3. 无论上面哪种情况，IC 数值都不变——证明这只是指标口径问题，不是样本变化。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.factor_ic_backtest import NavPoint
from app.services.factor_ic_pit import (
    _provably_short_history,
    compute_point_in_time_segmented_ic,
)

FACTOR_LOOKBACK = 10
CALENDAR_DAYS = 20
ANCHOR_INDEX = 11  # factor_lookback - 1 + QDII lag(2)
MATURE_CODES = ("100001", "100002", "100003", "100004", "100005", "100006")
YOUNG_CODE = "200001"
YOUNG_START_INDEX = 5


def _calendar() -> list[str]:
    start = date(2025, 3, 3)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(CALENDAR_DAYS)]


def _nav_series(calendar: list[str], *, seed: int, start_index: int = 0) -> list[NavPoint]:
    points: list[NavPoint] = []
    for index in range(start_index, len(calendar)):
        nav = 1.0 + seed * 0.01 + index * 0.001 * (seed + 1)
        points.append(NavPoint(calendar[index], nav, "daily_growth"))
    return points


def _nav_panel(calendar: list[str], *, include_young: bool) -> dict[str, list[NavPoint]]:
    panel = {
        code: _nav_series(calendar, seed=index)
        for index, code in enumerate(MATURE_CODES)
    }
    if include_young:
        # 只有后半段净值，凑不出 factor_lookback 个点。
        panel[YOUNG_CODE] = _nav_series(
            calendar, seed=99, start_index=YOUNG_START_INDEX
        )
    return panel


def _snapshot(
    calendar: list[str],
    *,
    young: str | None = None,
    young_inception: str | None = None,
) -> dict:
    members = [
        {
            "fund_code": code,
            "fund_type": "hh",
            "inception_date": calendar[0],
        }
        for code in MATURE_CODES
    ]
    if young is not None:
        member: dict[str, object] = {"fund_code": young, "fund_type": "hh"}
        if young_inception is not None:
            member["inception_date"] = young_inception
        members.append(member)
    return {
        "snapshot_id": "snapshot-1",
        "snapshot_date": calendar[ANCHOR_INDEX],
        "available_at": f"{calendar[ANCHOR_INDEX]}T00:00:00+00:00",
        "members": members,
    }


def _run(nav_panel: dict[str, list[NavPoint]], snapshot: dict):
    return compute_point_in_time_segmented_ic(
        nav_panel=nav_panel,
        snapshots=[snapshot],
        rebalance_step=100,  # 只留 ANCHOR_INDEX 这一个锚点，断言才好读
        forward_horizons=(5,),
        factor_lookback=FACTOR_LOOKBACK,
        min_cross_section=2,
        max_snapshot_age_days=400,
    )


def test_baseline_cohort_is_fully_covered() -> None:
    calendar = _calendar()
    _, coverage = _run(
        _nav_panel(calendar, include_young=False), _snapshot(calendar)
    )
    assert coverage["effective_anchor_count"] == 1
    assert coverage["cohort_membership_count"] == len(MATURE_CODES)
    assert coverage["nav_covered_membership_count"] == len(MATURE_CODES)
    assert coverage["cohort_nav_coverage_rate"] == 1.0


def test_member_proven_too_young_is_excluded_from_the_denominator() -> None:
    calendar = _calendar()
    _, coverage = _run(
        _nav_panel(calendar, include_young=True),
        _snapshot(
            calendar,
            young=YOUNG_CODE,
            young_inception=calendar[YOUNG_START_INDEX],
        ),
    )
    # 成立日证明该锚点上凑不出 10 个净值点 → 不进分母，覆盖率不被稀释。
    assert coverage["cohort_membership_count"] == len(MATURE_CODES)
    assert coverage["nav_covered_membership_count"] == len(MATURE_CODES)
    assert coverage["cohort_nav_coverage_rate"] == 1.0


def test_member_with_unknown_inception_still_counts() -> None:
    calendar = _calendar()
    _, coverage = _run(
        _nav_panel(calendar, include_young=True),
        _snapshot(calendar, young=YOUNG_CODE, young_inception=None),
    )
    # 证据缺失不等于不合格：仍进分母，由真实净值检查裁决，于是覆盖率下降。
    assert coverage["cohort_membership_count"] == len(MATURE_CODES) + 1
    assert coverage["nav_covered_membership_count"] == len(MATURE_CODES)
    assert coverage["cohort_nav_coverage_rate"] == pytest.approx(6 / 7, abs=1e-4)


def test_coverage_bookkeeping_never_changes_the_ic_numbers() -> None:
    """三种情况下 IC 完全一致，证明这只是分母口径、不是样本变化。"""
    calendar = _calendar()
    baseline, _ = _run(
        _nav_panel(calendar, include_young=False), _snapshot(calendar)
    )
    filtered, _ = _run(
        _nav_panel(calendar, include_young=True),
        _snapshot(
            calendar,
            young=YOUNG_CODE,
            young_inception=calendar[YOUNG_START_INDEX],
        ),
    )
    counted, _ = _run(
        _nav_panel(calendar, include_young=True),
        _snapshot(calendar, young=YOUNG_CODE, young_inception=None),
    )

    def _ic_rows(output: dict) -> dict:
        rows = {}
        for segment, segment_row in output.items():
            for horizon, horizon_row in (segment_row.get("horizons") or {}).items():
                for factor_row in horizon_row.get("factors") or []:
                    rows[(segment, horizon, factor_row["factor"])] = (
                        factor_row.get("mean_ic"),
                        factor_row.get("n_periods"),
                    )
        return rows

    assert _ic_rows(baseline) == _ic_rows(filtered)
    assert _ic_rows(baseline) == _ic_rows(counted)


class TestProvablyShortHistory:
    def test_unknown_inception_is_not_disqualifying(self) -> None:
        calendar = _calendar()
        assert (
            _provably_short_history(
                calendar,
                inception_date=None,
                as_of=calendar[ANCHOR_INDEX],
                minimum_points=FACTOR_LOOKBACK,
            )
            is False
        )

    def test_inception_after_as_of_is_short(self) -> None:
        calendar = _calendar()
        assert (
            _provably_short_history(
                calendar,
                inception_date=date.fromisoformat(calendar[ANCHOR_INDEX + 1]),
                as_of=calendar[ANCHOR_INDEX],
                minimum_points=FACTOR_LOOKBACK,
            )
            is True
        )

    def test_too_few_trading_days_since_inception_is_short(self) -> None:
        calendar = _calendar()
        # [calendar[5], calendar[11]] 只有 7 个交易日 < 10
        assert (
            _provably_short_history(
                calendar,
                inception_date=date.fromisoformat(calendar[5]),
                as_of=calendar[ANCHOR_INDEX],
                minimum_points=FACTOR_LOOKBACK,
            )
            is True
        )

    def test_exactly_enough_trading_days_is_not_short(self) -> None:
        calendar = _calendar()
        # [calendar[2], calendar[11]] 恰好 10 个交易日
        assert (
            _provably_short_history(
                calendar,
                inception_date=date.fromisoformat(calendar[2]),
                as_of=calendar[ANCHOR_INDEX],
                minimum_points=FACTOR_LOOKBACK,
            )
            is False
        )
