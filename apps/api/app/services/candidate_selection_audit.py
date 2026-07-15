"""Point-in-time candidate-selection audit and offline evaluation contracts.

This module is intentionally independent from the live candidate-pool pipeline.
It can be wired to that pipeline once every stage can provide immutable source and
point-in-time references.  The v2 contract is strict: incomplete lineage is an
invalid decision audit, not evidence that may silently participate in ranking.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


V1_SCHEMA_VERSION = "discovery_candidate_selection_audit.v1"
V2_SCHEMA_VERSION = "discovery_candidate_selection_audit.v2"
NORMALIZED_SCHEMA_VERSION = "candidate_selection_audit.normalized.v1"
EVALUATION_SCHEMA_VERSION = "candidate_selection_evaluation.v1"
STAGE_ORDER = ("recall", "gate", "prescreen", "final")
PIPELINE_SELECTION_POLICY_VERSION = "discovery_candidate_selection.v2"
PIPELINE_STAGE_VERSIONS = {
    "recall": "discovery_candidate_recall.v1",
    "gate": "discovery_candidate_gate.v1",
    "prescreen": "discovery_candidate_prescreen.v1",
    "final": "discovery_candidate_final_selection.v1",
}

_FUND_CODE_RE = re.compile(r"^[0-9]{6}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MISSING = object()


class CandidateSelectionAuditError(ValueError):
    """Raised when a caller requires a decision-eligible audit but gets none."""

    def __init__(self, validation: Mapping[str, Any]):
        self.validation = dict(validation)
        codes = [str(item.get("code")) for item in validation.get("errors", [])]
        super().__init__(
            "candidate selection audit is not decision eligible"
            + (f": {', '.join(codes)}" if codes else "")
        )


def build_candidate_selection_audit_v2(
    *,
    decision_at: datetime | str,
    recall_candidates: Sequence[Mapping[str, Any]],
    gate_candidates: Sequence[Mapping[str, Any]],
    prescreen_candidates: Sequence[Mapping[str, Any]],
    final_candidates: Sequence[Mapping[str, Any]],
    versions: Mapping[str, Any],
    stage_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, self-validating full-funnel audit snapshot.

    Candidate ranks default to the authoritative input order only when an
    explicit rank is absent; ``rank_basis`` makes that fallback visible.  No
    score, reason, source, PIT timestamp, or outcome is inferred.
    """

    decision_text = _datetime_text(decision_at)
    supplied_versions = deepcopy(dict(versions))
    supplied_versions.setdefault("audit_contract", V2_SCHEMA_VERSION)
    contexts = {
        stage: deepcopy(dict(stage_contexts.get(stage) or {}))
        for stage in STAGE_ORDER
    }
    inputs = {
        "recall": list(recall_candidates),
        "gate": list(gate_candidates),
        "prescreen": list(prescreen_candidates),
        "final": list(final_candidates),
    }

    construction_errors: list[dict[str, str]] = []
    records_by_stage: dict[str, dict[str, dict[str, Any]]] = {}
    raw_by_stage: dict[str, dict[str, Mapping[str, Any]]] = {}
    code_order: list[str] = []

    for stage in STAGE_ORDER:
        context = contexts[stage]
        stage_version = context.get("version") or supplied_versions.get(stage)
        context_sources = _as_ref_list(context.get("source_refs"))
        context_pit = _as_ref_list(context.get("pit_refs"))
        stage_records: dict[str, dict[str, Any]] = {}
        stage_raw: dict[str, Mapping[str, Any]] = {}
        for ordinal, candidate in enumerate(inputs[stage], start=1):
            if not isinstance(candidate, Mapping):
                construction_errors.append(
                    _issue(
                        "candidate_not_object",
                        f"stages.{stage}.candidates[{ordinal - 1}]",
                        "candidate must be an object",
                    )
                )
                continue
            code = _normalize_code(candidate.get("fund_code"))
            if code in stage_records:
                construction_errors.append(
                    _issue(
                        "duplicate_stage_candidate",
                        f"stages.{stage}.candidates[{ordinal - 1}].fund_code",
                        f"fund {code!r} appears more than once in {stage}",
                    )
                )
                continue
            rank_value = _candidate_rank(candidate, stage)
            rank_basis = "explicit" if rank_value is not _MISSING else "input_order"
            rank = ordinal if rank_value is _MISSING else rank_value
            score = _candidate_score(candidate, stage)
            record_sources = _merge_refs(
                context_sources, _as_ref_list(candidate.get("source_refs"))
            )
            record_pit = _merge_refs(
                context_pit, _as_ref_list(candidate.get("pit_refs"))
            )
            explicit_evidence_status = candidate.get(
                "audit_evidence_status",
                context.get("evidence_status"),
            )
            evidence_status = (
                str(explicit_evidence_status)
                if explicit_evidence_status not in (None, "")
                else "complete"
                if record_sources and record_pit
                else "unavailable"
            )
            evidence_issues = _text_list(
                candidate.get("audit_evidence_issues", context.get("evidence_issues"))
            )
            record: dict[str, Any] = {
                "present": True,
                "rank": scoreless_int(rank),
                "rank_basis": rank_basis,
                "score": None if score is _MISSING else deepcopy(score),
                "score_status": "unavailable" if score is _MISSING else "available",
                "score_components": deepcopy(
                    candidate.get("score_components")
                    or candidate.get("quality_score_components")
                    or {}
                ),
                "gates": _candidate_gates(candidate),
                "reason_codes": _reason_codes(candidate),
                "source_refs": record_sources,
                "pit_refs": record_pit,
                "evidence_status": evidence_status,
                "evidence_issues": evidence_issues,
                "version": deepcopy(stage_version),
            }
            record["candidate_snapshot_hash"] = _hash_or_none(record)
            stage_records[code] = record
            stage_raw[code] = candidate
            if code not in code_order:
                code_order.append(code)
        records_by_stage[stage] = stage_records
        raw_by_stage[stage] = stage_raw

    rows: list[dict[str, Any]] = []
    for code in code_order:
        identity: dict[str, Any] = {
            "fund_code": code,
            "fund_name": None,
            "sector_label": None,
            "share_family_key": None,
        }
        for stage in STAGE_ORDER:
            raw = raw_by_stage[stage].get(code)
            if raw is None:
                continue
            for key in ("fund_name", "sector_label", "share_family_key"):
                value = raw.get(key)
                if value not in (None, ""):
                    identity[key] = deepcopy(value)
            share_family = raw.get("share_family")
            if (
                identity["share_family_key"] in (None, "")
                and isinstance(share_family, Mapping)
            ):
                identity["share_family_key"] = deepcopy(share_family.get("family_key"))

        stage_records: dict[str, dict[str, Any]] = {}
        all_reasons: list[str] = []
        for stage in STAGE_ORDER:
            present = records_by_stage[stage].get(code)
            if present is None:
                stage_records[stage] = _absent_stage_record()
                continue
            stage_records[stage] = deepcopy(present)
            all_reasons.extend(present["reason_codes"])
        final_record = stage_records["final"]
        rows.append(
            {
                **identity,
                "stage_records": stage_records,
                "selected": bool(final_record["present"]),
                "final_rank": final_record["rank"] if final_record["present"] else None,
                "reason_codes": list(dict.fromkeys(all_reasons)),
            }
        )

    stages: dict[str, dict[str, Any]] = {}
    for stage in STAGE_ORDER:
        material = [
            {"fund_code": code, "stage_record": records_by_stage[stage][code]}
            for code in records_by_stage[stage]
        ]
        material.sort(key=_stage_material_sort_key)
        source_union = _merge_refs(
            *[record.get("source_refs", []) for record in records_by_stage[stage].values()]
        )
        pit_union = _merge_refs(
            *[record.get("pit_refs", []) for record in records_by_stage[stage].values()]
        )
        stages[stage] = {
            "version": deepcopy(contexts[stage].get("version") or supplied_versions.get(stage)),
            "candidate_count": len(inputs[stage]),
            "source_refs": source_union,
            "pit_refs": pit_union,
            "rows_hash": _hash_or_none(material),
            "evidence_complete": all(
                record.get("evidence_status") == "complete"
                and not record.get("evidence_issues")
                for record in records_by_stage[stage].values()
            ),
            "evidence_issue_count": sum(
                len(record.get("evidence_issues") or [])
                + int(record.get("evidence_status") != "complete")
                for record in records_by_stage[stage].values()
            ),
            "scope": deepcopy(contexts[stage].get("scope")),
        }

    audit: dict[str, Any] = {
        "schema_version": V2_SCHEMA_VERSION,
        "decision_at": decision_text,
        "versions": supplied_versions,
        "stage_order": list(STAGE_ORDER),
        "stage_counts": {stage: len(inputs[stage]) for stage in STAGE_ORDER},
        "stages": stages,
        "rows": rows,
        "hash_algorithm": "sha256",
        "canonicalization": "json_utf8_sort_keys_v1",
        "construction_errors": construction_errors,
    }
    audit["snapshot_hash"] = _hash_or_none(_snapshot_material(audit))
    audit["validation"] = validate_candidate_selection_audit(audit)
    return audit


def validate_candidate_selection_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a current v1 or full-funnel v2 audit without mutating it."""

    if not isinstance(audit, Mapping):
        return _validation(
            schema_version=None,
            errors=[_issue("audit_not_object", "$", "audit must be an object")],
        )
    schema = str(audit.get("schema_version") or "")
    if schema == V2_SCHEMA_VERSION:
        return _validate_v2(audit)
    if schema == V1_SCHEMA_VERSION:
        return _validate_v1(audit)
    return _validation(
        schema_version=schema or None,
        errors=[
            _issue(
                "unsupported_schema_version",
                "schema_version",
                f"unsupported candidate-selection audit schema {schema!r}",
            )
        ],
    )


def require_valid_candidate_selection_audit(
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Return validation or raise when the audit cannot support a decision."""

    validation = validate_candidate_selection_audit(audit)
    if not validation.get("decision_eligible"):
        raise CandidateSelectionAuditError(validation)
    return validation


def normalize_candidate_selection_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Expose a stable read model for v1 and v2 without upgrading legacy facts.

    A v1 audit is explicitly ``legacy_partial`` and never decision eligible:
    recall/gate lineage, source/PIT references, stage versions, and total score
    decomposition did not exist in that schema and therefore remain absent.
    """

    validation = validate_candidate_selection_audit(audit)
    schema = str(audit.get("schema_version") or "") if isinstance(audit, Mapping) else ""
    if schema == V2_SCHEMA_VERSION:
        return {
            "schema_version": NORMALIZED_SCHEMA_VERSION,
            "source_schema_version": schema,
            "compatibility_status": "native_v2",
            "decision_eligible": bool(validation.get("decision_eligible")),
            "decision_at": audit.get("decision_at"),
            "stage_counts": deepcopy(audit.get("stage_counts") or {}),
            "rows": deepcopy(audit.get("rows") or []),
            "source_snapshot_hash": audit.get("snapshot_hash"),
            "validation": validation,
        }

    rows: list[dict[str, Any]] = []
    if schema == V1_SCHEMA_VERSION and isinstance(audit.get("rows"), list):
        for legacy in audit["rows"]:
            if not isinstance(legacy, Mapping):
                continue
            code = _normalize_code(legacy.get("fund_code"))
            reasons = _reason_codes(legacy)
            components = {
                key: deepcopy(legacy.get(key))
                for key in (
                    "fund_quality_score",
                    "sector_fit_score",
                    "descriptive_performance_percentile",
                )
                if legacy.get(key) is not None
            }
            gates = {
                key: {"status": legacy.get(source_key)}
                for key, source_key in (
                    ("quality", "quality_gate_status"),
                    ("tradeability", "tradeability_gate_status"),
                    ("peer_rank", "peer_rank_status"),
                )
                if legacy.get(source_key) not in (None, "")
            }
            selected = legacy.get("selected") is True
            prescreen = _absent_stage_record()
            prescreen.update(
                {
                    "present": True,
                    "rank": legacy.get("post_family_rank"),
                    "rank_basis": "legacy_post_family_rank",
                    "score_components": components,
                    "gates": gates,
                    "reason_codes": reasons,
                    "version": V1_SCHEMA_VERSION,
                }
            )
            final = _absent_stage_record()
            if selected:
                final.update(
                    {
                        "present": True,
                        "rank": legacy.get("final_rank"),
                        "rank_basis": "legacy_final_rank",
                        "reason_codes": reasons,
                        "version": V1_SCHEMA_VERSION,
                    }
                )
            rows.append(
                {
                    "fund_code": code,
                    "fund_name": legacy.get("fund_name"),
                    "sector_label": legacy.get("sector_label"),
                    "share_family_key": legacy.get("share_family_key"),
                    "stage_records": {
                        "recall": _absent_stage_record(),
                        "gate": _absent_stage_record(),
                        "prescreen": prescreen,
                        "final": final,
                    },
                    "selected": selected,
                    "final_rank": legacy.get("final_rank") if selected else None,
                    "reason_codes": reasons,
                }
            )
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "source_schema_version": schema or None,
        "compatibility_status": "legacy_partial" if schema == V1_SCHEMA_VERSION else "unsupported",
        "decision_eligible": False,
        "decision_at": None,
        "stage_counts": {
            "recall": None,
            "gate": None,
            "prescreen": len(rows),
            "final": sum(row["selected"] for row in rows),
        },
        "rows": rows,
        "source_snapshot_hash": audit.get("snapshot_hash") if isinstance(audit, Mapping) else None,
        "validation": validation,
    }


def build_pipeline_candidate_selection_audit_v2(
    *,
    decision_at: datetime | str,
    recall_snapshot: Mapping[str, Any] | None,
    gate_candidates: Sequence[Mapping[str, Any]],
    prescreen_candidates: Sequence[Mapping[str, Any]],
    final_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the live discovery-path v2 audit from captured stage snapshots.

    The adapter never substitutes ``decision_at`` for a missing provider
    availability timestamp.  Missing catalogue/tradeability lineage is copied
    into ``audit_evidence_issues`` and therefore leaves the resulting audit
    invalid while retaining the observable funnel for diagnosis.
    """

    recall_payload = dict(recall_snapshot or {})
    recall_candidates = [
        dict(item)
        for item in recall_payload.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    stages = {
        "recall": recall_candidates,
        "gate": [dict(item) for item in gate_candidates if isinstance(item, Mapping)],
        "prescreen": [
            dict(item) for item in prescreen_candidates if isinstance(item, Mapping)
        ],
        "final": [dict(item) for item in final_candidates if isinstance(item, Mapping)],
    }
    stage_sets = {
        stage: {_normalize_code(item.get("fund_code")) for item in rows}
        for stage, rows in stages.items()
    }
    prepared = {
        stage: _prepare_pipeline_stage_candidates(
            stage,
            rows,
            next_stage_codes=(
                stage_sets[STAGE_ORDER[index + 1]]
                if index + 1 < len(STAGE_ORDER)
                else set()
            ),
        )
        for index, (stage, rows) in enumerate(stages.items())
    }
    recall_scope = recall_payload.get("scope")
    if not isinstance(recall_scope, Mapping):
        recall_scope = {
            "definition": (
                "unique candidates scored for requested target sectors, plus ranked "
                "fallback when invoked, before sector, share-family, and global pool caps"
            ),
            "complete": False,
            "candidate_count_total": len(recall_candidates),
            "candidate_count_retained": len(recall_candidates),
            "catalogue_rows_embedded": False,
            "incomplete_reason": "recall_capture_unavailable",
        }
    contexts = {
        "recall": {
            "version": PIPELINE_STAGE_VERSIONS["recall"],
            "scope": deepcopy(dict(recall_scope)),
        },
        "gate": {
            "version": PIPELINE_STAGE_VERSIONS["gate"],
            "scope": {
                "definition": "enriched candidates with quality and tradeability gates",
                "complete": True,
            },
        },
        "prescreen": {
            "version": PIPELINE_STAGE_VERSIONS["prescreen"],
            "scope": {
                "definition": (
                    "quality/tradeability-acceptable share-family winners before "
                    "sector quota and global pool cap"
                ),
                "complete": True,
            },
        },
        "final": {
            "version": PIPELINE_STAGE_VERSIONS["final"],
            "scope": {
                "definition": "final candidates after sector quota and global pool cap",
                "complete": True,
            },
        },
    }
    return build_candidate_selection_audit_v2(
        decision_at=decision_at,
        recall_candidates=prepared["recall"],
        gate_candidates=prepared["gate"],
        prescreen_candidates=prepared["prescreen"],
        final_candidates=prepared["final"],
        versions={"selection_policy": PIPELINE_SELECTION_POLICY_VERSION},
        stage_contexts=contexts,
    )


def _prepare_pipeline_stage_candidates(
    stage: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    next_stage_codes: set[str],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates, start=1):
        row = deepcopy(dict(candidate))
        code = _normalize_code(row.get("fund_code"))
        row[f"{stage}_rank"] = rank
        if row.get("fund_quality_score") is not None:
            row[f"{stage}_score"] = row.get("fund_quality_score")
        if not isinstance(row.get("score_components"), Mapping):
            row["score_components"] = deepcopy(row.get("quality_score_components") or {})
        row["reason_codes"] = _pipeline_stage_reasons(
            stage,
            row,
            promoted=code in next_stage_codes,
        )
        source_refs, pit_refs, issues = _pipeline_candidate_refs(row, stage)
        row["source_refs"] = source_refs
        row["pit_refs"] = pit_refs
        row["audit_evidence_issues"] = issues
        row["audit_evidence_status"] = "complete" if not issues else "incomplete"
        prepared.append(row)
    return prepared


def _pipeline_stage_reasons(
    stage: str,
    candidate: Mapping[str, Any],
    *,
    promoted: bool,
) -> list[str]:
    if stage == "recall":
        return [
            "promoted_to_enrichment"
            if promoted
            else "not_promoted_by_recall_sector_family_or_pool_cap"
        ]
    if stage == "gate":
        transition_reasons = _text_list(
            candidate.get("candidate_selection_transition_reasons")
        )
        if transition_reasons:
            return transition_reasons
        quality_status = str((candidate.get("quality_gate") or {}).get("status") or "")
        if quality_status == "excluded":
            return ["quality_or_tradeability_gate_excluded"]
        if quality_status == "eligible":
            return ["quality_and_tradeability_gate_eligible"]
        return ["quality_or_tradeability_gate_watch_only"]
    if stage == "prescreen":
        transition_reasons = _text_list(
            candidate.get("candidate_selection_transition_reasons")
        )
        if transition_reasons:
            return transition_reasons
        return [
            "selected_within_sector_quota_and_pool_cap"
            if promoted
            else "outside_final_sector_quota_or_pool_cap"
        ]
    return ["selected_final_candidate"]


def _pipeline_candidate_refs(
    candidate: Mapping[str, Any],
    stage: str,
) -> tuple[list[Any], list[Any], list[str]]:
    explicit_sources = _as_ref_list(candidate.get("source_refs"))
    explicit_pits = _as_ref_list(candidate.get("pit_refs"))
    if explicit_sources and explicit_pits:
        return explicit_sources, explicit_pits, _text_list(
            candidate.get("audit_evidence_issues")
        )

    code = _normalize_code(candidate.get("fund_code"))
    source = str(
        candidate.get("candidate_universe_source")
        or candidate.get("source")
        or ""
    ).strip()
    available_at = (
        candidate.get("candidate_universe_available_at")
        or candidate.get("snapshot_available_at")
        or candidate.get("membership_available_at")
    )
    issues: list[str] = []
    refs: list[Any] = []
    pits: list[Any] = []
    if source and available_at:
        material = {
            "fund_code": code,
            "source": source,
            "available_at": _datetime_text(available_at),
            "fund_quality_score": candidate.get("fund_quality_score"),
            "quality_score_components": candidate.get("quality_score_components"),
        }
        digest = _hash_or_none(material)
        if digest is not None:
            ref_id = f"{stage}:{code}:candidate-universe"
            refs.append(
                {
                    "ref_id": ref_id,
                    "source": source,
                    "version": "fund_universe_snapshot.v1",
                    "snapshot_hash": digest,
                }
            )
            pits.append(
                {
                    "fact_id": f"{ref_id}:fact",
                    "source_ref_id": ref_id,
                    "available_at": _datetime_text(available_at),
                    "snapshot_hash": digest,
                }
            )
    else:
        issues.append("candidate_universe_point_in_time_provenance_missing")

    if candidate.get("fund_quality_score") is None or not candidate.get(
        "quality_score_components"
    ):
        issues.append("quality_score_components_missing")
    if stage != "recall":
        quality_gate = candidate.get("quality_gate")
        if not isinstance(quality_gate, Mapping):
            issues.append("quality_gate_missing")
        tradeability = candidate.get("tradeability")
        if not isinstance(tradeability, Mapping):
            issues.append("tradeability_evidence_missing")
        else:
            checked_at = tradeability.get("checked_at")
            source_ids = [str(value) for value in tradeability.get("source_ids") or [] if str(value)]
            if not checked_at or not source_ids:
                issues.append("tradeability_point_in_time_provenance_missing")
            else:
                trade_material = {
                    "fund_code": code,
                    "schema_version": tradeability.get("schema_version"),
                    "source_ids": source_ids,
                    "checked_at": _datetime_text(checked_at),
                    "data_status": tradeability.get("data_status"),
                    "purchase_state": tradeability.get("purchase_state"),
                }
                digest = _hash_or_none(trade_material)
                if digest is not None:
                    ref_id = f"{stage}:{code}:tradeability"
                    refs.append(
                        {
                            "ref_id": ref_id,
                            "source": "|".join(source_ids),
                            "version": str(
                                tradeability.get("schema_version")
                                or "fund_tradeability.v1"
                            ),
                            "snapshot_hash": digest,
                        }
                    )
                    pits.append(
                        {
                            "fact_id": f"{ref_id}:fact",
                            "source_ref_id": ref_id,
                            "available_at": _datetime_text(checked_at),
                            "snapshot_hash": digest,
                        }
                    )
    return _merge_refs(refs), _merge_refs(pits), list(dict.fromkeys(issues))


def evaluate_candidate_selection_audit(
    audit: Mapping[str, Any],
    outcome_labels: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
    *,
    k: int = 3,
    universe_stage: str = "prescreen",
) -> dict[str, Any]:
    """Compute strict offline ranking metrics from explicitly mature outcomes.

    Missing outcomes are never converted to misses or zero returns.  Precision
    needs complete binary labels for selected top-k; NDCG and regret need full
    universe labels because their oracle/ideal rankings otherwise are unknown.
    """

    validation = validate_candidate_selection_audit(audit)
    normalized = normalize_candidate_selection_audit(audit)
    base = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "audit_schema_version": normalized.get("source_schema_version"),
        "audit_snapshot_hash": normalized.get("source_snapshot_hash"),
        "audit_compatibility_status": normalized.get("compatibility_status"),
        "k": k,
        "universe_stage": universe_stage,
        "warnings": [],
    }
    if normalized.get("compatibility_status") == "legacy_partial":
        base["warnings"].append(
            "legacy_v1_has_no_recall_gate_pit_source_or_version_lineage"
        )
    if validation.get("status") != "valid":
        return _unavailable_evaluation(
            base,
            reason="audit_validation_failed",
            errors=deepcopy(validation.get("errors") or []),
        )
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        return _unavailable_evaluation(
            base,
            reason="invalid_k",
            errors=[_issue("invalid_k", "k", "k must be a positive integer")],
        )
    if universe_stage not in STAGE_ORDER:
        return _unavailable_evaluation(
            base,
            reason="invalid_universe_stage",
            errors=[
                _issue(
                    "invalid_universe_stage",
                    "universe_stage",
                    f"universe_stage must be one of {STAGE_ORDER}",
                )
            ],
        )
    if (
        normalized.get("compatibility_status") == "legacy_partial"
        and universe_stage != "prescreen"
    ):
        return _unavailable_evaluation(
            base,
            reason="legacy_stage_lineage_unavailable",
            errors=[],
        )

    rows = [row for row in normalized.get("rows", []) if isinstance(row, Mapping)]
    universe_rows = [
        row
        for row in rows
        if isinstance(row.get("stage_records"), Mapping)
        and isinstance(row["stage_records"].get(universe_stage), Mapping)
        and row["stage_records"][universe_stage].get("present") is True
    ]
    universe_codes = [_normalize_code(row.get("fund_code")) for row in universe_rows]
    selected_rows = sorted(
        (row for row in rows if row.get("selected") is True),
        key=lambda row: (
            row.get("final_rank") is None,
            row.get("final_rank") if isinstance(row.get("final_rank"), int) else 10**9,
            _normalize_code(row.get("fund_code")),
        ),
    )
    selected_codes = [_normalize_code(row.get("fund_code")) for row in selected_rows]
    effective_k = min(k, len(selected_codes))
    top_codes = selected_codes[:effective_k]

    labels, label_errors, ignored_count = _normalize_outcome_labels(
        outcome_labels, set(universe_codes)
    )
    base["effective_k"] = effective_k
    base["selected_count"] = len(selected_codes)
    base["universe_count"] = len(universe_codes)
    base["ignored_outcome_label_count"] = ignored_count
    if label_errors:
        return _unavailable_evaluation(
            base,
            reason="outcome_label_contract_invalid",
            errors=label_errors,
        )

    mature_codes = [code for code in universe_codes if labels.get(code, {}).get("usable")]
    binary_codes = [code for code in mature_codes if labels[code].get("binary") is not None]
    graded_codes = [code for code in mature_codes if labels[code].get("relevance") is not None]
    utility_codes = [code for code in mature_codes if labels[code].get("utility") is not None]
    top_mature = [code for code in top_codes if labels.get(code, {}).get("usable")]
    top_binary = [code for code in top_codes if code in binary_codes]
    top_graded = [code for code in top_codes if code in graded_codes]
    top_utility = [code for code in top_codes if code in utility_codes]

    coverage = {
        "status": "available" if universe_codes else "unavailable",
        "value": (len(mature_codes) / len(universe_codes)) if universe_codes else None,
        "mature_label_count": len(mature_codes),
        "universe_count": len(universe_codes),
        "binary_label_count": len(binary_codes),
        "graded_label_count": len(graded_codes),
        "utility_label_count": len(utility_codes),
        "top_k_mature_label_count": len(top_mature),
        # K is a preregistered denominator.  A policy that emits fewer than K
        # selections must not make its label or selection coverage look
        # artificially complete by silently shrinking that denominator.
        "top_k_count": k,
        "top_k_value": len(top_mature) / k,
        "selected_top_k_count": effective_k,
        "selection_at_k_value": effective_k / k,
    }

    if len(selected_codes) < k:
        precision = _unavailable_metric(
            "selected_count_below_k",
            selected_count=len(selected_codes),
            required_k=k,
        )
        ndcg = _unavailable_metric(
            "selected_count_below_k",
            selected_count=len(selected_codes),
            required_k=k,
        )
        regret = _unavailable_metric(
            "selected_count_below_k",
            selected_count=len(selected_codes),
            required_k=k,
        )
    else:
        if len(top_binary) == effective_k:
            hits = sum(int(bool(labels[code]["binary"])) for code in top_codes)
            precision = {
                "status": "available",
                "value": hits / effective_k,
                "numerator": hits,
                "denominator": effective_k,
            }
        else:
            precision = _unavailable_metric(
                "selected_top_k_binary_labels_incomplete",
                missing_codes=[code for code in top_codes if code not in top_binary],
            )

        if len(graded_codes) == len(universe_codes) and len(top_graded) == effective_k:
            actual = [float(labels[code]["relevance"]) for code in top_codes]
            ideal = sorted(
                (float(labels[code]["relevance"]) for code in universe_codes),
                reverse=True,
            )[:effective_k]
            dcg = _dcg(actual)
            ideal_dcg = _dcg(ideal)
            ndcg = {
                "status": "available",
                "value": 0.0 if ideal_dcg == 0 else dcg / ideal_dcg,
                "dcg": dcg,
                "ideal_dcg": ideal_dcg,
                "gain": "2^relevance-1",
            }
        else:
            ndcg = _unavailable_metric(
                "universe_graded_labels_incomplete",
                missing_codes=[code for code in universe_codes if code not in graded_codes],
            )

        utility_bases = {
            str(labels[code].get("utility_basis"))
            for code in universe_codes
            if code in utility_codes
        }
        if (
            len(utility_codes) == len(universe_codes)
            and len(top_utility) == effective_k
            and len(utility_bases) == 1
        ):
            selected_utility = sum(float(labels[code]["utility"]) for code in top_codes) / effective_k
            oracle_values = sorted(
                (float(labels[code]["utility"]) for code in universe_codes),
                reverse=True,
            )[:effective_k]
            oracle_utility = sum(oracle_values) / effective_k
            regret = {
                "status": "available",
                "value": max(0.0, oracle_utility - selected_utility),
                "selected_mean_utility": selected_utility,
                "oracle_mean_utility": oracle_utility,
                "utility_basis": next(iter(utility_bases)),
            }
        else:
            reason = (
                "universe_utility_basis_inconsistent"
                if len(utility_codes) == len(universe_codes)
                and len(top_utility) == effective_k
                and len(utility_bases) > 1
                else "universe_utility_labels_incomplete"
            )
            regret = _unavailable_metric(
                reason,
                missing_codes=[code for code in universe_codes if code not in utility_codes],
                observed_utility_bases=sorted(utility_bases),
            )

    performance = [precision, ndcg, regret]
    available_count = sum(metric["status"] == "available" for metric in performance)
    evaluation_status = (
        "available"
        if available_count == len(performance)
        else "partial"
        if available_count
        else "unavailable"
    )
    return {
        **base,
        "status": evaluation_status,
        "reason": None if evaluation_status == "available" else "outcome_labels_incomplete",
        "errors": [],
        "coverage": coverage,
        "precision_at_k": precision,
        "ndcg_at_k": ndcg,
        "regret_at_k": regret,
    }


def _validate_v2(audit: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    if audit.get("stage_order") != list(STAGE_ORDER):
        errors.append(
            _issue("stage_order_invalid", "stage_order", "stage_order must be the canonical v2 order")
        )
    decision_at = _parse_datetime(audit.get("decision_at"))
    if decision_at is None:
        errors.append(
            _issue(
                "decision_at_invalid",
                "decision_at",
                "decision_at must be an ISO-8601 timestamp with timezone",
            )
        )
    versions = audit.get("versions")
    if not isinstance(versions, Mapping) or not str(versions.get("selection_policy") or "").strip():
        errors.append(
            _issue(
                "selection_policy_version_missing",
                "versions.selection_policy",
                "selection_policy version is required",
            )
        )
    if not isinstance(versions, Mapping) or versions.get("audit_contract") != V2_SCHEMA_VERSION:
        errors.append(
            _issue(
                "audit_contract_version_invalid",
                "versions.audit_contract",
                f"audit_contract must be {V2_SCHEMA_VERSION}",
            )
        )
    if audit.get("hash_algorithm") != "sha256":
        errors.append(_issue("hash_algorithm_invalid", "hash_algorithm", "sha256 is required"))
    if audit.get("canonicalization") != "json_utf8_sort_keys_v1":
        errors.append(
            _issue(
                "canonicalization_invalid",
                "canonicalization",
                "json_utf8_sort_keys_v1 is required",
            )
        )
    construction_errors = audit.get("construction_errors")
    if not isinstance(construction_errors, list):
        errors.append(
            _issue(
                "construction_errors_invalid",
                "construction_errors",
                "construction_errors must be a list",
            )
        )
    elif construction_errors:
        errors.extend(deepcopy(construction_errors))

    rows = audit.get("rows")
    stages = audit.get("stages")
    counts = audit.get("stage_counts")
    if not isinstance(rows, list):
        errors.append(_issue("rows_invalid", "rows", "rows must be a list"))
        rows = []
    if not isinstance(stages, Mapping):
        errors.append(_issue("stages_invalid", "stages", "stages must be an object"))
        stages = {}
    if not isinstance(counts, Mapping):
        errors.append(
            _issue("stage_counts_invalid", "stage_counts", "stage_counts must be an object")
        )
        counts = {}

    code_rows: dict[str, Mapping[str, Any]] = {}
    members: dict[str, list[str]] = {stage: [] for stage in STAGE_ORDER}
    stage_material: dict[str, list[dict[str, Any]]] = {stage: [] for stage in STAGE_ORDER}
    source_union: dict[str, list[Any]] = {stage: [] for stage in STAGE_ORDER}
    pit_union: dict[str, list[Any]] = {stage: [] for stage in STAGE_ORDER}

    for row_index, row in enumerate(rows):
        path = f"rows[{row_index}]"
        if not isinstance(row, Mapping):
            errors.append(_issue("row_not_object", path, "row must be an object"))
            continue
        code = str(row.get("fund_code") or "")
        if not _valid_code(code):
            errors.append(_issue("fund_code_invalid", f"{path}.fund_code", "fund_code must be six digits"))
        if code in code_rows:
            errors.append(_issue("duplicate_fund_code", f"{path}.fund_code", f"duplicate fund {code}"))
        code_rows[code] = row
        records = row.get("stage_records")
        if not isinstance(records, Mapping):
            errors.append(
                _issue("stage_records_invalid", f"{path}.stage_records", "stage_records must be an object")
            )
            continue
        if set(records) != set(STAGE_ORDER):
            errors.append(
                _issue(
                    "stage_records_incomplete",
                    f"{path}.stage_records",
                    "every canonical stage must be represented",
                )
            )
        for stage in STAGE_ORDER:
            record = records.get(stage)
            record_path = f"{path}.stage_records.{stage}"
            if not isinstance(record, Mapping):
                errors.append(_issue("stage_record_invalid", record_path, "stage record must be an object"))
                continue
            if record.get("present") is not True:
                if record.get("present") is not False:
                    errors.append(
                        _issue(
                            "stage_presence_invalid",
                            f"{record_path}.present",
                            "present must be an explicit boolean",
                        )
                    )
                absent_payload = {
                    "rank": record.get("rank"),
                    "rank_basis": record.get("rank_basis"),
                    "score": record.get("score"),
                    "score_components": record.get("score_components"),
                    "gates": record.get("gates"),
                    "reason_codes": record.get("reason_codes"),
                    "source_refs": record.get("source_refs"),
                    "pit_refs": record.get("pit_refs"),
                    "evidence_issues": record.get("evidence_issues"),
                    "version": record.get("version"),
                    "candidate_snapshot_hash": record.get("candidate_snapshot_hash"),
                }
                if (
                    record.get("score_status") != "unavailable"
                    or record.get("evidence_status") != "unavailable"
                    or any(
                        value not in (None, [], {})
                        for value in absent_payload.values()
                    )
                ):
                    errors.append(
                        _issue(
                            "absent_stage_payload_not_empty",
                            record_path,
                            "an absent stage may not carry hidden rank, score, evidence, or version data",
                        )
                    )
                continue
            members[stage].append(code)
            stage_material[stage].append({"fund_code": code, "stage_record": record})
            rank = record.get("rank")
            if not _positive_int(rank):
                errors.append(_issue("rank_invalid", f"{record_path}.rank", "rank must be a positive integer"))
            if record.get("rank_basis") not in {"explicit", "input_order"}:
                errors.append(
                    _issue(
                        "rank_basis_invalid",
                        f"{record_path}.rank_basis",
                        "rank_basis must be explicit or input_order",
                    )
                )
            score_status = record.get("score_status")
            score = record.get("score")
            if score_status == "available":
                if not _finite_number(score):
                    errors.append(
                        _issue("score_invalid", f"{record_path}.score", "available score must be finite")
                    )
                components = record.get("score_components")
                if not isinstance(components, Mapping) or not components:
                    errors.append(
                        _issue(
                            "score_components_missing",
                            f"{record_path}.score_components",
                            "an available score requires explicit components",
                        )
                    )
            elif score_status == "unavailable":
                if score is not None:
                    errors.append(
                        _issue(
                            "unavailable_score_not_null",
                            f"{record_path}.score",
                            "unavailable score must be null",
                        )
                    )
            else:
                errors.append(
                    _issue(
                        "score_status_invalid",
                        f"{record_path}.score_status",
                        "score_status must be available or unavailable",
                    )
                )
            if not _json_finite(record.get("score_components")):
                errors.append(
                    _issue(
                        "score_components_non_finite",
                        f"{record_path}.score_components",
                        "score components must be finite JSON values",
                    )
                )
            gates = record.get("gates")
            if not isinstance(gates, Mapping):
                errors.append(_issue("gates_invalid", f"{record_path}.gates", "gates must be an object"))
            elif stage == "gate" and not gates:
                errors.append(
                    _issue(
                        "gate_evidence_missing",
                        f"{record_path}.gates",
                        "gate stage must record at least one gate decision",
                    )
                )
            elif gates:
                for gate_name, gate in gates.items():
                    if not isinstance(gate, Mapping) or not str(gate.get("status") or "").strip():
                        errors.append(
                            _issue(
                                "gate_status_missing",
                                f"{record_path}.gates.{gate_name}",
                                "each gate must be an object with a non-empty status",
                            )
                        )
            reasons = record.get("reason_codes")
            if not isinstance(reasons, list) or not reasons or any(
                not isinstance(value, str) or not value.strip() for value in reasons
            ):
                errors.append(
                    _issue(
                        "reason_codes_missing",
                        f"{record_path}.reason_codes",
                        "every present stage record requires explicit reason codes",
                    )
                )
            if not str(record.get("version") or "").strip():
                errors.append(
                    _issue(
                        "stage_version_missing",
                        f"{record_path}.version",
                        "stage version is required",
                    )
                )
            evidence_status = str(record.get("evidence_status") or "")
            evidence_issues = record.get("evidence_issues")
            if evidence_status != "complete":
                errors.append(
                    _issue(
                        "stage_evidence_incomplete",
                        f"{record_path}.evidence_status",
                        "present candidate stage evidence must be complete",
                    )
                )
            if not isinstance(evidence_issues, list):
                errors.append(
                    _issue(
                        "stage_evidence_issues_invalid",
                        f"{record_path}.evidence_issues",
                        "evidence_issues must be a list",
                    )
                )
            elif evidence_issues:
                errors.append(
                    _issue(
                        "stage_evidence_issues_present",
                        f"{record_path}.evidence_issues",
                        f"unresolved evidence issues: {evidence_issues}",
                    )
                )
            sources = record.get("source_refs")
            pits = record.get("pit_refs")
            _validate_refs(sources, pits, decision_at, record_path, errors)
            if isinstance(sources, list):
                source_union[stage].append(sources)
            if isinstance(pits, list):
                pit_union[stage].append(pits)
            expected_candidate_hash = _hash_or_none(
                {key: value for key, value in record.items() if key != "candidate_snapshot_hash"}
            )
            if not _hash_matches(record.get("candidate_snapshot_hash"), expected_candidate_hash):
                errors.append(
                    _issue(
                        "candidate_snapshot_hash_mismatch",
                        f"{record_path}.candidate_snapshot_hash",
                        "candidate stage record hash does not match its contents",
                    )
                )

        final_record = records.get("final") if isinstance(records.get("final"), Mapping) else {}
        expected_selected = final_record.get("present") is True
        if row.get("selected") is not expected_selected:
            errors.append(
                _issue("selected_flag_mismatch", f"{path}.selected", "selected must match final-stage presence")
            )
        expected_final_rank = final_record.get("rank") if expected_selected else None
        if row.get("final_rank") != expected_final_rank:
            errors.append(
                _issue("final_rank_mismatch", f"{path}.final_rank", "final_rank must match final-stage rank")
            )
        if not isinstance(row.get("reason_codes"), list) or not row.get("reason_codes"):
            errors.append(
                _issue("row_reason_codes_missing", f"{path}.reason_codes", "candidate reasons may not be empty")
            )

    for earlier, later in zip(STAGE_ORDER, STAGE_ORDER[1:]):
        extras = sorted(set(members[later]) - set(members[earlier]))
        if extras:
            errors.append(
                _issue(
                    "stage_subset_violation",
                    f"stages.{later}",
                    f"{later} contains candidates absent from {earlier}: {extras}",
                )
            )

    for stage in STAGE_ORDER:
        ranks = []
        for code in members[stage]:
            record = code_rows[code]["stage_records"][stage]
            if _positive_int(record.get("rank")):
                ranks.append(record["rank"])
        if ranks and sorted(ranks) != list(range(1, len(members[stage]) + 1)):
            errors.append(
                _issue(
                    "stage_ranks_not_contiguous",
                    f"stages.{stage}",
                    "stage ranks must be unique and contiguous from one",
                )
            )
        declared_count = counts.get(stage)
        if declared_count != len(members[stage]):
            errors.append(
                _issue(
                    "stage_count_mismatch",
                    f"stage_counts.{stage}",
                    f"declared {declared_count!r}, observed {len(members[stage])}",
                )
            )
        summary = stages.get(stage)
        if not isinstance(summary, Mapping):
            errors.append(_issue("stage_summary_missing", f"stages.{stage}", "stage summary is required"))
            continue
        if summary.get("candidate_count") != len(members[stage]):
            errors.append(
                _issue(
                    "stage_summary_count_mismatch",
                    f"stages.{stage}.candidate_count",
                    "stage summary count does not match rows",
                )
            )
        if not str(summary.get("version") or "").strip():
            errors.append(
                _issue("stage_version_missing", f"stages.{stage}.version", "stage version is required")
            )
        expected_evidence_complete = all(
            code_rows[code]["stage_records"][stage].get("evidence_status") == "complete"
            and not code_rows[code]["stage_records"][stage].get("evidence_issues")
            for code in members[stage]
        )
        expected_issue_count = sum(
            len(code_rows[code]["stage_records"][stage].get("evidence_issues") or [])
            + int(
                code_rows[code]["stage_records"][stage].get("evidence_status")
                != "complete"
            )
            for code in members[stage]
        )
        if summary.get("evidence_complete") is not expected_evidence_complete:
            errors.append(
                _issue(
                    "stage_evidence_summary_mismatch",
                    f"stages.{stage}.evidence_complete",
                    "stage evidence completeness must match candidate records",
                )
            )
        if summary.get("evidence_issue_count") != expected_issue_count:
            errors.append(
                _issue(
                    "stage_evidence_issue_count_mismatch",
                    f"stages.{stage}.evidence_issue_count",
                    "stage evidence issue count must match candidate records",
                )
            )
        for code in members[stage]:
            record_version = code_rows[code]["stage_records"][stage].get("version")
            if record_version != summary.get("version"):
                errors.append(
                    _issue(
                        "stage_version_mismatch",
                        f"rows.{code}.stage_records.{stage}.version",
                        "candidate stage version must match the stage summary version",
                    )
                )
        stage_material[stage].sort(key=_stage_material_sort_key)
        expected_rows_hash = _hash_or_none(stage_material[stage])
        if not _hash_matches(summary.get("rows_hash"), expected_rows_hash):
            errors.append(
                _issue(
                    "stage_rows_hash_mismatch",
                    f"stages.{stage}.rows_hash",
                    "stage rows hash does not match candidate records",
                )
            )
        expected_sources = _merge_refs(*source_union[stage])
        expected_pits = _merge_refs(*pit_union[stage])
        if summary.get("source_refs") != expected_sources:
            errors.append(
                _issue(
                    "stage_source_refs_mismatch",
                    f"stages.{stage}.source_refs",
                    "stage source refs must equal the candidate-ref union",
                )
            )
        if summary.get("pit_refs") != expected_pits:
            errors.append(
                _issue(
                    "stage_pit_refs_mismatch",
                    f"stages.{stage}.pit_refs",
                    "stage PIT refs must equal the candidate-ref union",
                )
            )

    recall_scope = (
        stages.get("recall", {}).get("scope")
        if isinstance(stages.get("recall"), Mapping)
        else None
    )
    if not isinstance(recall_scope, Mapping):
        errors.append(
            _issue(
                "recall_scope_missing",
                "stages.recall.scope",
                "recall scope and completeness semantics are required",
            )
        )
    else:
        if not str(recall_scope.get("definition") or "").strip():
            errors.append(
                _issue(
                    "recall_scope_definition_missing",
                    "stages.recall.scope.definition",
                    "recall scope definition is required",
                )
            )
        if recall_scope.get("catalogue_rows_embedded") is not False:
            errors.append(
                _issue(
                    "recall_scope_catalogue_policy_invalid",
                    "stages.recall.scope.catalogue_rows_embedded",
                    "the full catalogue must not be embedded in candidate audit",
                )
            )
        retained = recall_scope.get("candidate_count_retained")
        total = recall_scope.get("candidate_count_total")
        if retained != len(members["recall"]):
            errors.append(
                _issue(
                    "recall_scope_retained_count_mismatch",
                    "stages.recall.scope.candidate_count_retained",
                    "retained count must match recall stage rows",
                )
            )
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < len(members["recall"])
        ):
            errors.append(
                _issue(
                    "recall_scope_total_count_invalid",
                    "stages.recall.scope.candidate_count_total",
                    "total count must be an integer no smaller than retained rows",
                )
            )
        if recall_scope.get("complete") is not True:
            errors.append(
                _issue(
                    "recall_scope_incomplete",
                    "stages.recall.scope.complete",
                    "a truncated or partially scanned recall cannot be decision eligible",
                )
            )
        elif isinstance(total, int) and total != retained:
            errors.append(
                _issue(
                    "recall_scope_complete_count_mismatch",
                    "stages.recall.scope",
                    "a complete recall must retain every unique scored candidate",
                )
            )

    expected_snapshot = _hash_or_none(_snapshot_material(audit))
    if not _hash_matches(audit.get("snapshot_hash"), expected_snapshot):
        errors.append(
            _issue(
                "snapshot_hash_mismatch",
                "snapshot_hash",
                "audit snapshot hash does not match its canonical contents",
            )
        )
    if not _json_finite(_snapshot_material(audit)):
        errors.append(
            _issue("non_finite_value", "$", "audit snapshot contains NaN or infinity")
        )
    return _validation(
        schema_version=V2_SCHEMA_VERSION,
        errors=errors,
        warnings=warnings,
        compatibility_status="native_v2",
        decision_eligible=not errors,
    )


def _validate_v1(audit: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    rows = audit.get("rows")
    if not isinstance(rows, list):
        errors.append(_issue("rows_invalid", "rows", "rows must be a list"))
        rows = []
    seen: set[str] = set()
    selected_ranks: list[int] = []
    selected_count = 0
    for index, row in enumerate(rows):
        path = f"rows[{index}]"
        if not isinstance(row, Mapping):
            errors.append(_issue("row_not_object", path, "row must be an object"))
            continue
        code = str(row.get("fund_code") or "")
        if not _valid_code(code):
            errors.append(_issue("fund_code_invalid", f"{path}.fund_code", "fund_code must be six digits"))
        if code in seen:
            errors.append(_issue("duplicate_fund_code", f"{path}.fund_code", f"duplicate fund {code}"))
        seen.add(code)
        if not isinstance(row.get("reason_codes"), list):
            errors.append(
                _issue("reason_codes_invalid", f"{path}.reason_codes", "reason_codes must be a list")
            )
        for key in (
            "fund_quality_score",
            "sector_fit_score",
            "descriptive_performance_percentile",
        ):
            value = row.get(key)
            if value is not None and not _finite_number(value):
                errors.append(_issue("score_invalid", f"{path}.{key}", f"{key} must be finite"))
        if row.get("selected") is True:
            selected_count += 1
            rank = row.get("final_rank")
            if not _positive_int(rank):
                errors.append(
                    _issue("final_rank_invalid", f"{path}.final_rank", "selected row needs positive final rank")
                )
            else:
                selected_ranks.append(rank)
        elif row.get("final_rank") is not None:
            errors.append(
                _issue("unselected_final_rank", f"{path}.final_rank", "unselected row final rank must be null")
            )
    if selected_ranks and sorted(selected_ranks) != list(range(1, selected_count + 1)):
        errors.append(
            _issue("final_ranks_not_contiguous", "rows", "selected final ranks must be contiguous from one")
        )
    if audit.get("prescreen_count") != len(rows):
        errors.append(
            _issue("prescreen_count_mismatch", "prescreen_count", "prescreen_count must equal row count")
        )
    if audit.get("selected_count") != selected_count:
        errors.append(
            _issue("selected_count_mismatch", "selected_count", "selected_count must match selected rows")
        )
    for key in ("post_share_family_count", "acceptable_count"):
        if not isinstance(audit.get(key), int) or isinstance(audit.get(key), bool) or audit.get(key) < 0:
            errors.append(_issue("count_invalid", key, f"{key} must be a non-negative integer"))
    expected_hash = _hash_or_none(rows)
    if not _hash_matches(audit.get("snapshot_hash"), expected_hash):
        errors.append(
            _issue("snapshot_hash_mismatch", "snapshot_hash", "legacy row hash does not match rows")
        )
    return _validation(
        schema_version=V1_SCHEMA_VERSION,
        errors=errors,
        warnings=["legacy_v1_has_no_recall_gate_pit_source_or_version_lineage"],
        compatibility_status="legacy_partial",
        decision_eligible=False,
    )


def _validation(
    *,
    schema_version: str | None,
    errors: Sequence[Mapping[str, Any]],
    warnings: Sequence[str] = (),
    compatibility_status: str = "unsupported",
    decision_eligible: bool = False,
) -> dict[str, Any]:
    return {
        "status": "invalid" if errors else "valid",
        "schema_version": schema_version,
        "compatibility_status": compatibility_status,
        "decision_eligible": bool(decision_eligible and not errors),
        "errors": [dict(item) for item in errors],
        "warnings": list(warnings),
    }


def _validate_refs(
    sources: Any,
    pits: Any,
    decision_at: datetime | None,
    base_path: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(sources, list) or not sources:
        errors.append(
            _issue(
                "source_refs_missing",
                f"{base_path}.source_refs",
                "at least one structured source reference is required",
            )
        )
        sources = []
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        path = f"{base_path}.source_refs[{index}]"
        if not isinstance(source, Mapping):
            errors.append(_issue("source_ref_invalid", path, "source ref must be an object"))
            continue
        ref_id = str(source.get("ref_id") or "").strip()
        if not ref_id:
            errors.append(_issue("source_ref_id_missing", f"{path}.ref_id", "ref_id is required"))
        elif ref_id in source_ids:
            errors.append(_issue("source_ref_duplicate", f"{path}.ref_id", f"duplicate ref_id {ref_id}"))
        source_ids.add(ref_id)
        for key in ("source", "version"):
            if not str(source.get(key) or "").strip():
                errors.append(_issue("source_ref_field_missing", f"{path}.{key}", f"{key} is required"))
        if not _valid_hash(source.get("snapshot_hash")):
            errors.append(
                _issue(
                    "source_snapshot_hash_invalid",
                    f"{path}.snapshot_hash",
                    "source snapshot_hash must be SHA-256",
                )
            )

    if not isinstance(pits, list) or not pits:
        errors.append(
            _issue(
                "pit_refs_missing",
                f"{base_path}.pit_refs",
                "at least one point-in-time fact reference is required",
            )
        )
        pits = []
    fact_ids: set[str] = set()
    for index, pit in enumerate(pits):
        path = f"{base_path}.pit_refs[{index}]"
        if not isinstance(pit, Mapping):
            errors.append(_issue("pit_ref_invalid", path, "PIT ref must be an object"))
            continue
        fact_id = str(pit.get("fact_id") or "").strip()
        if not fact_id:
            errors.append(_issue("pit_fact_id_missing", f"{path}.fact_id", "fact_id is required"))
        elif fact_id in fact_ids:
            errors.append(_issue("pit_fact_duplicate", f"{path}.fact_id", f"duplicate fact_id {fact_id}"))
        fact_ids.add(fact_id)
        source_ref_id = str(pit.get("source_ref_id") or "").strip()
        if not source_ref_id or source_ref_id not in source_ids:
            errors.append(
                _issue(
                    "pit_source_ref_unresolved",
                    f"{path}.source_ref_id",
                    "source_ref_id must resolve within the stage record",
                )
            )
        available_at = _parse_datetime(pit.get("available_at"))
        if available_at is None:
            errors.append(
                _issue(
                    "pit_available_at_invalid",
                    f"{path}.available_at",
                    "available_at must be an ISO-8601 timestamp with timezone",
                )
            )
        elif decision_at is not None and available_at > decision_at:
            errors.append(
                _issue(
                    "pit_after_decision",
                    f"{path}.available_at",
                    "PIT fact was not available at decision_at",
                )
            )
        if not _valid_hash(pit.get("snapshot_hash")):
            errors.append(
                _issue(
                    "pit_snapshot_hash_invalid",
                    f"{path}.snapshot_hash",
                    "PIT snapshot_hash must be SHA-256",
                )
            )


def _normalize_outcome_labels(
    labels: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
    universe_codes: set[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], int]:
    if labels is None:
        return {}, [], 0
    errors: list[dict[str, str]] = []
    items: list[tuple[Any, Any]] = []
    if isinstance(labels, Mapping):
        items = list(labels.items())
    elif isinstance(labels, Sequence) and not isinstance(labels, (str, bytes, bytearray)):
        items = [(None, value) for value in labels]
    else:
        return {}, [_issue("outcome_labels_invalid", "outcome_labels", "labels must be a map or list")], 0

    normalized: dict[str, dict[str, Any]] = {}
    ignored = 0
    for index, (key, raw) in enumerate(items):
        path = f"outcome_labels[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(_issue("outcome_label_not_object", path, "outcome label must be an object"))
            continue
        raw_code = raw.get("fund_code")
        code = _normalize_code(key if key is not None else raw_code)
        if key is not None and raw_code not in (None, "") and _normalize_code(raw_code) != code:
            errors.append(
                _issue("outcome_code_conflict", f"{path}.fund_code", "mapping key and fund_code disagree")
            )
        if not _valid_code(code):
            errors.append(_issue("outcome_code_invalid", f"{path}.fund_code", "fund_code must be six digits"))
            continue
        if code in normalized:
            errors.append(_issue("duplicate_outcome_label", path, f"duplicate outcome label for {code}"))
            continue
        if code not in universe_codes:
            ignored += 1
        ref = raw.get("source_ref") or raw.get("observation_id") or raw.get("event_id")
        lineage_available = bool(ref)
        mature = raw.get("mature") is True
        usable = mature and raw.get("skipped") is not True and raw.get("eligible") is not False and lineage_available

        binary: bool | None = None
        candidate_binary = raw.get("binary_relevance", _MISSING)
        if candidate_binary is _MISSING:
            candidate_binary = raw.get("direction_hit", raw.get("direction_aligned", _MISSING))
        if candidate_binary is _MISSING and str(raw.get("status") or "") in {"hit", "miss"}:
            candidate_binary = str(raw.get("status")) == "hit"
        if isinstance(candidate_binary, bool):
            binary = candidate_binary
        elif _finite_number(candidate_binary) and float(candidate_binary) in {0.0, 1.0}:
            binary = bool(candidate_binary)

        relevance: float | None = None
        candidate_relevance = raw.get("relevance", _MISSING)
        if candidate_relevance is not _MISSING and _finite_number(candidate_relevance):
            candidate_relevance_float = float(candidate_relevance)
            if candidate_relevance_float >= 0:
                relevance = candidate_relevance_float
        elif binary is not None:
            relevance = 1.0 if binary else 0.0

        utility: float | None = None
        utility_basis: str | None = None
        for key_name in ("utility", "return_percent", "period_change_percent"):
            candidate_utility = raw.get(key_name, _MISSING)
            if candidate_utility is not _MISSING and _finite_number(candidate_utility):
                utility = float(candidate_utility)
                declared_basis = str(raw.get("utility_basis") or "").strip()
                utility_basis = declared_basis or (
                    "explicit_utility"
                    if key_name == "utility"
                    else "observed_return_percent"
                )
                break
        normalized[code] = {
            "usable": usable,
            "mature": mature,
            "lineage_available": lineage_available,
            "binary": binary if usable else None,
            "relevance": relevance if usable else None,
            "utility": utility if usable else None,
            "utility_basis": utility_basis if usable else None,
            "source_ref": deepcopy(ref) if ref else None,
        }
    return normalized, errors, ignored


def _candidate_rank(candidate: Mapping[str, Any], stage: str) -> Any:
    keys = [f"{stage}_rank", "stage_rank", "rank"]
    if stage == "final":
        keys.insert(0, "candidate_final_rank")
        keys.insert(1, "final_rank")
    elif stage == "prescreen":
        keys.insert(0, "post_family_rank")
    for key in keys:
        if key in candidate and candidate.get(key) is not None:
            return candidate.get(key)
    return _MISSING


def _candidate_score(candidate: Mapping[str, Any], stage: str) -> Any:
    for key in (f"{stage}_score", "selection_score", "score", "fund_quality_score"):
        if key in candidate and candidate.get(key) is not None:
            return candidate.get(key)
    return _MISSING


def _candidate_gates(candidate: Mapping[str, Any]) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    raw_gates = candidate.get("gates")
    if isinstance(raw_gates, Mapping):
        gates.update(deepcopy(dict(raw_gates)))
    for key, output_key in (
        ("quality_gate", "quality"),
        ("tradeability_gate", "tradeability"),
        ("peer_gate", "peer"),
    ):
        value = candidate.get(key)
        if isinstance(value, Mapping):
            gates.setdefault(output_key, deepcopy(dict(value)))
    tradeability = candidate.get("tradeability")
    if isinstance(tradeability, Mapping) and isinstance(tradeability.get("tradeability_gate"), Mapping):
        gates.setdefault("tradeability", deepcopy(dict(tradeability["tradeability_gate"])))
    return gates


def _reason_codes(candidate: Mapping[str, Any]) -> list[str]:
    value = candidate.get("reason_codes", candidate.get("reasons", candidate.get("selection_reason")))
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(
        dict.fromkeys(str(item).strip() for item in values if str(item).strip())
    )


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    if isinstance(values, set):
        values = sorted(values, key=str)
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _absent_stage_record() -> dict[str, Any]:
    return {
        "present": False,
        "rank": None,
        "rank_basis": None,
        "score": None,
        "score_status": "unavailable",
        "score_components": {},
        "gates": {},
        "reason_codes": [],
        "source_refs": [],
        "pit_refs": [],
        "evidence_status": "unavailable",
        "evidence_issues": [],
        "version": None,
        "candidate_snapshot_hash": None,
    }


def _snapshot_material(audit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in audit.items()
        if key not in {"snapshot_hash", "validation"}
    }


def _stage_material_sort_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    record = value.get("stage_record")
    rank = record.get("rank") if isinstance(record, Mapping) else None
    return (
        not _positive_int(rank),
        rank if _positive_int(rank) else 10**9,
        str(value.get("fund_code") or ""),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_or_none(value: Any) -> str | None:
    try:
        material = _canonical_json(value)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _merge_refs(*groups: Sequence[Any]) -> list[Any]:
    by_material: dict[str, Any] = {}
    for group in groups:
        for value in group:
            try:
                key = _canonical_json(value)
            except (TypeError, ValueError):
                key = repr(value)
            by_material.setdefault(key, deepcopy(value))
    return [by_material[key] for key in sorted(by_material)]


def _as_ref_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return deepcopy(value)
    if isinstance(value, tuple):
        return deepcopy(list(value))
    return [deepcopy(value)]


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def _valid_code(value: Any) -> bool:
    text = str(value or "")
    return bool(_FUND_CODE_RE.fullmatch(text)) and text != "000000"


def _valid_hash(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "")))


def _hash_matches(value: Any, expected: str | None) -> bool:
    return expected is not None and _valid_hash(value) and str(value).lower() == expected.lower()


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def scoreless_int(value: Any) -> Any:
    """Preserve invalid ranks for validation while accepting integral floats."""

    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return deepcopy(value)


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _json_finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_json_finite(key) and _json_finite(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_json_finite(item) for item in value)
    return True


def _datetime_text(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _dcg(values: Sequence[float]) -> float:
    return sum((2.0**value - 1.0) / math.log2(index + 2) for index, value in enumerate(values))


def _unavailable_metric(reason: str, **details: Any) -> dict[str, Any]:
    return {"status": "unavailable", "value": None, "reason": reason, **details}


def _unavailable_evaluation(
    base: Mapping[str, Any],
    *,
    reason: str,
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        **deepcopy(dict(base)),
        "status": "unavailable",
        "reason": reason,
        "errors": [dict(item) for item in errors],
        "coverage": _unavailable_metric(reason),
        "precision_at_k": _unavailable_metric(reason),
        "ndcg_at_k": _unavailable_metric(reason),
        "regret_at_k": _unavailable_metric(reason),
    }


__all__ = [
    "CandidateSelectionAuditError",
    "EVALUATION_SCHEMA_VERSION",
    "NORMALIZED_SCHEMA_VERSION",
    "STAGE_ORDER",
    "V1_SCHEMA_VERSION",
    "V2_SCHEMA_VERSION",
    "build_candidate_selection_audit_v2",
    "build_pipeline_candidate_selection_audit_v2",
    "evaluate_candidate_selection_audit",
    "normalize_candidate_selection_audit",
    "require_valid_candidate_selection_audit",
    "validate_candidate_selection_audit",
]
