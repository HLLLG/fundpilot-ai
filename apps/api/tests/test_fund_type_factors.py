from __future__ import annotations

import math

from app.services.fund_factor_nav import factor_input_from_navs
from app.services.fund_type_factors import (
    TYPE_FACTOR_SCHEMA_VERSION,
    build_type_factor_evidence,
    compute_type_factor_values,
    type_factor_keys,
)


def _navs(*, drift: float = 0.0005, shock: float = 0.0) -> list[float]:
    value = 1.0
    result = []
    for index in range(260):
        period_return = drift + math.sin(index / 8) * shock
        value *= 1 + period_return
        result.append(value)
    return result


def test_active_equity_and_bond_use_different_factor_families() -> None:
    gp = compute_type_factor_values("gp", _navs(shock=0.003))
    zq = compute_type_factor_values("zq", _navs(drift=0.00015, shock=0.0004))

    assert set(gp) == set(type_factor_keys("gp"))
    assert "momentum_acceleration" in gp
    assert "stable_return" in zq
    assert "medium_momentum" not in zq
    assert all(value is None or math.isfinite(value) for value in {**gp, **zq}.values())


def test_risk_features_are_oriented_higher_is_better() -> None:
    stable = compute_type_factor_values("zq", _navs(shock=0.0002))
    volatile = compute_type_factor_values("zq", _navs(shock=0.015))

    assert stable["downside_resilience"] > volatile["downside_resilience"]
    assert stable["tail_resilience"] > volatile["tail_resilience"]


def test_index_tracking_fails_closed_without_exact_benchmark() -> None:
    evidence = build_type_factor_evidence("zs", _navs(), nav_age_days=0)

    assert evidence["tracking_evidence"]["status"] == "insufficient"
    assert "精确" in evidence["tracking_evidence"]["reason"]
    assert "tracking_difference" not in evidence["values"]
    assert "tracking_quality" not in evidence["values"]


def test_index_tracking_is_available_only_with_benchmark_series() -> None:
    benchmark = _navs(drift=0.00045, shock=0.0003)
    evidence = build_type_factor_evidence(
        "zs",
        _navs(drift=0.0005, shock=0.0004),
        benchmark_navs=benchmark,
        nav_age_days=0,
    )

    assert evidence["tracking_evidence"]["status"] == "available"
    assert evidence["values"]["tracking_difference"] is not None
    assert evidence["values"]["tracking_quality"] is not None


def test_index_tracking_rejects_unaligned_benchmark_length() -> None:
    evidence = build_type_factor_evidence(
        "zs",
        _navs(),
        benchmark_navs=_navs()[:-1],
        nav_age_days=0,
    )

    assert evidence["tracking_evidence"]["status"] == "insufficient"
    assert "对齐" in evidence["tracking_evidence"]["reason"]
    assert "tracking_difference" not in evidence["values"]


def test_qdii_relative_momentum_is_not_invented_without_benchmark() -> None:
    values = compute_type_factor_values("qdii", _navs())
    assert "relative_momentum" not in values


def test_scale_is_capacity_guard_and_never_a_return_factor() -> None:
    evidence = build_type_factor_evidence(
        "fof",
        _navs(),
        nav_age_days=0,
        fund_scale_yi=12.5,
    )

    assert evidence["capacity_gate"] == {
        "role": "risk_guard",
        "status": "available",
        "scale_yi": 12.5,
        "used_as_return_factor": False,
    }
    assert "size" not in evidence["values"]


def test_factor_input_carries_backward_compatible_typed_nav_library() -> None:
    row = factor_input_from_navs("000001", "测试", _navs())

    assert row.typed_feature_meta["schema_version"] == TYPE_FACTOR_SCHEMA_VERSION
    assert row.typed_feature_meta["source"] == "point_in_time_nav"
    assert row.typed_feature_values["medium_momentum"] is not None
