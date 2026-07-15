from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

import pytest

from app.services.candidate_selection_audit import (
    CandidateSelectionAuditError,
    build_candidate_selection_audit_v2,
    evaluate_candidate_selection_audit,
    normalize_candidate_selection_audit,
    require_valid_candidate_selection_audit,
    validate_candidate_selection_audit,
)


DECISION_AT = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context(stage: str, *, available_at: str = "2026-07-14T07:30:00+00:00") -> dict:
    ref_id = f"source:{stage}"
    context = {
        "version": f"{stage}.v3",
        "source_refs": [
            {
                "ref_id": ref_id,
                "source": f"frozen_{stage}_catalogue",
                "version": "2026-07-14",
                "snapshot_hash": _digest(f"source-{stage}"),
            }
        ],
        "pit_refs": [
            {
                "fact_id": f"fact:{stage}",
                "source_ref_id": ref_id,
                "available_at": available_at,
                "snapshot_hash": _digest(f"fact-{stage}"),
            }
        ],
    }
    if stage == "recall":
        context["scope"] = {
            "definition": "pytest complete unique scored recall",
            "complete": True,
            "candidate_count_total": 3,
            "candidate_count_retained": 3,
            "catalogue_rows_embedded": False,
        }
    return context


def _candidate(
    code: str,
    stage: str,
    rank: int,
    *,
    score: float,
    gates: dict | None = None,
) -> dict:
    return {
        "fund_code": code,
        "fund_name": f"基金{code}",
        "sector_label": "科技",
        f"{stage}_rank": rank,
        f"{stage}_score": score,
        "score_components": {
            "quality": round(score * 0.7, 4),
            "fit": round(score * 0.3, 4),
        },
        "gates": gates or {},
        "reason_codes": [f"{stage}_reason_{code}"],
    }


def _valid_v2() -> dict:
    recall = [
        _candidate("100001", "recall", 1, score=91.0),
        _candidate("100002", "recall", 2, score=90.0),
        _candidate("100003", "recall", 3, score=89.0),
    ]
    # Later stages deliberately reorder candidates.  Stage hashes must follow
    # their own ranks, not the recall row order.
    gate = [
        _candidate(
            "100002",
            "gate",
            1,
            score=94.0,
            gates={"quality": {"status": "pass"}},
        ),
        _candidate(
            "100001",
            "gate",
            2,
            score=92.0,
            gates={"quality": {"status": "pass"}},
        ),
        _candidate(
            "100003",
            "gate",
            3,
            score=72.0,
            gates={"quality": {"status": "watch"}},
        ),
    ]
    prescreen = [
        _candidate("100002", "prescreen", 1, score=95.0),
        _candidate("100001", "prescreen", 2, score=93.0),
        _candidate("100003", "prescreen", 3, score=75.0),
    ]
    final = [
        _candidate("100002", "final", 1, score=95.0),
        _candidate("100001", "final", 2, score=93.0),
    ]
    return build_candidate_selection_audit_v2(
        decision_at=DECISION_AT,
        recall_candidates=recall,
        gate_candidates=gate,
        prescreen_candidates=prescreen,
        final_candidates=final,
        versions={"selection_policy": "candidate_policy.v7"},
        stage_contexts={stage: _context(stage) for stage in ("recall", "gate", "prescreen", "final")},
    )


def _legacy_v1() -> dict:
    rows = [
        {
            "fund_code": "100002",
            "fund_name": "基金100002",
            "sector_label": "科技",
            "share_family_key": "family-b",
            "quality_gate_status": "eligible",
            "tradeability_gate_status": "eligible",
            "fund_quality_score": 95.0,
            "sector_fit_score": 80.0,
            "peer_group_key": "active_equity",
            "peer_rank_status": "available",
            "descriptive_performance_percentile": 0.9,
            "post_family_rank": 1,
            "selected": True,
            "final_rank": 1,
            "reason_codes": [],
        },
        {
            "fund_code": "100001",
            "fund_name": "基金100001",
            "sector_label": "科技",
            "share_family_key": "family-a",
            "quality_gate_status": "eligible",
            "tradeability_gate_status": "eligible",
            "fund_quality_score": 90.0,
            "sector_fit_score": 75.0,
            "peer_group_key": "active_equity",
            "peer_rank_status": "available",
            "descriptive_performance_percentile": 0.8,
            "post_family_rank": 2,
            "selected": False,
            "final_rank": None,
            "reason_codes": ["outside_final_sector_quota_or_pool_cap"],
        },
    ]
    material = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": "discovery_candidate_selection_audit.v1",
        "prescreen_count": 2,
        "post_share_family_count": 2,
        "acceptable_count": 2,
        "selected_count": 1,
        "rows": rows,
        "snapshot_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
    }


def _labels(*, include_third: bool = True) -> dict[str, dict]:
    labels = {
        "100001": {
            "observation_id": "obs:100001:T+20",
            "mature": True,
            "skipped": False,
            "eligible": True,
            "binary_relevance": True,
            "relevance": 2.0,
            "utility": 1.0,
        },
        "100002": {
            "observation_id": "obs:100002:T+20",
            "mature": True,
            "skipped": False,
            "eligible": True,
            "binary_relevance": False,
            "relevance": 0.0,
            "utility": 0.0,
        },
    }
    if include_third:
        labels["100003"] = {
            "observation_id": "obs:100003:T+20",
            "mature": True,
            "skipped": False,
            "eligible": True,
            "binary_relevance": True,
            "relevance": 3.0,
            "utility": 3.0,
        }
    return labels


def test_v2_builds_full_funnel_rank_score_gate_pit_and_hash_contract() -> None:
    audit = _valid_v2()

    assert audit["schema_version"] == "discovery_candidate_selection_audit.v2"
    assert audit["stage_counts"] == {"recall": 3, "gate": 3, "prescreen": 3, "final": 2}
    assert audit["validation"]["status"] == "valid"
    assert audit["validation"]["decision_eligible"] is True
    assert len(audit["snapshot_hash"]) == 64
    assert all(len(audit["stages"][stage]["rows_hash"]) == 64 for stage in audit["stage_order"])

    rows = {row["fund_code"]: row for row in audit["rows"]}
    gate = rows["100002"]["stage_records"]["gate"]
    assert gate["rank"] == 1
    assert gate["score_components"] == {"quality": 65.8, "fit": 28.2}
    assert gate["gates"]["quality"]["status"] == "pass"
    assert gate["source_refs"][0]["ref_id"] == "source:gate"
    assert gate["pit_refs"][0]["available_at"] < audit["decision_at"]
    assert len(gate["candidate_snapshot_hash"]) == 64
    assert rows["100003"]["stage_records"]["final"]["present"] is False
    assert rows["100003"]["selected"] is False
    assert require_valid_candidate_selection_audit(audit)["status"] == "valid"

    rebuilt = _valid_v2()
    assert rebuilt["rows"] == audit["rows"]
    assert rebuilt["snapshot_hash"] == audit["snapshot_hash"]


def test_v2_detects_tampering_future_pit_stage_skips_and_nonfinite_scores() -> None:
    tampered = deepcopy(_valid_v2())
    tampered["rows"][0]["stage_records"]["recall"]["score"] = 999.0
    validation = validate_candidate_selection_audit(tampered)
    codes = {error["code"] for error in validation["errors"]}
    assert "candidate_snapshot_hash_mismatch" in codes
    assert "stage_rows_hash_mismatch" in codes
    assert "snapshot_hash_mismatch" in codes
    with pytest.raises(CandidateSelectionAuditError):
        require_valid_candidate_selection_audit(tampered)

    hidden = deepcopy(_valid_v2())
    hidden["rows"][2]["stage_records"]["final"]["version"] = "hidden.v1"
    hidden["rows"][0]["stage_records"]["gate"]["version"] = "wrong-gate.v1"
    hidden_codes = {
        error["code"] for error in validate_candidate_selection_audit(hidden)["errors"]
    }
    assert "absent_stage_payload_not_empty" in hidden_codes
    assert "stage_version_mismatch" in hidden_codes

    future_contexts = {stage: _context(stage) for stage in ("recall", "gate", "prescreen", "final")}
    future_contexts["gate"] = _context("gate", available_at="2026-07-14T08:00:01+00:00")
    invalid = build_candidate_selection_audit_v2(
        decision_at=DECISION_AT,
        recall_candidates=[_candidate("100001", "recall", 1, score=90.0)],
        gate_candidates=[
            _candidate(
                "100001",
                "gate",
                1,
                score=float("nan"),
                gates={"quality": {"status": "pass"}},
            )
        ],
        prescreen_candidates=[_candidate("100099", "prescreen", 1, score=80.0)],
        final_candidates=[],
        versions={"selection_policy": "candidate_policy.v7"},
        stage_contexts=future_contexts,
    )
    codes = {error["code"] for error in invalid["validation"]["errors"]}
    assert "pit_after_decision" in codes
    assert "stage_subset_violation" in codes
    assert "score_invalid" in codes
    assert "non_finite_value" in codes
    assert invalid["snapshot_hash"] is None

    evaluated = evaluate_candidate_selection_audit(invalid, _labels(), k=2)
    assert evaluated["status"] == "unavailable"
    assert evaluated["reason"] == "audit_validation_failed"
    assert evaluated["precision_at_k"]["value"] is None


def test_v1_is_preserved_as_legacy_partial_without_fabricated_lineage() -> None:
    audit = _legacy_v1()

    validation = validate_candidate_selection_audit(audit)
    assert validation["status"] == "valid"
    assert validation["compatibility_status"] == "legacy_partial"
    assert validation["decision_eligible"] is False
    normalized = normalize_candidate_selection_audit(audit)
    assert normalized["compatibility_status"] == "legacy_partial"
    assert normalized["decision_eligible"] is False
    row = normalized["rows"][0]
    assert row["stage_records"]["recall"]["present"] is False
    assert row["stage_records"]["gate"]["source_refs"] == []
    assert row["stage_records"]["prescreen"]["score"] is None
    assert row["stage_records"]["prescreen"]["score_components"]["fund_quality_score"] == 95.0
    assert row["stage_records"]["final"]["rank"] == 1
    with pytest.raises(CandidateSelectionAuditError):
        require_valid_candidate_selection_audit(audit)

    tampered = deepcopy(audit)
    tampered["rows"][0]["final_rank"] = 2
    assert validate_candidate_selection_audit(tampered)["status"] == "invalid"


def test_complete_outcomes_compute_precision_ndcg_coverage_and_regret() -> None:
    result = evaluate_candidate_selection_audit(_valid_v2(), _labels(), k=2)

    assert result["status"] == "available"
    assert result["coverage"]["value"] == 1.0
    assert result["coverage"]["top_k_value"] == 1.0
    assert result["precision_at_k"] == {
        "status": "available",
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
    }
    expected_dcg = (2**0 - 1) / math.log2(2) + (2**2 - 1) / math.log2(3)
    expected_idcg = (2**3 - 1) / math.log2(2) + (2**2 - 1) / math.log2(3)
    assert result["ndcg_at_k"]["dcg"] == pytest.approx(expected_dcg)
    assert result["ndcg_at_k"]["ideal_dcg"] == pytest.approx(expected_idcg)
    assert result["ndcg_at_k"]["value"] == pytest.approx(expected_dcg / expected_idcg)
    assert result["regret_at_k"]["selected_mean_utility"] == 0.5
    assert result["regret_at_k"]["oracle_mean_utility"] == 2.0
    assert result["regret_at_k"]["value"] == 1.5

    mixed_units = _labels()
    mixed_units["100003"]["return_percent"] = mixed_units["100003"].pop("utility")
    mixed_result = evaluate_candidate_selection_audit(_valid_v2(), mixed_units, k=2)
    assert mixed_result["regret_at_k"]["status"] == "unavailable"
    assert mixed_result["regret_at_k"]["reason"] == "universe_utility_basis_inconsistent"


def test_missing_outcomes_are_unavailable_not_zero_or_misses() -> None:
    result = evaluate_candidate_selection_audit(_valid_v2(), None, k=2)

    assert result["status"] == "unavailable"
    assert result["coverage"]["status"] == "available"
    assert result["coverage"]["value"] == 0.0
    assert result["coverage"]["mature_label_count"] == 0
    for key in ("precision_at_k", "ndcg_at_k", "regret_at_k"):
        assert result[key]["status"] == "unavailable"
        assert result[key]["value"] is None


def test_selected_count_below_preregistered_k_never_shrinks_denominators() -> None:
    result = evaluate_candidate_selection_audit(_valid_v2(), _labels(), k=3)

    assert result["selected_count"] == 2
    assert result["effective_k"] == 2
    assert result["coverage"]["top_k_count"] == 3
    assert result["coverage"]["selected_top_k_count"] == 2
    assert result["coverage"]["selection_at_k_value"] == pytest.approx(2 / 3)
    for key in ("precision_at_k", "ndcg_at_k", "regret_at_k"):
        assert result[key]["status"] == "unavailable"
        assert result[key]["reason"] == "selected_count_below_k"


def test_partial_outcomes_only_enable_metrics_whose_required_labels_are_complete() -> None:
    result = evaluate_candidate_selection_audit(_valid_v2(), _labels(include_third=False), k=2)

    assert result["status"] == "partial"
    assert result["coverage"]["value"] == pytest.approx(2 / 3)
    assert result["precision_at_k"]["status"] == "available"
    assert result["precision_at_k"]["value"] == 0.5
    assert result["ndcg_at_k"]["status"] == "unavailable"
    assert result["ndcg_at_k"]["missing_codes"] == ["100003"]
    assert result["regret_at_k"]["status"] == "unavailable"
    assert result["regret_at_k"]["missing_codes"] == ["100003"]


def test_duplicate_or_untraceable_outcomes_fail_closed() -> None:
    duplicate = [
        {"fund_code": "100001", **_labels()["100001"]},
        {"fund_code": "100001", **_labels()["100001"]},
    ]
    result = evaluate_candidate_selection_audit(_valid_v2(), duplicate, k=2)
    assert result["status"] == "unavailable"
    assert result["reason"] == "outcome_label_contract_invalid"
    assert {error["code"] for error in result["errors"]} == {"duplicate_outcome_label"}

    untraceable = _labels()
    untraceable["100002"].pop("observation_id")
    result = evaluate_candidate_selection_audit(_valid_v2(), untraceable, k=2)
    assert result["coverage"]["mature_label_count"] == 2
    assert result["precision_at_k"]["status"] == "unavailable"
    assert result["precision_at_k"]["missing_codes"] == ["100002"]


def test_legacy_v1_can_be_evaluated_descriptively_but_keeps_lineage_warning() -> None:
    labels = {
        "100001": {
            "observation_id": "obs:100001",
            "mature": True,
            "binary_relevance": False,
            "return_percent": -1.0,
        },
        "100002": {
            "observation_id": "obs:100002",
            "mature": True,
            "binary_relevance": True,
            "return_percent": 2.0,
        },
    }
    result = evaluate_candidate_selection_audit(_legacy_v1(), labels, k=1)

    assert result["status"] == "available"
    assert result["audit_compatibility_status"] == "legacy_partial"
    assert result["warnings"] == [
        "legacy_v1_has_no_recall_gate_pit_source_or_version_lineage"
    ]
    assert result["precision_at_k"]["value"] == 1.0
    assert result["regret_at_k"]["value"] == 0.0
