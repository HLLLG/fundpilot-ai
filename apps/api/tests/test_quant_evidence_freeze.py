from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.decision_contract import (
    _factor_reliability_selection,
    build_report_decision_bundle,
)
from app.services.portfolio_snapshot import _compact_factor_scores


def _factor_scores(code: str) -> dict:
    return {
        "available": True,
        "model_version": "factor_ic.v3",
        "ic_status": {
            "available": True,
            "state": "available",
            "snapshot_id": "snapshot-20260713",
            "schema_version": 3,
            "cohort_mode": "point_in_time",
            "run_date": "2026-07-13",
            "generated_at": "2026-07-13T05:00:00+00:00",
            "published_at": "2026-07-13T05:05:00+00:00",
        },
        "factor_reliability": {},
        "holdings": [
            {
                "fund_code": code,
                "peer_group": "equity",
                "composite_score": 82.5,
                "composite_grade": "A",
                "applicable": True,
                "factor_percentiles": {
                    "momentum": 88,
                    "risk_adjusted": 72,
                    "drawdown": 61,
                },
                "factor_reliability": {
                    "momentum": {"level": "高", "basis": "PIT OOS qualified"},
                    "risk_adjusted": {"level": "中", "basis": "PIT OOS qualified"},
                    "drawdown": {"level": "低", "basis": "weak"},
                },
                "base_composite_score": 78.2,
                "typed_factor_schema": "fund_type_factors.v1",
                "typed_used_keys": ["return_consistency", "medium_momentum"],
                "typed_factor_percentiles": {
                    "medium_momentum": 91,
                    "return_consistency": 76,
                },
                "typed_factor_reliability": {
                    "medium_momentum": {
                        "level": "高",
                        "qualified": True,
                        "orientation": "higher_is_better",
                        "economic_significance": {"qualified": True},
                    },
                    "return_consistency": {
                        "level": "高",
                        "qualified": True,
                        "orientation": "higher_is_better",
                        "economic_significance": {"qualified": True},
                    },
                },
                "typed_factor_applicable": True,
                "typed_feature_completeness": 1.0,
                "typed_factor_score": 83.5,
                "typed_factor_basis": "PIT经济门槛合格",
                "target_feature_as_of": "2026-07-10",
                "target_feature_observed_at": "2026-07-13T06:20:00+00:00",
                "target_feature_source": "fund_nav_history",
                "target_return_coverage": 0.996,
                "target_nav_age_trading_days": 1,
                "target_feature_freshness": "fresh",
                "target_feature_max_age_trading_days": 1,
            }
        ],
    }


def test_calibration_bucket_prioritizes_the_type_factor_actually_used() -> None:
    selected = _factor_reliability_selection(
        {"momentum": 99},
        {"momentum": {"level": "高"}},
        typed_percentiles={"medium_momentum": 60},
        typed_reliability={
            "medium_momentum": {
                "level": "低",
                "qualified": True,
                "orientation": "higher_is_better",
                "economic_significance": {"qualified": True},
            }
        },
        typed_used_keys=["medium_momentum"],
        typed_applicable=True,
    )

    assert selected == {
        "level": "低",
        "factor_key": "medium_momentum",
        "factor_family": "fund_type_specific",
        "percentile": 60.0,
        "direction": "positive",
    }


def test_calibration_bucket_falls_back_when_used_type_evidence_is_invalid() -> None:
    selected = _factor_reliability_selection(
        {"momentum": 80},
        {"momentum": {"level": "中"}},
        typed_percentiles={"medium_momentum": None},
        typed_reliability={"medium_momentum": {"level": "高"}},
        typed_used_keys=["medium_momentum"],
        typed_applicable=True,
    )

    assert selected["level"] == "中"
    assert selected["factor_family"] == "common"


def _report(kind: str, *, with_factor: bool = True) -> dict:
    code = "008586"
    facts = {
        "data_evidence": {"items": []},
    }
    if with_factor:
        facts[
            "factor_scores" if kind == "daily" else "candidate_factor_scores"
        ] = _factor_scores(code)
    base = {
        "id": f"{kind}-report",
        "created_at": "2026-07-13T06:30:00+00:00",
        "provider": "deepseek-chat",
    }
    if kind == "daily":
        return {
            **base,
            "analysis_facts": facts,
            "fund_recommendations": [
                {"fund_code": code, "fund_name": "测试基金", "action": "分批加仓"}
            ],
        }
    return {
        **base,
        "discovery_facts": facts,
        "recommendations": [
            {"fund_code": code, "fund_name": "测试基金", "action": "分批买入"}
        ],
    }


@pytest.mark.parametrize("kind", ["daily", "discovery"])
def test_decision_event_freezes_point_in_time_quant_evidence(kind: str) -> None:
    event = build_report_decision_bundle(
        _report(kind), decision_kind=kind  # type: ignore[arg-type]
    )["events"][0]

    evidence = event["quant_evidence"]
    assert evidence == {
        "schema_version": "quant_evidence.v2",
        "state": "available",
        "reason": None,
        "source": "factor_scores" if kind == "daily" else "candidate_factor_scores",
        "factor_snapshot_id": "snapshot-20260713",
        "model_version": "factor_ic.v3",
        "schema": 3,
        "cohort_mode": "point_in_time",
        "peer_group": "equity",
        "composite_score": 82.5,
        "composite_grade": "A",
        "base_composite_score": 78.2,
        "factor_percentiles": {
            "drawdown": 61,
            "momentum": 88,
            "risk_adjusted": 72,
        },
        "reliability": {
            "drawdown": {"basis": "weak", "level": "低"},
            "momentum": {"basis": "PIT OOS qualified", "level": "高"},
            "risk_adjusted": {"basis": "PIT OOS qualified", "level": "中"},
        },
        "reliability_bucket": "高",
        "reliability_factor_key": "medium_momentum",
        "reliability_factor_family": "fund_type_specific",
        "reliability_factor_percentile": 91.0,
        "reliability_factor_direction": "positive",
        "typed_factor_schema": "fund_type_factors.v1",
        "typed_used_keys": ["medium_momentum", "return_consistency"],
        "typed_factor_percentiles": {
            "medium_momentum": 91,
            "return_consistency": 76,
        },
        "typed_factor_reliability": {
            "medium_momentum": {
                "economic_significance": {"qualified": True},
                "level": "高",
                "orientation": "higher_is_better",
                "qualified": True,
            },
            "return_consistency": {
                "economic_significance": {"qualified": True},
                "level": "高",
                "orientation": "higher_is_better",
                "qualified": True,
            },
        },
        "typed_factor_applicable": True,
        "typed_feature_completeness": 1.0,
        "typed_factor_score": 83.5,
        "typed_factor_basis": "PIT经济门槛合格",
        "applicable": True,
        "data_as_of": "2026-07-10",
        "model_data_as_of": "2026-07-13",
        "model_generated_at": "2026-07-13T05:00:00+00:00",
        "model_published_at": "2026-07-13T05:05:00+00:00",
        "target_feature_as_of": "2026-07-10",
        "target_feature_observed_at": "2026-07-13T06:20:00+00:00",
        "target_feature_source": "fund_nav_history",
        "target_return_coverage": 0.996,
        "target_nav_age_trading_days": 1,
        "target_feature_freshness": "fresh",
        "target_feature_max_age_trading_days": 1,
        "frozen_at": "2026-07-13T06:30:00+00:00",
    }


def test_missing_quant_evidence_is_frozen_as_unavailable_and_never_backfilled() -> None:
    report = _report("daily", with_factor=False)
    first = build_report_decision_bundle(report, decision_kind="daily")["events"][0]
    frozen = deepcopy(first["quant_evidence"])

    report["analysis_facts"]["factor_scores"] = _factor_scores("008586")

    assert first["quant_evidence"] == frozen
    assert frozen["state"] == "unavailable"
    assert frozen["applicable"] is False
    assert frozen["factor_snapshot_id"] is None
    assert frozen["reason"] == "factor_evidence_not_attached_at_decision_time"
    second = build_report_decision_bundle(report, decision_kind="daily")["events"][0]
    assert second["quant_evidence"]["state"] == "available"


def test_stale_factor_snapshot_is_not_calibration_applicable() -> None:
    report = _report("daily")
    report["analysis_facts"]["factor_scores"]["ic_status"]["state"] = "stale"
    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]

    assert event["quant_evidence"]["state"] == "unavailable"
    assert event["quant_evidence"]["applicable"] is False
    assert (
        event["quant_evidence"]["reason"]
        == "factor_ic_snapshot_not_current_at_decision_time"
    )


def test_future_published_factor_snapshot_is_not_calibration_applicable() -> None:
    report = _report("daily")
    report["analysis_facts"]["factor_scores"]["ic_status"]["published_at"] = (
        "2026-07-13T07:00:00+00:00"
    )

    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]

    assert event["quant_evidence"]["state"] == "unavailable"
    assert (
        event["quant_evidence"]["reason"]
        == "factor_model_published_after_decision_time"
    )


def test_stale_target_feature_is_not_calibration_applicable() -> None:
    report = _report("daily")
    row = report["analysis_facts"]["factor_scores"]["holdings"][0]
    row["target_feature_freshness"] = "insufficient"
    row["target_nav_age_trading_days"] = 4

    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]

    assert event["quant_evidence"]["state"] == "unavailable"
    assert (
        event["quant_evidence"]["reason"]
        == "target_factor_feature_not_fresh_at_decision_time"
    )


def test_compact_facts_records_only_type_factors_that_entered_final_score() -> None:
    payload = {
        "available": True,
        "model_version": "factor_ic.v3",
        "funds": [
            {
                "fund_code": "008586",
                "factors": {},
                "typed_factor_candidates": ["medium_momentum", "return_consistency"],
                "typed_factor_applicable": True,
            },
            {
                "fund_code": "008587",
                "factors": {},
                "typed_factor_candidates": ["medium_momentum"],
                "typed_factor_applicable": False,
            },
        ],
    }

    compact = _compact_factor_scores(payload, {}, {"state": "available"})

    assert compact["holdings"][0]["typed_used_keys"] == [
        "medium_momentum",
        "return_consistency",
    ]
    assert compact["holdings"][1]["typed_used_keys"] == []


def test_compact_facts_removes_type_evidence_when_ic_is_stale() -> None:
    payload = {
        "available": True,
        "model_version": "factor_ic.v3",
        "funds": [
            {
                "fund_code": "008586",
                "factors": {},
                "typed_factor_schema": "fund_type_factors.v1",
                "typed_factor_candidates": ["medium_momentum"],
                "typed_factor_percentiles": {"medium_momentum": 90},
                "typed_factor_reliability": {
                    "medium_momentum": {"level": "中", "qualified": True}
                },
                "typed_factor_applicable": True,
                "typed_feature_completeness": 1.0,
                "typed_factor_score": 90,
            }
        ],
    }

    compact = _compact_factor_scores(
        payload,
        {"momentum": {"level": "不足", "basis": "IC 已过期"}},
        {"state": "stale", "available": True, "stale": True},
        research_model={"version": "factor_ic.v3"},
    )

    row = compact["holdings"][0]
    assert row["typed_used_keys"] == []
    assert row["typed_factor_percentiles"] == {}
    assert row["typed_factor_reliability"] == {}
    assert row["typed_factor_applicable"] is False
    assert compact["factor_reliability"]["momentum"]["level"] == "不足"
