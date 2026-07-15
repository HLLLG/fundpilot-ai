from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.services.fund_peer_ranking import (
    PEER_GROUP_SCHEMA_VERSION,
    PEER_METRIC_REGISTRY_VERSION,
    PEER_RANK_SCHEMA_VERSION,
    build_fund_peer_group,
    build_peer_rank,
    fund_family_key,
    resolve_benchmark_comparison,
)
from app.services.discovery_candidate_pool import attach_candidate_benchmark_research


DECISION_AT = "2026-07-14T08:00:00+00:00"
AVAILABLE_AT = "2026-07-14T07:30:00+00:00"


def _formal_benchmark(code: str = "000300") -> dict:
    return {
        "schema_version": "fund_benchmark_mapping.v1",
        "mapping_id": f"fbm-{code}",
        "tier": "fund_contract_exact",
        "benchmark_kind": "official_contract",
        "completeness": "complete",
        "formal_excess_eligible": True,
        "contract_verification_kind": "verified_fund_contract",
        "benchmark_code": code,
        "benchmark_name": "沪深300指数收益率×95%+银行活期存款利率×5%",
        "available_at": AVAILABLE_AT,
    }


def _tracking_benchmark(code: str = "000300") -> dict:
    return {
        "schema_version": "fund_benchmark_mapping.v1",
        "mapping_id": f"fbm-track-{code}",
        "tier": "tracked_index_exact",
        "benchmark_kind": "tracking_index",
        "completeness": "complete",
        "formal_excess_eligible": False,
        "contract_verification_kind": "xq_akshare_aggregator",
        "benchmark_code": code,
        "benchmark_name": "沪深300指数",
        "available_at": AVAILABLE_AT,
    }


def _row(
    code: str,
    name: str,
    *,
    value: float,
    fund_type: str = "gp",
    available_at: str = AVAILABLE_AT,
) -> dict:
    row = {
        "fund_code": code,
        "fund_name": name,
        "fund_type": fund_type,
        "available_at": available_at,
        "return_3m_percent": value,
        "return_6m_percent": value,
        "return_1y_percent": value,
        "max_drawdown_1y_percent": -value,
        "fund_scale_yi": value,
        "source": "frozen_test_snapshot",
    }
    if fund_type == "gp":
        row.update(
            {
                "benchmark_spec": _formal_benchmark(),
                "benchmark_excess_return_1y_percent": value,
                "downside_capture_1y_percent": max(0.0, 100.0 - value),
                "style_drift_score": max(0.0, 100.0 - value),
            }
        )
    return row


def _typed_row(profile: str, code: str, *, value: float) -> dict:
    if profile == "equity":
        return _row(code, f"主动成长股票{code}A", value=value)
    if profile == "mixed":
        return _row(code, f"偏股混合{code}A", value=value, fund_type="hh")
    if profile == "bond":
        return _row(code, f"稳健中短债{code}A", value=value, fund_type="zq")
    if profile in {"passive_index", "enhanced_index"}:
        marker = "沪深300指数增强" if profile == "enhanced_index" else "沪深300指数"
        row = _row(code, f"{marker}{code}A", value=value, fund_type="zs")
        row["benchmark_spec"] = _tracking_benchmark()
        return row
    if profile == "qdii":
        return _row(
            code,
            f"全球精选股票(QDII){code}A",
            value=value,
            fund_type="qdii",
        )
    if profile == "fof":
        return _row(
            code,
            f"养老目标日期2040FOF{code}A",
            value=value,
            fund_type="fof",
        )
    if profile == "money":
        return _row(code, f"现金货币{code}A", value=value, fund_type="货币型")
    raise AssertionError(f"unsupported profile: {profile}")


def test_active_index_and_enhanced_index_are_separate_groups() -> None:
    active = build_fund_peer_group(
        _row("000001", "主动成长股票A", value=10),
        decision_at=DECISION_AT,
    )
    passive_row = _row("000002", "沪深300指数A", value=10, fund_type="zs")
    passive_row["benchmark_spec"] = _tracking_benchmark()
    passive = build_fund_peer_group(passive_row, decision_at=DECISION_AT)
    enhanced_row = _row("000003", "沪深300指数增强A", value=10, fund_type="zs")
    enhanced_row["benchmark_spec"] = _tracking_benchmark()
    enhanced = build_fund_peer_group(enhanced_row, decision_at=DECISION_AT)

    assert active["schema_version"] == PEER_GROUP_SCHEMA_VERSION
    assert active["management_style"] == "active"
    assert passive["management_style"] == "passive_index"
    assert enhanced["management_style"] == "enhanced_index"
    assert len({active["group_key"], passive["group_key"], enhanced["group_key"]}) == 3
    assert passive["qualified"] is True


def test_index_without_point_in_time_tracking_identity_fails_closed() -> None:
    row = _row("000002", "未知标的指数A", value=10, fund_type="zs")

    group = build_fund_peer_group(row, decision_at=DECISION_AT)

    assert group["qualified"] is False
    assert group["reason"] == "index_tracking_reference_unavailable"
    assert "reference-unspecified" in group["group_key"]


def test_bond_subtypes_do_not_share_a_peer_group() -> None:
    short = build_fund_peer_group(
        _row("000010", "稳健中短债A", value=2, fund_type="zq"),
        decision_at=DECISION_AT,
    )
    secondary = build_fund_peer_group(
        _row("000011", "稳健二级债基A", value=2, fund_type="zq"),
        decision_at=DECISION_AT,
    )
    unknown = build_fund_peer_group(
        _row("000012", "稳健债券A", value=2, fund_type="zq"),
        decision_at=DECISION_AT,
    )

    assert short["bond_subtype"] == "short_duration"
    assert secondary["bond_subtype"] == "secondary_bond"
    assert short["group_key"] != secondary["group_key"]
    assert unknown["qualified"] is False
    assert unknown["reason"] == "bond_subtype_unavailable"


def test_bond_index_and_qdii_bond_are_not_misclassified_as_equity() -> None:
    domestic = _row(
        "000013",
        "中证信用债指数A",
        value=2,
        fund_type="债券指数型",
    )
    domestic["benchmark_spec"] = _tracking_benchmark("CBA001")
    overseas = _row(
        "000014",
        "全球高收益债券(QDII)A",
        value=2,
        fund_type="QDII-债券型",
    )
    overseas["risk_exposure"] = {
        "asset_class": "bond",
        "region": "global",
        "available_at": AVAILABLE_AT,
    }
    domestic_group = build_fund_peer_group(domestic, decision_at=DECISION_AT)
    overseas_group = build_fund_peer_group(overseas, decision_at=DECISION_AT)

    assert domestic_group["asset_class"] == "bond"
    assert domestic_group["bond_subtype"] == "bond_index"
    assert domestic_group["qualified"] is True
    assert overseas_group["asset_class"] == "bond"
    assert overseas_group["region"] == "overseas"
    assert overseas_group["bond_subtype"] == "high_yield"
    assert overseas_group["qdii_subtype"] == "bond"
    assert overseas_group["qdii_region"] == "global"
    assert overseas_group["qualified"] is True


def test_qdii_is_split_by_underlying_strategy_region_and_reference() -> None:
    index_row = _row(
        "000020",
        "纳斯达克100指数(QDII)A",
        value=8,
        fund_type="qdii",
    )
    index_row["benchmark_spec"] = _tracking_benchmark("NDX")
    active_row = _row(
        "000021",
        "全球精选股票(QDII)A",
        value=8,
        fund_type="qdii",
    )
    index_group = build_fund_peer_group(index_row, decision_at=DECISION_AT)
    active_group = build_fund_peer_group(active_row, decision_at=DECISION_AT)

    assert index_group["qdii_subtype"] == "equity_index"
    assert index_group["qdii_region"] == "united_states"
    assert index_group["qualified"] is True
    assert active_group["qdii_subtype"] == "equity_active"
    assert active_group["qdii_region"] == "global"
    assert active_group["qualified"] is True
    assert active_group["group_key"] != index_group["group_key"]


@pytest.mark.parametrize(
    ("profile", "expected_metric"),
    [
        ("equity", "downside_capture_1y_percent"),
        ("mixed", "style_drift_score"),
        ("bond", "modified_duration_years"),
        ("passive_index", "tracking_error_1y_percent"),
        ("enhanced_index", "tracking_difference_1y_percent"),
        ("qdii", "fx_exposure_percent"),
        ("fof", "underlying_fund_overlap_percent"),
        ("money", "seven_day_annualized_yield_percent"),
    ],
)
def test_each_fund_profile_declares_its_own_metric_registry(
    profile: str,
    expected_metric: str,
) -> None:
    group = build_fund_peer_group(
        _typed_row(profile, "009001", value=5),
        decision_at=DECISION_AT,
    )

    assert group["metric_registry_version"] == PEER_METRIC_REGISTRY_VERSION
    assert group["metric_profile"] == profile
    assert expected_metric in group["applicable_metrics"]


@pytest.mark.parametrize(
    ("profile", "applicable_metric", "not_applicable_metric"),
    [
        ("bond", "modified_duration_years", "tracking_error_1y_percent"),
        ("passive_index", "tracking_error_1y_percent", "modified_duration_years"),
        ("qdii", "fx_exposure_percent", "underlying_fund_overlap_percent"),
        ("fof", "underlying_fund_overlap_percent", "fx_exposure_percent"),
    ],
)
def test_type_specific_missing_and_not_applicable_metrics_fail_closed(
    profile: str,
    applicable_metric: str,
    not_applicable_metric: str,
) -> None:
    result = build_peer_rank(
        _typed_row(profile, "009010", value=5),
        [_typed_row(profile, "009011", value=3)],
        decision_at=DECISION_AT,
        minimum_peer_count=1,
        minimum_metric_coverage=1.0,
    )

    missing = result["metrics"][applicable_metric]
    assert missing["applicable"] is True
    assert missing["applicability"] == "applicable"
    assert missing["available"] is False
    assert missing["availability"] == "unavailable"
    assert missing["percentile"] is None
    assert missing["reason"] == "target_metric_value_missing"

    irrelevant = result["metrics"][not_applicable_metric]
    assert irrelevant["applicable"] is False
    assert irrelevant["applicability"] == "not_applicable"
    assert irrelevant["available"] is False
    assert irrelevant["availability"] == "not_applicable"
    assert irrelevant["sample_count"] == 0
    assert irrelevant["coverage_rate"] is None
    assert irrelevant["percentile"] is None
    assert irrelevant["reason"] == f"metric_not_applicable_to_{profile}"
    assert result["qualified"] is False
    assert result["execution_tilt_eligible"] is False


def test_pit_risk_exposure_refines_mixed_group_but_future_exposure_is_ignored() -> None:
    row = _row("000030", "全能混合A", value=8, fund_type="hh")
    row["risk_exposure"] = {
        "asset_class": "mixed",
        "equity_percent": 80,
        "available_at": AVAILABLE_AT,
    }
    current = build_fund_peer_group(row, decision_at=DECISION_AT)
    future_row = deepcopy(row)
    future_row["risk_exposure"]["available_at"] = "2026-07-14T08:01:00+00:00"
    future = build_fund_peer_group(future_row, decision_at=DECISION_AT)

    assert current["mixed_subtype"] == "equity_biased"
    assert current["risk_bucket"] == "equity_80_plus"
    assert current["qualified"] is True
    assert future["mixed_subtype"] == "unspecified"
    assert future["qualified"] is False
    assert "risk_exposure_available_after_decision_at" in future["warnings"]


def test_share_classes_map_to_one_family_and_explicit_key_wins() -> None:
    a = _row("000040", "同一底层组合 A", value=8)
    c = _row("000041", "同一底层组合C", value=8)
    explicit = deepcopy(c)
    explicit["share_family"] = {"family_key": "portfolio-42"}

    assert fund_family_key(a) == fund_family_key(c)
    assert fund_family_key(explicit) == "explicit:portfolio-42"


def test_verified_contract_is_the_only_formal_excess_source() -> None:
    formal = resolve_benchmark_comparison(
        _formal_benchmark(), decision_at=DECISION_AT
    )
    legacy = _formal_benchmark()
    legacy.pop("contract_verification_kind")
    legacy_result = resolve_benchmark_comparison(legacy, decision_at=DECISION_AT)
    tracking = resolve_benchmark_comparison(
        _tracking_benchmark(), decision_at=DECISION_AT
    )

    assert formal["comparison_role"] == "formal_excess"
    assert formal["formal_excess_eligible"] is True
    assert legacy_result["comparison_role"] == "tracking_reference"
    assert legacy_result["formal_excess_eligible"] is False
    assert legacy_result["reason"] == "contract_source_not_verified"
    assert tracking["comparison_role"] == "tracking_reference"


def test_benchmark_missing_or_future_availability_is_unavailable() -> None:
    missing = _formal_benchmark()
    missing.pop("available_at")
    future = _formal_benchmark()
    future["available_at"] = "2026-07-14T08:00:01+00:00"

    missing_result = resolve_benchmark_comparison(missing, decision_at=DECISION_AT)
    future_result = resolve_benchmark_comparison(future, decision_at=DECISION_AT)

    assert missing_result["comparison_role"] == "unavailable"
    assert missing_result["reason"] == "benchmark_available_at_missing_or_invalid"
    assert future_result["comparison_role"] == "unavailable"
    assert future_result["reason"] == "benchmark_available_after_decision_at"


def test_benchmark_attachment_rebuilds_rank_in_the_final_reference_group() -> None:
    target = _typed_row("passive_index", "009100", value=10)
    peer = _typed_row("passive_index", "009101", value=4)
    target.pop("benchmark_spec")
    peer.pop("benchmark_spec")
    old_rank = build_peer_rank(
        target,
        [peer],
        decision_at=DECISION_AT,
        minimum_peer_count=1,
        minimum_metric_coverage=1.0,
    )
    assert "reference-unspecified" in old_rank["peer_group"]["group_key"]
    old_sample_hash = old_rank["metrics"]["return_3m_percent"]["peer_sample_hash"]
    pool = [
        {**target, "peer_group": old_rank["peer_group"], "peer_rank": old_rank},
        {**peer, "peer_group": old_rank["peer_group"], "peer_rank": old_rank},
    ]

    attached = attach_candidate_benchmark_research(
        pool,
        {
            "009100": _formal_benchmark("000300"),
            "009101": _formal_benchmark("000300"),
        },
        decision_at=datetime.fromisoformat(DECISION_AT),
    )

    rebuilt = attached[0]["peer_rank"]
    top_group = attached[0]["peer_group"]
    assert attached[0]["benchmark_comparison"]["comparison_role"] == "formal_excess"
    assert "reference-000300" in top_group["group_key"]
    assert "reference-unspecified" not in top_group["group_key"]
    assert rebuilt["schema_version"] == "peer_rank.v2"
    assert rebuilt["peer_group"]["group_key"] == top_group["group_key"]
    assert rebuilt["peer_group"] == top_group
    assert rebuilt["metrics"]["return_3m_percent"]["sample_count"] == 1
    assert (
        rebuilt["metrics"]["return_3m_percent"]["peer_sample_hash"]
        != old_sample_hash
    )
    assert all("_peer_rank_universe" not in row for row in attached)
    assert rebuilt["execution_tilt_eligible"] is False


def test_active_excess_metric_requires_a_verified_formal_benchmark() -> None:
    target = _typed_row("equity", "009110", value=10)
    peer = _typed_row("equity", "009111", value=4)
    target.pop("benchmark_spec")

    result = build_peer_rank(
        target,
        [peer],
        decision_at=DECISION_AT,
        minimum_peer_count=1,
        minimum_metric_coverage=1.0,
    )

    excess = result["metrics"]["benchmark_excess_return_1y_percent"]
    assert excess["applicable"] is True
    assert excess["available"] is False
    assert excess["percentile"] is None
    assert excess["reason"] == "target_formal_benchmark_required"
    assert result["qualified"] is False
    assert result["execution_tilt_eligible"] is False


def test_complete_point_in_time_peer_rank_is_execution_qualified() -> None:
    target = _row("000100", "目标主动股票A", value=10)
    universe = [
        _row("000101", "同行一A", value=2),
        _row("000102", "同行二A", value=4),
        _row("000103", "同行三A", value=6),
    ]

    result = build_peer_rank(
        target,
        universe,
        decision_at=DECISION_AT,
        minimum_peer_count=3,
        minimum_metric_coverage=1.0,
    )

    assert result["schema_version"] == PEER_RANK_SCHEMA_VERSION
    assert result["metric_registry_version"] == PEER_METRIC_REGISTRY_VERSION
    assert result["metric_profile"] == "equity"
    assert result["status"] == "qualified"
    assert result["qualified"] is True
    assert result["research_shadow_rerank_eligible"] is True
    assert result["execution_tilt_eligible"] is False
    assert result["execution_tilt_gate"] == {
        "status": "blocked",
        "eligible": False,
        "required_method": "peer_rank_pit_statistical_and_economic",
        "reason": "peer_rank_predictive_qualification_unavailable",
    }
    assert result["universe"]["independent_peer_family_count"] == 3
    assert result["qualified_metric_count"] == 8
    assert result["target_metric_coverage_rate"] == 1.0
    for field, metric in result["metrics"].items():
        assert "applicability" in metric
        assert "availability" in metric
        assert "orientation" in metric
        assert "sample_count" in metric
        assert "coverage_rate" in metric
        assert "percentile" in metric
        if metric["applicable"] is False:
            assert metric["availability"] == "not_applicable"
            assert metric["sample_count"] == 0
            assert metric["coverage_rate"] is None
            assert metric["percentile"] is None
            continue
        assert metric["sample_count"] == 3
        assert metric["coverage_rate"] == 1.0
        assert metric["percentile"] == (
            0.0 if field == "max_drawdown_1y_percent" else 100.0
        )
        assert metric["qualified"] is True
    assert result["metrics"]["fund_scale_yi"]["role"] == "capacity_context_only"
    assert "not_expected_return" in result["metrics"]["fund_scale_yi"]["orientation"]


def test_a_c_families_contribute_once_and_matching_target_share_is_preferred() -> None:
    target = _row("000200", "目标组合C", value=50)
    universe = [
        _row("000201", "目标组合A", value=40),
        _row("000200", "目标组合C", value=50),
        _row("000210", "同行一A", value=10),
        _row("000211", "同行一C", value=90),
        _row("000220", "同行二A", value=20),
    ]

    result = build_peer_rank(
        target,
        universe,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["universe"] == {
        "raw_member_count": 5,
        "point_in_time_member_count": 5,
        "membership_unavailable_or_future_count": 0,
        "group_share_class_count": 5,
        "independent_peer_family_count": 2,
        "target_family_share_class_count_excluded": 2,
        "duplicate_share_class_count": 2,
    }
    assert result["metrics"]["return_3m_percent"]["sample_count"] == 2
    # Peer family one contributes C=90 (same class as target), not both A=10/C=90.
    assert result["metrics"]["return_3m_percent"]["percentile"] == 50.0
    assert result["qualified"] is True
    assert result["execution_tilt_eligible"] is False


def test_legacy_applicable_or_unrelated_factor_flag_cannot_enable_peer_tilt() -> None:
    target = _row("000250", "目标主动股票A", value=10)
    target.update(
        {
            "applicable": True,
            "execution_qualified": True,
            "execution_qualified_factor_keys": ["medium_momentum"],
        }
    )
    peers = [
        _row("000251", "同行一A", value=2),
        _row("000252", "同行二A", value=4),
    ]

    result = build_peer_rank(
        target,
        peers,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["qualified"] is True
    assert result["execution_tilt_eligible"] is False
    assert result["execution_tilt_gate"]["reason"] == (
        "peer_rank_predictive_qualification_unavailable"
    )


def test_missing_metric_is_not_imputed_and_coverage_fails_closed() -> None:
    target = _row("000300", "目标主动股票A", value=10)
    peer_one = _row("000301", "同行一A", value=2)
    peer_two = _row("000302", "同行二A", value=4)
    peer_two.pop("fund_scale_yi")

    result = build_peer_rank(
        target,
        [peer_one, peer_two],
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    scale = result["metrics"]["fund_scale_yi"]
    assert scale["sample_count"] == 1
    assert scale["coverage_rate"] == 0.5
    assert scale["qualified"] is False
    assert scale["reasons"] == [
        "metric_sample_count_below_minimum",
        "metric_coverage_below_minimum",
    ]
    assert result["status"] == "descriptive_only"
    assert result["execution_tilt_eligible"] is False


def test_future_membership_and_future_metric_are_excluded_at_decision_time() -> None:
    target = _row("000400", "目标主动股票A", value=10)
    current = _row("000401", "同行一A", value=2)
    future_member = _row(
        "000402",
        "同行二A",
        value=4,
        available_at="2026-07-14T08:00:01+00:00",
    )
    future_metric = _row("000403", "同行三A", value=6)
    future_metric["metric_evidence"] = {
        "return_3m_percent": {
            "value": 6,
            "available_at": "2026-07-14T08:00:01+00:00",
        }
    }

    result = build_peer_rank(
        target,
        [current, future_member, future_metric],
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["universe"]["membership_unavailable_or_future_count"] == 1
    assert result["universe"]["independent_peer_family_count"] == 2
    metric = result["metrics"]["return_3m_percent"]
    assert metric["sample_count"] == 1
    assert metric["coverage_rate"] == 0.5
    assert metric["qualified"] is False
    assert result["qualified"] is False


def test_metric_as_of_date_after_decision_is_not_used() -> None:
    target = _row("000450", "目标主动股票A", value=10)
    current = _row("000451", "同行一A", value=2)
    future_nav = _row("000452", "同行二A", value=4)
    future_nav["nav_date"] = "2026-07-15"

    result = build_peer_rank(
        target,
        [current, future_nav],
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["metrics"]["return_3m_percent"]["sample_count"] == 1
    assert result["metrics"]["max_drawdown_1y_percent"]["sample_count"] == 1
    # Scale does not inherit NAV's as-of date and remains independently usable.
    assert result["metrics"]["fund_scale_yi"]["sample_count"] == 2
    assert result["qualified"] is False


def test_missing_target_metric_keeps_other_dimensions_descriptive_only() -> None:
    target = _row("000500", "目标主动股票A", value=10)
    target.pop("return_1y_percent")
    peers = [
        _row("000501", "同行一A", value=2),
        _row("000502", "同行二A", value=4),
    ]

    result = build_peer_rank(
        target,
        peers,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    annual = result["metrics"]["return_1y_percent"]
    assert annual["value"] is None
    assert annual["percentile"] is None
    assert annual["reason"] == "target_metric_value_missing"
    assert result["descriptive_percentile_count"] == 7
    assert result["status"] == "descriptive_only"
    assert result["qualified"] is False


def test_unknown_membership_timestamp_never_becomes_execution_qualified() -> None:
    target = _row("000600", "目标主动股票A", value=10)
    target.pop("available_at")
    target["metric_evidence"] = {
        field: {"value": value, "available_at": AVAILABLE_AT}
        for field, value in {
            "return_3m_percent": 10,
            "return_6m_percent": 10,
            "return_1y_percent": 10,
            "max_drawdown_1y_percent": -10,
            "fund_scale_yi": 10,
        }.items()
    }
    peers = [
        _row("000601", "同行一A", value=2),
        _row("000602", "同行二A", value=4),
    ]

    result = build_peer_rank(
        target,
        peers,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["status"] == "descriptive_only"
    assert result["qualified"] is False
    assert "target_membership_available_at_missing_or_invalid" in result["reasons"]


def test_positive_drawdown_is_rejected_instead_of_guessing_source_semantics() -> None:
    target = _row("000700", "目标主动股票A", value=10)
    target["max_drawdown_1y_percent"] = 10
    peers = [
        _row("000701", "同行一A", value=2),
        _row("000702", "同行二A", value=4),
    ]

    result = build_peer_rank(
        target,
        peers,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    drawdown = result["metrics"]["max_drawdown_1y_percent"]
    assert drawdown["value"] is None
    assert drawdown["reason"] == (
        "target_drawdown_must_be_signed_non_positive_percent"
    )
    assert result["qualified"] is False


def test_under_specified_group_can_show_percentiles_but_never_tilt_execution() -> None:
    target = _row("000800", "普通债券A", value=3, fund_type="zq")
    peers = [
        _row("000801", "同行债券A", value=1, fund_type="zq"),
        _row("000802", "另一债券A", value=2, fund_type="zq"),
    ]

    result = build_peer_rank(
        target,
        peers,
        decision_at=DECISION_AT,
        minimum_peer_count=2,
        minimum_metric_coverage=1.0,
    )

    assert result["peer_group"]["qualified"] is False
    assert result["metrics"]["return_3m_percent"]["percentile"] == 100.0
    assert result["status"] == "descriptive_only"
    assert result["execution_tilt_eligible"] is False


def test_rank_is_deterministic_and_rejects_naive_decision_clock() -> None:
    target = _row("000900", "目标主动股票A", value=10)
    peers = [
        _row("000901", "同行一A", value=2),
        _row("000902", "同行二A", value=4),
    ]
    kwargs = {
        "decision_at": DECISION_AT,
        "minimum_peer_count": 2,
        "minimum_metric_coverage": 1.0,
    }

    first = build_peer_rank(target, peers, **kwargs)
    second = build_peer_rank(target, reversed(peers), **kwargs)

    assert first == second
    with pytest.raises(ValueError, match="timezone"):
        build_peer_rank(
            target,
            peers,
            decision_at=datetime(2026, 7, 14, 8, 0),
            minimum_peer_count=2,
        )


def test_metadata_snapshot_format_is_supported_without_scalar_backfill() -> None:
    target = _row("001000", "目标主动股票A", value=10)
    peer = _row("001001", "同行一A", value=2)
    metadata = {
        key: peer.pop(key)
        for key in (
            "return_3m_percent",
            "return_6m_percent",
            "return_1y_percent",
            "max_drawdown_1y_percent",
            "fund_scale_yi",
        )
    }
    metadata["snapshot_available_at"] = AVAILABLE_AT
    peer["metadata"] = metadata

    result = build_peer_rank(
        target,
        [peer],
        decision_at=DECISION_AT,
        minimum_peer_count=1,
        minimum_metric_coverage=1.0,
    )

    assert result["qualified"] is True
    assert all(
        metric["sample_count"] == 1
        for metric in result["metrics"].values()
        if metric["applicable"] is True
    )


def test_invalid_threshold_arguments_are_rejected() -> None:
    target = _row("001100", "目标主动股票A", value=10)

    with pytest.raises(ValueError, match="minimum_peer_count"):
        build_peer_rank(target, [], decision_at=DECISION_AT, minimum_peer_count=0)
    with pytest.raises(ValueError, match="minimum_metric_coverage"):
        build_peer_rank(
            target,
            [],
            decision_at=DECISION_AT,
            minimum_metric_coverage=1.1,
        )


def test_decision_at_accepts_aware_datetime() -> None:
    group = build_fund_peer_group(
        _row("001200", "主动股票A", value=10),
        decision_at=datetime(2026, 7, 14, 8, tzinfo=timezone.utc),
    )

    assert group["decision_at"] == DECISION_AT
