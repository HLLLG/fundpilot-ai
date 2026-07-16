from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.amac_benchmark_index_data import amac_name_to_code_pairs
from app.services.fund_benchmark_research import (
    attach_fund_benchmark_metrics,
    build_fund_benchmark_research,
    build_fund_benchmark_research_batch,
)


DECISION_AT = datetime(2026, 7, 14, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
AVAILABLE_AT = "2026-07-14T15:59:00+08:00"


def test_csi_health_care_index_uses_the_official_000933_identity() -> None:
    mappings = dict(amac_name_to_code_pairs())

    assert mappings["中证医药卫生指数"] == "000933"


def _business_days(count: int, *, end: date = date(2026, 7, 14)) -> list[date]:
    output: list[date] = []
    cursor = end
    while len(output) < count:
        if cursor.weekday() < 5:
            output.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(output))


def _series(
    daily_return: float,
    *,
    count: int = 300,
    value_key: str = "nav",
    available_at: str = AVAILABLE_AT,
    source: str = "pytest.frozen_series",
    future_return: float | None = None,
) -> dict:
    value = 1.0
    rows: list[dict] = []
    for day in _business_days(count):
        rows.append({"date": day.isoformat(), value_key: value})
        value *= 1.0 + daily_return
    if future_return is not None:
        rows.append({"date": "2026-07-15", value_key: value * (1 + future_return)})
    return {
        "points": rows,
        "source": source,
        "available_at": available_at,
    }


def _formal_spec() -> dict:
    return {
        "schema_version": "fund_benchmark_mapping.v1",
        "mapping_id": "fbm-formal",
        "tier": "fund_contract_exact",
        "benchmark_kind": "official_contract",
        "contract_verification_kind": "verified_fund_contract",
        "completeness": "complete",
        "formal_excess_eligible": True,
        "benchmark_name": "沪深300×60%+中债综合×40%",
        "available_at": "2026-07-14T15:00:00+08:00",
        "components": [
            {
                "component_id": "equity",
                "benchmark_code": "000300",
                "weight_percent": 60,
            },
            {
                "component_id": "bond",
                "benchmark_code": "H11001",
                "weight_percent": 40,
            },
        ],
    }


def _tracking_spec() -> dict:
    return {
        "schema_version": "fund_benchmark_mapping.v1",
        "mapping_id": "fbm-tracking",
        "tier": "tracked_index_exact",
        "benchmark_kind": "tracking_index",
        "contract_verification_kind": "third_party_profile",
        "completeness": "complete",
        "formal_excess_eligible": False,
        "benchmark_code": "000300",
        "benchmark_name": "沪深300指数",
        "available_at": "2026-07-14T15:00:00+08:00",
        "components": [
            {
                "component_id": "index:000300",
                "benchmark_code": "000300",
                "weight_percent": 100,
            }
        ],
    }


def test_formal_contract_produces_excess_drawdown_and_rolling_metrics() -> None:
    result = build_fund_benchmark_research(
        _series(0.0010),
        _formal_spec(),
        {
            "equity": _series(0.0008, value_key="close"),
            "bond": _series(0.0002, value_key="close"),
        },
        decision_at=DECISION_AT,
        management_style="active",
    )

    assert result["status"] == "qualified"
    assert result["comparison_role"] == "formal_excess"
    assert result["formal_excess_eligible"] is True
    assert result["execution_tilt_eligible"] is False
    assert result["alignment"]["common_return_sample_days"] == 299
    assert result["available_horizon_count"] == 3
    for label in ("3m", "6m", "1y"):
        row = result["horizons"][label]
        assert row["status"] == "available"
        assert row["formal_excess_return_percent"] > 0
        assert row["reference_difference_percent"] is None
        assert row["fund_max_drawdown_percent"] == 0
        assert row["benchmark_max_drawdown_percent"] == 0
    assert result["rolling_comparison"]["formal_excess_win_rate_percent"] == 100
    assert result["rolling_comparison"]["reference_outperformance_rate_percent"] is None
    assert result["tracking_metrics"]["applicable"] is False
    assert len(result["snapshot_hash"]) == 64


def test_tracking_reference_never_becomes_formal_excess() -> None:
    result = build_fund_benchmark_research(
        _series(0.0007),
        _tracking_spec(),
        {"index:000300": _series(0.0006, value_key="close")},
        decision_at=DECISION_AT,
        management_style="passive_index",
    )

    assert result["status"] == "qualified"
    assert result["comparison_role"] == "tracking_reference"
    assert result["formal_excess_eligible"] is False
    row = result["horizons"]["1y"]
    assert row["formal_excess_return_percent"] is None
    assert row["reference_difference_percent"] > 0
    assert result["rolling_comparison"]["formal_excess_win_rate_percent"] is None
    assert result["rolling_comparison"]["reference_outperformance_rate_percent"] == 100
    assert result["tracking_metrics"]["applicable"] is True
    assert result["tracking_metrics"]["available"] is True
    assert result["tracking_metrics"]["tracking_difference_percent"] > 0


def test_friendly_formal_flag_without_verified_contract_stays_reference_only() -> None:
    forged = _tracking_spec()
    forged.update(
        {
            "tier": "fund_contract_exact",
            "benchmark_kind": "official_contract",
            "formal_excess_eligible": True,
            "contract_verification_kind": "live_fund_disclosure",
        }
    )
    result = build_fund_benchmark_research(
        _series(0.0007),
        forged,
        {"index:000300": _series(0.0006, value_key="close")},
        decision_at=DECISION_AT,
    )

    assert result["comparison_role"] == "tracking_reference"
    assert result["formal_excess_eligible"] is False
    assert result["horizons"]["3m"]["formal_excess_return_percent"] is None


def test_missing_composite_leg_fails_closed_without_reweighting() -> None:
    result = build_fund_benchmark_research(
        _series(0.0010),
        _formal_spec(),
        {"equity": _series(0.0008, value_key="close")},
        decision_at=DECISION_AT,
    )

    assert result["status"] == "unavailable"
    assert result["qualified"] is False
    assert result["reason_codes"] == [
        "benchmark_component_bond_snapshot_envelope_missing"
    ]
    assert result["components"][0]["status"] == "available"
    assert result["components"][1]["status"] == "unavailable"


@pytest.mark.parametrize(
    ("mutator", "reason_fragment"),
    [
        (
            lambda payload: payload.update(
                {"available_at": "2026-07-14T16:00:01+08:00"}
            ),
            "snapshot_available_after_decision",
        ),
        (
            lambda payload: payload["points"].append(dict(payload["points"][-1])),
            "point_date_duplicated",
        ),
    ],
)
def test_invalid_or_future_fund_snapshot_fails_closed(mutator, reason_fragment) -> None:
    fund = _series(0.0010)
    mutator(fund)
    result = build_fund_benchmark_research(
        fund,
        _tracking_spec(),
        {"index:000300": _series(0.0006, value_key="close")},
        decision_at=DECISION_AT,
    )

    assert result["status"] == "unavailable"
    assert any(reason_fragment in reason for reason in result["reason_codes"])


def test_future_price_points_are_dropped_before_all_metrics() -> None:
    baseline = build_fund_benchmark_research(
        _series(0.0007),
        _tracking_spec(),
        {"index:000300": _series(0.0006, value_key="close")},
        decision_at=DECISION_AT,
        management_style="passive_index",
    )
    future = build_fund_benchmark_research(
        _series(0.0007, future_return=5.0),
        _tracking_spec(),
        {
            "index:000300": _series(
                0.0006,
                value_key="close",
                future_return=-0.9,
            )
        },
        decision_at=DECISION_AT,
        management_style="passive_index",
    )

    assert future["horizons"] == baseline["horizons"]
    assert future["rolling_comparison"] == baseline["rolling_comparison"]
    assert future["tracking_metrics"] == baseline["tracking_metrics"]


def test_insufficient_sample_remains_descriptive_and_unqualified() -> None:
    result = build_fund_benchmark_research(
        _series(0.0007, count=40),
        _tracking_spec(),
        {"index:000300": _series(0.0006, count=40, value_key="close")},
        decision_at=DECISION_AT,
    )

    assert result["status"] == "insufficient"
    assert result["qualified"] is False
    assert result["reason_codes"] == ["aligned_return_sample_insufficient"]
    assert result["execution_tilt_eligible"] is False


def test_batch_fetches_shared_component_once_and_attaches_without_reordering() -> None:
    calls = {"fund": [], "component": []}

    def fetch_fund(code: str, name: str, trading_days: int):
        calls["fund"].append((code, name, trading_days))
        return _series(0.0007)

    def fetch_component(component: dict, *, start_date: str, end_date: str):
        calls["component"].append(
            (component["component_id"], start_date, end_date)
        )
        return _series(0.0006, value_key="close")

    funds = [
        {
            "fund_code": "000002",
            "fund_name": "沪深300联接C",
            "fund_type": "指数型",
            "benchmark_spec": _tracking_spec(),
        },
        {
            "fund_code": "000001",
            "fund_name": "沪深300联接A",
            "fund_type": "指数型",
            "benchmark_spec": _tracking_spec(),
        },
    ]
    metrics = build_fund_benchmark_research_batch(
        funds,
        decision_at=DECISION_AT,
        fetch_fund=fetch_fund,
        fetch_component=fetch_component,
    )
    attached = attach_fund_benchmark_metrics(funds, metrics)

    assert list(metrics) == ["000001", "000002"]
    assert sorted(code for code, _name, _days in calls["fund"]) == ["000001", "000002"]
    assert len(calls["component"]) == 1
    assert calls["component"][0][0] == "index:000300"
    assert [row["fund_code"] for row in attached] == ["000002", "000001"]
    assert all(row["benchmark_metrics"]["status"] == "qualified" for row in attached)


def test_batch_does_not_fetch_when_benchmark_identity_is_unavailable() -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("unavailable benchmark must not trigger provider I/O")

    metrics = build_fund_benchmark_research_batch(
        [
            {
                "fund_code": "000001",
                "fund_name": "普通混合A",
                "fund_type": "混合型",
                "benchmark_spec": {
                    "schema_version": "fund_benchmark_mapping.v1",
                    "tier": "unavailable",
                    "status": "unavailable",
                    "formal_excess_eligible": False,
                    "reason": "point_in_time_benchmark_mapping_unavailable",
                    "components": [],
                },
            }
        ],
        decision_at=DECISION_AT,
        fetch_fund=unexpected,
        fetch_component=unexpected,
    )

    assert metrics["000001"]["status"] == "unavailable"
    assert metrics["000001"]["comparison_role"] == "unavailable"
    assert "point_in_time_benchmark_mapping_unavailable" in metrics["000001"][
        "reason_codes"
    ]


def test_default_live_provider_is_not_used_for_historical_replay() -> None:
    metrics = build_fund_benchmark_research_batch(
        [
            {
                "fund_code": "000001",
                "fund_name": "沪深300联接A",
                "fund_type": "指数型",
                "benchmark_spec": _tracking_spec(),
            }
        ],
        decision_at=DECISION_AT,
    )

    assert metrics["000001"]["status"] == "unavailable"
    assert metrics["000001"]["comparison_role"] == "tracking_reference"
    assert metrics["000001"]["reason_codes"] == [
        "historical_live_fetch_disallowed"
    ]


def test_research_records_snapshot_sources_and_availability() -> None:
    result = build_fund_benchmark_research(
        _series(0.0007, source="fund_fixture"),
        _tracking_spec(),
        {
            "index:000300": _series(
                0.0006,
                value_key="close",
                source="index_fixture",
            )
        },
        decision_at=DECISION_AT,
    )

    assert result["fund_series"]["source"] == "fund_fixture"
    assert result["fund_series"]["available_at"] == "2026-07-14T07:59:00+00:00"
    assert result["components"][0]["source"] == "index_fixture"
    assert result["components"][0]["available_at"] == "2026-07-14T07:59:00+00:00"
