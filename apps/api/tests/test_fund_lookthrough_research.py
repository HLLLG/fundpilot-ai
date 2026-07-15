from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.fund_lookthrough_research import (
    LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
    build_fund_lookthrough_research,
    compact_fund_lookthrough_for_llm,
)
from app.services.fund_holdings_snapshot import compute_fund_holdings_snapshot_hash


CN = ZoneInfo("Asia/Shanghai")
DECISION = datetime(2026, 8, 31, 12, 0, tzinfo=CN)


def _evidence(value: str, *, available_at: str = "2026-07-20T09:00:00+08:00") -> dict:
    return {
        "value": value,
        "available_at": available_at,
        "first_observed_at": available_at,
        "source": "frozen.security_master",
        "ref_id": f"ref-{value}",
    }


def _holding(
    security_id: str | None,
    code: str,
    weight: object,
    *,
    name: str | None = None,
    industry: str | None = "Technology",
    market: str | None = "SSE",
    evidence_at: str = "2026-07-20T09:00:00+08:00",
) -> dict:
    row = {
        "security_code": code,
        "security_name": name or f"Security {code}",
        "weight_percent": weight,
    }
    if security_id is not None:
        row["security_id"] = security_id
    if industry is not None:
        row["industry"] = _evidence(industry, available_at=evidence_at)
    if market is not None:
        row["listing_market"] = _evidence(market, available_at=evidence_at)
    return row


def _snapshot(
    fund_code: str,
    holdings: list[dict],
    *,
    as_of: str = "2026-06-30",
    available_at: str = "2026-07-21T00:00:00+08:00",
    first_observed_at: str = "2026-07-21T00:05:00+08:00",
    snapshot_decision: str = "2026-07-21T12:00:00+08:00",
    qualified: bool = True,
    master_key: str | None = None,
    verification: dict | None = None,
) -> dict:
    weight_sum = sum(
        float(row["weight_percent"])
        for row in holdings
        if isinstance(row.get("weight_percent"), (int, float))
        and math.isfinite(float(row["weight_percent"]))
    )
    result = {
        "schema_version": "fund_holdings_snapshot.v1",
        "fund_code": fund_code,
        "fund_master_key": master_key or fund_code,
        "decision_at": snapshot_decision,
        "report_period": "2026-Q2",
        "as_of_date": as_of,
        "available_at": available_at,
        "first_observed_at": first_observed_at,
        "status": "qualified" if qualified else "unavailable",
        "qualified": qualified,
        "source_validation": {
            "schema_version": "fund_holdings_source_validation.v1",
            "status": "qualified" if qualified else "unavailable",
            "qualified": qualified,
            "valid_snapshot": qualified,
            "available_at_known": True,
            "disclosure_scope_identified": qualified,
            "weight_validation_passed": qualified,
            "reason_codes": [] if qualified else ["fixture_unavailable"],
        },
        "qualification": {
            "valid_snapshot": qualified,
            "pit_eligible": qualified,
            "disclosed_overlap_lower_bound_eligible": qualified,
            "exact_full_portfolio_overlap_eligible": False,
        },
        "scope": {"kind": "top10", "completeness": "partial"},
        # This stored freshness is intentionally not authoritative to C2.
        "freshness": {"label": "fresh", "report_age_days": 1},
        "coverage": {"weight_sum_percent": weight_sum},
        "holdings": holdings,
        "snapshot_hash": None,
    }
    if verification is not None:
        result["master_key_verification"] = verification
    result["snapshot_hash"] = compute_fund_holdings_snapshot_hash(result)
    return result


def _denominator_source(
    *, available_at: str = "2026-08-31T10:00:00+08:00"
) -> dict:
    return {
        "available_at": available_at,
        "first_observed_at": available_at,
        "source": "portfolio.ledger",
        "ref_id": "account-snapshot-1",
    }


def _baseline() -> tuple[list[dict], list[dict], list[dict]]:
    existing = [
        _snapshot(
            "000001",
            [
                _holding("SEC-1", "600001", 30, industry="Technology"),
                _holding("SEC-2", "600002", 20, industry="Finance"),
            ],
        ),
        _snapshot(
            "000002",
            [
                _holding("SEC-1", "600001", 10, industry="Technology"),
                _holding("SEC-3", "600003", 40, industry="Industrials"),
            ],
        ),
    ]
    positions = [
        {"fund_code": "000001", "holding_amount": 600},
        {"fund_code": "000002", "holding_amount": 400},
    ]
    candidates = [
        _snapshot(
            "000003",
            [
                _holding("SEC-1", "600001", 20, industry="Technology"),
                _holding("SEC-2", "600002", 10, industry="Finance"),
                _holding("SEC-4", "600004", 30, industry="Consumer"),
            ],
        )
    ]
    return existing, positions, candidates


def _build(
    existing: object,
    positions: object,
    candidates: object,
    **kwargs: object,
) -> dict:
    position_input = positions
    if isinstance(positions, list):
        position_input = {
            "positions": positions,
            "positions_complete": kwargs.get("portfolio_positions_complete", True),
            "available_at": "2026-08-31T10:00:00+08:00",
            "first_observed_at": "2026-08-31T10:00:00+08:00",
            "as_of_date": "2026-08-31",
            "source": "portfolio.ledger",
            "ref_id": "positions-1",
        }
    return build_fund_lookthrough_research(
        existing,
        position_input,
        candidates,
        decision_at=kwargs.pop("decision_at", DECISION),
        portfolio_positions_complete=kwargs.pop("portfolio_positions_complete", True),
        portfolio_denominator_yuan=kwargs.pop("portfolio_denominator_yuan", 1000),
        portfolio_denominator_source=kwargs.pop(
            "portfolio_denominator_source", _denominator_source()
        ),
        **kwargs,
    )


def test_weighted_portfolio_and_pair_overlap_are_disclosed_lower_bounds() -> None:
    existing, positions, candidates = _baseline()

    result = _build(existing, positions, candidates)

    assert result["schema_version"] == LOOKTHROUGH_RESEARCH_SCHEMA_VERSION
    assert result["status"] == "qualified"
    assert result["execution_qualified"] is False
    assert result["portfolio_execution_qualified"] is True
    assert result["portfolio"]["scope"] == "whole_account"
    exposures = {
        row["security_key"]: row["exposure_lower_bound_percent"]
        for row in result["portfolio"]["security_exposure_lower_bounds"]
    }
    assert exposures == {
        "security_id:SEC-1|listing:SSE:600001": 22.0,
        "security_id:SEC-2|listing:SSE:600002": 12.0,
        "security_id:SEC-3|listing:SSE:600003": 16.0,
    }
    assert result["portfolio"]["disclosed_security_mass_lower_bound_percent"] == 50
    assert result["portfolio"]["identity_known_security_mass_lower_bound_percent"] == 50
    assert result["portfolio"]["unknown_account_mass_percent"] == 50

    candidate = result["candidates"][0]
    assert candidate["capabilities"] == {
        "research_eligible": True,
        "concentration_risk_guard_eligible": True,
        "allocation_authorization_eligible": False,
        "reason_codes": [],
    }
    assert candidate["vintage_alignment"] == {
        "status": "same_as_of_date",
        "gap_days": 0,
        "as_of_dates": ["2026-06-30"],
    }
    assert candidate["portfolio_security_overlap_lower_bound_percent"] == 30
    assert candidate["max_existing_fund_overlap_lower_bound_percent"] == 30
    pairs = {
        row["existing_fund_code"]: row
        for row in candidate["existing_fund_overlaps"]
    }
    assert pairs["000001"]["overlap_lower_bound_percent"] == 30
    assert pairs["000002"]["overlap_lower_bound_percent"] == 10
    assert pairs["000001"]["left_disclosed_mass_percent"] == 50
    assert pairs["000001"]["left_unknown_mass_percent"] == 50
    assert pairs["000001"]["right_disclosed_mass_percent"] == 60
    assert pairs["000001"]["right_unknown_mass_percent"] == 40
    assert candidate["exact_full_portfolio_overlap_percent"] is None
    assert candidate["exact_full_portfolio_overlap_eligible"] is False


def test_missing_account_denominator_is_fund_holdings_only_and_never_executable() -> None:
    existing, positions, candidates = _baseline()

    result = build_fund_lookthrough_research(
        existing,
        positions,
        candidates,
        decision_at=DECISION,
        portfolio_positions_complete=True,
    )

    assert result["status"] == "qualified"
    assert result["execution_qualified"] is False
    assert result["portfolio"]["scope"] == "fund_holdings_only"
    assert result["portfolio"]["whole_account_denominator_yuan"] is None
    assert result["portfolio"]["unknown_account_mass_percent"] is None
    assert result["portfolio"]["unknown_fund_holdings_scope_mass_percent"] == 50
    assert result["candidates"][0]["execution_qualified"] is False
    assert "whole_account_denominator_unqualified" in result["candidates"][0][
        "reason_codes"
    ]


def test_whole_account_denominator_preserves_cash_and_other_unknown_mass() -> None:
    existing, positions, candidates = _baseline()

    result = _build(
        existing,
        positions,
        candidates,
        portfolio_denominator_yuan=2000,
    )

    assert result["portfolio"]["fund_position_mass_percent"] == 50
    assert result["portfolio"]["non_fund_or_cash_mass_percent"] == 50
    assert result["portfolio"]["identity_known_security_mass_lower_bound_percent"] == 25
    assert result["portfolio"]["unknown_account_mass_percent"] == 75
    assert result["candidates"][0][
        "portfolio_security_overlap_lower_bound_percent"
    ] == 17


def test_no_common_top10_is_not_described_as_zero_full_portfolio_overlap() -> None:
    existing = [_snapshot("000001", [_holding("SEC-1", "600001", 10)])]
    candidate = _snapshot("000003", [_holding("SEC-9", "600009", 10)])

    result = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    pair = result["candidates"][0]["existing_fund_overlaps"][0]
    assert pair["overlap_lower_bound_percent"] is None
    assert pair["common_disclosed_weight_percent"] == 0
    assert pair["interpretation"] == "no_common_in_disclosed_scope"
    assert pair["exact_full_portfolio_overlap_percent"] is None
    assert "zero_overlap" not in json.dumps(pair)


def test_unknown_identity_is_retained_as_unknown_mass_without_code_only_join() -> None:
    # No security_id, and listing market has no PIT evidence.
    left = _snapshot(
        "000001",
        [_holding(None, "600001", 25, market=None, industry=None)],
    )
    right = _snapshot(
        "000003",
        [_holding(None, "600001", 30, market=None, industry=None)],
    )

    result = _build(
        [left],
        [{"fund_code": "000001", "holding_amount": 100}],
        [right],
        portfolio_denominator_yuan=100,
    )

    existing_summary = result["existing_funds"][0]["lookthrough"]
    assert existing_summary["disclosed_mass_percent"] == 25
    assert existing_summary["identity_known_disclosed_mass_percent"] == 0
    assert existing_summary["unknown_mass_percent"] == 100
    pair = result["candidates"][0]["existing_fund_overlaps"][0]
    assert pair["interpretation"] == "identity_evidence_insufficient"
    assert pair["top_common_securities"] == []


def test_listing_and_industry_classification_require_pit_evidence() -> None:
    future = "2026-09-01T00:00:00+08:00"
    left = _snapshot(
        "000001",
        [
            _holding(
                None,
                "600001",
                25,
                market="SSE",
                industry="Technology",
                evidence_at=future,
            )
        ],
    )
    right = _snapshot(
        "000003",
        [
            _holding(
                None,
                "600001",
                30,
                market="SSE",
                industry="Technology",
                evidence_at=future,
            )
        ],
    )

    result = _build(
        [left],
        [{"fund_code": "000001", "holding_amount": 100}],
        [right],
        portfolio_denominator_yuan=100,
    )

    assert result["portfolio"]["industry_exposure_lower_bounds"] == []
    assert result["portfolio"]["listing_market_exposure_lower_bounds"] == []
    assert result["candidates"][0]["portfolio_overlap_interpretation"] == (
        "identity_evidence_insufficient"
    )


def test_conflicting_listing_assertions_do_not_hard_merge_same_security_id() -> None:
    left = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 20, market="SSE")],
    )
    right = _snapshot(
        "000003",
        [_holding("SEC-1", "000001", 20, market="SZSE")],
    )

    result = _build(
        [left],
        [{"fund_code": "000001", "holding_amount": 100}],
        [right],
        portfolio_denominator_yuan=100,
    )

    pair = result["candidates"][0]["existing_fund_overlaps"][0]
    assert pair["interpretation"] == "no_common_in_disclosed_scope"
    assert pair["overlap_lower_bound_percent"] is None
    assert pair["common_disclosed_weight_percent"] == 0


def test_current_decision_recomputes_freshness_without_changing_snapshot_hash() -> None:
    existing = [_snapshot("000001", [_holding("SEC-1", "600001", 10)])]
    candidate = _snapshot("000003", [_holding("SEC-1", "600001", 10)])
    original_hash = candidate["snapshot_hash"]

    fresh = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
        decision_at=DECISION,
    )
    stale = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
        decision_at=datetime(2027, 2, 1, 12, 0, tzinfo=CN),
        portfolio_denominator_source=_denominator_source(
            available_at="2027-02-01T10:00:00+08:00"
        ),
    )

    assert fresh["candidates"][0]["snapshot"]["snapshot_hash"] == original_hash
    assert fresh["candidates"][0]["snapshot"]["current_freshness_label"] == "fresh"
    assert stale["status"] == "unavailable"
    assert stale["candidates"][0]["snapshot"]["snapshot_hash"] == original_hash
    assert stale["candidates"][0]["snapshot"]["current_freshness_label"] == "stale"
    assert "snapshot_stale_at_decision" in stale["candidates"][0]["reason_codes"]
    assert stale["candidates"][0][
        "portfolio_security_overlap_lower_bound_percent"
    ] is None


def test_late_stale_view_replays_to_early_decision_as_eligible() -> None:
    existing = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 10)],
        snapshot_decision="2027-02-01T12:00:00+08:00",
    )
    candidate = _snapshot(
        "000003",
        [_holding("SEC-1", "600001", 10)],
        snapshot_decision="2027-02-01T12:00:00+08:00",
    )
    for snapshot in (existing, candidate):
        snapshot["freshness"] = {"label": "stale", "report_age_days": 216}
        snapshot["qualification"]["disclosed_overlap_lower_bound_eligible"] = False

    result = _build(
        [existing],
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
        decision_at=DECISION,
    )

    assert result["status"] == "qualified"
    assert result["candidates"][0]["snapshot"]["current_freshness_label"] == "fresh"
    assert result["candidates"][0][
        "portfolio_security_overlap_lower_bound_percent"
    ] == 10


def test_future_snapshot_is_never_used_for_overlap() -> None:
    existing = [_snapshot("000001", [_holding("SEC-1", "600001", 10)])]
    future_candidate = _snapshot(
        "000003",
        [_holding("SEC-1", "600001", 10)],
        available_at="2026-09-02T00:00:00+08:00",
        snapshot_decision="2026-09-02T12:00:00+08:00",
    )

    result = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [future_candidate],
        portfolio_denominator_yuan=100,
    )

    assert result["status"] == "unavailable"
    assert result["candidates"][0]["status"] == "unavailable"
    assert "snapshot_available_after_decision" in result["candidates"][0][
        "reason_codes"
    ]
    assert result["candidates"][0][
        "portfolio_security_overlap_lower_bound_percent"
    ] is None


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True])
def test_invalid_holding_amounts_fail_closed(amount: object) -> None:
    existing, _positions, candidates = _baseline()

    result = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": amount}],
        candidates,
        portfolio_denominator_yuan=1000,
    )

    assert result["status"] == "invalid"
    assert result["portfolio"] is None


def test_nan_weight_and_duplicate_security_identity_fail_closed() -> None:
    existing, positions, candidates = _baseline()
    broken_nan = copy.deepcopy(existing)
    broken_nan[0]["holdings"][0]["weight_percent"] = float("nan")
    broken_nan[0]["coverage"]["weight_sum_percent"] = 20
    nan_result = _build(broken_nan, positions, candidates)
    assert nan_result["status"] == "invalid"
    assert "existing_snapshot_holding_weight_invalid" in nan_result["reason_codes"]

    duplicate = _snapshot(
        "000001",
        [
            _holding("SEC-1", "600001", 10),
            _holding("SEC-1", "600001", 11),
        ],
    )
    duplicate_result = _build(
        [duplicate],
        [{"fund_code": "000001", "holding_amount": 100}],
        candidates,
        portfolio_denominator_yuan=100,
    )
    assert duplicate_result["status"] == "invalid"
    assert "existing_snapshot_holding_duplicate_identity_conflict" in duplicate_result[
        "reason_codes"
    ]


def test_tampered_holdings_with_old_snapshot_hash_fail_closed() -> None:
    existing, positions, candidates = _baseline()
    tampered = copy.deepcopy(existing)
    old_hash = tampered[0]["snapshot_hash"]
    tampered[0]["holdings"][0]["weight_percent"] = 31
    tampered[0]["coverage"]["weight_sum_percent"] = 51
    assert tampered[0]["snapshot_hash"] == old_hash

    result = _build(tampered, positions, candidates)

    assert result["status"] == "invalid"
    assert "existing_snapshot_hash_mismatch" in result["reason_codes"]
    assert result["portfolio"] is None


def test_duplicate_snapshot_and_position_conflicts_fail_closed() -> None:
    first = _snapshot("000001", [_holding("SEC-1", "600001", 10)])
    conflict = _snapshot("000001", [_holding("SEC-2", "600002", 10)])
    candidate = _snapshot("000003", [_holding("SEC-1", "600001", 10)])

    snapshot_result = _build(
        [first, conflict],
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
    )
    assert snapshot_result["status"] == "invalid"
    assert "existing_snapshot_duplicate_conflict" in snapshot_result["reason_codes"]

    position_result = _build(
        [first],
        [
            {"fund_code": "000001", "holding_amount": 50},
            {"fund_code": "000001", "holding_amount": 50},
        ],
        [candidate],
        portfolio_denominator_yuan=100,
    )
    assert position_result["status"] == "invalid"
    assert "user_holding_duplicate_fund_conflict" in position_result["reason_codes"]


def test_unverified_share_class_master_is_not_hard_merged() -> None:
    proof = {
        "verified": True,
        "master_key": "family-1",
        "available_at": "2026-07-20T00:00:00+08:00",
        "first_observed_at": "2026-07-20T00:00:00+08:00",
        "source": "fund.contract",
        "ref_id": "master-proof-1",
    }
    verified = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 10)],
        master_key="family-1",
        verification=proof,
    )
    unverified = _snapshot(
        "000002",
        [_holding("SEC-1", "600001", 10)],
        master_key="family-1",
        verification={**proof, "verified": False},
    )
    candidate = _snapshot("000003", [_holding("SEC-1", "600001", 10)])

    result = _build(
        [verified, unverified],
        [
            {"fund_code": "000001", "holding_amount": 50},
            {"fund_code": "000002", "holding_amount": 50},
        ],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    rows = {item["fund_code"]: item["snapshot"] for item in result["existing_funds"]}
    assert rows["000001"]["aggregation_key"] == "family-1"
    assert rows["000001"]["master_key_verified"] is True
    assert rows["000002"]["aggregation_key"] == "000002"
    assert rows["000002"]["master_key_verified"] is False


def test_only_two_verified_share_classes_form_one_position_group() -> None:
    proof = {
        "verified": True,
        "master_key": "family-1",
        "available_at": "2026-07-20T00:00:00+08:00",
        "first_observed_at": "2026-07-20T00:00:00+08:00",
        "source": "fund.contract",
        "ref_id": "master-proof-1",
    }
    class_a = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 10)],
        master_key="family-1",
        verification=proof,
    )
    class_c = _snapshot(
        "000002",
        [_holding("SEC-1", "600001", 10)],
        master_key="family-1",
        verification=proof,
    )
    unverified = _snapshot(
        "000004",
        [_holding("SEC-2", "600002", 10)],
        master_key="family-1",
        verification={**proof, "verified": False},
    )
    candidate = _snapshot("000003", [_holding("SEC-1", "600001", 10)])

    result = _build(
        [class_a, class_c, unverified],
        [
            {"fund_code": "000001", "holding_amount": 30},
            {"fund_code": "000002", "holding_amount": 20},
            {"fund_code": "000004", "holding_amount": 50},
        ],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    groups = {
        item["aggregation_key"]: item
        for item in result["portfolio"]["fund_position_groups"]
    }
    assert groups["family-1"] == {
        "aggregation_key": "family-1",
        "fund_codes": ["000001", "000002"],
        "holding_amount_yuan": 50,
        "portfolio_weight_percent": 50,
        "verified_master_key": True,
        "hard_merge_applied": True,
    }
    assert groups["000004"]["fund_codes"] == ["000004"]
    assert groups["000004"]["hard_merge_applied"] is False


def test_hash_is_stable_across_input_order() -> None:
    existing, positions, candidates = _baseline()
    first = _build(existing, positions, candidates)

    reversed_existing = []
    for snapshot in reversed(copy.deepcopy(existing)):
        reversed_existing.append(snapshot)
    reversed_candidates = copy.deepcopy(candidates)
    second = _build(
        reversed_existing,
        list(reversed(positions)),
        reversed_candidates,
    )

    assert first["research_hash"] == second["research_hash"]
    assert first == second


def test_unverified_or_future_denominator_is_not_used_as_account_truth() -> None:
    existing, positions, candidates = _baseline()
    no_source = _build(
        existing,
        positions,
        candidates,
        portfolio_denominator_yuan=2000,
        portfolio_denominator_source=None,
    )
    assert no_source["portfolio"]["scope"] == "fund_holdings_only"
    assert no_source["portfolio"]["analysis_denominator_yuan"] == 1000
    assert no_source["execution_qualified"] is False

    future_source = _build(
        existing,
        positions,
        candidates,
        portfolio_denominator_yuan=2000,
        portfolio_denominator_source=_denominator_source(
            available_at="2026-09-01T00:00:00+08:00"
        ),
    )
    assert future_source["portfolio"]["scope"] == "fund_holdings_only"
    assert future_source["execution_qualified"] is False


def test_denominator_below_positions_fails_closed() -> None:
    existing, positions, candidates = _baseline()

    result = _build(
        existing,
        positions,
        candidates,
        portfolio_denominator_yuan=999,
    )

    assert result["status"] == "invalid"
    assert result["reason_codes"] == ["portfolio_denominator_below_fund_holdings"]


def test_compact_helper_is_bounded_and_never_echoes_raw_holdings() -> None:
    existing, positions, candidates = _baseline()
    result = _build(existing, positions, candidates)

    compact = compact_fund_lookthrough_for_llm(
        result,
        max_candidates=1,
        max_common_per_candidate=1,
        max_exposures=1,
    )

    assert compact["raw_holdings_included"] is False
    assert len(compact["candidates"]) == 1
    assert len(compact["candidates"][0]["top_common_with_portfolio"]) == 1
    assert len(compact["portfolio"]["top_security_exposure_lower_bounds"]) == 1
    assert "existing_funds" not in compact
    assert "lookthrough" not in compact["candidates"][0]
    assert compact["candidates"][0]["portfolio_overlap_interpretation"] == (
        "positive_disclosed_overlap_lower_bound"
    )


def test_cross_vintage_overlap_is_descriptive_and_never_a_reported_guard() -> None:
    existing = [
        _snapshot(
            "000001",
            [_holding("SEC-1", "600001", 30)],
            as_of="2026-06-30",
        )
    ]
    candidate = _snapshot(
        "000003",
        [_holding("SEC-1", "600001", 20)],
        as_of="2026-03-31",
    )

    result = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    row = result["candidates"][0]
    pair = row["existing_fund_overlaps"][0]
    assert row["vintage_alignment"] == {
        "status": "cross_vintage",
        "gap_days": 91,
        "as_of_dates": ["2026-03-31", "2026-06-30"],
    }
    assert row["reported_as_of_disclosed_overlap_percent"] is None
    assert row["portfolio_security_overlap_lower_bound_percent"] is None
    assert row["cross_vintage_disclosed_similarity_percent"] == 20
    assert row["overlap_evidence_state"] == "cross_vintage_descriptive_only"
    assert pair["overlap_lower_bound_percent"] is None
    assert pair["cross_vintage_disclosed_similarity_percent"] == 20
    assert row["capabilities"]["concentration_risk_guard_eligible"] is False
    assert row["capabilities"]["allocation_authorization_eligible"] is False


def test_plain_positions_without_pit_lineage_are_research_only() -> None:
    existing, positions, candidates = _baseline()

    result = build_fund_lookthrough_research(
        existing,
        positions,
        candidates,
        decision_at=DECISION,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=1000,
        portfolio_denominator_source=_denominator_source(),
    )

    assert result["status"] == "qualified"
    assert result["portfolio"]["position_truth_pit_qualified"] is False
    assert result["portfolio_execution_qualified"] is False
    assert result["execution_qualified"] is False
    candidate = result["candidates"][0]
    assert candidate["capabilities"]["research_eligible"] is True
    assert candidate["capabilities"]["concentration_risk_guard_eligible"] is False
    assert "portfolio_position_truth_not_pit_qualified" in candidate["reason_codes"]


def test_snapshot_first_observed_after_historical_decision_is_rejected() -> None:
    late = "2026-09-01T00:00:00+08:00"
    existing = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 20)],
        first_observed_at=late,
    )
    candidate = _snapshot(
        "000003",
        [_holding("SEC-1", "600001", 20)],
        first_observed_at=late,
    )

    result = _build(
        [existing],
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    assert result["status"] == "unavailable"
    assert "snapshot_first_observed_after_decision" in result["candidates"][0][
        "reason_codes"
    ]
    assert result["candidates"][0][
        "reported_as_of_disclosed_overlap_percent"
    ] is None


def test_current_same_run_observation_is_researchable_but_not_replayable() -> None:
    now = datetime.now(CN)
    decision = now - timedelta(minutes=2)
    first_observed = decision + timedelta(minutes=1)
    available = decision - timedelta(hours=1)
    as_of = decision.date().isoformat()
    existing = _snapshot(
        "000001",
        [_holding("SEC-1", "600001", 25, industry=None, market=None)],
        as_of=as_of,
        available_at=available.isoformat(),
        first_observed_at=first_observed.isoformat(),
        snapshot_decision=now.isoformat(),
    )
    candidate = _snapshot(
        "000003",
        [_holding("SEC-1", "600001", 25, industry=None, market=None)],
        as_of=as_of,
        available_at=available.isoformat(),
        first_observed_at=first_observed.isoformat(),
        snapshot_decision=now.isoformat(),
    )
    positions = {
        "positions": [{"fund_code": "000001", "holding_amount": 100}],
        "positions_complete": True,
        "available_at": available.isoformat(),
        "first_observed_at": available.isoformat(),
        "as_of_date": as_of,
        "source": "portfolio.ledger",
        "ref_id": "current-position-1",
    }
    denominator_source = {
        "available_at": available.isoformat(),
        "first_observed_at": available.isoformat(),
        "as_of_date": as_of,
        "source": "portfolio.ledger",
        "ref_id": "current-account-1",
    }
    observation_rows = sorted(
        [
            {
                "snapshot_hash": existing["snapshot_hash"],
                "first_observed_at": first_observed.isoformat(),
            },
            {
                "snapshot_hash": candidate["snapshot_hash"],
                "first_observed_at": first_observed.isoformat(),
            },
        ],
        key=lambda row: (row["snapshot_hash"], row["first_observed_at"]),
    )
    proof_material = {
        "mode": "current_live_same_run",
        "decision_at": decision.isoformat(),
        "observed_at": first_observed.isoformat(),
        "snapshot_hashes": [row["snapshot_hash"] for row in observation_rows],
        "observations": observation_rows,
        "source": "fund_lookthrough_context.live_resolution",
    }
    observation_proof = {
        **proof_material,
        "ref_id": hashlib.sha256(
            json.dumps(
                proof_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
    }

    result = build_fund_lookthrough_research(
        [existing],
        positions,
        [candidate],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source=denominator_source,
        current_run_observation=observation_proof,
    )
    replayed = build_fund_lookthrough_research(
        [existing],
        positions,
        [candidate],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source=denominator_source,
        current_run_observation=observation_proof,
    )
    unproven = build_fund_lookthrough_research(
        [existing],
        positions,
        [candidate],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source=denominator_source,
    )
    forged_ref = build_fund_lookthrough_research(
        [existing],
        positions,
        [candidate],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source=denominator_source,
        current_run_observation={**observation_proof, "ref_id": "0" * 64},
    )
    forged_material = copy.deepcopy(proof_material)
    forged_material["observations"][0]["first_observed_at"] = (
        first_observed + timedelta(seconds=1)
    ).isoformat()
    forged_material["observed_at"] = (
        first_observed + timedelta(seconds=1)
    ).isoformat()
    forged_row_proof = {
        **forged_material,
        "ref_id": hashlib.sha256(
            json.dumps(
                forged_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
    }
    forged_row = build_fund_lookthrough_research(
        [existing],
        positions,
        [candidate],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source=denominator_source,
        current_run_observation=forged_row_proof,
    )

    assert result["status"] == "qualified"
    assert result["existing_funds"][0]["snapshot"]["observation_status"] == (
        "current_live_same_run"
    )
    assert result["existing_funds"][0]["snapshot"]["replay_eligible"] is False
    assert result["portfolio_execution_qualified"] is False
    assert result["candidates"][0]["capabilities"][
        "concentration_risk_guard_eligible"
    ] is False
    assert replayed == result
    assert replayed["research_hash"] == result["research_hash"]
    assert unproven["status"] == "unavailable"
    assert "snapshot_first_observed_after_decision" in unproven["candidates"][0][
        "reason_codes"
    ]
    assert forged_ref["status"] == "unavailable"
    assert forged_row["status"] != "qualified"


def test_low_disclosed_coverage_cannot_enable_concentration_guard() -> None:
    existing = [_snapshot("000001", [_holding("SEC-1", "600001", 10)])]
    candidate = _snapshot("000003", [_holding("SEC-1", "600001", 10)])

    result = _build(
        existing,
        [{"fund_code": "000001", "holding_amount": 100}],
        [candidate],
        portfolio_denominator_yuan=100,
    )

    row = result["candidates"][0]
    assert row["reported_as_of_disclosed_overlap_percent"] == 10
    assert row["capabilities"]["concentration_risk_guard_eligible"] is False
    assert "concentration_risk_guard_evidence_insufficient" in row["reason_codes"]


def test_compact_helper_strips_untrusted_nested_keys_at_every_level() -> None:
    existing, positions, candidates = _baseline()
    result = _build(existing, positions, candidates)
    poisoned = copy.deepcopy(result)
    poisoned["qualification"]["payload"] = {"secret": "LEAK_SENTINEL"}
    poisoned["capabilities"]["portfolio_lookthrough"]["payload"] = {
        "secret": "LEAK_SENTINEL"
    }
    poisoned["portfolio"]["security_exposure_lower_bounds"][0]["payload"] = {
        "secret": "LEAK_SENTINEL"
    }
    poisoned["candidates"][0]["coverage"]["payload"] = {
        "secret": "LEAK_SENTINEL"
    }
    poisoned["candidates"][0]["vintage_alignment"]["payload"] = {
        "secret": "LEAK_SENTINEL"
    }
    poisoned["candidates"][0]["top_common_with_portfolio"][0]["payload"] = {
        "secret": "LEAK_SENTINEL"
    }
    poisoned["candidates"][0]["reason_codes"] = [
        "safe_reason",
        {"secret": "LEAK_SENTINEL"},
    ]

    compact = compact_fund_lookthrough_for_llm(poisoned)

    assert "LEAK_SENTINEL" not in json.dumps(compact, ensure_ascii=False)
    assert "payload" not in json.dumps(compact, ensure_ascii=False)
    assert compact["candidates"][0]["reason_codes"] == ["safe_reason"]


def test_naive_decision_and_invalid_compact_limits_fail_closed() -> None:
    existing, positions, candidates = _baseline()
    result = build_fund_lookthrough_research(
        existing,
        positions,
        candidates,
        decision_at=datetime(2026, 8, 31, 12, 0),
    )
    assert result["status"] == "invalid"
    assert result["reason_codes"] == ["decision_at_timezone_required"]

    compact = compact_fund_lookthrough_for_llm({}, max_candidates=0)
    assert compact["status"] == "invalid"
    assert compact["raw_holdings_included"] is False
