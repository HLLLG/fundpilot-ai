from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import InvestorProfile
from app.routes import factor_evidence
from app.services.decision_score_shadow import (
    COMPONENT_WEIGHTS,
    attach_decision_score_shadow,
    build_decision_score_shadow,
    build_decision_score_shadow_digest,
    validate_decision_score_shadow,
)
from app.services.discovery_payload import build_user_payload
from app.services.factor_ic_research import EXECUTION_QUALIFICATION_METHOD
from app.services.fund_tradeability import (
    normalize_purchase_fee_tiers,
    normalize_redemption_fee_tiers,
)


DECISION_AT = datetime(2026, 7, 18, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _metric(percentile: float) -> dict:
    return {
        "applicable": True,
        "available": True,
        "qualified": True,
        "percentile": percentile,
        "sample_count": 500,
        "coverage_rate": 0.95,
        "peer_sample_hash": "a" * 64,
    }


def _tradeability(*, purchase_fee_percent: float) -> dict:
    return {
        "data_status": "complete",
        "freshness": "fresh",
        "purchase_state": "open",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_initial_purchase_yuan": 100.0,
        "minimum_additional_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
        "fee_freshness": "fresh",
        "standard_purchase_fee_tiers": normalize_purchase_fee_tiers(
            [
                {
                    "condition": "小于50万元",
                    "standard_rate": f"{purchase_fee_percent}%",
                }
            ]
        ),
        "redemption_fee_tiers": normalize_redemption_fee_tiers(
            [
                {"condition": "小于30天", "rate": "0.50%"},
                {"condition": "大于等于30天", "rate": "0.00%"},
            ]
        ),
        "sales_service_fee_annual_percent": 0.0,
        "sales_service_fee_status": "known_zero",
        "share_class_fee_status": "standard_upper_bound_available",
        "source_ids": ["pytest.tradeability"],
    }


def _candidate(
    code: str,
    *,
    sector: str,
    benchmark: float,
    downside: float,
    purchase_fee_percent: float,
    quality_status: str = "eligible",
) -> dict:
    return {
        "fund_code": code,
        "fund_name": f"测试主动基金{code}A",
        "sector_label": sector,
        "quality_gate": {"status": quality_status},
        "tradeability": _tradeability(purchase_fee_percent=purchase_fee_percent),
        "peer_rank": {
            "schema_version": "peer_rank.v2",
            "status": "qualified",
            "qualified": True,
            "research_shadow_rerank_eligible": True,
            "metric_profile": "equity",
            "metric_registry_version": "peer_metric_registry.v2",
            "peer_group": {"group_key": "equity:active"},
            "metrics": {
                "benchmark_excess_return_1y_percent": _metric(benchmark),
                "max_drawdown_1y_percent": _metric(downside),
                "downside_capture_1y_percent": _metric(downside),
            },
        },
    }


def _factor_payload(scores: dict[str, float]) -> dict:
    return {
        "available": True,
        "ic_status": {
            "state": "available",
            "available": True,
            "stale": False,
            "confidence_eligible": True,
            "schema_version": 3,
            "snapshot_id": "b" * 64,
            "point_in_time": {
                "point_in_time_scope": "membership_only",
                "nav_revision_pit": False,
            },
        },
        "holdings": [
            {
                "fund_code": code,
                "composite_score": score,
                "execution_qualified": True,
                "execution_qualified_factor_keys": [
                    "momentum",
                    "equity_low_volatility",
                ],
                "execution_qualification": {
                    "status": "qualified",
                    "method": EXECUTION_QUALIFICATION_METHOD,
                },
                "typed_factor_applicable": True,
                "typed_factor_score": score,
                "typed_used_keys": ["equity_low_volatility"],
                "target_feature_as_of": "2026-07-17",
                "target_feature_observed_at": "2026-07-18T07:00:00+00:00",
                "target_feature_source": "pytest.factor_features",
            }
            for code, score in scores.items()
        ],
    }


def _gap() -> dict:
    return {
        "weight_denominator_yuan": 20_000.0,
        "sector_exposure_complete": True,
        "held_sectors": [{"sector_name": "科技", "amount": 3_000.0}],
    }


def _profile() -> dict:
    return {"concentration_limit_percent": 35.0}


def test_shadow_score_is_deterministic_strict_and_non_executing() -> None:
    candidates = [
        _candidate(
            "000001",
            sector="科技",
            benchmark=85,
            downside=80,
            purchase_fee_percent=1.5,
        ),
        _candidate(
            "000002",
            sector="医药",
            benchmark=75,
            downside=70,
            purchase_fee_percent=0.5,
        ),
    ]
    original = deepcopy(candidates)

    artifact = build_decision_score_shadow(
        candidates,
        candidate_factor_scores=_factor_payload({"000001": 90, "000002": 70}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
        top_k=1,
    )

    assert candidates == original
    assert artifact["validation"] == {
        "status": "valid",
        "shadow_evaluable": True,
        "error_codes": [],
    }
    assert artifact["weights"] == COMPONENT_WEIGHTS
    assert artifact["selection_effect"] == "none_shadow_only"
    assert artifact["actual_decision_unchanged"] is True
    assert artifact["automatic_promotion_allowed"] is False
    assert artifact["allocation_tilt_eligible"] is False
    assert artifact["coverage"]["scored_count"] == 2
    assert artifact["source_top_k_fund_codes"] == ["000001"]
    assert artifact["comparable_baseline_top_k_fund_codes"] == ["000001"]
    assert artifact["shadow_top_k_fund_codes"] == ["000002"]
    assert artifact["top_k_changed"] is True
    rows = {row["fund_code"]: row for row in artifact["rows"]}
    assert rows["000002"]["shadow_rank"] == 1
    assert rows["000001"]["components"]["cost_efficiency"]["score"] == 25.0
    assert rows["000002"]["components"]["cost_efficiency"]["score"] == 75.0
    assert rows["000001"]["data_confidence"] < 1.0

    repeated = build_decision_score_shadow(
        candidates,
        candidate_factor_scores=_factor_payload({"000001": 90, "000002": 70}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
        top_k=1,
    )
    assert repeated == artifact


def test_missing_factor_does_not_fill_zero_or_renormalize_weights() -> None:
    candidates = [
        _candidate(
            "000001",
            sector="科技",
            benchmark=85,
            downside=80,
            purchase_fee_percent=1.5,
        ),
        _candidate(
            "000002",
            sector="医药",
            benchmark=75,
            downside=70,
            purchase_fee_percent=0.5,
        ),
    ]

    artifact = build_decision_score_shadow(
        candidates,
        candidate_factor_scores=_factor_payload({"000001": 90}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )

    row = next(item for item in artifact["rows"] if item["fund_code"] == "000002")
    assert row["status"] == "insufficient_evidence"
    assert row["score"] is None
    assert row["base_component_score"] is None
    assert row["data_confidence"] is None
    assert row["missing_components"] == ["factor_peer"]
    assert row["components"]["factor_peer"]["score"] is None
    assert artifact["missing_component_policy"] == (
        "no_imputation_no_zero_fill_no_weight_renormalization"
    )


def test_valid_v2_factor_context_is_not_enough_for_v1_shadow_score() -> None:
    factors = _factor_payload({"000001": 90})
    factors["ic_status"]["schema_version"] = 2
    candidate = _candidate(
        "000001",
        sector="科技",
        benchmark=85,
        downside=80,
        purchase_fee_percent=1.5,
    )

    artifact = build_decision_score_shadow(
        [candidate],
        candidate_factor_scores=factors,
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )

    row = artifact["rows"][0]
    assert row["status"] == "insufficient_evidence"
    assert row["score"] is None
    assert row["components"]["factor_peer"]["reason_codes"] == [
        "factor_ic_not_decision_eligible"
    ]


def test_unregistered_bond_benchmark_component_fails_closed() -> None:
    candidate = _candidate(
        "000001",
        sector="债券",
        benchmark=85,
        downside=80,
        purchase_fee_percent=0.5,
    )
    candidate["peer_rank"]["metric_profile"] = "bond"
    candidate["peer_rank"]["metrics"] = {
        "max_drawdown_1y_percent": _metric(80),
        "investment_grade_exposure_percent": _metric(85),
    }

    artifact = build_decision_score_shadow(
        [candidate],
        candidate_factor_scores=_factor_payload({"000001": 90}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )

    row = artifact["rows"][0]
    assert row["status"] == "insufficient_evidence"
    assert row["components"]["benchmark_consistency"]["reason_codes"] == [
        "benchmark_consistency_unsupported_for_peer_profile"
    ]


def test_incomplete_portfolio_sector_exposure_is_not_treated_as_diversification() -> None:
    gap = _gap()
    gap["sector_exposure_complete"] = False
    candidate = _candidate(
        "000001",
        sector="科技",
        benchmark=85,
        downside=80,
        purchase_fee_percent=1.5,
    )

    artifact = build_decision_score_shadow(
        [candidate],
        candidate_factor_scores=_factor_payload({"000001": 90}),
        portfolio_gap=gap,
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )

    row = artifact["rows"][0]
    assert row["status"] == "insufficient_evidence"
    assert row["components"]["portfolio_diversification"]["reason_codes"] == [
        "portfolio_sector_exposure_incomplete"
    ]


def test_hard_gate_blocked_candidate_never_receives_a_score() -> None:
    candidate = _candidate(
        "000001",
        sector="科技",
        benchmark=85,
        downside=80,
        purchase_fee_percent=1.5,
        quality_status="watch_only",
    )

    artifact = build_decision_score_shadow(
        [candidate],
        candidate_factor_scores=_factor_payload({"000001": 90}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )

    row = artifact["rows"][0]
    assert row["status"] == "hard_gate_blocked"
    assert row["score"] is None
    assert row["hard_gate"]["reason_codes"] == ["quality_gate_not_eligible"]
    assert artifact["shadow_top_k_fund_codes"] == []


def test_shadow_validation_detects_score_and_hash_tampering() -> None:
    artifact = build_decision_score_shadow(
        [
            _candidate(
                "000001",
                sector="科技",
                benchmark=85,
                downside=80,
                purchase_fee_percent=1.5,
            ),
            _candidate(
                "000002",
                sector="医药",
                benchmark=75,
                downside=70,
                purchase_fee_percent=0.5,
            ),
        ],
        candidate_factor_scores=_factor_payload({"000001": 90, "000002": 70}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )
    tampered = deepcopy(artifact)
    tampered["rows"][0]["score"] = 99.0

    validation = validate_decision_score_shadow(tampered)

    assert validation["status"] == "invalid"
    assert "score_formula_mismatch" in validation["error_codes"]
    assert "snapshot_hash_invalid" in validation["error_codes"]


def test_shadow_validation_fails_closed_for_malformed_stored_row() -> None:
    artifact = build_decision_score_shadow(
        [
            _candidate(
                "000001",
                sector="科技",
                benchmark=85,
                downside=80,
                purchase_fee_percent=1.5,
            )
        ],
        candidate_factor_scores=_factor_payload({"000001": 90}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )
    malformed = deepcopy(artifact)
    malformed["rows"][0]["hard_gate"] = "invalid"
    malformed["rows"][0]["score"] = "not-a-number"

    validation = validate_decision_score_shadow(malformed)

    assert validation["status"] == "invalid"
    assert "hard_gate_invalid" in validation["error_codes"]
    assert "score_formula_mismatch" in validation["error_codes"]
    assert "snapshot_hash_invalid" in validation["error_codes"]


def test_attached_shadow_artifact_is_persisted_but_not_sent_to_llm() -> None:
    candidate = _candidate(
        "000001",
        sector="科技",
        benchmark=85,
        downside=80,
        purchase_fee_percent=1.5,
    )
    facts = {
        "session": {"decision_at": DECISION_AT.isoformat(), "calendar_date": "2026-07-18"},
        "profile": _profile(),
        "portfolio_gap": _gap(),
        "candidate_pool": [candidate],
        "candidate_factor_scores": _factor_payload({"000001": 90}),
        "candidate_selection_audit": {
            "schema_version": "discovery_candidate_selection_audit.v2",
            "decision_at": DECISION_AT.isoformat(),
            "snapshot_hash": "c" * 64,
            "validation": {"status": "valid"},
        },
        "sector_heat": [],
        "sector_opportunities": [],
    }
    original_candidate = deepcopy(candidate)

    artifact = attach_decision_score_shadow(
        facts,
        [candidate],
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )
    payload = build_user_payload(
        discovery_facts=facts,
        profile=InvestorProfile(),
        focus_sectors=[],
        scan_mode="full_market",
        market_news=[],
        topic_briefs=[],
        analysis_mode="deep",
    )

    assert facts["decision_score_shadow"] == artifact
    assert artifact["source_candidate_selection_audit"] == {
        "schema_version": "discovery_candidate_selection_audit.v2",
        "snapshot_hash": "c" * 64,
        "decision_at": DECISION_AT.isoformat(),
        "validation_status": "valid",
    }
    assert candidate == original_candidate
    assert "decision_score_shadow" not in payload["discovery_facts"]


def test_shadow_digest_exposes_coverage_without_candidate_details(monkeypatch) -> None:
    artifact = build_decision_score_shadow(
        [
            _candidate(
                "000001",
                sector="科技",
                benchmark=85,
                downside=80,
                purchase_fee_percent=1.5,
            ),
            _candidate(
                "000002",
                sector="医药",
                benchmark=75,
                downside=70,
                purchase_fee_percent=0.5,
            ),
        ],
        candidate_factor_scores=_factor_payload({"000001": 90, "000002": 70}),
        portfolio_gap=_gap(),
        profile=_profile(),
        decision_at=DECISION_AT,
        minimum_holding_days=30,
    )
    reports = [
        {
            "id": "report-1",
            "created_at": DECISION_AT.isoformat(),
            "discovery_facts": {"decision_score_shadow": artifact},
        },
        {"id": "legacy", "created_at": "2026-07-17T16:00:00+08:00"},
    ]

    digest = build_decision_score_shadow_digest(reports)

    assert digest["artifact_count"] == 1
    assert digest["valid_artifact_count"] == 1
    assert digest["shadow_evaluable_report_count"] == 1
    assert digest["candidate_count"] == 2
    assert digest["scored_count"] == 2
    assert digest["latest"]["report_id"] == "report-1"
    assert "rows" not in digest["latest"]

    monkeypatch.setattr(
        factor_evidence,
        "list_discovery_reports",
        lambda *, limit: reports[:limit],
    )
    assert factor_evidence.decision_score_shadow_digest(limit=999) == digest
