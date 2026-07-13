from __future__ import annotations

from datetime import date, timedelta

from app.services.factor_ic_pit import (
    aggregate_economic_significance,
    economic_significance_qualified,
)


def _observations(count: int = 50, *, top: float = 0.02, coverage: float = 0.95):
    start = date(2024, 1, 1)
    rows = []
    for index in range(count):
        bottom = -0.01 + (index % 3) * 0.0001
        top_return = top + (index % 5) * 0.0001
        valid = 95
        eligible = round(valid / coverage)
        rows.append(
            {
                "anchor": (start + timedelta(days=100 + index * 10)).isoformat(),
                "valid_count": valid,
                "eligible_count": eligible,
                "top_relative_return": top_return,
                "bottom_relative_return": bottom,
                "spread": top_return - bottom,
                "quintile_relative_returns": [bottom, -0.004, 0.0, 0.008, top_return],
                "top_codes": [f"{offset:06d}" for offset in range(20)],
            }
        )
    return rows


def _calendar() -> list[str]:
    start = date(2024, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(700)]


def _aggregate(rows):
    return aggregate_economic_significance(
        rows,
        hac_lags=1,
        walk_forward_folds=5,
        embargo_days=20,
        trading_calendar=_calendar(),
    )


def test_economic_significance_reports_cost_downside_and_walk_forward() -> None:
    result = _aggregate(_observations())

    assert result["schema_version"] == "factor_economic_significance.v1"
    assert result["label_type"] == "peer_group_relative_total_return"
    assert result["point_in_time_scope"] == "membership_only"
    assert result["nav_revision_pit"] is False
    assert result["entry_rule"] == "next_trading_day_first_available_nav"
    assert result["entry_offset_trading_days"] == 1
    assert result["top_bottom_spread"] > 0
    assert result["ci_low"] > 0
    assert result["quintile_monotonicity"] == 1.0
    assert {row["fee_rate"] for row in result["cost_scenarios"]} == {0.0, 0.005, 0.01}
    assert result["top_relative_return_p10"] is not None
    assert result["top_relative_return_worst"] is not None
    assert result["walk_forward"]["valid_fold_count"] == 5
    assert result["qualified"] is True


def test_economic_gate_fails_closed_for_short_history() -> None:
    result = _aggregate(_observations(35))
    assert result["period_count"] == 35
    assert result["qualified"] is False


def test_economic_gate_fails_closed_after_cost_or_low_coverage() -> None:
    after_cost = _aggregate(_observations(top=0.003))
    low_coverage = _aggregate(_observations(coverage=0.70))

    assert after_cost["cost_scenarios"][1]["top_net_relative_return"] < 0
    assert economic_significance_qualified(after_cost) is False
    assert low_coverage["peer_relative_coverage_rate"] < 0.8
    assert economic_significance_qualified(low_coverage) is False
