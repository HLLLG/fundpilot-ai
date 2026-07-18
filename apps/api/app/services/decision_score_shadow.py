"""Deterministic, non-executing fund decision score for shadow evaluation.

The artifact deliberately consumes only evidence already present in the discovery
request.  It never changes candidate order, recommendation actions, or allocation
priority.  Missing components are not imputed and their weights are not
renormalized: a candidate receives no score until every registered component is
available.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
import math
from typing import Any

from app.services.factor_ic_research import EXECUTION_QUALIFICATION_METHOD
from app.services.fund_tradeability import (
    assess_tradeability_for_amount,
    build_tradeability_gate,
)


LEGACY_DECISION_SCORE_SHADOW_SCHEMA_VERSION = "decision_score_shadow.v1"
LEGACY_DECISION_SCORE_MODEL_VERSION = "decision_score.v1"
DECISION_SCORE_SHADOW_SCHEMA_VERSION = "decision_score_shadow.v2"
DECISION_SCORE_MODEL_VERSION = "decision_score.v2"
DECISION_SCORE_MODE = "shadow_record_only"
BENCHMARK_POLICY_VERSION = "fund_type_benchmark_policy.2026-07.v2"
FEE_EVIDENCE_POLICY_VERSION = "candidate_fee_evidence.2026-07.v2"

COMPONENT_WEIGHTS: dict[str, float] = {
    "factor_peer": 0.30,
    "benchmark_consistency": 0.25,
    "downside_control": 0.20,
    "portfolio_diversification": 0.15,
    "cost_efficiency": 0.10,
}
REQUIRED_COMPONENTS = tuple(COMPONENT_WEIGHTS)

_FORMAL_EXCESS_PROFILES = {
    "equity",
    "mixed",
    "bond",
    "enhanced_index",
    "qdii",
    "fof",
}
_TRACKING_PROFILES = {"passive_index", "enhanced_index"}

_DOWNSIDE_METRICS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "equity": ("max_drawdown_1y_percent", "downside_capture_1y_percent"),
    "mixed": ("max_drawdown_1y_percent", "downside_capture_1y_percent"),
    "bond": ("max_drawdown_1y_percent",),
    "passive_index": ("max_drawdown_1y_percent",),
    "enhanced_index": ("max_drawdown_1y_percent",),
    "qdii": ("max_drawdown_1y_percent",),
    "fof": ("max_drawdown_1y_percent",),
}

_FUND_CODE_LENGTH = 6


def attach_decision_score_shadow(
    discovery_facts: dict[str, Any],
    candidate_pool: Sequence[Mapping[str, Any]],
    *,
    decision_at: datetime | str | None,
    minimum_holding_days: int | None,
) -> dict[str, Any]:
    """Build and persist the shadow artifact without exposing it to the LLM."""

    effective_decision_at = decision_at
    if effective_decision_at is None:
        session = discovery_facts.get("session")
        if isinstance(session, Mapping):
            effective_decision_at = session.get("decision_at")
    artifact = build_decision_score_shadow(
        candidate_pool,
        candidate_factor_scores=(
            discovery_facts.get("candidate_factor_scores")
            if isinstance(discovery_facts.get("candidate_factor_scores"), Mapping)
            else {}
        ),
        portfolio_gap=(
            discovery_facts.get("portfolio_gap")
            if isinstance(discovery_facts.get("portfolio_gap"), Mapping)
            else {}
        ),
        profile=(
            discovery_facts.get("profile")
            if isinstance(discovery_facts.get("profile"), Mapping)
            else {}
        ),
        source_candidate_selection_audit=(
            discovery_facts.get("candidate_selection_audit")
            if isinstance(discovery_facts.get("candidate_selection_audit"), Mapping)
            else None
        ),
        decision_at=effective_decision_at,
        minimum_holding_days=minimum_holding_days,
    )
    discovery_facts["decision_score_shadow"] = artifact
    return artifact


def build_decision_score_shadow(
    candidate_pool: Sequence[Mapping[str, Any]],
    *,
    candidate_factor_scores: Mapping[str, Any] | None,
    portfolio_gap: Mapping[str, Any] | None,
    profile: Mapping[str, Any] | None,
    source_candidate_selection_audit: Mapping[str, Any] | None = None,
    decision_at: datetime | str | None,
    minimum_holding_days: int | None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Build a self-validating shadow-only score from frozen discovery facts."""

    factors = dict(candidate_factor_scores or {})
    gap = dict(portfolio_gap or {})
    profile_snapshot = dict(profile or {})
    factor_rows = _factor_rows_by_code(factors)
    decision_text = _decision_text(decision_at)
    rows: list[dict[str, Any]] = []

    for source_rank, candidate in enumerate(candidate_pool, start=1):
        raw = dict(candidate) if isinstance(candidate, Mapping) else {}
        code = _fund_code(raw.get("fund_code"))
        peer_profile = _peer_profile(raw)
        quality_gate = raw.get("quality_gate") if isinstance(raw.get("quality_gate"), Mapping) else {}
        tradeability = raw.get("tradeability") if isinstance(raw.get("tradeability"), Mapping) else None
        tradeability_gate = build_tradeability_gate(tradeability)

        hard_gate_reasons: list[str] = []
        if code is None:
            hard_gate_reasons.append("candidate_fund_code_invalid")
        if str(quality_gate.get("status") or "watch_only") != "eligible":
            hard_gate_reasons.append("quality_gate_not_eligible")
        if str(tradeability_gate.get("status") or "watch_only") != "eligible":
            hard_gate_reasons.append("tradeability_gate_not_eligible")

        factor_component = _factor_component(
            factor_rows.get(code or ""),
            factors,
        )
        benchmark_component = _benchmark_component_v2(
            raw,
            peer_profile=peer_profile,
            decision_at=decision_text,
        )
        downside_component = _peer_percentile_component(
            raw,
            peer_profile=peer_profile,
            metric_keys=_DOWNSIDE_METRICS_BY_PROFILE.get(peer_profile),
            component="downside_control",
        )
        diversification_component = _diversification_component(
            raw,
            portfolio_gap=gap,
            profile=profile_snapshot,
        )
        cost_component, cost_blocks_execution = _cost_component(
            tradeability,
            tradeability_gate=tradeability_gate,
            minimum_holding_days=minimum_holding_days,
        )
        if cost_blocks_execution:
            hard_gate_reasons.append("holding_period_cost_gate_not_executable")

        components = {
            "factor_peer": factor_component,
            "benchmark_consistency": benchmark_component,
            "downside_control": downside_component,
            "portfolio_diversification": diversification_component,
            "cost_efficiency": cost_component,
        }
        for key, component in components.items():
            component["weight"] = COMPONENT_WEIGHTS[key]

        rows.append(
            {
                "fund_code": code,
                "fund_name": str(raw.get("fund_name") or "").strip() or None,
                "sector_label": _sector_label(raw),
                "peer_profile": peer_profile,
                "source_rank": source_rank,
                "shadow_rank": None,
                "status": "pending",
                "score": None,
                "base_component_score": None,
                "data_confidence": None,
                "hard_gate": {
                    "eligible": not hard_gate_reasons,
                    "reason_codes": list(dict.fromkeys(hard_gate_reasons)),
                },
                "components": components,
                "missing_components": [],
            }
        )

    _assign_benchmark_percentiles(rows)
    _assign_cost_percentiles(rows)
    _calculate_scores(rows)
    scored_rows = sorted(
        (row for row in rows if row["status"] == "scored"),
        key=lambda row: (-float(row["score"]), str(row.get("fund_code") or "")),
    )
    for shadow_rank, row in enumerate(scored_rows, start=1):
        row["shadow_rank"] = shadow_rank

    for row in rows:
        row["row_hash"] = _hash_payload(row)

    normalized_top_k = top_k if isinstance(top_k, int) and not isinstance(top_k, bool) and top_k > 0 else 3
    comparable_baseline = sorted(
        scored_rows,
        key=lambda row: (int(row["source_rank"]), str(row.get("fund_code") or "")),
    )[:normalized_top_k]
    shadow_top = scored_rows[:normalized_top_k]
    source_top = rows[:normalized_top_k]
    baseline_codes = [str(row["fund_code"]) for row in comparable_baseline]
    shadow_codes = [str(row["fund_code"]) for row in shadow_top]

    artifact: dict[str, Any] = {
        "schema_version": DECISION_SCORE_SHADOW_SCHEMA_VERSION,
        "model_version": DECISION_SCORE_MODEL_VERSION,
        "mode": DECISION_SCORE_MODE,
        "decision_at": decision_text,
        "candidate_universe_stage": "final_candidate_pool",
        "source_candidate_selection_audit": _candidate_audit_ref(
            source_candidate_selection_audit
        ),
        "selection_effect": "none_shadow_only",
        "actual_decision_unchanged": True,
        "automatic_promotion_allowed": False,
        "allocation_tilt_eligible": False,
        "weights": dict(COMPONENT_WEIGHTS),
        "required_components": list(REQUIRED_COMPONENTS),
        "missing_component_policy": "no_imputation_no_zero_fill_no_weight_renormalization",
        "policies": {
            "factor": (
                "pit_v3_execution_qualified_and_type_factor_complete_only"
            ),
            "benchmark": (
                "verified_fund_contract_excess_or_exact_tracking_reference_"
                "within_type_cross_section_only"
            ),
            "downside": "type_specific_peer_universe_percentiles_only",
            "diversification": (
                "pre_decision_sector_capacity_proxy_not_correlation_or_risk_contribution"
            ),
            "cost": (
                "public_standard_fee_upper_bound_not_actual_channel_fee_at_"
                "effective_initial_minimum_and_strategy_horizon"
            ),
            "ranking": "score_desc_then_fund_code",
        },
        "policy_versions": {
            "benchmark": BENCHMARK_POLICY_VERSION,
            "fee_evidence": FEE_EVIDENCE_POLICY_VERSION,
        },
        "top_k": normalized_top_k,
        "source_top_k_fund_codes": [
            str(row["fund_code"])
            for row in source_top
            if row.get("fund_code") is not None
        ],
        "comparable_baseline_top_k_fund_codes": baseline_codes,
        "shadow_top_k_fund_codes": shadow_codes,
        "top_k_changed": baseline_codes != shadow_codes,
        "top_k_symmetric_difference_fund_codes": sorted(
            set(baseline_codes) ^ set(shadow_codes)
        ),
        "coverage": _coverage(rows),
        "rows": rows,
        "hash_algorithm": "sha256",
        "canonicalization": "json_utf8_sort_keys_v1",
    }
    artifact["status"] = (
        "shadow_evaluable"
        if artifact["coverage"]["scored_count"] >= 2
        else "insufficient_evidence"
    )
    artifact["snapshot_hash"] = _hash_payload(_artifact_hash_material(artifact))
    artifact["validation"] = validate_decision_score_shadow(artifact)
    return artifact


def validate_decision_score_shadow(artifact: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    version_pair = (
        artifact.get("schema_version"),
        artifact.get("model_version"),
    )
    current_pair = (
        DECISION_SCORE_SHADOW_SCHEMA_VERSION,
        DECISION_SCORE_MODEL_VERSION,
    )
    legacy_pair = (
        LEGACY_DECISION_SCORE_SHADOW_SCHEMA_VERSION,
        LEGACY_DECISION_SCORE_MODEL_VERSION,
    )
    if version_pair not in {current_pair, legacy_pair}:
        errors.append("schema_version_invalid")
        errors.append("model_version_invalid")
    if artifact.get("mode") != DECISION_SCORE_MODE:
        errors.append("mode_invalid")
    if artifact.get("selection_effect") != "none_shadow_only":
        errors.append("selection_effect_invalid")
    if artifact.get("actual_decision_unchanged") is not True:
        errors.append("actual_decision_unchanged_invalid")
    if artifact.get("automatic_promotion_allowed") is not False:
        errors.append("automatic_promotion_must_be_false")
    if artifact.get("allocation_tilt_eligible") is not False:
        errors.append("allocation_tilt_must_be_false")
    if artifact.get("weights") != COMPONENT_WEIGHTS:
        errors.append("weights_invalid")
    if artifact.get("required_components") != list(REQUIRED_COMPONENTS):
        errors.append("required_components_invalid")
    if version_pair == current_pair:
        policy_versions = (
            artifact.get("policy_versions")
            if isinstance(artifact.get("policy_versions"), Mapping)
            else {}
        )
        if policy_versions.get("benchmark") != BENCHMARK_POLICY_VERSION:
            errors.append("benchmark_policy_version_invalid")
        if policy_versions.get("fee_evidence") != FEE_EVIDENCE_POLICY_VERSION:
            errors.append("fee_evidence_policy_version_invalid")
    if _decision_text(artifact.get("decision_at")) is None:
        errors.append("decision_at_invalid")

    rows = artifact.get("rows")
    if not isinstance(rows, list):
        rows = []
        errors.append("rows_invalid")
    codes = [row.get("fund_code") for row in rows if isinstance(row, Mapping)]
    valid_codes = [code for code in codes if isinstance(code, str)]
    if any(code is not None and not isinstance(code, str) for code in codes):
        errors.append("fund_code_type_invalid")
    if len(valid_codes) != len(set(valid_codes)):
        errors.append("fund_code_duplicated")

    scored: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            errors.append("row_invalid")
            continue
        row_material = dict(row)
        row_hash = row_material.pop("row_hash", None)
        try:
            expected_row_hash = _hash_payload(row_material)
        except (TypeError, ValueError, OverflowError):
            expected_row_hash = None
            errors.append("row_hash_material_invalid")
        if row_hash != expected_row_hash:
            errors.append("row_hash_invalid")
        if row.get("status") != "scored":
            continue
        scored.append(row)
        hard_gate = row.get("hard_gate")
        if not isinstance(hard_gate, Mapping):
            hard_gate = {}
            errors.append("hard_gate_invalid")
        components = row.get("components")
        if not isinstance(components, Mapping) or set(components) != set(REQUIRED_COMPONENTS):
            errors.append("scored_components_invalid")
            continue
        if any(
            not isinstance(components.get(key), Mapping)
            or components[key].get("status") != "available"
            or _bounded_number(components[key].get("score"), 0.0, 100.0) is None
            or _bounded_number(components[key].get("confidence"), 0.0, 1.0) is None
            for key in REQUIRED_COMPONENTS
        ):
            errors.append("scored_component_unavailable")
            continue
        if version_pair == current_pair:
            benchmark = components["benchmark_consistency"]
            benchmark_evidence = (
                benchmark.get("evidence")
                if isinstance(benchmark.get("evidence"), Mapping)
                else {}
            )
            if (
                benchmark.get("basis")
                != "verified_contract_type_cross_section_percentile_mean"
                or benchmark_evidence.get("policy_version")
                != BENCHMARK_POLICY_VERSION
            ):
                errors.append("benchmark_component_policy_invalid")
            cost = components["cost_efficiency"]
            cost_evidence = (
                cost.get("evidence")
                if isinstance(cost.get("evidence"), Mapping)
                else {}
            )
            if (
                cost_evidence.get("fee_evidence_policy_version")
                != FEE_EVIDENCE_POLICY_VERSION
                or cost_evidence.get("fee_evidence_basis")
                != "public_standard_fee_upper_bound"
                or cost_evidence.get("actual_channel_fee_available") is not False
            ):
                errors.append("cost_component_evidence_policy_invalid")
        base = sum(
            COMPONENT_WEIGHTS[key] * float(components[key]["score"])
            for key in REQUIRED_COMPONENTS
        )
        confidence = sum(
            COMPONENT_WEIGHTS[key] * float(components[key]["confidence"])
            for key in REQUIRED_COMPONENTS
        )
        expected = round(base * confidence, 4)
        if not _close(row.get("base_component_score"), round(base, 4)):
            errors.append("base_component_score_mismatch")
        if not _close(row.get("data_confidence"), round(confidence, 4)):
            errors.append("data_confidence_mismatch")
        if not _close(row.get("score"), expected):
            errors.append("score_formula_mismatch")
        if hard_gate.get("eligible") is not True:
            errors.append("scored_hard_gate_ineligible")

    expected_ranks = list(range(1, len(scored) + 1))
    actual_ranks = sorted(
        int(row["shadow_rank"])
        for row in scored
        if isinstance(row.get("shadow_rank"), int)
        and not isinstance(row.get("shadow_rank"), bool)
    )
    if actual_ranks != expected_ranks:
        errors.append("shadow_rank_invalid")
    ranked = sorted(
        scored,
        key=_score_sort_key,
    )
    if [row.get("fund_code") for row in ranked] != [
        row.get("fund_code")
        for row in sorted(
            scored,
            key=lambda row: (
                row.get("shadow_rank")
                if isinstance(row.get("shadow_rank"), int)
                and not isinstance(row.get("shadow_rank"), bool)
                else 10**9
            ),
        )
    ]:
        errors.append("shadow_rank_order_invalid")

    try:
        expected_hash = _hash_payload(_artifact_hash_material(artifact))
    except (TypeError, ValueError, OverflowError):
        expected_hash = None
        errors.append("snapshot_hash_material_invalid")
    if artifact.get("snapshot_hash") != expected_hash:
        errors.append("snapshot_hash_invalid")
    return {
        "status": "valid" if not errors else "invalid",
        "shadow_evaluable": not errors and len(scored) >= 2,
        "error_codes": sorted(set(errors)),
    }


def build_decision_score_shadow_digest(
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate persisted shadow coverage without exposing candidate details."""

    all_artifacts: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for report in reports:
        if not isinstance(report, Mapping):
            continue
        facts = report.get("discovery_facts")
        if not isinstance(facts, Mapping):
            continue
        artifact = facts.get("decision_score_shadow")
        if isinstance(artifact, Mapping):
            all_artifacts.append((report, artifact))

    model_version_counts: dict[str, int] = {}
    for _report, artifact in all_artifacts:
        version = str(artifact.get("model_version") or "unknown")
        model_version_counts[version] = model_version_counts.get(version, 0) + 1
    artifacts = [
        item
        for item in all_artifacts
        if item[1].get("schema_version") == DECISION_SCORE_SHADOW_SCHEMA_VERSION
        and item[1].get("model_version") == DECISION_SCORE_MODEL_VERSION
    ]

    candidate_count = 0
    scored_count = 0
    valid_count = 0
    evaluable_count = 0
    top_k_changed_count = 0
    status_counts: dict[str, int] = {}
    missing_counts = {key: 0 for key in REQUIRED_COMPONENTS}
    for _report, artifact in artifacts:
        coverage = artifact.get("coverage") if isinstance(artifact.get("coverage"), Mapping) else {}
        candidate_count += _count(coverage.get("candidate_count"))
        scored_count += _count(coverage.get("scored_count"))
        for key in REQUIRED_COMPONENTS:
            values = coverage.get("missing_component_counts")
            if isinstance(values, Mapping):
                missing_counts[key] += _count(values.get(key))
        validation = validate_decision_score_shadow(artifact)
        valid_count += int(validation.get("status") == "valid")
        evaluable_count += int(validation.get("shadow_evaluable") is True)
        top_k_changed_count += int(artifact.get("top_k_changed") is True)
        status = str(artifact.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    latest: dict[str, Any] | None = None
    if artifacts:
        report, artifact = max(
            artifacts,
            key=lambda item: str(
                item[0].get("created_at") or item[1].get("decision_at") or ""
            ),
        )
        latest_coverage = artifact.get("coverage") if isinstance(artifact.get("coverage"), Mapping) else {}
        latest_validation = validate_decision_score_shadow(artifact)
        latest = {
            "report_id": report.get("id"),
            "created_at": report.get("created_at"),
            "decision_at": artifact.get("decision_at"),
            "model_version": artifact.get("model_version"),
            "status": artifact.get("status"),
            "validation_status": latest_validation.get("status"),
            "shadow_evaluable": latest_validation.get("shadow_evaluable") is True,
            "candidate_count": _count(latest_coverage.get("candidate_count")),
            "scored_count": _count(latest_coverage.get("scored_count")),
            "scored_coverage_percent": (
                _bounded_number(
                    latest_coverage.get("scored_coverage_percent"),
                    0.0,
                    100.0,
                )
                or 0.0
            ),
            "top_k_changed": artifact.get("top_k_changed") is True,
            "snapshot_hash": artifact.get("snapshot_hash"),
        }

    return {
        "schema_version": "decision_score_shadow_digest.v2",
        "mode": DECISION_SCORE_MODE,
        "current_model_version": DECISION_SCORE_MODEL_VERSION,
        "automatic_promotion_allowed": False,
        "report_count": len(reports),
        "artifact_count": len(artifacts),
        "total_artifact_count": len(all_artifacts),
        "legacy_artifact_count": len(all_artifacts) - len(artifacts),
        "model_version_counts": dict(sorted(model_version_counts.items())),
        "valid_artifact_count": valid_count,
        "shadow_evaluable_report_count": evaluable_count,
        "top_k_changed_report_count": top_k_changed_count,
        "candidate_count": candidate_count,
        "scored_count": scored_count,
        "scored_coverage_percent": (
            round(scored_count / candidate_count * 100.0, 4)
            if candidate_count
            else 0.0
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "missing_component_counts": missing_counts,
        "latest": latest,
    }


def _factor_rows_by_code(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for row in payload.get("holdings") or []:
        if not isinstance(row, Mapping):
            continue
        code = _fund_code(row.get("fund_code"))
        if code is not None:
            rows[code] = row
    return rows


def _factor_component(
    row: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    status = payload.get("ic_status") if isinstance(payload.get("ic_status"), Mapping) else {}
    if (
        payload.get("available") is not True
        or str(status.get("state") or "") != "available"
        or status.get("available", True) is False
        or status.get("stale") is True
        or status.get("confidence_eligible") is not True
        or _count(status.get("schema_version")) < 3
    ):
        return _missing_component("factor_ic_not_decision_eligible")
    if not isinstance(row, Mapping):
        return _missing_component("factor_target_not_covered")
    qualification = row.get("execution_qualification")
    qualified_keys = [
        str(key)
        for key in row.get("execution_qualified_factor_keys") or []
        if str(key).strip()
    ]
    if (
        row.get("execution_qualified") is not True
        or not isinstance(qualification, Mapping)
        or qualification.get("status") != "qualified"
        or qualification.get("method") != EXECUTION_QUALIFICATION_METHOD
        or not qualified_keys
    ):
        return _missing_component("factor_statistical_or_economic_gate_not_qualified")
    if row.get("typed_factor_applicable") is not True or _score(row.get("typed_factor_score")) is None:
        return _missing_component("type_specific_factor_not_complete")
    score = _score(row.get("composite_score"))
    if score is None:
        return _missing_component("factor_composite_score_invalid")

    point_in_time = status.get("point_in_time") if isinstance(status.get("point_in_time"), Mapping) else {}
    full_nav_pit = bool(
        point_in_time.get("point_in_time_scope") == "nav_observation_pit"
        and point_in_time.get("nav_revision_pit") is True
    )
    return _available_component(
        score=score,
        raw_value=score,
        confidence=1.0 if full_nav_pit else 0.8,
        basis="pit_v3_qualified_composite_with_type_factor",
        evidence={
            "snapshot_id": status.get("snapshot_id"),
            "schema_version": status.get("schema_version"),
            "point_in_time_scope": point_in_time.get("point_in_time_scope"),
            "nav_revision_pit": point_in_time.get("nav_revision_pit"),
            "qualified_factor_keys": qualified_keys,
            "typed_used_keys": list(row.get("typed_used_keys") or []),
            "target_feature_as_of": row.get("target_feature_as_of"),
            "target_feature_observed_at": row.get("target_feature_observed_at"),
            "target_feature_source": row.get("target_feature_source"),
        },
    )


def _benchmark_component_v2(
    candidate: Mapping[str, Any],
    *,
    peer_profile: str,
    decision_at: str | None,
) -> dict[str, Any]:
    if peer_profile not in _FORMAL_EXCESS_PROFILES | _TRACKING_PROFILES:
        return _missing_component("benchmark_consistency_unsupported_for_peer_profile")
    research = (
        candidate.get("benchmark_metrics")
        if isinstance(candidate.get("benchmark_metrics"), Mapping)
        else {}
    )
    if research.get("schema_version") != "fund_benchmark_research.v1":
        return _missing_component("benchmark_research_schema_invalid")
    if not _benchmark_snapshot_hash_valid(research):
        return _missing_component("benchmark_research_snapshot_hash_invalid")
    if decision_at is None or _decision_text(research.get("decision_at")) != decision_at:
        return _missing_component("benchmark_research_decision_at_mismatch")
    if research.get("status") != "qualified" or research.get("qualified") is not True:
        return _missing_component("benchmark_research_not_qualified")
    comparison_policy = (
        research.get("comparison_policy")
        if isinstance(research.get("comparison_policy"), Mapping)
        else {}
    )
    if (
        research.get("descriptive_only") is not True
        or research.get("execution_tilt_eligible") is not False
        or comparison_policy.get("formal_excess_requires_verified_contract") is not True
        or comparison_policy.get("tracking_reference_never_formal_excess") is not True
    ):
        return _missing_component("benchmark_research_policy_contract_invalid")

    ranking_metrics: list[dict[str, Any]] = []
    if peer_profile in _FORMAL_EXCESS_PROFILES:
        if (
            research.get("comparison_role") != "formal_excess"
            or research.get("formal_excess_eligible") is not True
            or research.get("contract_verification_kind") != "verified_fund_contract"
        ):
            return _missing_component("formal_contract_benchmark_unavailable")
        horizons = (
            research.get("horizons")
            if isinstance(research.get("horizons"), Mapping)
            else {}
        )
        one_year = (
            horizons.get("1y")
            if isinstance(horizons.get("1y"), Mapping)
            else {}
        )
        excess = _finite_number(one_year.get("formal_excess_return_percent"))
        if one_year.get("status") != "available" or excess is None:
            return _missing_component("formal_contract_excess_1y_unavailable")
        rolling = (
            research.get("rolling_comparison")
            if isinstance(research.get("rolling_comparison"), Mapping)
            else {}
        )
        win_rate = _bounded_number(
            rolling.get("formal_excess_win_rate_percent"),
            0.0,
            100.0,
        )
        if rolling.get("status") != "available" or win_rate is None:
            return _missing_component("formal_contract_rolling_win_rate_unavailable")
        ranking_metrics.extend(
            (
                {
                    "key": "formal_excess_return_1y_percent",
                    "value": round(excess, 6),
                    "higher_is_better": True,
                },
                {
                    "key": "formal_excess_win_rate_percent",
                    "value": round(win_rate, 6),
                    "higher_is_better": True,
                },
            )
        )

    if peer_profile in _TRACKING_PROFILES:
        expected_role = (
            "tracking_reference" if peer_profile == "passive_index" else "formal_excess"
        )
        if research.get("comparison_role") != expected_role:
            return _missing_component("type_specific_tracking_role_invalid")
        tracking = (
            research.get("tracking_metrics")
            if isinstance(research.get("tracking_metrics"), Mapping)
            else {}
        )
        tracking_error = _nonnegative_number(
            tracking.get("tracking_error_annualized_percent")
        )
        tracking_difference = _finite_number(
            tracking.get("tracking_difference_percent")
        )
        if (
            tracking.get("applicable") is not True
            or tracking.get("available") is not True
            or tracking_error is None
            or tracking_difference is None
        ):
            return _missing_component("exact_tracking_metrics_unavailable")
        ranking_metrics.extend(
            (
                {
                    "key": "tracking_error_annualized_percent",
                    "value": round(tracking_error, 6),
                    "higher_is_better": False,
                },
                {
                    "key": "absolute_tracking_difference_percent",
                    "value": round(abs(tracking_difference), 6),
                    "higher_is_better": False,
                },
            )
        )

    if not ranking_metrics:
        return _missing_component("type_specific_benchmark_metrics_unavailable")
    return {
        "status": "available",
        "score": None,
        "raw_value": ranking_metrics[0]["value"],
        "confidence": 0.95,
        "basis": "verified_contract_type_cross_section_percentile_mean",
        "reason_codes": [],
        "evidence": {
            "policy_version": BENCHMARK_POLICY_VERSION,
            "peer_profile": peer_profile,
            "mapping_id": research.get("mapping_id"),
            "benchmark_code": research.get("benchmark_code"),
            "benchmark_name": research.get("benchmark_name"),
            "comparison_role": research.get("comparison_role"),
            "contract_verification_kind": research.get("contract_verification_kind"),
            "benchmark_snapshot_hash": research.get("snapshot_hash"),
            "ranking_metrics": ranking_metrics,
        },
    }


def _assign_benchmark_percentiles(rows: Sequence[dict[str, Any]]) -> None:
    populations: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        component = row["components"]["benchmark_consistency"]
        evidence = component.get("evidence") if isinstance(component.get("evidence"), Mapping) else {}
        metrics = evidence.get("ranking_metrics") if isinstance(evidence.get("ranking_metrics"), list) else []
        if row.get("hard_gate", {}).get("eligible") is not True or component.get("status") != "available":
            continue
        for metric in metrics:
            if not isinstance(metric, Mapping):
                continue
            key = str(metric.get("key") or "")
            value = _finite_number(metric.get("value"))
            if key and value is not None:
                populations.setdefault((str(row.get("peer_profile") or "unknown"), key), []).append(value)

    for row in rows:
        component = row["components"]["benchmark_consistency"]
        evidence = component.get("evidence") if isinstance(component.get("evidence"), dict) else {}
        metrics = evidence.get("ranking_metrics") if isinstance(evidence.get("ranking_metrics"), list) else []
        if row.get("hard_gate", {}).get("eligible") is not True or component.get("status") != "available":
            continue
        scores: list[float] = []
        sample_counts: list[int] = []
        ranked_metrics: list[dict[str, Any]] = []
        for metric in metrics:
            if not isinstance(metric, Mapping):
                continue
            key = str(metric.get("key") or "")
            value = _finite_number(metric.get("value"))
            population = populations.get((str(row.get("peer_profile") or "unknown"), key), [])
            if not key or value is None or not population:
                scores = []
                break
            higher_is_better = metric.get("higher_is_better") is True
            favorable = sum(
                item < value if higher_is_better else item > value
                for item in population
            )
            tied = sum(
                math.isclose(item, value, rel_tol=0.0, abs_tol=1e-12)
                for item in population
            )
            percentile = round((favorable + 0.5 * tied) / len(population) * 100.0, 4)
            scores.append(percentile)
            sample_counts.append(len(population))
            ranked_metrics.append(
                {
                    **dict(metric),
                    "percentile": percentile,
                    "sample_count": len(population),
                }
            )
        if scores:
            component["score"] = round(sum(scores) / len(scores), 4)
            cross_section_sample_count = min(sample_counts)
            if cross_section_sample_count >= 5:
                component["confidence"] = 0.95
            elif cross_section_sample_count >= 2:
                component["confidence"] = 0.75
            else:
                component["confidence"] = 0.5
            evidence["ranking_metrics"] = ranked_metrics
            evidence["cross_section_sample_count"] = cross_section_sample_count


def _benchmark_snapshot_hash_valid(value: Mapping[str, Any]) -> bool:
    supplied = value.get("snapshot_hash")
    if not isinstance(supplied, str) or len(supplied) != 64:
        return False
    material = dict(value)
    material.pop("snapshot_hash", None)
    expected = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return supplied == expected


def _peer_percentile_component(
    candidate: Mapping[str, Any],
    *,
    peer_profile: str,
    metric_keys: Sequence[str] | None,
    component: str,
) -> dict[str, Any]:
    if not metric_keys:
        return _missing_component(f"{component}_unsupported_for_peer_profile")
    peer = candidate.get("peer_rank") if isinstance(candidate.get("peer_rank"), Mapping) else {}
    if (
        peer.get("qualified") is not True
        or peer.get("status") != "qualified"
        or peer.get("research_shadow_rerank_eligible") is not True
        or str(peer.get("metric_profile") or "") != peer_profile
    ):
        return _missing_component("peer_rank_not_shadow_qualified")
    metrics = peer.get("metrics") if isinstance(peer.get("metrics"), Mapping) else {}
    values: list[float] = []
    metric_evidence: dict[str, Any] = {}
    for key in metric_keys:
        metric = metrics.get(key) if isinstance(metrics.get(key), Mapping) else {}
        percentile = _score(metric.get("percentile"))
        if (
            metric.get("applicable") is not True
            or metric.get("available") is not True
            or metric.get("qualified") is not True
            or percentile is None
        ):
            return _missing_component(f"peer_metric_{key}_unavailable")
        values.append(percentile)
        metric_evidence[key] = {
            "percentile": percentile,
            "sample_count": metric.get("sample_count"),
            "coverage_rate": metric.get("coverage_rate"),
            "peer_sample_hash": metric.get("peer_sample_hash"),
        }
    score = round(sum(values) / len(values), 4)
    return _available_component(
        score=score,
        raw_value=score,
        confidence=0.9,
        basis="type_specific_peer_universe_percentile_mean",
        evidence={
            "peer_profile": peer_profile,
            "peer_group": peer.get("peer_group"),
            "metric_registry_version": peer.get("metric_registry_version"),
            "peer_rank_snapshot_hash": peer.get("snapshot_hash"),
            "metrics": metric_evidence,
        },
    )


def _diversification_component(
    candidate: Mapping[str, Any],
    *,
    portfolio_gap: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    sector = _sector_label(candidate)
    denominator = _positive_number(portfolio_gap.get("weight_denominator_yuan"))
    limit_percent = _bounded_number(profile.get("concentration_limit_percent"), 0.0, 100.0)
    held_sectors = portfolio_gap.get("held_sectors")
    if sector is None:
        return _missing_component("candidate_sector_unavailable")
    if denominator is None or limit_percent is None or limit_percent <= 0:
        return _missing_component("portfolio_concentration_context_unavailable")
    if portfolio_gap.get("sector_exposure_complete") is not True:
        return _missing_component("portfolio_sector_exposure_incomplete")
    if not isinstance(held_sectors, list):
        return _missing_component("portfolio_sector_exposure_unavailable")
    exposure = 0.0
    for row in held_sectors:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("sector_name") or "").strip() != sector:
            continue
        value = _nonnegative_number(row.get("amount"))
        if value is None:
            return _missing_component("portfolio_sector_exposure_invalid")
        exposure += value
    limit_amount = denominator * limit_percent / 100.0
    if limit_amount <= 0:
        return _missing_component("portfolio_sector_limit_invalid")
    utilization = min(exposure / limit_amount, 1.0)
    score = round((1.0 - utilization) * 100.0, 4)
    return _available_component(
        score=score,
        raw_value=round(exposure, 2),
        confidence=0.75,
        basis="pre_decision_sector_capacity_proxy",
        evidence={
            "sector_label": sector,
            "existing_sector_exposure_yuan": round(exposure, 2),
            "concentration_denominator_yuan": round(denominator, 2),
            "concentration_limit_percent": round(limit_percent, 4),
            "sector_limit_yuan": round(limit_amount, 2),
            "warning": "not_correlation_or_marginal_risk_contribution",
        },
    )


def _cost_component(
    tradeability: Mapping[str, Any] | None,
    *,
    tradeability_gate: Mapping[str, Any],
    minimum_holding_days: int | None,
) -> tuple[dict[str, Any], bool]:
    amount = _positive_number(tradeability_gate.get("effective_initial_min_purchase_yuan"))
    if amount is None:
        return _missing_component("cost_probe_amount_unavailable"), True
    if not isinstance(minimum_holding_days, int) or isinstance(minimum_holding_days, bool) or minimum_holding_days <= 0:
        return _missing_component("strategy_minimum_holding_days_unavailable"), True
    assessment = assess_tradeability_for_amount(
        tradeability,
        amount_yuan=amount,
        hold_horizon=f"DecisionScore v2 最短持有期 {minimum_holding_days} 天",
        minimum_holding_days=minimum_holding_days,
    )
    if assessment.get("executable") is not True:
        return (
            _missing_component(
                "holding_period_cost_assessment_not_executable",
                evidence={
                    "probe_amount_yuan": amount,
                    "minimum_holding_days": minimum_holding_days,
                    "block_reasons": list(assessment.get("block_reasons") or []),
                },
            ),
            True,
        )
    raw_cost = _nonnegative_number(
        assessment.get("estimated_total_cost_upper_bound_percent")
    )
    if raw_cost is None:
        return _missing_component("holding_period_cost_upper_bound_unavailable"), True
    return (
        {
            "status": "available",
            "score": None,
            "raw_value": round(raw_cost, 6),
            "confidence": 0.75,
            "basis": "cross_sectional_lower_cost_percentile",
            "reason_codes": [],
            "evidence": {
                "probe_amount_yuan": round(amount, 2),
                "minimum_holding_days": minimum_holding_days,
                "fee_status": assessment.get("fee_status"),
                "fee_components_complete": assessment.get("fee_components_complete"),
                "estimated_total_cost_upper_bound_percent": round(raw_cost, 6),
                "fee_evidence_policy_version": FEE_EVIDENCE_POLICY_VERSION,
                "fee_evidence_basis": "public_standard_fee_upper_bound",
                "actual_channel_fee_available": False,
                "actual_channel_fee_reason": "candidate_channel_transaction_not_observed",
                "source_ids": list((tradeability or {}).get("source_ids") or []),
                "checked_at": (tradeability or {}).get("checked_at"),
                "fee_checked_at": (tradeability or {}).get("fee_checked_at"),
            },
        },
        False,
    )


def _assign_cost_percentiles(rows: Sequence[dict[str, Any]]) -> None:
    by_profile: dict[str, list[float]] = {}
    for row in rows:
        component = row["components"]["cost_efficiency"]
        raw = _nonnegative_number(component.get("raw_value"))
        if (
            row.get("hard_gate", {}).get("eligible") is True
            and component.get("status") == "available"
            and raw is not None
        ):
            by_profile.setdefault(str(row.get("peer_profile") or "unknown"), []).append(raw)
    for row in rows:
        component = row["components"]["cost_efficiency"]
        raw = _nonnegative_number(component.get("raw_value"))
        population = by_profile.get(str(row.get("peer_profile") or "unknown"), [])
        if (
            row.get("hard_gate", {}).get("eligible") is not True
            or component.get("status") != "available"
            or raw is None
            or not population
        ):
            continue
        worse = sum(value > raw for value in population)
        tied = sum(math.isclose(value, raw, rel_tol=0.0, abs_tol=1e-12) for value in population)
        component["score"] = round((worse + 0.5 * tied) / len(population) * 100.0, 4)
        component["evidence"]["peer_profile_cost_sample_count"] = len(population)


def _calculate_scores(rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        components = row["components"]
        missing = [
            key
            for key in REQUIRED_COMPONENTS
            if components[key].get("status") != "available"
            or _score(components[key].get("score")) is None
        ]
        row["missing_components"] = missing
        if row["hard_gate"]["eligible"] is not True:
            row["status"] = "hard_gate_blocked"
            continue
        if missing:
            row["status"] = "insufficient_evidence"
            continue
        base = sum(
            COMPONENT_WEIGHTS[key] * float(components[key]["score"])
            for key in REQUIRED_COMPONENTS
        )
        confidence = sum(
            COMPONENT_WEIGHTS[key] * float(components[key]["confidence"])
            for key in REQUIRED_COMPONENTS
        )
        row["base_component_score"] = round(base, 4)
        row["data_confidence"] = round(confidence, 4)
        row["score"] = round(base * confidence, 4)
        row["status"] = "scored"


def _coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    scored = sum(row.get("status") == "scored" for row in rows)
    hard_blocked = sum(row.get("status") == "hard_gate_blocked" for row in rows)
    missing_counts = {
        key: sum(key in (row.get("missing_components") or []) for row in rows)
        for key in REQUIRED_COMPONENTS
    }
    return {
        "candidate_count": total,
        "scored_count": scored,
        "hard_gate_blocked_count": hard_blocked,
        "insufficient_evidence_count": total - scored - hard_blocked,
        "scored_coverage_percent": round(scored / total * 100.0, 4) if total else 0.0,
        "missing_component_counts": missing_counts,
    }


def _candidate_audit_ref(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    validation = value.get("validation")
    return {
        "schema_version": value.get("schema_version"),
        "snapshot_hash": value.get("snapshot_hash"),
        "decision_at": value.get("decision_at"),
        "validation_status": (
            validation.get("status") if isinstance(validation, Mapping) else None
        ),
    }


def _available_component(
    *,
    score: float,
    raw_value: float,
    confidence: float,
    basis: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": "available",
        "score": round(score, 4),
        "raw_value": round(raw_value, 6),
        "confidence": round(confidence, 4),
        "basis": basis,
        "reason_codes": [],
        "evidence": dict(evidence),
    }


def _missing_component(
    reason: str,
    *,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "score": None,
        "raw_value": None,
        "confidence": 0.0,
        "basis": None,
        "reason_codes": [reason],
        "evidence": dict(evidence or {}),
    }


def _peer_profile(candidate: Mapping[str, Any]) -> str:
    peer = candidate.get("peer_rank") if isinstance(candidate.get("peer_rank"), Mapping) else {}
    value = str(peer.get("metric_profile") or "").strip()
    return value or "unknown"


def _sector_label(candidate: Mapping[str, Any]) -> str | None:
    value = str(candidate.get("sector_label") or candidate.get("sector_name") or "").strip()
    return value or None


def _fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > _FUND_CODE_LENGTH:
        return None
    code = text.zfill(_FUND_CODE_LENGTH)
    return None if code == "000000" else code


def _decision_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.isoformat()


def _score(value: Any) -> float | None:
    return _bounded_number(value, 0.0, 100.0)


def _positive_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _bounded_number(value: Any, lower: float, upper: float) -> float | None:
    parsed = _finite_number(value)
    if parsed is None or parsed < lower or parsed > upper:
        return None
    return parsed


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _close(value: Any, expected: float) -> bool:
    parsed = _finite_number(value)
    return parsed is not None and math.isclose(parsed, expected, rel_tol=0.0, abs_tol=1e-4)


def _score_sort_key(row: Mapping[str, Any]) -> tuple[float, str]:
    score = _score(row.get("score"))
    return (-(score if score is not None else -1.0), str(row.get("fund_code") or ""))


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if parsed >= 0 else 0


def _artifact_hash_material(value: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(value)
    material.pop("snapshot_hash", None)
    material.pop("validation", None)
    return material


def _hash_payload(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "COMPONENT_WEIGHTS",
    "DECISION_SCORE_MODEL_VERSION",
    "DECISION_SCORE_SHADOW_SCHEMA_VERSION",
    "attach_decision_score_shadow",
    "build_decision_score_shadow_digest",
    "build_decision_score_shadow",
    "validate_decision_score_shadow",
]
