"""Operational orchestration for immutable D1 decision-quality snapshots.

The pure evaluator deliberately has no storage or provider dependencies.  This
module is the single operations adapter around it: it reads already-frozen
primary-store evidence, evaluates at an explicit point-in-time cutoff, and
optionally appends one content-addressed snapshot.  It never fetches NAV data,
runs an LLM, settles outcomes, or changes a production decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.candidate_selection_outcomes import (
    CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
    CANDIDATE_OUTCOME_SET_SCHEMA_VERSION,
    CandidateAuditCommitReceiptLate,
    CandidateAuditSourceCaptureLate,
    CandidateSelectionSettlementError,
    candidate_case_id,
    candidate_preregistered_target_from_artifact,
    candidate_target_from_artifact,
    validate_candidate_outcome_provider_projection,
    validate_candidate_outcome_set,
)
from app.services.decision_quality_artifacts import (
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_AUDIT_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_MODE,
    CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS,
    CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS,
    CANDIDATE_LABEL_POLICY_VERSION,
    build_candidate_label_plan,
)
from app.services.candidate_selection_audit import (
    validate_candidate_selection_audit,
)
from app.services.decision_quality_evaluation import (
    CANDIDATE_SELECTION_EVALUATOR_VERSION,
    CANDIDATE_SELECTION_EVIDENCE_SCOPE,
    CANDIDATE_SELECTION_CASE_SCHEMA_VERSION,
    CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION,
    GATE_POLICY_SCHEMA_VERSION,
    PAIRED_CASE_SCHEMA_VERSION,
    evaluate_decision_quality,
)
from app.services.decision_quality_provider_receipts import (
    ProviderReceiptValidationError,
    canonical_provider_hash,
    validate_provider_origin_receipt,
)
from app.services.decision_quality_provider_policy import (
    CandidateProviderAdapterPolicyError,
    candidate_provider_adapter_stratum,
    candidate_provider_adapter_stratum_hash,
    rebuild_candidate_provider_normalized_payload,
    verify_candidate_provider_adapter_policy,
)
from app.services.decision_repository import (
    DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION,
    DECISION_QUALITY_READINESS_STATUSES,
    DecisionQualityIntegrityError,
    DecisionRepositoryError,
    _decode_artifact_receipt_row,
    _decode_provider_receipt_row,
    _fetchall,
    canonical_hash,
    canonical_json,
    decision_event_content_hash,
    get_decision_quality_contract_rollout,
    list_decision_quality_input_artifacts,
    list_decision_quality_evaluation_snapshots,
    normalize_decision_quality_input_artifact,
    normalize_decision_quality_evaluation_snapshot,
    put_decision_quality_evaluation_snapshot,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    PROMPT_GATE_POLICY_SCHEMA_VERSION,
    PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
    PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
    PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
    PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
    PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
    PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
    PromptShadowContractError,
    normalize_prompt_gate_policy,
    normalize_prompt_shadow_attempt,
    normalize_prompt_shadow_output,
    normalize_prompt_shadow_registration,
)
from app.services.prompt_shadow_evaluation import (
    PromptShadowEvaluationError,
    build_prompt_shadow_paired_case,
    evaluate_prompt_shadow_gate,
    prompt_shadow_stratum_hash,
)


DECISION_QUALITY_SNAPSHOT_RUN_SCHEMA_VERSION = "decision_quality_snapshot_run.v1"
DECISION_QUALITY_SNAPSHOT_READ_SCHEMA_VERSION = "decision_quality_snapshot_read.v1"
DECISION_QUALITY_INPUT_MANIFEST_SCHEMA_VERSION = "decision_quality_input_manifest.v4"
DECISION_QUALITY_EVALUATOR_VERSION = "decision_quality_evaluator.2026-07.d5-v4"

READINESS_INSUFFICIENT = "insufficient_data"
READINESS_SHADOW = "shadow_evaluation"
READINESS_MANUAL_REVIEW = "ready_for_manual_review"
_READINESS_STATUSES = set(DECISION_QUALITY_READINESS_STATUSES)

MIN_SHADOW_MATURE_DECISION_DAYS = 20
MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS = 60
MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT = 80.0
DEFAULT_WINDOW_DAYS = 365
MAX_WINDOW_DAYS = 3650
_PAGE_SIZE = 1_000
_IN_CLAUSE_BATCH_SIZE = 250
_CN_TZ = ZoneInfo("Asia/Shanghai")
_LEGACY_VARIANT_VERSION_FIELDS = {
    "model_version",
    "prompt_version",
    "strategy_version",
    "policy_version",
    "data_version",
    "fee_model_version",
}
_LEGACY_VARIANT_HASH_FIELDS = {
    "model_hash",
    "prompt_hash",
    "prompt_contract_hash",
    "strategy_hash",
    "policy_hash",
    "data_hash",
    "evidence_hash",
    "fee_model_hash",
    "variant_hash",
}
_D2_REPLAY_DECLARATION_FIELDS = {
    "quality_contract_version",
    "replay_contract_required",
    "replay_bundle",
    "replay_bundle_hash",
    "variant_manifest",
}
_EXPECTED_PENDING_OUTCOME_EXCLUSION_REASONS = {
    "outcome_observation_not_terminal_mature",
}


class DecisionQualitySnapshotError(RuntimeError):
    """Base error for the operations adapter."""


class DecisionQualitySnapshotContractError(DecisionQualitySnapshotError):
    """Stored evidence or evaluation output violated its immutable contract."""


class DecisionQualitySnapshotStorageError(DecisionQualitySnapshotError):
    """The primary evidence store could not be read or written safely."""


def parse_evaluation_as_of(value: str | datetime) -> datetime:
    """Return a canonical aware UTC cutoff; naive timestamps are forbidden."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("evaluation_as_of must be an ISO timestamp") from exc
    else:
        raise ValueError("evaluation_as_of is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evaluation_as_of must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def resolve_decision_quality_readiness(
    *,
    mature_decision_day_count: int,
    formal_label_coverage_percent: float | None,
) -> str:
    """Apply the pre-registered readiness ladder without enabling anything."""

    if mature_decision_day_count < MIN_SHADOW_MATURE_DECISION_DAYS:
        return READINESS_INSUFFICIENT
    if mature_decision_day_count < MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS:
        return READINESS_SHADOW
    if (
        formal_label_coverage_percent is not None
        and formal_label_coverage_percent
        >= MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT
    ):
        return READINESS_MANUAL_REVIEW
    return READINESS_SHADOW


def evaluate_and_persist_decision_quality_snapshots(
    *,
    evaluation_as_of: str | datetime,
    user_ids: Iterable[int] | None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    persist: bool = True,
    connection_factory: Any | None = None,
) -> dict[str, Any]:
    """Evaluate one or all evidence-bearing tenants in one fail-closed run.

    ``user_ids=None`` means all tenants with formal decision evidence or frozen
    D2 input artifacts.  An explicit empty iterable means no tenants.
    """

    cutoff = parse_evaluation_as_of(evaluation_as_of)
    safe_window = _validated_window_days(window_days)
    requested_users = _normalize_user_ids(user_ids)
    factory = connection_factory or _default_connection_factory

    try:
        if requested_users is None:
            with _connection(factory, commit_on_success=False) as connection:
                _require_primary_store(connection)
                selected_users = _list_evidence_user_ids(connection)
        else:
            selected_users = requested_users
    except DecisionQualitySnapshotError:
        raise
    except (DecisionQualityIntegrityError, DecisionRepositoryError) as exc:
        raise DecisionQualitySnapshotStorageError(
            "decision-quality evidence storage failed closed"
        ) from exc
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualitySnapshotContractError(
            "decision-quality evidence failed its immutable contract"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - translate at the operations boundary
        raise DecisionQualitySnapshotStorageError(
            "decision-quality primary store is unavailable"
        ) from exc

    rows: list[dict[str, Any]] = []
    failed_user_errors: list[tuple[int, DecisionQualitySnapshotContractError]] = []
    for user_id in selected_users:
        try:
            rows.append(
                _evaluate_one_user_snapshot(
                    user_id=user_id,
                    evaluation_as_of=cutoff,
                    window_days=safe_window,
                    persist=persist,
                    connection_factory=factory,
                )
            )
        except DecisionQualitySnapshotContractError as exc:
            # A malformed tenant must not roll back snapshots already committed
            # for healthy tenants.  Continue the isolated batch, then return a
            # non-zero CLI contract result after all tenants were attempted.
            failed_user_errors.append((user_id, exc))
        except DecisionQualitySnapshotStorageError:
            raise
    if failed_user_errors:
        joined = ",".join(str(value) for value, _ in failed_user_errors)
        raise DecisionQualitySnapshotContractError(
            "decision-quality evaluation failed closed for isolated user ids: "
            + joined
        ) from failed_user_errors[0][1]

    return {
        "schema_version": DECISION_QUALITY_SNAPSHOT_RUN_SCHEMA_VERSION,
        "status": "completed",
        "evaluation_as_of": cutoff.isoformat(),
        "window_days": safe_window,
        "persisted": bool(persist),
        "automatic_promotion_allowed": False,
        "user_count": len(rows),
        "snapshots": rows,
    }


def _evaluate_one_user_snapshot(
    *,
    user_id: int,
    evaluation_as_of: datetime,
    window_days: int,
    persist: bool,
    connection_factory: Any,
) -> dict[str, Any]:
    try:
        with _connection(
            connection_factory,
            commit_on_success=persist,
        ) as connection:
            _require_primary_store(connection)
            snapshot = build_decision_quality_snapshot(
                user_id=user_id,
                evaluation_as_of=evaluation_as_of,
                window_days=window_days,
                connection=connection,
            )
            if persist:
                stored = put_decision_quality_evaluation_snapshot(
                    user_id=user_id,
                    snapshot=snapshot,
                    connection=connection,
                )
                normalized = _snapshot_payload(stored)
            else:
                normalized = normalize_decision_quality_evaluation_snapshot(snapshot)
            result = _snapshot_run_row(user_id, normalized)
    except DecisionQualitySnapshotError:
        raise
    except DecisionQualityIntegrityError as exc:
        raise DecisionQualitySnapshotContractError(
            "decision-quality stored evidence failed its integrity contract"
        ) from exc
    except DecisionRepositoryError as exc:
        raise DecisionQualitySnapshotStorageError(
            "decision-quality evidence storage failed closed"
        ) from exc
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualitySnapshotContractError(
            "decision-quality evidence failed its immutable contract"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - translate at the operations boundary
        raise DecisionQualitySnapshotStorageError(
            "decision-quality primary store is unavailable"
        ) from exc
    return result


def build_decision_quality_snapshot(
    *,
    user_id: int,
    evaluation_as_of: str | datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    connection: Any,
) -> dict[str, Any]:
    """Build, but do not persist, one immutable evaluator snapshot."""

    normalized_user_id = _positive_user_id(user_id)
    cutoff = parse_evaluation_as_of(evaluation_as_of)
    safe_window = _validated_window_days(window_days)
    window_start = cutoff - timedelta(days=safe_window)
    try:
        rollout_marker = get_decision_quality_contract_rollout(
            connection=connection
        )
    except DecisionQualityIntegrityError as exc:
        raise DecisionQualitySnapshotContractError(
            "decision-quality rollout marker failed closed"
        ) from exc

    event_rows = _fetch_decision_event_rows(
        user_id=normalized_user_id,
        window_start=window_start,
        cutoff=cutoff,
        connection=connection,
    )
    visible_events = _events_in_window(
        event_rows,
        window_start=window_start,
        cutoff=cutoff,
    )
    for row in visible_events:
        _validate_event_storage_binding(row)
    events, nonformal_events = _partition_event_inputs_by_rollout(
        visible_events,
        rollout_marker=rollout_marker,
    )
    event_ids = {
        str(_row_payload(row).get("event_id") or "")
        for row in events
        if _row_payload(row).get("event_id")
    }

    outcome_rows = (
        _fetch_outcome_observation_rows(
            user_id=normalized_user_id,
            event_ids=event_ids,
            cutoff=cutoff,
            connection=connection,
        )
        if event_ids
        else []
    )
    outcomes = [
        row
        for row in outcome_rows
        if _terminal_outcome_for_selected_event(
            row,
            event_ids,
            evaluation_as_of=cutoff,
        )
    ]

    artifact_rows = _fetch_decision_quality_input_artifact_rows(
        user_id=normalized_user_id,
        window_start=window_start,
        cutoff=cutoff,
        connection=connection,
    )
    artifacts = _artifacts_in_window(
        artifact_rows,
        window_start=window_start,
        cutoff=cutoff,
    )
    artifact_receipts = _fetch_decision_quality_artifact_receipt_rows(
        user_id=normalized_user_id,
        cutoff=cutoff,
        connection=connection,
    )
    provider_receipt_ids = _candidate_provider_receipt_ids(artifacts)
    provider_receipts = _fetch_decision_quality_provider_receipt_rows(
        receipt_ids=provider_receipt_ids,
        cutoff=cutoff,
        connection=connection,
    )
    partitioned = _partition_artifacts(
        artifacts,
        artifact_receipts=artifact_receipts,
        provider_receipts=provider_receipts,
        evaluation_as_of=cutoff,
    )

    paired_comparison = None
    if partitioned["champion_cases"] or partitioned["challenger_cases"]:
        paired_comparison = {
            "champion": partitioned["champion_cases"],
            "challenger": partitioned["challenger_cases"],
        }

    evaluation = evaluate_decision_quality(
        events,
        outcomes,
        claim_audits=partitioned["claim_audits"],
        abstention_shadow_labels=partitioned["abstention_shadow_labels"],
        candidate_selection_cases=partitioned["candidate_selection_cases"],
        evaluation_as_of=cutoff,
        paired_comparison=paired_comparison,
        gate_policy=partitioned["gate_policy"],
    )
    evaluation["prompt_shadow_gate"] = partitioned["prompt_shadow_gate"]
    evaluation["prompt_shadow_gate_history"] = partitioned[
        "prompt_shadow_gate_history"
    ]
    evaluation["evaluation_hash"] = canonical_hash(
        {
            key: value
            for key, value in evaluation.items()
            if key != "evaluation_hash"
        }
    )
    _raise_for_evaluation_contract_failures(evaluation, events=events)

    mature_dates = _mature_decision_dates(evaluation, events)
    coverage = _formal_label_coverage(evaluation)
    readiness = resolve_decision_quality_readiness(
        mature_decision_day_count=len(mature_dates),
        formal_label_coverage_percent=coverage,
    )
    manifest = _input_manifest(
        window_start=window_start,
        cutoff=cutoff,
        events=events,
        nonformal_events=nonformal_events,
        outcomes=outcomes,
        artifacts=partitioned["used_artifact_rows"],
        ignored_artifacts=partitioned["ignored_artifact_rows"],
        ignored_artifact_count=partitioned["ignored_artifact_count"],
        artifact_receipts=partitioned["used_artifact_receipts"],
        provider_receipts=partitioned["used_provider_receipts"],
        candidate_capture_records=partitioned["candidate_capture_records"],
        prompt_shadow_manifest=partitioned["prompt_shadow_manifest"],
        mature_decision_dates=mature_dates,
        rollout_marker=rollout_marker,
    )
    return {
        "schema_version": DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION,
        "evaluation_as_of": cutoff.isoformat(),
        "evaluator_version": DECISION_QUALITY_EVALUATOR_VERSION,
        "readiness_status": readiness,
        "automatic_promotion_allowed": False,
        "store_authority": "primary",
        "audit_eligible": True,
        "input_manifest": manifest,
        "config": {
            "window_days": safe_window,
            "minimum_shadow_mature_decision_days": (
                MIN_SHADOW_MATURE_DECISION_DAYS
            ),
            "minimum_manual_review_mature_decision_days": (
                MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
            ),
            "minimum_manual_review_label_coverage_percent": (
                MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT
            ),
            "automatic_promotion_allowed": False,
        },
        "evaluation": evaluation,
    }


def read_latest_decision_quality_snapshot(
    *,
    user_id: int,
    connection_factory: Any | None = None,
) -> dict[str, Any] | None:
    """Read and redact the latest immutable snapshot; never compute on GET."""

    normalized_user_id = _positive_user_id(user_id)
    factory = connection_factory or _default_read_connection_factory
    try:
        with _connection(factory, commit_on_success=False) as connection:
            _require_primary_store(connection)
            rows = list_decision_quality_evaluation_snapshots(
                user_id=normalized_user_id,
                limit=1,
                connection=connection,
            )
            if not rows:
                return None
            return redact_decision_quality_snapshot(rows[0])
    except DecisionQualitySnapshotError:
        raise
    except (DecisionQualityIntegrityError, DecisionRepositoryError) as exc:
        raise DecisionQualitySnapshotStorageError(
            "decision-quality snapshot integrity check failed"
        ) from exc
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualitySnapshotContractError(
            "decision-quality snapshot contract is invalid"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - no fallback at this read boundary
        raise DecisionQualitySnapshotStorageError(
            "decision-quality primary store is unavailable"
        ) from exc


def redact_decision_quality_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded internal read projection with no case-level evidence."""

    payload = _snapshot_payload(snapshot)
    evaluation = payload.get("evaluation")
    manifest = payload.get("input_manifest")
    config = payload.get("config")
    if not isinstance(evaluation, Mapping):
        raise DecisionQualitySnapshotContractError("snapshot evaluation is missing")
    if not isinstance(manifest, Mapping) or not isinstance(config, Mapping):
        raise DecisionQualitySnapshotContractError("snapshot metadata is missing")
    readiness = str(payload.get("readiness_status") or "")
    if readiness not in _READINESS_STATUSES:
        raise DecisionQualitySnapshotContractError("snapshot readiness is invalid")

    overall = evaluation.get("overall")
    overall_map = overall if isinstance(overall, Mapping) else {}
    paired = evaluation.get("paired_gate")
    prompt_shadow = evaluation.get("prompt_shadow_gate")
    claim_audits = evaluation.get("claim_audits")
    candidate = evaluation.get("candidate_selection")
    return {
        "schema_version": DECISION_QUALITY_SNAPSHOT_READ_SCHEMA_VERSION,
        "snapshot_id": payload.get("snapshot_id"),
        "content_hash": payload.get("content_hash"),
        "evaluation_hash": payload.get("evaluation_hash"),
        "evaluation_as_of": payload.get("evaluation_as_of"),
        "evaluator_schema_version": payload.get("evaluator_schema_version"),
        "evaluator_version": payload.get("evaluator_version"),
        "status": payload.get("status"),
        "readiness": {
            "status": readiness,
            "mature_decision_day_count": int(
                manifest.get("mature_decision_day_count") or 0
            ),
            "formal_label_coverage_percent": _optional_number(
                overall_map.get("label_coverage_percent")
            ),
            "minimum_shadow_mature_decision_days": config.get(
                "minimum_shadow_mature_decision_days"
            ),
            "minimum_manual_review_mature_decision_days": config.get(
                "minimum_manual_review_mature_decision_days"
            ),
            "minimum_manual_review_label_coverage_percent": config.get(
                "minimum_manual_review_label_coverage_percent"
            ),
        },
        "window": {
            "days": config.get("window_days"),
            "start": manifest.get("window_start"),
            "end": manifest.get("evaluation_as_of"),
        },
        "overall": _redacted_summary(overall_map),
        "by_decision_kind": _redacted_groups(evaluation, "decision_kind"),
        "by_horizon": _redacted_groups(evaluation, "horizon"),
        "claim_audits": _redacted_claim_audits(claim_audits),
        "candidate_selection": _redacted_candidate_selection(candidate),
        "paired_gate": _redacted_paired_gate(paired),
        "prompt_shadow_gate": _redacted_prompt_shadow_gate(prompt_shadow),
        "input_counts": {
            "decision_event_count": int(manifest.get("decision_event_count") or 0),
            "nonformal_decision_event_count": int(
                manifest.get("nonformal_decision_event_count") or 0
            ),
            "terminal_outcome_count": int(
                manifest.get("terminal_outcome_count") or 0
            ),
            "input_artifact_count": int(manifest.get("input_artifact_count") or 0),
            "consumed_input_artifact_count": int(
                manifest.get("consumed_input_artifact_count") or 0
            ),
            "ignored_artifact_count": int(
                manifest.get("ignored_artifact_count") or 0
            ),
            "artifact_receipt_count": int(
                manifest.get("artifact_receipt_count") or 0
            ),
            "provider_receipt_count": int(
                manifest.get("provider_receipt_count") or 0
            ),
            "candidate_capture_count": int(
                manifest.get("candidate_capture_count") or 0
            ),
            "candidate_capture_status_counts": _redacted_nonnegative_count_map(
                manifest.get("candidate_capture_status_counts")
            ),
            "candidate_capture_reason_counts": _redacted_nonnegative_count_map(
                manifest.get("candidate_capture_reason_counts")
            ),
            "prompt_shadow_input_artifact_count": int(
                manifest.get("prompt_shadow_input_artifact_count") or 0
            ),
            "prompt_shadow_artifact_receipt_count": int(
                manifest.get("prompt_shadow_artifact_receipt_count") or 0
            ),
            "prompt_shadow_assigned_registration_count": int(
                manifest.get("prompt_shadow_assigned_registration_count") or 0
            ),
            "prompt_shadow_paired_case_count": int(
                manifest.get("prompt_shadow_paired_case_count") or 0
            ),
        },
        "automatic_promotion_allowed": False,
        "notices": [
            "这些统计只描述历史、点时冻结样本的覆盖与一致性，不预测未来收益，不构成基金评价、排名或投资建议。",
            "挑战者未参与线上决策；达到人工复核门槛仅表示可进入人工审查，不代表策略更优，也不会自动启用。",
        ],
    }


def _redacted_nonnegative_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): int(count)
        for key, count in value.items()
        if isinstance(key, str)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    }


def _redacted_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    calibration = summary.get("calibration")
    abstention = summary.get("abstention")
    replay = summary.get("replay")
    return {
        "input_event_horizon_count": int(
            summary.get("input_event_horizon_count") or 0
        ),
        "formal_event_horizon_count": int(summary.get("event_horizon_count") or 0),
        "actionable_event_horizon_count": int(
            summary.get("actionable_event_horizon_count") or 0
        ),
        "matched_terminal_outcome_count": int(
            summary.get("matched_terminal_outcome_count") or 0
        ),
        "label_coverage_percent": _optional_number(
            summary.get("label_coverage_percent")
        ),
        "metrics": _redacted_metrics(metrics),
        "calibration": _redacted_calibration(calibration),
        "abstention": _redacted_abstention(abstention),
        "replay": _redacted_replay(replay),
    }


def _redacted_groups(evaluation: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    stratified = evaluation.get("stratified")
    rows = stratified.get(key) if isinstance(stratified, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        result.append(
            {
                "value": row.get("value"),
                **_redacted_summary(row),
            }
        )
    return result


def _redacted_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for name, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
        result[str(name)] = {
            "eligible_count": int(raw.get("eligible_count") or 0),
            "mature_count": int(raw.get("mature_count") or 0),
            "unavailable_count": int(raw.get("unavailable_count") or 0),
            "coverage_percent": _optional_number(raw.get("coverage_percent")),
        }
    return result


def _redacted_calibration(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "sample_count": 0}
    return {
        "status": value.get("status"),
        "reason": value.get("reason"),
        "metric": value.get("metric"),
        "sample_count": int(value.get("sample_count") or 0),
        "minimum_sample_count": int(value.get("minimum_sample_count") or 0),
        "ece": _optional_number(value.get("ece")),
        "brier": _optional_number(value.get("brier")),
    }


def _redacted_abstention(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"quality_status": "unavailable"}
    return {
        "quality_status": value.get("quality_status"),
        "reason": value.get("reason"),
        "event_horizon_count": int(value.get("event_horizon_count") or 0),
        "abstained_count": int(value.get("abstained_count") or 0),
        "decision_coverage_percent": _optional_number(
            value.get("decision_coverage_percent")
        ),
        "shadow_label_count": int(value.get("shadow_label_count") or 0),
        "shadow_label_coverage_percent": _optional_number(
            value.get("shadow_label_coverage_percent")
        ),
        "correct_abstention_rate_percent": _optional_number(
            value.get("correct_abstention_rate_percent")
        ),
    }


def _redacted_replay(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"eligible_count": 0, "ineligible_count": 0, "coverage_percent": None}
    return {
        "eligible_count": int(value.get("eligible_count") or 0),
        "ineligible_count": int(value.get("ineligible_count") or 0),
        "coverage_percent": _optional_number(value.get("coverage_percent")),
    }


def _redacted_claim_audits(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "audit_count": 0}
    return {
        key: value.get(key)
        for key in (
            "status",
            "audit_count",
            "classified_count",
            "unclassified_count",
            "coverage_percent",
            "clean_count",
            "sanitized_count",
            "violation_count",
        )
    }


def _redacted_candidate_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "case_count": 0}
    strata = value.get("stratified")
    redacted_strata = []
    if isinstance(strata, Sequence) and not isinstance(strata, (str, bytes)):
        for row in strata:
            if not isinstance(row, Mapping):
                continue
            dimensions = row.get("dimensions")
            dimension_map = dimensions if isinstance(dimensions, Mapping) else {}
            redacted_strata.append(
                {
                    "dimensions": {
                        key: _candidate_scalar(dimension_map.get(key))
                        for key in (
                            "horizon_trading_days",
                            "k",
                            "universe_stage",
                            "label_policy_version",
                            "selection_policy_version",
                        )
                    },
                    "aggregate": _redacted_candidate_aggregate(
                        row.get("aggregate")
                    ),
                    "readiness": _redacted_candidate_readiness(
                        row.get("readiness")
                    ),
                }
            )
    return {
        key: _candidate_scalar(value.get(key))
        for key in (
            "status",
            "case_count",
            "pit_eligible_case_count",
            "metric_available_case_count",
            "formal_case_count",
            "formal_pit_eligible_case_count",
            "formal_metric_available_case_count",
            "metric_available_case_coverage_percent",
            "ranking_algorithm",
            "metric_scope",
        )
    } | {
        "aggregate": _redacted_candidate_aggregate(value.get("aggregate")),
        "stratified": redacted_strata,
        "readiness": _redacted_candidate_readiness(value.get("readiness")),
        "capture_coverage": _redacted_candidate_capture_coverage(
            value.get("capture_coverage")
        ),
        "automatic_promotion_allowed": False,
    }


def _redacted_candidate_aggregate(value: Any) -> dict[str, Any]:
    mapping = value if isinstance(value, Mapping) else {}
    result = {
        key: _candidate_scalar(mapping.get(key))
        for key in ("case_count", "pit_eligible_case_count")
    }
    metric_fields = {
        "precision_at_k": (
            "status",
            "case_count",
            "macro_average",
            "micro_average",
            "numerator",
            "denominator",
        ),
        "ndcg_at_k": ("status", "case_count", "mean"),
        "regret_at_k": (
            "status",
            "case_count",
            "mean",
            "median",
            "utility_basis",
            "reason",
        ),
        "coverage": (
            "status",
            "mature_label_count",
            "universe_count",
            "universe_label_coverage_percent",
            "top_k_mature_label_count",
            "top_k_count",
            "top_k_label_coverage_percent",
            "selected_top_k_count",
            "selection_at_k_coverage_percent",
        ),
    }
    for key, fields in metric_fields.items():
        raw = mapping.get(key)
        metric = raw if isinstance(raw, Mapping) else {}
        result[key] = {
            field: _candidate_scalar(metric.get(field)) for field in fields
        }
        if key == "regret_at_k":
            bases = metric.get("observed_utility_bases")
            result[key]["observed_utility_bases"] = (
                [item for item in bases if isinstance(item, str)]
                if isinstance(bases, Sequence)
                and not isinstance(bases, (str, bytes))
                else []
            )
    return result


def _redacted_candidate_readiness(value: Any) -> dict[str, Any]:
    mapping = value if isinstance(value, Mapping) else {}
    return {
        key: _candidate_scalar(mapping.get(key))
        for key in (
            "status",
            "stratum_count",
            "eligible_stratum_count",
            "mature_decision_day_count",
            "mature_decision_date_basis",
            "declared_mature_decision_day_count",
            "fully_available_case_count",
            "fully_available_case_coverage_percent",
            "minimum_shadow_mature_decision_days",
            "minimum_manual_review_mature_decision_days",
            "minimum_manual_review_coverage_percent",
        )
    } | {"automatic_promotion_allowed": False}


def _redacted_candidate_capture_coverage(value: Any) -> dict[str, Any]:
    mapping = value if isinstance(value, Mapping) else {}
    result = {
        key: _candidate_scalar(mapping.get(key))
        for key in (
            "observed_capture_count",
            "eligible_capture_count",
            "capture_late_count",
            "capture_ineligible_count",
            "invalid_capture_count",
        )
    }
    for field in ("status_counts", "reason_counts"):
        raw = mapping.get(field)
        result[field] = (
            {
                str(key): int(count)
                for key, count in raw.items()
                if isinstance(key, str)
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
            }
            if isinstance(raw, Mapping)
            else {}
        )
    return result


def _candidate_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _redacted_paired_gate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "status": "not_evaluated",
            "automatic_promotion_allowed": False,
        }
    return {
        key: value.get(key)
        for key in (
            "schema_version",
            "evaluation_as_of",
            "status",
            "reason_codes",
            "policy_id",
            "policy_hash",
            "paired_case_count",
            "champion_case_count",
            "challenger_case_count",
            "mean_utility_delta",
            "mean_risk_delta",
            "challenger_claim_violation_rate",
            "challenger_claim_sanitized_rate",
            "threshold_results",
            "gate_hash",
        )
    } | {"automatic_promotion_allowed": False}


def _redacted_prompt_shadow_gate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "status": "not_evaluated",
            "automatic_promotion_allowed": False,
        }
    scalar_fields = (
        "schema_version",
        "evaluator_version",
        "evaluation_as_of",
        "status",
        "policy_id",
        "policy_hash",
        "stratum_hash",
        "assigned_registration_count",
        "paired_case_count",
        "formal_paired_case_count",
        "differing_case_count",
        "mature_decision_day_count",
        "paired_label_coverage",
        "challenger_valid_completion_count",
        "challenger_timeout_count",
        "challenger_invalid_count",
        "challenger_valid_completion_rate",
        "challenger_timeout_rate",
        "challenger_invalid_rate",
        "integrity_failure_count",
        "tenant_failure_count",
        "guard_failure_count",
        "budget_violation_count",
        "champion_sanitized_rate",
        "challenger_sanitized_rate",
        "sanitized_rate_delta",
        "mean_utility_delta_pp",
        "utility_sign_flip_p_value",
        "mean_drawdown_delta_pp",
        "drawdown_sign_flip_p_value",
        "day_cluster_count",
        "gate_hash",
    )
    result = {key: _candidate_scalar(value.get(key)) for key in scalar_fields}
    reason_codes = value.get("reason_codes")
    result["reason_codes"] = (
        [item for item in reason_codes if isinstance(item, str)]
        if isinstance(reason_codes, Sequence)
        and not isinstance(reason_codes, (str, bytes))
        else []
    )
    for key in ("utility_ci95_pp", "drawdown_ci95_pp"):
        raw = value.get(key)
        result[key] = (
            {
                "lower": _optional_number(raw.get("lower")),
                "upper": _optional_number(raw.get("upper")),
            }
            if isinstance(raw, Mapping)
            else None
        )
    threshold_results = value.get("threshold_results")
    result["threshold_results"] = (
        {
            str(key): bool(item)
            for key, item in threshold_results.items()
            if isinstance(key, str) and isinstance(item, bool)
        }
        if isinstance(threshold_results, Mapping)
        else {}
    )
    result["automatic_promotion_allowed"] = False
    return result


def _partition_artifacts(
    rows: Sequence[Mapping[str, Any]],
    *,
    artifact_receipts: Sequence[Mapping[str, Any]],
    provider_receipts: Sequence[Mapping[str, Any]],
    evaluation_as_of: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "claim_audits": [],
        "abstention_shadow_labels": [],
        "candidate_selection_cases": [],
        "champion_cases": [],
        "challenger_cases": [],
        "gate_policy": None,
        "used_artifact_rows": [],
        "ignored_artifact_rows": [],
        "ignored_artifact_count": 0,
        "used_artifact_receipts": [],
        "used_provider_receipts": [],
        "candidate_capture_records": [],
        "prompt_shadow_gate": None,
        "prompt_shadow_gate_history": [],
        "prompt_shadow_manifest": {
            "artifact_rows": [],
            "artifact_receipt_rows": [],
            "registration_count": 0,
            "paired_case_refs": [],
            "gate_refs": [],
        },
    }
    artifact_receipts_by_artifact = _artifact_receipts_by_artifact_id(
        artifact_receipts
    )
    provider_receipts_by_id = _provider_receipts_by_id(provider_receipts)
    candidate_audits: dict[str, Mapping[str, Any]] = {}
    candidate_capture_failures: dict[str, Mapping[str, Any]] = {}
    candidate_outcomes: dict[str, list[Mapping[str, Any]]] = {}
    candidate_source_reports: dict[str, str] = {}
    deferred_candidate_rows: set[str] = set()
    prompt_shadow_rows: list[Mapping[str, Any]] = []
    sorted_rows = sorted(
        rows,
        key=lambda item: str(_row_payload(item).get("artifact_id") or ""),
    )
    for row in sorted_rows:
        envelope = _row_payload(row)
        artifact = envelope.get("artifact")
        if not isinstance(artifact, Mapping):
            raise DecisionQualitySnapshotContractError(
                "decision-quality input artifact payload is missing"
            )
        value = dict(artifact)
        artifact_type = str(envelope.get("artifact_type") or "").strip().lower()
        schema = str(value.get("schema_version") or "").strip()
        artifact_id = str(envelope.get("artifact_id") or "")
        if (
            artifact_type == CANDIDATE_AUDIT_ARTIFACT_TYPE
            and schema == CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
        ):
            if not artifact_id or artifact_id in candidate_audits:
                raise DecisionQualitySnapshotContractError(
                    "candidate audit artifact identity is duplicated or missing"
                )
            source_report_id = str(envelope.get("source_report_id") or "")
            if (
                not source_report_id
                or source_report_id in candidate_source_reports
            ):
                raise DecisionQualitySnapshotContractError(
                    "native candidate capture identity is duplicated or missing"
                )
            candidate_source_reports[source_report_id] = artifact_id
            candidate_audits[artifact_id] = row
            deferred_candidate_rows.add(artifact_id)
            continue
        if (
            artifact_type == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE
            or schema == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
        ):
            if not (
                artifact_type == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE
                and schema == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
            ):
                result["ignored_artifact_count"] += 1
                result["ignored_artifact_rows"].append(row)
                continue
            source_report_id = str(envelope.get("source_report_id") or "")
            if (
                not artifact_id
                or artifact_id in candidate_capture_failures
                or not source_report_id
                or source_report_id in candidate_source_reports
            ):
                raise DecisionQualitySnapshotContractError(
                    "native candidate capture identity is duplicated or missing"
                )
            candidate_source_reports[source_report_id] = artifact_id
            candidate_capture_failures[artifact_id] = row
            deferred_candidate_rows.add(artifact_id)
            continue
        if (
            artifact_type == CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE
            or schema == CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
        ):
            if not (
                artifact_type == CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE
                and schema == CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
            ):
                result["ignored_artifact_count"] += 1
                result["ignored_artifact_rows"].append(row)
                continue
            audit_id = str(value.get("audit_artifact_id") or "")
            if not audit_id:
                raise DecisionQualitySnapshotContractError(
                    "candidate outcome set has no audit artifact identity"
                )
            candidate_outcomes.setdefault(audit_id, []).append(row)
            deferred_candidate_rows.add(artifact_id)
            continue
        if _is_prompt_shadow_artifact(artifact_type, schema):
            prompt_shadow_rows.append(row)
            continue
        consumed = True
        if schema == CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION or artifact_type in {
            "claim_audit_wrapper",
            "decision_quality_claim_audit_wrapper",
        }:
            result["claim_audits"].append(value)
        elif artifact_type in {
            "abstention_shadow_label",
            "decision_quality_abstention_shadow_label",
        }:
            result["abstention_shadow_labels"].append(value)
        elif artifact_type in {
            "candidate_selection_case",
            "decision_quality_candidate_selection_case",
        } or {"audit", "outcome_labels"} <= set(value):
            # Pre-D4 direct cases accepted caller-authored labels and had no
            # immutable join to the original audit/outcome receipts.  Keep
            # them manifested, but never mix them into formal D4 metrics.
            consumed = False
            result["ignored_artifact_count"] += 1
            result["ignored_artifact_rows"].append(row)
        elif schema == PAIRED_CASE_SCHEMA_VERSION or artifact_type in {
            "paired_case",
            "paired_champion_case",
            "paired_challenger_case",
        }:
            inferred_role = (
                "champion"
                if "champion" in artifact_type
                else "challenger"
                if "challenger" in artifact_type
                else ""
            )
            role = str(
                value.get("variant_role")
                or value.get("role")
                or inferred_role
            ).strip().lower()
            if role == "champion":
                result["champion_cases"].append(value)
            elif role == "challenger":
                result["challenger_cases"].append(value)
            else:
                raise DecisionQualitySnapshotContractError(
                    "paired decision-quality artifact has no champion/challenger role"
                )
        elif schema == GATE_POLICY_SCHEMA_VERSION or artifact_type in {
            "gate_policy",
            "decision_quality_gate_policy",
        }:
            # Rows are sorted by content id for deterministic evaluation.  A
            # deployment must publish only one active preregistered policy per
            # evaluation window; multiple distinct policies are ambiguous.
            prior = result["gate_policy"]
            if prior is not None and prior != value:
                raise DecisionQualitySnapshotContractError(
                    "multiple gate policies are active in one evaluation window"
                )
            result["gate_policy"] = value
        else:
            consumed = False
            result["ignored_artifact_count"] += 1
            result["ignored_artifact_rows"].append(row)
        if consumed:
            result["used_artifact_rows"].append(row)

    unmatched_outcomes = set(candidate_outcomes) - set(candidate_audits)
    if unmatched_outcomes:
        raise DecisionQualitySnapshotContractError(
            "candidate outcome set has no matching frozen audit"
        )
    for audit_id, audit_row in sorted(candidate_audits.items()):
        audit_source_row = {
            **dict(audit_row),
            "user_id": int(audit_row.get("userId") or 0),
        }
        capture = _classify_native_candidate_audit_capture(audit_source_row)
        base_target = capture.get("base_target")
        audit_receipt = artifact_receipts_by_artifact.get(audit_id)
        audit_receipt_status = _candidate_artifact_receipt_status(
            artifact_row=audit_row,
            receipt_row=audit_receipt,
            evaluation_as_of=evaluation_as_of,
        )
        target = base_target
        if capture["status"] == "eligible" and audit_receipt is not None:
            try:
                target = candidate_target_from_artifact(
                    audit_source_row,
                    artifact_receipt=audit_receipt,
                )
            except CandidateAuditCommitReceiptLate:
                audit_receipt_status = "late"
            except CandidateAuditSourceCaptureLate as exc:
                capture = {
                    **capture,
                    "status": "capture_late",
                    "reason": "source_capture_delay_exceeded",
                    "source_capture_delay_seconds": exc.delay_seconds,
                }
                target = None
            except CandidateSelectionSettlementError as exc:
                raise DecisionQualitySnapshotContractError(
                    "candidate audit commit receipt failed deterministic binding"
                ) from exc
            else:
                audit_receipt_status = "verified"
            if capture["status"] == "eligible" and target is None:
                raise DecisionQualitySnapshotContractError(
                    "verified D4 audit did not produce a formal target"
                )
        outcome_rows = candidate_outcomes.get(audit_id, [])
        if len(outcome_rows) > 1:
            raise DecisionQualitySnapshotContractError(
                "multiple candidate outcome sets share one frozen audit"
            )
        outcome_row = outcome_rows[0] if outcome_rows else None
        if capture["status"] != "eligible" and outcome_row is not None:
            raise DecisionQualitySnapshotContractError(
                "nonformal candidate capture cannot have a terminal outcome"
            )
        if audit_receipt is None and outcome_row is not None:
            raise DecisionQualitySnapshotContractError(
                "candidate outcome cannot precede its audit commit receipt"
            )
        if audit_receipt_status == "late" and outcome_row is not None:
            raise DecisionQualitySnapshotContractError(
                "late candidate audit receipt cannot have a terminal outcome"
            )
        outcome_receipt = None
        if outcome_row is not None:
            outcome_id = str(
                _row_payload(outcome_row).get("artifact_id") or ""
            )
            outcome_receipt = artifact_receipts_by_artifact.get(outcome_id)
            if outcome_receipt is not None:
                _validate_artifact_receipt_binding(
                    receipt_row=outcome_receipt,
                    artifact_row=outcome_row,
                    evaluation_as_of=evaluation_as_of,
                )
        if capture["status"] == "eligible":
            assert isinstance(target, Mapping)
            case, used_provider_rows = _candidate_case_from_artifacts(
                target=target,
                audit_receipt_status=audit_receipt_status,
                audit_receipt_row=audit_receipt,
                audit_source_row=audit_row,
                outcome_row=outcome_row,
                outcome_receipt_row=outcome_receipt,
                provider_receipts_by_id=provider_receipts_by_id,
                evaluation_as_of=evaluation_as_of,
            )
        else:
            case = _candidate_nonformal_case_from_artifact(
                artifact_row=audit_row,
                capture=capture,
                audit_receipt_status=audit_receipt_status,
                audit_receipt_row=audit_receipt,
            )
            used_provider_rows = []
        result["candidate_selection_cases"].append(case)
        result["candidate_capture_records"].append(
            _candidate_capture_record(case)
        )
        result["used_artifact_rows"].append(audit_row)
        if audit_receipt is not None:
            result["used_artifact_receipts"].append(audit_receipt)
        if outcome_row is not None:
            result["used_artifact_rows"].append(outcome_row)
        if outcome_receipt is not None:
            result["used_artifact_receipts"].append(outcome_receipt)
        result["used_provider_receipts"].extend(used_provider_rows)

    for sentinel_id, sentinel_row in sorted(candidate_capture_failures.items()):
        audit_receipt = artifact_receipts_by_artifact.get(sentinel_id)
        receipt_status = _candidate_artifact_receipt_status(
            artifact_row=sentinel_row,
            receipt_row=audit_receipt,
            evaluation_as_of=evaluation_as_of,
        )
        case = _candidate_capture_failure_case(
            artifact_row=sentinel_row,
            audit_receipt_status=receipt_status,
            audit_receipt_row=audit_receipt,
        )
        result["candidate_selection_cases"].append(case)
        result["candidate_capture_records"].append(
            _candidate_capture_record(case)
        )
        result["used_artifact_rows"].append(sentinel_row)
        if audit_receipt is not None:
            result["used_artifact_receipts"].append(audit_receipt)

    prompt_partition = _evaluate_prompt_shadow_artifacts(
        prompt_shadow_rows,
        artifact_receipts_by_artifact=artifact_receipts_by_artifact,
        candidate_selection_cases=result["candidate_selection_cases"],
        evaluation_as_of=evaluation_as_of,
    )
    result["prompt_shadow_gate"] = prompt_partition["selected_gate"]
    result["prompt_shadow_gate_history"] = prompt_partition["gates"]
    result["prompt_shadow_manifest"] = prompt_partition["manifest"]
    result["used_artifact_rows"].extend(prompt_shadow_rows)
    result["used_artifact_receipts"].extend(
        prompt_partition["used_artifact_receipts"]
    )

    # A legacy audit that was ignored cannot have a formal outcome set.  Every
    # other deferred row was consumed above; retain an explicit accounting
    # assertion so a new schema cannot silently disappear from the manifest.
    accounted_ids = {
        str(_row_payload(row).get("artifact_id") or "")
        for row in (
            result["used_artifact_rows"] + result["ignored_artifact_rows"]
        )
    }
    if deferred_candidate_rows - accounted_ids:
        raise DecisionQualitySnapshotContractError(
            "candidate decision-quality artifacts were not fully accounted"
        )
    result["used_artifact_receipts"] = _deduplicate_receipt_rows(
        result["used_artifact_receipts"], prefix="dqr_"
    )
    result["used_provider_receipts"] = _deduplicate_receipt_rows(
        result["used_provider_receipts"], prefix="dqpr_"
    )
    return result


_PROMPT_SHADOW_ARTIFACT_SCHEMAS = {
    PROMPT_GATE_POLICY_ARTIFACT_TYPE: PROMPT_GATE_POLICY_SCHEMA_VERSION,
    PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE: (
        PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION
    ),
    PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE: PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
    PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE: PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
}


def _is_prompt_shadow_artifact(artifact_type: str, schema: str) -> bool:
    return _PROMPT_SHADOW_ARTIFACT_SCHEMAS.get(artifact_type) == schema


def _evaluate_prompt_shadow_artifacts(
    rows: Sequence[Mapping[str, Any]],
    *,
    artifact_receipts_by_artifact: Mapping[str, Mapping[str, Any]],
    candidate_selection_cases: Sequence[Mapping[str, Any]],
    evaluation_as_of: datetime,
) -> dict[str, Any]:
    """Join current D5 evidence and evaluate each policy/transport stratum."""

    policies: dict[str, dict[str, Any]] = {}
    policy_rows: dict[str, Mapping[str, Any]] = {}
    registrations: dict[str, dict[str, Any]] = {}
    registration_rows: dict[str, Mapping[str, Any]] = {}
    attempts: dict[tuple[str, str], dict[str, Any]] = {}
    attempt_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
    outputs: dict[tuple[str, str], dict[str, Any]] = {}
    output_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
    used_receipts: list[Mapping[str, Any]] = []
    tenant_ids = {int(row.get("userId") or 0) for row in rows}
    if rows and (len(tenant_ids) != 1 or next(iter(tenant_ids)) <= 0):
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow evidence crossed its tenant boundary"
        )
    user_id = next(iter(tenant_ids), 0)

    try:
        for row in rows:
            envelope = _row_payload(row)
            artifact = envelope.get("artifact")
            if not isinstance(artifact, Mapping):
                raise DecisionQualitySnapshotContractError(
                    "prompt-shadow inner artifact is missing"
                )
            artifact_id = str(envelope.get("artifact_id") or "")
            receipt = artifact_receipts_by_artifact.get(artifact_id)
            if receipt is not None:
                _validate_artifact_receipt_binding(
                    receipt_row=receipt,
                    artifact_row=row,
                    evaluation_as_of=evaluation_as_of,
                )
                used_receipts.append(receipt)
            artifact_type = str(envelope.get("artifact_type") or "")
            if artifact_type == PROMPT_GATE_POLICY_ARTIFACT_TYPE:
                normalized = normalize_prompt_gate_policy(artifact)
                identity = normalized["policy_hash"]
                if identity in policies:
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow policy identity is duplicated"
                    )
                policies[identity] = normalized
                policy_rows[identity] = row
            elif artifact_type == PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE:
                normalized = normalize_prompt_shadow_registration(
                    artifact, expected_user_id=user_id
                )
                identity = normalized["run_id"]
                if identity in registrations:
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow registration identity is duplicated"
                    )
                registrations[identity] = normalized
                registration_rows[identity] = row
            elif artifact_type == PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE:
                normalized = normalize_prompt_shadow_attempt(
                    artifact, expected_user_id=user_id
                )
                identity = (normalized["run_id"], normalized["role"])
                if identity in attempts:
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow attempt identity is duplicated"
                    )
                attempts[identity] = normalized
                attempt_rows[identity] = row
            elif artifact_type == PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE:
                normalized = normalize_prompt_shadow_output(
                    artifact, expected_user_id=user_id
                )
                identity = (normalized["run_id"], normalized["role"])
                if identity in outputs:
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow output identity is duplicated"
                    )
                outputs[identity] = normalized
                output_rows[identity] = row
    except (PromptShadowContractError, PromptShadowEvaluationError) as exc:
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow evidence failed its immutable contract"
        ) from exc

    orphan_run_ids = {
        run_id for run_id, _role in (*attempts.keys(), *outputs.keys())
    } - set(registrations)
    if orphan_run_ids:
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow attempt or output has no registration"
        )

    candidate_by_audit = {
        str(case.get("audit_artifact_id") or ""): case
        for case in candidate_selection_cases
        if case.get("audit_artifact_id")
    }
    if len(candidate_by_audit) != sum(
        bool(case.get("audit_artifact_id")) for case in candidate_selection_cases
    ):
        raise DecisionQualitySnapshotContractError(
            "candidate label identity is duplicated for prompt-shadow evaluation"
        )

    cases: list[dict[str, Any]] = []
    normalized_registrations: list[dict[str, Any]] = []
    try:
        for run_id, registration in sorted(registrations.items()):
            policy_hash = registration["policy_ref"]["policy_hash"]
            policy = policies.get(policy_hash)
            if policy is None:
                raise DecisionQualitySnapshotContractError(
                    "prompt-shadow registration references a missing policy"
                )
            registration = normalize_prompt_shadow_registration(
                registration, policy=policy, expected_user_id=user_id
            )
            policy_receipt_ref = _prompt_shadow_receipt_ref(
                policy_rows[policy_hash], artifact_receipts_by_artifact
            )
            if policy_receipt_ref is None or not _prompt_receipt_binding_matches(
                registration["policy_ref"], policy_receipt_ref
            ):
                raise DecisionQualitySnapshotContractError(
                    "prompt-shadow registration is detached from its policy receipt"
                )
            normalized_registrations.append(registration)
            identities = [
                (run_id, "champion"),
                (run_id, "challenger"),
            ]
            registration_receipt_ref = _prompt_shadow_receipt_ref(
                registration_rows[run_id], artifact_receipts_by_artifact
            )
            for identity in identities:
                attempt = attempts.get(identity)
                output = outputs.get(identity)
                if output is not None and attempt is None:
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow output has no preregistered attempt"
                    )
                if attempt is None:
                    continue
                normalized_attempt = normalize_prompt_shadow_attempt(
                    attempt,
                    registration=registration,
                    expected_user_id=user_id,
                )
                if registration_receipt_ref is None or not (
                    _prompt_receipt_binding_matches(
                        normalized_attempt["policy_ref"], policy_receipt_ref
                    )
                    and _prompt_receipt_binding_matches(
                        normalized_attempt["registration_ref"],
                        registration_receipt_ref,
                    )
                ):
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow attempt is detached from preregistered receipts"
                    )
                attempts[identity] = normalized_attempt
                if output is None:
                    continue
                attempt_receipt_ref = _prompt_shadow_receipt_ref(
                    attempt_rows[identity], artifact_receipts_by_artifact
                )
                normalized_output = normalize_prompt_shadow_output(
                    output,
                    registration=registration,
                    attempt=normalized_attempt,
                    expected_user_id=user_id,
                )
                if attempt_receipt_ref is None or not (
                    _prompt_receipt_binding_matches(
                        normalized_output["policy_ref"], policy_receipt_ref
                    )
                    and _prompt_receipt_binding_matches(
                        normalized_output["registration_ref"],
                        registration_receipt_ref,
                    )
                    and _prompt_receipt_binding_matches(
                        normalized_output["attempt_ref"], attempt_receipt_ref
                    )
                ):
                    raise DecisionQualitySnapshotContractError(
                        "prompt-shadow output is detached from preregistered receipts"
                    )
                outputs[identity] = normalized_output
            if not all(identity in attempts and identity in outputs for identity in identities):
                continue
            champion_attempt = normalize_prompt_shadow_attempt(
                attempts[(run_id, "champion")],
                registration=registration,
                expected_user_id=user_id,
            )
            challenger_attempt = normalize_prompt_shadow_attempt(
                attempts[(run_id, "challenger")],
                registration=registration,
                expected_user_id=user_id,
            )
            champion_output = normalize_prompt_shadow_output(
                outputs[(run_id, "champion")],
                registration=registration,
                attempt=champion_attempt,
                expected_user_id=user_id,
            )
            challenger_output = normalize_prompt_shadow_output(
                outputs[(run_id, "challenger")],
                registration=registration,
                attempt=challenger_attempt,
                expected_user_id=user_id,
            )
            audit_id = str(
                champion_output["candidate_audit_ref"].get("artifact_id") or ""
            )
            case = build_prompt_shadow_paired_case(
                policy=policy,
                policy_receipt=_prompt_shadow_receipt_ref(
                    policy_rows[policy_hash], artifact_receipts_by_artifact
                ),
                registration=registration,
                registration_receipt=_prompt_shadow_receipt_ref(
                    registration_rows[run_id], artifact_receipts_by_artifact
                ),
                champion_attempt=champion_attempt,
                champion_attempt_receipt=_prompt_shadow_receipt_ref(
                    attempt_rows[(run_id, "champion")],
                    artifact_receipts_by_artifact,
                ),
                champion_output=champion_output,
                champion_output_receipt=_prompt_shadow_receipt_ref(
                    output_rows[(run_id, "champion")],
                    artifact_receipts_by_artifact,
                ),
                challenger_attempt=challenger_attempt,
                challenger_attempt_receipt=_prompt_shadow_receipt_ref(
                    attempt_rows[(run_id, "challenger")],
                    artifact_receipts_by_artifact,
                ),
                challenger_output=challenger_output,
                challenger_output_receipt=_prompt_shadow_receipt_ref(
                    output_rows[(run_id, "challenger")],
                    artifact_receipts_by_artifact,
                ),
                candidate_case=candidate_by_audit.get(audit_id),
                evaluation_as_of=evaluation_as_of,
                expected_user_id=user_id,
            )
            cases.append(case)
    except (PromptShadowContractError, PromptShadowEvaluationError) as exc:
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow paired evaluation failed closed"
        ) from exc

    registrations_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for registration in normalized_registrations:
        key = (
            registration["policy_ref"]["policy_hash"],
            prompt_shadow_stratum_hash(registration),
        )
        registrations_by_group.setdefault(key, []).append(registration)
    cases_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in cases:
        key = (case["policy_hash"], case["stratum_hash"])
        cases_by_group.setdefault(key, []).append(case)

    gates: list[dict[str, Any]] = []
    global_budget_violations = _prompt_shadow_budget_violation_count(
        normalized_registrations,
        [
            attempt
            for (_run_id, role), attempt in attempts.items()
            if role == "challenger"
        ],
    )
    for key, group in sorted(registrations_by_group.items()):
        policy_hash, _stratum_hash = key
        try:
            gate = evaluate_prompt_shadow_gate(
                policy=policies[policy_hash],
                registrations=group,
                paired_cases=cases_by_group.get(key, []),
                evaluation_as_of=evaluation_as_of,
                budget_violation_count=global_budget_violations,
            )
        except PromptShadowEvaluationError as exc:
            raise DecisionQualitySnapshotContractError(
                "prompt-shadow statistical gate failed closed"
            ) from exc
        gates.append(gate)
    gates.sort(
        key=lambda gate: (
            str(policies[gate["policy_hash"]]["registered_at"]),
            str(gate["policy_hash"]),
            str(gate["stratum_hash"]),
        )
    )
    selected_gate = gates[-1] if gates else None
    return {
        "selected_gate": selected_gate,
        "gates": gates,
        "used_artifact_receipts": used_receipts,
        "manifest": {
            "artifact_rows": list(rows),
            "artifact_receipt_rows": used_receipts,
            "registration_count": len(registrations),
            "paired_case_refs": [
                {"case_id": case["case_id"], "content_hash": case["content_hash"]}
                for case in sorted(cases, key=lambda item: item["case_id"])
            ],
            "gate_refs": [
                {
                    "policy_hash": gate["policy_hash"],
                    "stratum_hash": gate["stratum_hash"],
                    "gate_hash": gate["gate_hash"],
                }
                for gate in gates
            ],
        },
    }


def _prompt_shadow_receipt_ref(
    artifact_row: Mapping[str, Any],
    receipts_by_artifact: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    envelope = _row_payload(artifact_row)
    receipt_row = receipts_by_artifact.get(str(envelope.get("artifact_id") or ""))
    if receipt_row is None:
        return None
    receipt = _row_payload(receipt_row)
    return {
        "user_id": int(receipt.get("user_id") or 0),
        "artifact_id": str(receipt.get("artifact_id") or ""),
        "artifact_type": str(receipt.get("artifact_type") or ""),
        "artifact_content_hash": str(receipt.get("artifact_content_hash") or ""),
        "receipt_id": str(receipt.get("receipt_id") or ""),
        "receipt_content_hash": str(receipt.get("content_hash") or ""),
        "source_row_created_at": str(receipt.get("source_row_created_at") or ""),
        "source_visible_at": str(receipt.get("source_visible_at") or ""),
    }


def _prompt_receipt_binding_matches(
    embedded: Mapping[str, Any], actual: Mapping[str, Any]
) -> bool:
    return all(
        embedded.get(key) == actual.get(key)
        for key in (
            "user_id",
            "artifact_id",
            "artifact_type",
            "artifact_content_hash",
            "receipt_id",
            "receipt_content_hash",
            "source_row_created_at",
            "source_visible_at",
        )
    )


def _prompt_shadow_budget_violation_count(
    registrations: Sequence[Mapping[str, Any]],
    challenger_attempts: Sequence[Mapping[str, Any]],
) -> int:
    registered_runs = {registration["run_id"] for registration in registrations}
    seen: set[tuple[str, str, int]] = set()
    violations = 0
    for attempt in challenger_attempts:
        if attempt["run_id"] not in registered_runs:
            violations += 1
            continue
        reservation = attempt.get("budget_reservation")
        if not isinstance(reservation, Mapping):
            violations += 1
            continue
        identity = (
            str(reservation.get("scope_key") or ""),
            str(reservation.get("budget_date_local") or ""),
            int(reservation.get("reserved_ordinal") or 0),
        )
        if identity in seen:
            violations += 1
        seen.add(identity)
    return violations


def _candidate_artifact_receipt_status(
    *,
    artifact_row: Mapping[str, Any],
    receipt_row: Mapping[str, Any] | None,
    evaluation_as_of: datetime,
) -> str:
    if receipt_row is None:
        return "pending"
    _validate_artifact_receipt_binding(
        receipt_row=receipt_row,
        artifact_row=artifact_row,
        evaluation_as_of=evaluation_as_of,
    )
    receipt = _row_payload(receipt_row)
    visible_at = _candidate_artifact_timestamp(
        receipt.get("source_visible_at"),
        "candidate artifact commit visibility clock",
    )
    source_created_at = _artifact_storage_created_at(artifact_row)
    return (
        "late"
        if (visible_at - source_created_at).total_seconds()
        > CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        else "verified"
    )


def _classify_native_candidate_audit_capture(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify live capture from immutable storage clocks, never report claims."""

    envelope = _row_payload(row)
    artifact = envelope.get("artifact")
    audit = artifact.get("audit") if isinstance(artifact, Mapping) else None
    plan = artifact.get("label_plan") if isinstance(artifact, Mapping) else None
    if not isinstance(artifact, Mapping) or not isinstance(audit, Mapping) or not isinstance(
        plan, Mapping
    ):
        raise DecisionQualitySnapshotContractError(
            "native candidate audit wrapper is incomplete"
        )
    decision_at = _candidate_artifact_timestamp(
        envelope.get("decision_at"), "candidate audit decision clock"
    )
    recorded_at = _candidate_artifact_timestamp(
        envelope.get("recorded_at"), "candidate audit recorded clock"
    )
    source_created_at = _artifact_storage_created_at(row)
    validation = validate_candidate_selection_audit(audit)
    registered_delay = (recorded_at - decision_at).total_seconds()
    registered_timely = (
        registered_delay <= CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
    )
    expected_envelope_eligible = envelope.get("audit_eligible") is True
    if expected_envelope_eligible and (
        envelope.get("store_authority") != "primary"
        or validation.get("decision_eligible") is not True
        or not registered_timely
    ):
        raise DecisionQualitySnapshotContractError(
            "native candidate audit eligibility conflicts with capture evidence"
        )
    expected_capture_status = (
        "capture_late"
        if not registered_timely
        else "eligible"
        if expected_envelope_eligible
        else "capture_ineligible"
    )
    expected_capture_reason = (
        "source_capture_delay_exceeded"
        if expected_capture_status == "capture_late"
        else "eligible"
        if expected_capture_status == "eligible"
        else "candidate_audit_not_decision_eligible"
        if validation.get("decision_eligible") is not True
        else "anchor_event_not_audit_eligible"
    )
    try:
        expected_plan = build_candidate_label_plan(
            decision_at=decision_at.isoformat(),
            registered_at=recorded_at.isoformat(),
            decision_eligible=expected_envelope_eligible,
        )
    except ValueError as exc:
        raise DecisionQualitySnapshotContractError(
            "native candidate label plan clocks are invalid"
        ) from exc
    source_report_id = str(envelope.get("source_report_id") or "")
    if (
        envelope.get("artifact_type") != CANDIDATE_AUDIT_ARTIFACT_TYPE
        or envelope.get("artifact_schema_version")
        != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
        or envelope.get("logical_key") != f"candidate_audit:{source_report_id}"
        or envelope.get("source_type") != "discovery"
        or envelope.get("decision_event_id") is not None
        or envelope.get("store_authority") != "primary"
        or artifact.get("schema_version")
        != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
        or artifact.get("source_report_id") != source_report_id
        or artifact.get("decision_at") != decision_at.isoformat()
        or artifact.get("recorded_at") != recorded_at.isoformat()
        or artifact.get("audit_snapshot_hash") != audit.get("snapshot_hash")
        or artifact.get("capture_validation") != validation
        or artifact.get("capture_mode") != CANDIDATE_CAPTURE_MODE
        or artifact.get("post_commit_receipt_required") is not True
        or artifact.get("formal_receipt_max_delay_seconds")
        != CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        or artifact.get("formal_source_capture_max_delay_seconds")
        != CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        or artifact.get("formal_source_capture_delay_basis")
        != "candidate_audit_source_row_created_at_minus_decision_at"
        or artifact.get("capture_status") != expected_capture_status
        or artifact.get("capture_reason") != expected_capture_reason
        or dict(plan) != expected_plan
    ):
        raise DecisionQualitySnapshotContractError(
            "native candidate audit capture contract is invalid"
        )
    actual_delay = (source_created_at - decision_at).total_seconds()
    if actual_delay > CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS:
        return {
            "status": "capture_late",
            "reason": "source_capture_delay_exceeded",
            "source_capture_delay_seconds": actual_delay,
            "base_target": None,
        }
    if not expected_envelope_eligible:
        return {
            "status": "capture_ineligible",
            "reason": expected_capture_reason,
            "source_capture_delay_seconds": actual_delay,
            "base_target": None,
        }
    try:
        base_target = candidate_preregistered_target_from_artifact(row)
    except CandidateSelectionSettlementError as exc:
        raise DecisionQualitySnapshotContractError(
            "candidate audit failed its D4 preregistration contract"
        ) from exc
    if base_target is None:
        raise DecisionQualitySnapshotContractError(
            "eligible native candidate audit has no preregistered target"
        )
    return {
        "status": "eligible",
        "reason": "eligible",
        "source_capture_delay_seconds": actual_delay,
        "base_target": base_target,
    }


def _candidate_receipt_case_fields(
    *,
    status: str,
    receipt_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    receipt = _row_payload(receipt_row) if receipt_row is not None else None
    return {
        "audit_commit_receipt_status": status,
        "audit_commit_receipt_id": (
            receipt.get("receipt_id") if receipt is not None else None
        ),
        "audit_commit_receipt_content_hash": (
            receipt.get("content_hash") if receipt is not None else None
        ),
        "audit_commit_receipt_source_visible_at": (
            receipt.get("source_visible_at") if receipt is not None else None
        ),
    }


def _candidate_nonformal_case_from_artifact(
    *,
    artifact_row: Mapping[str, Any],
    capture: Mapping[str, Any],
    audit_receipt_status: str,
    audit_receipt_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    envelope = _row_payload(artifact_row)
    artifact = envelope["artifact"]
    audit = artifact["audit"]
    plan = artifact["label_plan"]
    decision_at = _candidate_artifact_timestamp(
        envelope.get("decision_at"), "candidate decision clock"
    )
    source_created_at = _artifact_storage_created_at(artifact_row)
    artifact_id = str(envelope.get("artifact_id") or "")
    content_hash = str(envelope.get("content_hash") or "")
    case_id = "candidate_case_" + canonical_hash(
        {"capture_artifact_id": artifact_id, "capture_status": capture["status"]}
    )
    selection_policy = str(
        audit.get("versions", {}).get("selection_policy")
        if isinstance(audit.get("versions"), Mapping)
        else ""
    )
    declared_date = decision_at.astimezone(_CN_TZ).date().isoformat()
    return {
        "schema_version": CANDIDATE_SELECTION_CASE_SCHEMA_VERSION,
        "candidate_evaluator_version": CANDIDATE_SELECTION_EVALUATOR_VERSION,
        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
        "case_id": case_id,
        "recorded_at": source_created_at.isoformat(),
        "decision_at": decision_at.isoformat(),
        "audit_source_row_created_at": source_created_at.isoformat(),
        "capture_status": str(capture["status"]),
        "capture_reason": str(capture["reason"]),
        "capture_reason_hash": canonical_hash({"reason": capture["reason"]}),
        "source_capture_delay_seconds": float(
            capture["source_capture_delay_seconds"]
        ),
        "capture_artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
        "audit_artifact_id": artifact_id,
        "audit_content_hash": content_hash,
        "audit_snapshot_hash": str(artifact.get("audit_snapshot_hash") or ""),
        "label_plan_hash": str(plan.get("plan_hash") or ""),
        **_candidate_receipt_case_fields(
            status=audit_receipt_status,
            receipt_row=audit_receipt_row,
        ),
        "outcome_commit_receipt_status": "absent",
        "outcome_artifact_id": None,
        "outcome_content_hash": None,
        "outcome_commit_receipt_id": None,
        "outcome_commit_receipt_content_hash": None,
        "outcome_commit_receipt_source_visible_at": None,
        "provider_receipt_refs": [],
        "provider_receipt_count": 0,
        "provider_receipt_manifest_hash": canonical_hash([]),
        "provider_adapter_stratum": [],
        "provider_adapter_stratum_hash": candidate_provider_adapter_stratum_hash([]),
        "label_storage_created_at": None,
        "horizon_trading_days": int(plan["horizon_trading_days"]),
        "decision_date_local": str(plan["decision_date_local"]),
        "declared_decision_date_local": declared_date,
        "live_cohort_date_local": None,
        "label_policy_version": str(plan["policy_version"]),
        "selection_policy_version": selection_policy,
        "audit": deepcopy(dict(audit)),
        "outcome_labels": {},
        "k": int(plan["k"]),
        "universe_stage": str(plan["universe_stage"]),
        "automatic_promotion_allowed": False,
    }


def _candidate_capture_failure_case(
    *,
    artifact_row: Mapping[str, Any],
    audit_receipt_status: str,
    audit_receipt_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    envelope = _row_payload(artifact_row)
    sentinel = envelope.get("artifact")
    if not isinstance(sentinel, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate capture failure sentinel is missing"
        )
    expected_fields = {
        "schema_version",
        "source_report_id",
        "decision_at",
        "recorded_at",
        "capture_mode",
        "capture_status",
        "capture_reason",
        "capture_reason_hash",
        "formal_source_capture_max_delay_seconds",
        "formal_receipt_max_delay_seconds",
        "post_commit_receipt_required",
        "automatic_promotion_allowed",
    }
    reason = str(sentinel.get("capture_reason") or "")
    source_report_id = str(envelope.get("source_report_id") or "")
    decision_at = _candidate_artifact_timestamp(
        envelope.get("decision_at"), "candidate capture failure decision clock"
    )
    recorded_at = _candidate_artifact_timestamp(
        envelope.get("recorded_at"), "candidate capture failure recorded clock"
    )
    source_created_at = _artifact_storage_created_at(artifact_row)
    if (
        set(sentinel) != expected_fields
        or envelope.get("artifact_type")
        != CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE
        or envelope.get("artifact_schema_version")
        != CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
        or envelope.get("logical_key")
        != f"candidate_capture_failure:{source_report_id}"
        or envelope.get("source_type") != "discovery"
        or envelope.get("decision_event_id") is not None
        or envelope.get("store_authority") != "primary"
        or envelope.get("audit_eligible") is not False
        or sentinel.get("schema_version")
        != CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
        or sentinel.get("source_report_id") != source_report_id
        or sentinel.get("decision_at") != decision_at.isoformat()
        or sentinel.get("recorded_at") != recorded_at.isoformat()
        or sentinel.get("capture_mode") != CANDIDATE_CAPTURE_MODE
        or sentinel.get("capture_status") != "capture_ineligible"
        or reason not in {
            "candidate_selection_audit_missing",
            "candidate_selection_audit_not_mapping",
        }
        or sentinel.get("capture_reason_hash")
        != canonical_hash({"reason": reason})
        or sentinel.get("formal_source_capture_max_delay_seconds")
        != CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        or sentinel.get("formal_receipt_max_delay_seconds")
        != CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        or sentinel.get("post_commit_receipt_required") is not True
        or sentinel.get("automatic_promotion_allowed") is not False
        or not decision_at <= recorded_at <= source_created_at
    ):
        raise DecisionQualitySnapshotContractError(
            "candidate capture failure sentinel contract is invalid"
        )
    artifact_id = str(envelope.get("artifact_id") or "")
    content_hash = str(envelope.get("content_hash") or "")
    declared_date = decision_at.astimezone(_CN_TZ).date().isoformat()
    return {
        "schema_version": CANDIDATE_SELECTION_CASE_SCHEMA_VERSION,
        "candidate_evaluator_version": CANDIDATE_SELECTION_EVALUATOR_VERSION,
        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
        "case_id": "candidate_case_" + canonical_hash(
            {"capture_failure_artifact_id": artifact_id}
        ),
        "recorded_at": source_created_at.isoformat(),
        "decision_at": decision_at.isoformat(),
        "audit_source_row_created_at": source_created_at.isoformat(),
        "capture_status": "capture_ineligible",
        "capture_reason": reason,
        "capture_reason_hash": str(sentinel["capture_reason_hash"]),
        "source_capture_delay_seconds": (
            source_created_at - decision_at
        ).total_seconds(),
        "capture_artifact_type": CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
        "audit_artifact_id": artifact_id,
        "audit_content_hash": content_hash,
        "audit_snapshot_hash": canonical_hash(dict(sentinel)),
        "label_plan_hash": canonical_hash(
            {"capture_failure_artifact_id": artifact_id}
        ),
        **_candidate_receipt_case_fields(
            status=audit_receipt_status,
            receipt_row=audit_receipt_row,
        ),
        "outcome_commit_receipt_status": "absent",
        "outcome_artifact_id": None,
        "outcome_content_hash": None,
        "outcome_commit_receipt_id": None,
        "outcome_commit_receipt_content_hash": None,
        "outcome_commit_receipt_source_visible_at": None,
        "provider_receipt_refs": [],
        "provider_receipt_count": 0,
        "provider_receipt_manifest_hash": canonical_hash([]),
        "provider_adapter_stratum": [],
        "provider_adapter_stratum_hash": candidate_provider_adapter_stratum_hash([]),
        "label_storage_created_at": None,
        "horizon_trading_days": 20,
        "decision_date_local": declared_date,
        "declared_decision_date_local": declared_date,
        "live_cohort_date_local": None,
        "label_policy_version": CANDIDATE_LABEL_POLICY_VERSION,
        "selection_policy_version": "capture_unavailable",
        "audit": None,
        "outcome_labels": {},
        "k": 3,
        "universe_stage": "prescreen",
        "automatic_promotion_allowed": False,
    }


def _candidate_capture_record(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(case.get("case_id") or ""),
        "artifact_id": str(case.get("audit_artifact_id") or ""),
        "artifact_type": str(case.get("capture_artifact_type") or ""),
        "capture_status": str(case.get("capture_status") or ""),
        "capture_reason": str(case.get("capture_reason") or ""),
        "declared_decision_date_local": case.get(
            "declared_decision_date_local"
        ),
        "live_cohort_date_local": case.get("live_cohort_date_local"),
    }


_PROVIDER_REF_FIELDS = frozenset(
    {
        "receipt_id",
        "content_hash",
        "provider",
        "operation",
        "capture_mode",
        "request_hash",
        "adapter_output_sha256",
        "normalized_payload_hash",
        "origin_fetched_at",
        "completed_at",
        "origin_receipt_hash",
        "adapter_policy_id",
        "adapter_policy_hash",
        "adapter_contract_version",
        "adapter_script_sha256",
        "adapter_policy_script_sha256",
        "adapter_library_name",
        "adapter_library_version",
        "adapter_python_version",
    }
)


def _artifact_receipts_by_artifact_id(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        artifact_id = str(_row_payload(row).get("artifact_id") or "")
        if not artifact_id or artifact_id in result:
            raise DecisionQualitySnapshotContractError(
                "artifact commit receipt binding is duplicated or missing"
            )
        result[artifact_id] = row
    return result


def _provider_receipts_by_id(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        receipt_id = str(_row_payload(row).get("receipt_id") or "")
        if not receipt_id or receipt_id in result:
            raise DecisionQualitySnapshotContractError(
                "provider receipt binding is duplicated or missing"
            )
        result[receipt_id] = row
    return result


def _deduplicate_receipt_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
) -> list[Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        receipt_id = str(_row_payload(row).get("receipt_id") or "")
        if not receipt_id.startswith(prefix):
            raise DecisionQualitySnapshotContractError(
                "decision-quality receipt identity is invalid"
            )
        prior = result.get(receipt_id)
        if prior is not None and canonical_json(_row_payload(prior)) != canonical_json(
            _row_payload(row)
        ):
            raise DecisionQualitySnapshotContractError(
                "decision-quality receipt identity has conflicting content"
            )
        result[receipt_id] = row
    return [result[key] for key in sorted(result)]


def _validate_artifact_receipt_binding(
    *,
    receipt_row: Mapping[str, Any],
    artifact_row: Mapping[str, Any],
    evaluation_as_of: datetime,
) -> None:
    receipt = _row_payload(receipt_row)
    artifact = _row_payload(artifact_row)
    source_created_at = _artifact_storage_created_at(artifact_row)
    source_visible_at = _candidate_artifact_timestamp(
        receipt.get("source_visible_at"),
        "artifact receipt visibility clock",
    )
    receipt_created_at = _candidate_artifact_timestamp(
        receipt_row.get("created_at"),
        "artifact receipt storage clock",
    )
    receipt_user_id = int(receipt.get("user_id") or 0)
    artifact_user_id = int(artifact_row.get("userId") or 0)
    if (
        receipt.get("artifact_id") != artifact.get("artifact_id")
        or receipt.get("artifact_type") != artifact.get("artifact_type")
        or receipt.get("artifact_content_hash") != artifact.get("content_hash")
        or receipt.get("store_authority") != "primary"
        or artifact.get("store_authority") != "primary"
        or _candidate_artifact_timestamp(
            receipt.get("source_row_created_at"),
            "artifact receipt source row clock",
        )
        != source_created_at
        or source_visible_at < source_created_at
        or source_visible_at != receipt_created_at
        or source_visible_at > evaluation_as_of
        or artifact_user_id <= 0
        or receipt_user_id != artifact_user_id
        or int(receipt_row.get("userId") or 0) != artifact_user_id
    ):
        raise DecisionQualitySnapshotContractError(
            "artifact commit receipt conflicts with its source artifact"
        )


def _candidate_provider_refs(
    outcome: Mapping[str, Any],
) -> list[dict[str, Any]]:
    refs = outcome.get("provider_receipt_refs")
    if not isinstance(refs, Mapping) or set(refs) != {"calendar", "nav_by_code"}:
        raise DecisionQualitySnapshotContractError(
            "candidate outcome provider receipt references are missing"
        )
    calendar = refs.get("calendar")
    nav_by_code = refs.get("nav_by_code")
    if not isinstance(calendar, Mapping) or not isinstance(nav_by_code, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate outcome provider receipt references are invalid"
        )
    values: list[Mapping[str, Any]] = [calendar]
    for code, ref in sorted(nav_by_code.items(), key=lambda item: str(item[0])):
        if (
            not isinstance(code, str)
            or len(code) != 6
            or not code.isdigit()
            or not isinstance(ref, Mapping)
        ):
            raise DecisionQualitySnapshotContractError(
                "candidate NAV provider receipt mapping is invalid"
            )
        values.append(ref)
    normalized: dict[str, dict[str, Any]] = {}
    for ref in values:
        if set(ref) != _PROVIDER_REF_FIELDS:
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt reference shape is invalid"
            )
        value = dict(ref)
        receipt_id = str(value.get("receipt_id") or "")
        for field in ("origin_fetched_at", "completed_at"):
            parsed = _candidate_artifact_timestamp(
                value.get(field), f"provider reference {field}"
            ).isoformat()
            if value.get(field) != parsed:
                raise DecisionQualitySnapshotContractError(
                    "candidate provider receipt reference clock is not canonical"
                )
        if not receipt_id.startswith("dqpr_") or value.get("capture_mode") != "live":
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt reference identity is invalid"
            )
        prior = normalized.get(receipt_id)
        if prior is not None and canonical_json(prior) != canonical_json(value):
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt reference is contradictory"
            )
        normalized[receipt_id] = value
    return sorted(
        normalized.values(),
        key=lambda value: (
            str(value["provider"]),
            str(value["operation"]),
            str(value["receipt_id"]),
        ),
    )


def _candidate_provider_ref_bindings(
    outcome: Mapping[str, Any],
) -> dict[str, tuple[str, str | None]]:
    refs = outcome.get("provider_receipt_refs")
    if not isinstance(refs, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt binding map is missing"
        )
    calendar = refs.get("calendar")
    nav_by_code = refs.get("nav_by_code")
    if not isinstance(calendar, Mapping) or not isinstance(nav_by_code, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt binding map is invalid"
        )
    result = {str(calendar.get("receipt_id") or ""): ("calendar", None)}
    for code, ref in nav_by_code.items():
        if not isinstance(ref, Mapping):
            raise DecisionQualitySnapshotContractError(
                "candidate NAV provider receipt binding is invalid"
            )
        receipt_id = str(ref.get("receipt_id") or "")
        prior = result.get(receipt_id)
        binding = ("nav", str(code))
        if prior is not None and prior != binding:
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt is reused across source identities"
            )
        result[receipt_id] = binding
    return result


def _candidate_provider_delivery_for_binding(
    outcome: Mapping[str, Any],
    binding: tuple[str, str | None] | None,
) -> Mapping[str, Any]:
    deliveries = outcome.get("provider_deliveries")
    if not isinstance(deliveries, Mapping) or binding is None:
        raise DecisionQualitySnapshotContractError(
            "candidate provider delivery binding is missing"
        )
    kind, fund_code = binding
    if kind == "calendar":
        delivery = deliveries.get("calendar")
    elif kind == "nav" and isinstance(fund_code, str):
        nav_by_code = deliveries.get("nav_by_code")
        delivery = (
            nav_by_code.get(fund_code)
            if isinstance(nav_by_code, Mapping)
            else None
        )
    else:
        delivery = None
    if not isinstance(delivery, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate provider delivery binding is invalid"
        )
    return delivery


def _validate_provider_receipt_ref_binding(
    *,
    ref: Mapping[str, Any],
    receipt_row: Mapping[str, Any],
    expected_binding: tuple[str, str | None] | None,
    outcome_storage_created_at: datetime,
    evaluation_as_of: datetime,
) -> None:
    receipt = _row_payload(receipt_row)
    adapter_output = receipt.get("adapter_output")
    if not isinstance(adapter_output, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt adapter output is missing"
        )
    try:
        policy = verify_candidate_provider_adapter_policy(adapter_output)
    except CandidateProviderAdapterPolicyError as exc:
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt does not match the production adapter policy"
        ) from exc
    expected = {
        "receipt_id": receipt.get("receipt_id"),
        "content_hash": receipt.get("content_hash"),
        "provider": receipt.get("provider"),
        "operation": receipt.get("operation"),
        "capture_mode": receipt.get("capture_mode"),
        "request_hash": receipt.get("request_hash"),
        "adapter_output_sha256": receipt.get("adapter_output_sha256"),
        "normalized_payload_hash": receipt.get("normalized_payload_hash"),
        "origin_fetched_at": receipt.get("origin_fetched_at"),
        "completed_at": receipt.get("completed_at"),
        "origin_receipt_hash": adapter_output.get("origin_receipt_hash"),
        "adapter_policy_id": policy["adapter_policy_id"],
        "adapter_policy_hash": policy["adapter_policy_hash"],
        "adapter_contract_version": policy["adapter_contract_version"],
        "adapter_script_sha256": policy["adapter_script_sha256"],
        "adapter_policy_script_sha256": policy[
            "adapter_policy_script_sha256"
        ],
        "adapter_library_name": policy["adapter_library_name"],
        "adapter_library_version": policy["adapter_library_version"],
        "adapter_python_version": policy["adapter_python_version"],
    }
    request = adapter_output.get("request")
    parameters = request.get("parameters") if isinstance(request, Mapping) else None
    if expected_binding is None or not isinstance(parameters, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt request binding is missing"
        )
    binding_kind, fund_code = expected_binding
    if binding_kind == "calendar":
        request_binding_valid = (
            adapter_output.get("provider_id")
            == "akshare.tool_trade_date_hist_sina"
            and adapter_output.get("operation") == "tool_trade_date_hist_sina"
            and dict(parameters) == {}
        )
    elif binding_kind == "nav":
        trading_days = parameters.get("trading_days")
        request_binding_valid = bool(
            adapter_output.get("provider_id")
            == "akshare.fund_open_fund_info_em"
            and adapter_output.get("operation") == "fund_open_fund_info_em"
            and set(parameters) == {"fund_code", "trading_days", "indicator"}
            and parameters.get("fund_code") == fund_code
            and type(trading_days) is int
            and int(trading_days) > 0
            and parameters.get("indicator") == "单位净值走势"
        )
    else:
        request_binding_valid = False
    completed_at = _candidate_artifact_timestamp(
        receipt.get("completed_at"),
        "provider receipt completion clock",
    )
    provider_storage_created_at = _candidate_artifact_timestamp(
        receipt_row.get("created_at"),
        "provider receipt storage clock",
    )
    if (
        not request_binding_valid
        or dict(ref) != expected
        or completed_at > provider_storage_created_at
        or provider_storage_created_at > outcome_storage_created_at
        or provider_storage_created_at > evaluation_as_of
    ):
        raise DecisionQualitySnapshotContractError(
            "candidate provider receipt reference conflicts with stored origin"
        )


def _validate_provider_receipt_temporal_order(
    *,
    artifact: Mapping[str, Any],
    provider_receipts_by_id: Mapping[str, Mapping[str, Any]],
    audit_visible_at: datetime,
    outcome_visible_at: datetime,
) -> None:
    refs = artifact.get("provider_receipt_refs")
    nav_by_code = refs.get("nav_by_code") if isinstance(refs, Mapping) else None
    if not isinstance(nav_by_code, Mapping):
        raise DecisionQualitySnapshotContractError(
            "candidate NAV provider receipt references are missing"
        )
    nav_ids = {
        str(ref.get("receipt_id") or "")
        for ref in nav_by_code.values()
        if isinstance(ref, Mapping)
    }
    for ref in _candidate_provider_refs(artifact):
        receipt_id = str(ref["receipt_id"])
        row = provider_receipts_by_id.get(receipt_id)
        if row is None:
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt disappeared during validation"
            )
        completed_at = _candidate_artifact_timestamp(
            _row_payload(row).get("completed_at"),
            "provider receipt completion clock",
        )
        if completed_at > outcome_visible_at or (
            receipt_id in nav_ids and completed_at <= audit_visible_at
        ):
            raise DecisionQualitySnapshotContractError(
                "candidate provider receipt violates point-in-time ordering"
            )


def _evaluation_candidate_labels(
    labels: Mapping[str, Any],
    *,
    label_available_at: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code, raw_label in sorted(labels.items(), key=lambda item: str(item[0])):
        if not isinstance(code, str) or not isinstance(raw_label, Mapping):
            raise DecisionQualitySnapshotContractError(
                "candidate outcome label mapping is invalid"
            )
        label = deepcopy(dict(raw_label))
        label["label_available_at"] = label_available_at
        label["availability_basis"] = "post_commit_artifact_receipt"
        label["label_hash"] = canonical_hash(
            {key: value for key, value in label.items() if key != "label_hash"}
        )
        result[code] = label
    return result


def _candidate_case_from_artifacts(
    *,
    target: Mapping[str, Any],
    audit_receipt_status: str,
    audit_receipt_row: Mapping[str, Any] | None,
    audit_source_row: Mapping[str, Any],
    outcome_row: Mapping[str, Any] | None,
    outcome_receipt_row: Mapping[str, Any] | None,
    provider_receipts_by_id: Mapping[str, Mapping[str, Any]],
    evaluation_as_of: datetime,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    plan = target["label_plan"]
    labels: Mapping[str, Any] = {}
    label_storage_created_at: str | None = None
    audit_receipt = (
        _row_payload(audit_receipt_row)
        if audit_receipt_row is not None
        else None
    )
    if (
        audit_receipt_status not in {"verified", "pending", "late"}
        or (audit_receipt_status == "pending") != (audit_receipt is None)
    ):
        raise DecisionQualitySnapshotContractError(
            "candidate audit receipt eligibility classification is invalid"
        )
    audit_status = audit_receipt_status
    outcome_status = "absent"
    outcome_artifact_id: str | None = None
    outcome_content_hash: str | None = None
    outcome_receipt: Mapping[str, Any] | None = None
    case_provider_refs: list[dict[str, Any]] = []
    used_provider_rows: list[Mapping[str, Any]] = []
    if outcome_row is not None:
        if audit_status != "verified" or audit_receipt is None:
            raise DecisionQualitySnapshotContractError(
                "candidate outcome has no eligible audit commit receipt"
            )
        envelope = _row_payload(outcome_row)
        artifact = envelope.get("artifact")
        if not isinstance(artifact, Mapping):
            raise DecisionQualitySnapshotContractError(
                "candidate outcome artifact payload is missing"
            )
        try:
            validate_candidate_outcome_set(artifact, target=target)
        except CandidateSelectionSettlementError as exc:
            raise DecisionQualitySnapshotContractError(
                "candidate outcome set failed deterministic validation"
            ) from exc
        storage_created_at = _artifact_storage_created_at(outcome_row)
        materialized_at = _candidate_artifact_timestamp(
            artifact.get("materialized_at"),
            "candidate outcome materialized_at",
        )
        available_at = _candidate_artifact_timestamp(
            envelope.get("available_at"), "candidate outcome available_at"
        )
        recorded_at = _candidate_artifact_timestamp(
            envelope.get("recorded_at"), "candidate outcome recorded_at"
        )
        audit_cutoff = _candidate_artifact_timestamp(
            target.get("audit_storage_cutoff"), "candidate audit storage cutoff"
        )
        if (
            envelope.get("artifact_type")
            != CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE
            or envelope.get("artifact_schema_version")
            != CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
            or envelope.get("logical_key")
            != f"candidate_outcome:{target['audit_artifact_id']}"
            or envelope.get("source_type") != "discovery"
            or envelope.get("source_report_id") != target["source_report_id"]
            or envelope.get("decision_event_id") is not None
            or envelope.get("decision_at") != target["decision_at"]
            or envelope.get("store_authority") != "primary"
            or envelope.get("audit_eligible") is not True
            or available_at != materialized_at
            or recorded_at != materialized_at
            or storage_created_at < recorded_at
            or storage_created_at <= audit_cutoff
        ):
            raise DecisionQualitySnapshotContractError(
                "candidate outcome storage receipt conflicts with payload"
            )
        outcome_artifact_id = str(envelope.get("artifact_id") or "")
        outcome_content_hash = str(envelope.get("content_hash") or "")
        if not outcome_artifact_id or not outcome_content_hash:
            raise DecisionQualitySnapshotContractError(
                "candidate outcome content address is missing"
            )
        provider_refs = _candidate_provider_refs(artifact)
        provider_bindings = _candidate_provider_ref_bindings(artifact)
        for ref in provider_refs:
            receipt_id = str(ref["receipt_id"])
            provider_row = provider_receipts_by_id.get(receipt_id)
            if provider_row is None:
                raise DecisionQualitySnapshotContractError(
                    "candidate outcome references a missing provider receipt"
                )
            binding = provider_bindings.get(receipt_id)
            _validate_provider_receipt_ref_binding(
                ref=ref,
                receipt_row=provider_row,
                expected_binding=binding,
                outcome_storage_created_at=storage_created_at,
                evaluation_as_of=evaluation_as_of,
            )
            provider_origin = _row_payload(provider_row).get("adapter_output")
            if not isinstance(provider_origin, Mapping) or binding is None:
                raise DecisionQualitySnapshotContractError(
                    "candidate provider projection origin is missing"
                )
            try:
                validate_candidate_outcome_provider_projection(
                    artifact,
                    target=target,
                    kind=binding[0],
                    fund_code=binding[1],
                    origin_receipt=provider_origin,
                    delivery=_candidate_provider_delivery_for_binding(
                        artifact,
                        binding,
                    ),
                )
            except CandidateSelectionSettlementError as exc:
                raise DecisionQualitySnapshotContractError(
                    "candidate outcome projection conflicts with stored provider origin"
                ) from exc
            used_provider_rows.append(provider_row)
        if outcome_receipt_row is not None:
            outcome_receipt = _row_payload(outcome_receipt_row)
            outcome_status = "verified"
            outcome_visible_at = _candidate_artifact_timestamp(
                outcome_receipt.get("source_visible_at"),
                "candidate outcome commit visibility clock",
            )
            audit_visible_at = _candidate_artifact_timestamp(
                audit_receipt.get("source_visible_at"),
                "candidate audit commit visibility clock",
            )
            if (
                outcome_visible_at <= audit_visible_at
                or outcome_visible_at < storage_created_at
                or outcome_visible_at > evaluation_as_of
            ):
                raise DecisionQualitySnapshotContractError(
                    "candidate audit/outcome commit receipt order is invalid"
                )
            _validate_provider_receipt_temporal_order(
                artifact=artifact,
                provider_receipts_by_id=provider_receipts_by_id,
                audit_visible_at=audit_visible_at,
                outcome_visible_at=outcome_visible_at,
            )
            labels_value = artifact.get("outcome_labels")
            if not isinstance(labels_value, Mapping):
                raise DecisionQualitySnapshotContractError(
                    "candidate outcome labels are missing"
                )
            labels = _evaluation_candidate_labels(
                labels_value,
                label_available_at=outcome_visible_at.isoformat(),
            )
            label_storage_created_at = outcome_visible_at.isoformat()
            case_provider_refs = provider_refs
        else:
            outcome_status = "pending"
        if outcome_status == "verified" and not case_provider_refs:
            raise DecisionQualitySnapshotContractError(
                "verified candidate outcome has no provider receipts"
            )
    provider_manifest_hash = canonical_hash(case_provider_refs)
    try:
        provider_adapter_stratum = candidate_provider_adapter_stratum(
            case_provider_refs
        )
        provider_adapter_stratum_hash = candidate_provider_adapter_stratum_hash(
            case_provider_refs
        )
    except CandidateProviderAdapterPolicyError as exc:
        raise DecisionQualitySnapshotContractError(
            "candidate case provider adapter stratum is invalid"
        ) from exc
    if outcome_status == "verified" and (
        artifact.get("provider_adapter_stratum") != provider_adapter_stratum
        or artifact.get("provider_adapter_stratum_hash")
        != provider_adapter_stratum_hash
    ):
        raise DecisionQualitySnapshotContractError(
            "candidate outcome adapter stratum conflicts with formal case"
        )
    audit_source_created_at = _artifact_storage_created_at(audit_source_row)
    decision_at = _candidate_artifact_timestamp(
        target.get("decision_at"), "candidate decision clock"
    )
    declared_decision_date_local = decision_at.astimezone(_CN_TZ).date().isoformat()
    live_cohort_date_local = (
        str(artifact.get("entry_date") or "")
        if outcome_status == "verified"
        else (
            _candidate_artifact_timestamp(
                audit_receipt.get("source_visible_at"),
                "candidate audit commit visibility clock",
            )
            .astimezone(_CN_TZ)
            .date()
            .isoformat()
            if audit_receipt_status == "verified" and audit_receipt is not None
            else None
        )
    )
    case = {
        "schema_version": CANDIDATE_SELECTION_CASE_SCHEMA_VERSION,
        "candidate_evaluator_version": CANDIDATE_SELECTION_EVALUATOR_VERSION,
        "evidence_scope": CANDIDATE_SELECTION_EVIDENCE_SCOPE,
        "case_id": candidate_case_id(target),
        "recorded_at": str(
            target.get("audit_storage_cutoff")
            or target.get("audit_phase1_storage_cutoff")
            or ""
        ),
        "decision_at": decision_at.isoformat(),
        "audit_source_row_created_at": audit_source_created_at.isoformat(),
        "capture_status": "eligible",
        "capture_reason": "eligible",
        "capture_reason_hash": canonical_hash({"reason": "eligible"}),
        "capture_artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
        "source_capture_delay_seconds": (
            audit_source_created_at - decision_at
        ).total_seconds(),
        "audit_artifact_id": target["audit_artifact_id"],
        "audit_content_hash": target["audit_content_hash"],
        "audit_snapshot_hash": target["audit_snapshot_hash"],
        "label_plan_hash": plan["plan_hash"],
        "audit_commit_receipt_status": audit_status,
        "audit_commit_receipt_id": (
            audit_receipt.get("receipt_id") if audit_receipt is not None else None
        ),
        "audit_commit_receipt_content_hash": (
            audit_receipt.get("content_hash")
            if audit_receipt is not None
            else None
        ),
        "audit_commit_receipt_source_visible_at": (
            audit_receipt.get("source_visible_at")
            if audit_receipt is not None
            else None
        ),
        "outcome_commit_receipt_status": outcome_status,
        "outcome_artifact_id": outcome_artifact_id,
        "outcome_content_hash": outcome_content_hash,
        "outcome_commit_receipt_id": (
            outcome_receipt.get("receipt_id")
            if outcome_receipt is not None
            else None
        ),
        "outcome_commit_receipt_content_hash": (
            outcome_receipt.get("content_hash")
            if outcome_receipt is not None
            else None
        ),
        "outcome_commit_receipt_source_visible_at": (
            outcome_receipt.get("source_visible_at")
            if outcome_receipt is not None
            else None
        ),
        "provider_receipt_refs": case_provider_refs,
        "provider_receipt_count": len(case_provider_refs),
        "provider_receipt_manifest_hash": provider_manifest_hash,
        "provider_adapter_stratum": provider_adapter_stratum,
        "provider_adapter_stratum_hash": provider_adapter_stratum_hash,
        "label_storage_created_at": label_storage_created_at,
        "horizon_trading_days": plan["horizon_trading_days"],
        "decision_date_local": plan["decision_date_local"],
        "declared_decision_date_local": declared_decision_date_local,
        "live_cohort_date_local": live_cohort_date_local,
        "label_policy_version": plan["policy_version"],
        "selection_policy_version": str(
            target["audit"].get("versions", {}).get("selection_policy") or ""
        ),
        "audit": target["audit"],
        "outcome_labels": labels,
        "k": plan["k"],
        "universe_stage": plan["universe_stage"],
        "automatic_promotion_allowed": False,
    }
    return case, used_provider_rows


def _artifact_storage_created_at(row: Mapping[str, Any]) -> datetime:
    value = row.get("created_at")
    if value is None:
        raise DecisionQualitySnapshotContractError(
            "decision-quality artifact storage receipt is missing"
        )
    return _candidate_artifact_timestamp(
        value, "decision-quality artifact storage receipt"
    )


def _candidate_artifact_timestamp(value: object, name: str) -> datetime:
    try:
        return parse_evaluation_as_of(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DecisionQualitySnapshotContractError(f"{name} is invalid") from exc


def _raise_for_evaluation_contract_failures(
    evaluation: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
) -> None:
    input_audit = evaluation.get("input_audit")
    if isinstance(input_audit, Mapping):
        event_by_id = {
            str(_row_payload(row).get("event_id") or ""): _row_payload(row)
            for row in events
        }
        event_records = input_audit.get("event_records")
        if isinstance(event_records, Sequence) and not isinstance(
            event_records, (str, bytes)
        ):
            for record in event_records:
                if not isinstance(record, Mapping) or record.get("status") != "excluded":
                    continue
                reason = str(record.get("reason") or "")
                event = event_by_id.get(str(record.get("event_id") or ""))
                if event is None or not _legacy_event_exclusion_allowed(reason, event):
                    raise DecisionQualitySnapshotContractError(
                        "evaluator rejected stored decision event evidence"
                    )
        match_records = input_audit.get("event_horizon_matches")
        if isinstance(match_records, Sequence) and not isinstance(
            match_records, (str, bytes)
        ):
            for record in match_records:
                if (
                    not isinstance(record, Mapping)
                    or record.get("replay_status") != "eligible"
                ):
                    raise DecisionQualitySnapshotContractError(
                        "formal decision event is not replay eligible"
                    )
        outcome_exclusions = input_audit.get("outcome_exclusions")
        if isinstance(outcome_exclusions, Sequence) and not isinstance(
            outcome_exclusions, (str, bytes)
        ):
            unexpected_outcome_exclusions = [
                row
                for row in outcome_exclusions
                if not isinstance(row, Mapping)
                or row.get("reason")
                not in _EXPECTED_PENDING_OUTCOME_EXCLUSION_REASONS
            ]
            if unexpected_outcome_exclusions:
                raise DecisionQualitySnapshotContractError(
                    "evaluator rejected stored evidence: outcome_exclusions"
                )
        shadow_label_exclusions = input_audit.get("shadow_label_exclusions")
        if (
            isinstance(shadow_label_exclusions, Sequence)
            and not isinstance(shadow_label_exclusions, (str, bytes))
            and shadow_label_exclusions
        ):
            raise DecisionQualitySnapshotContractError(
                "evaluator rejected stored evidence: shadow_label_exclusions"
            )
    claims = evaluation.get("claim_audits")
    if isinstance(claims, Mapping):
        exclusions = claims.get("exclusion_reasons")
        if isinstance(exclusions, Sequence) and not isinstance(
            exclusions, (str, bytes)
        ):
            unexpected = [
                row
                for row in exclusions
                if not isinstance(row, Mapping)
                or row.get("reason")
                != "claim_audit_event_missing_or_replay_ineligible"
            ]
            if unexpected:
                raise DecisionQualitySnapshotContractError(
                    "evaluator rejected stored claim audit evidence"
                )
    candidate = evaluation.get("candidate_selection")
    if isinstance(candidate, Mapping):
        rows = candidate.get("evaluations")
        receipt_pending_reasons = {
            "candidate_selection_audit_commit_receipt_pending",
            "candidate_selection_audit_commit_receipt_late",
            "candidate_selection_outcome_commit_receipt_pending",
            "candidate_selection_outcome_artifact_absent",
            "candidate_selection_capture_late",
            "candidate_selection_capture_ineligible",
        }
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                reason = str(row.get("reason") or "")
                if (
                    row.get("status") == "unavailable"
                    and row.get("formal_status")
                    in {
                        "receipt_pending",
                        "receipt_policy_gap",
                        "capture_late",
                        "capture_ineligible",
                    }
                    and reason in receipt_pending_reasons
                ):
                    continue
                if _source_verified_selected_count_below_k(row):
                    continue
                if row.get("status") == "unavailable":
                    raise DecisionQualitySnapshotContractError(
                        "evaluator rejected stored candidate evidence"
                    )


def _source_verified_selected_count_below_k(
    row: Mapping[str, Any],
) -> bool:
    """Allow only the preregistered, fully-labelled ``selected_count < k`` gap."""

    if (
        row.get("status") != "unavailable"
        or row.get("formal_status") != "source_verified"
        or row.get("reason") != "outcome_labels_incomplete"
    ):
        return False
    evaluation = row.get("evaluation")
    if not isinstance(evaluation, Mapping):
        return False
    k = evaluation.get("k")
    selected_count = evaluation.get("selected_count")
    if (
        type(k) is not int
        or k <= 0
        or type(selected_count) is not int
        or selected_count < 0
        or selected_count >= k
        or evaluation.get("status") != "unavailable"
        or evaluation.get("reason") != "outcome_labels_incomplete"
    ):
        return False
    coverage = evaluation.get("coverage")
    if not isinstance(coverage, Mapping):
        return False
    universe_count = coverage.get("universe_count")
    if (
        type(universe_count) is not int
        or universe_count <= 0
        or coverage.get("mature_label_count") != universe_count
        or coverage.get("top_k_count") != k
    ):
        return False
    for metric_name in ("precision_at_k", "ndcg_at_k", "regret_at_k"):
        metric = evaluation.get(metric_name)
        if (
            not isinstance(metric, Mapping)
            or metric.get("status") != "unavailable"
            or metric.get("reason") != "selected_count_below_k"
            or metric.get("selected_count") != selected_count
            or metric.get("required_k") != k
        ):
            return False
    return True


def _legacy_event_exclusion_allowed(
    reason: str,
    event: Mapping[str, Any],
) -> bool:
    """Allow only precisely absent, forward-only D2 replay metadata.

    A present-but-invalid field is never grandfathered.  Repository hash and
    receipt failures also remain fatal, so this cannot hide tampering.
    """

    if reason == "decision_event_replay_bundle_missing_or_invalid":
        return "replay_bundle" not in event
    if reason == "decision_event_variant_manifest_missing_or_invalid":
        return "variant_manifest" not in event
    if reason == "decision_event_replay_bundle_hash_missing_or_invalid":
        return "replay_bundle_hash" not in event
    version_prefix = "decision_event_variant_version_missing:"
    if reason.startswith(version_prefix):
        field = reason[len(version_prefix) :]
        return field in _LEGACY_VARIANT_VERSION_FIELDS and field not in event
    hash_prefix = "decision_event_variant_hash_invalid:"
    if reason.startswith(hash_prefix):
        field = reason[len(hash_prefix) :]
        return field in _LEGACY_VARIANT_HASH_FIELDS and field not in event
    return False


def _mature_decision_dates(
    evaluation: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    event_dates: dict[str, str] = {}
    for row in events:
        payload = _row_payload(row)
        event_id = str(payload.get("event_id") or "")
        decision_date = str(payload.get("decision_date") or "").strip()
        if event_id and decision_date:
            event_dates[event_id] = decision_date
    input_audit = evaluation.get("input_audit")
    matches = (
        input_audit.get("event_horizon_matches")
        if isinstance(input_audit, Mapping)
        else None
    )
    dates: set[str] = set()
    if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes)):
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            if (
                match.get("match_status") == "matched_terminal"
                and match.get("formal_score_status") == "included"
            ):
                value = event_dates.get(str(match.get("event_id") or ""))
                if value:
                    dates.add(value)
    return sorted(dates)


def _formal_label_coverage(evaluation: Mapping[str, Any]) -> float | None:
    overall = evaluation.get("overall")
    return (
        _optional_number(overall.get("label_coverage_percent"))
        if isinstance(overall, Mapping)
        else None
    )


def _input_manifest(
    *,
    window_start: datetime,
    cutoff: datetime,
    events: Sequence[Mapping[str, Any]],
    nonformal_events: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    ignored_artifacts: Sequence[Mapping[str, Any]],
    ignored_artifact_count: int,
    artifact_receipts: Sequence[Mapping[str, Any]],
    provider_receipts: Sequence[Mapping[str, Any]],
    candidate_capture_records: Sequence[Mapping[str, Any]],
    prompt_shadow_manifest: Mapping[str, Any],
    mature_decision_dates: Sequence[str],
    rollout_marker: Mapping[str, Any],
) -> dict[str, Any]:
    capture_reason_counts: dict[str, int] = {}
    capture_status_counts: dict[str, int] = {}
    for row in candidate_capture_records:
        reason = str(row.get("capture_reason") or "unknown")
        status = str(row.get("capture_status") or "unknown")
        capture_reason_counts[reason] = capture_reason_counts.get(reason, 0) + 1
        capture_status_counts[status] = capture_status_counts.get(status, 0) + 1
    prompt_manifest = _prompt_shadow_input_manifest(prompt_shadow_manifest)
    return {
        "schema_version": DECISION_QUALITY_INPUT_MANIFEST_SCHEMA_VERSION,
        "contract_rollout_marker": dict(rollout_marker),
        "window_start": window_start.isoformat(),
        "evaluation_as_of": cutoff.isoformat(),
        "decision_event_count": len(events),
        "nonformal_decision_event_count": len(nonformal_events),
        "observed_decision_event_count": len(events) + len(nonformal_events),
        "terminal_outcome_count": len(outcomes),
        "input_artifact_count": len(artifacts) + len(ignored_artifacts),
        "consumed_input_artifact_count": len(artifacts),
        "ignored_artifact_count": int(ignored_artifact_count),
        "artifact_receipt_count": len(artifact_receipts),
        "provider_receipt_count": len(provider_receipts),
        "candidate_capture_count": len(candidate_capture_records),
        "candidate_capture_status_counts": dict(sorted(capture_status_counts.items())),
        "candidate_capture_reason_counts": dict(sorted(capture_reason_counts.items())),
        "prompt_shadow_input_artifact_count": prompt_manifest[
            "input_artifact_count"
        ],
        "prompt_shadow_artifact_receipt_count": prompt_manifest[
            "artifact_receipt_count"
        ],
        "prompt_shadow_assigned_registration_count": prompt_manifest[
            "assigned_registration_count"
        ],
        "prompt_shadow_paired_case_count": prompt_manifest["paired_case_count"],
        "prompt_shadow_gate_count": prompt_manifest["gate_count"],
        "prompt_shadow_evidence": prompt_manifest,
        "candidate_capture_records": sorted(
            [dict(row) for row in candidate_capture_records],
            key=lambda row: (str(row.get("artifact_id") or ""), str(row.get("case_id") or "")),
        ),
        "mature_decision_dates": list(mature_decision_dates),
        "mature_decision_day_count": len(mature_decision_dates),
        "decision_events": _event_manifest_rows(events),
        "nonformal_decision_events": _event_manifest_rows(nonformal_events),
        "terminal_outcomes": _outcome_manifest_rows(outcomes),
        "input_artifacts": _artifact_manifest_rows(artifacts),
        "ignored_input_artifacts": _artifact_manifest_rows(ignored_artifacts),
        "artifact_receipts": _artifact_receipt_manifest_rows(artifact_receipts),
        "provider_receipts": _provider_receipt_manifest_rows(provider_receipts),
    }


def _prompt_shadow_input_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact_rows = value.get("artifact_rows")
    receipt_rows = value.get("artifact_receipt_rows")
    paired_refs = value.get("paired_case_refs")
    gate_refs = value.get("gate_refs")
    if not isinstance(artifact_rows, Sequence) or isinstance(
        artifact_rows, (str, bytes)
    ) or not isinstance(receipt_rows, Sequence) or isinstance(
        receipt_rows, (str, bytes)
    ) or not isinstance(paired_refs, Sequence) or isinstance(
        paired_refs, (str, bytes)
    ) or not isinstance(gate_refs, Sequence) or isinstance(gate_refs, (str, bytes)):
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow manifest inputs are invalid"
        )
    artifact_refs = [
        {
            "artifact_id": str(_row_payload(row).get("artifact_id") or ""),
            "artifact_type": str(_row_payload(row).get("artifact_type") or ""),
            "artifact_schema_version": str(
                _row_payload(row).get("artifact_schema_version") or ""
            ),
            "content_hash": str(_row_payload(row).get("content_hash") or ""),
        }
        for row in artifact_rows
    ]
    receipt_refs = [
        {
            "receipt_id": str(_row_payload(row).get("receipt_id") or ""),
            "content_hash": str(_row_payload(row).get("content_hash") or ""),
            "artifact_id": str(_row_payload(row).get("artifact_id") or ""),
            "artifact_content_hash": str(
                _row_payload(row).get("artifact_content_hash") or ""
            ),
        }
        for row in receipt_rows
    ]
    artifact_refs.sort(key=lambda row: (row["artifact_id"], row["content_hash"]))
    receipt_refs.sort(key=lambda row: (row["artifact_id"], row["receipt_id"]))
    normalized_paired = sorted(
        [dict(item) for item in paired_refs if isinstance(item, Mapping)],
        key=lambda row: (str(row.get("case_id") or ""), str(row.get("content_hash") or "")),
    )
    normalized_gates = sorted(
        [dict(item) for item in gate_refs if isinstance(item, Mapping)],
        key=lambda row: (
            str(row.get("policy_hash") or ""),
            str(row.get("stratum_hash") or ""),
        ),
    )
    if len(normalized_paired) != len(paired_refs) or len(normalized_gates) != len(
        gate_refs
    ) or any(
        not row["artifact_id"]
        or not row["artifact_type"]
        or not row["artifact_schema_version"]
        or not _is_manifest_sha256(row["content_hash"])
        for row in artifact_refs
    ) or any(
        not row["receipt_id"]
        or not row["artifact_id"]
        or not _is_manifest_sha256(row["content_hash"])
        or not _is_manifest_sha256(row["artifact_content_hash"])
        for row in receipt_refs
    ):
        raise DecisionQualitySnapshotContractError(
            "prompt-shadow manifest reference is incomplete"
        )
    result: dict[str, Any] = {
        "schema_version": "decision_quality_prompt_shadow_manifest.v1",
        "input_artifact_count": len(artifact_refs),
        "artifact_receipt_count": len(receipt_refs),
        "assigned_registration_count": int(value.get("registration_count") or 0),
        "paired_case_count": len(normalized_paired),
        "gate_count": len(normalized_gates),
        "input_artifact_refs": artifact_refs,
        "artifact_receipt_refs": receipt_refs,
        "paired_case_refs": normalized_paired,
        "gate_refs": normalized_gates,
    }
    result["manifest_hash"] = canonical_hash(result)
    return result


def _is_manifest_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _manifest_identity_and_hash(
    row: Mapping[str, Any], identity_key: str
) -> tuple[str, str]:
    payload = _row_payload(row)
    identity = str(payload.get(identity_key) or row.get(identity_key) or "").strip()
    digest = str(payload.get("content_hash") or row.get("content_hash") or "").strip()
    if not identity or not digest:
        raise DecisionQualitySnapshotContractError(
            f"manifest source lacks {identity_key} or content_hash"
        )
    return identity, digest


def _event_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        event_id, digest = _manifest_identity_and_hash(row, "event_id")
        created_at = row.get("created_at")
        if created_at is None:
            raise DecisionQualitySnapshotContractError(
                "decision event manifest source lacks created_at"
            )
        result.append(
            {
                "event_id": event_id,
                "content_hash": digest,
                "created_at": parse_evaluation_as_of(created_at).isoformat(),
            }
        )
    return sorted(result, key=lambda item: (item["event_id"], item["content_hash"]))


def _outcome_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        observation_id, digest = _manifest_identity_and_hash(
            row, "observation_id"
        )
        revision_no = row.get("revision_no")
        finalized_at = row.get("finalized_at")
        created_at = row.get("created_at")
        if (
            isinstance(revision_no, bool)
            or not isinstance(revision_no, int)
            or revision_no <= 0
            or finalized_at is None
            or created_at is None
        ):
            raise DecisionQualitySnapshotContractError(
                "terminal outcome manifest lacks revision_no or receipt times"
            )
        canonical_first_observed = parse_evaluation_as_of(created_at).isoformat()
        canonical_label_recorded = parse_evaluation_as_of(finalized_at).isoformat()
        result.append(
            {
                "observation_id": observation_id,
                "content_hash": digest,
                "revision_no": revision_no,
                "first_observed_at": canonical_first_observed,
                "label_recorded_at": canonical_label_recorded,
                # Retained for readers of the v1 manifest shape.
                "finalized_at": canonical_label_recorded,
            }
        )
    return sorted(
        result,
        key=lambda item: (item["observation_id"], item["revision_no"]),
    )


def _artifact_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        artifact_id, digest = _manifest_identity_and_hash(row, "artifact_id")
        payload = _row_payload(row)
        recorded_at = payload.get("recorded_at") or row.get("recorded_at")
        created_at = row.get("created_at")
        if recorded_at is None or created_at is None:
            raise DecisionQualitySnapshotContractError(
                "input artifact manifest source lacks recorded_at or created_at"
            )
        result.append(
            {
                "artifact_id": artifact_id,
                "content_hash": digest,
                "recorded_at": parse_evaluation_as_of(recorded_at).isoformat(),
                "created_at": parse_evaluation_as_of(created_at).isoformat(),
            }
        )
    return sorted(
        result,
        key=lambda item: (item["artifact_id"], item["content_hash"]),
    )


def _artifact_receipt_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return only bounded receipt bindings; never copy receipt payloads."""

    result: list[dict[str, Any]] = []
    for row in rows:
        receipt = _row_payload(row)
        result.append(
            {
                "receipt_id": str(receipt.get("receipt_id") or ""),
                "content_hash": str(receipt.get("content_hash") or ""),
                "user_id": int(receipt.get("user_id") or 0),
                "artifact_id": str(receipt.get("artifact_id") or ""),
                "artifact_type": str(receipt.get("artifact_type") or ""),
                "artifact_content_hash": str(
                    receipt.get("artifact_content_hash") or ""
                ),
                "source_row_created_at": _candidate_artifact_timestamp(
                    receipt.get("source_row_created_at"),
                    "artifact receipt source row clock",
                ).isoformat(),
                "source_visible_at": _candidate_artifact_timestamp(
                    receipt.get("source_visible_at"),
                    "artifact receipt visibility clock",
                ).isoformat(),
                "store_authority": str(receipt.get("store_authority") or ""),
            }
        )
    if any(
        not item["receipt_id"]
        or not item["content_hash"]
        or item["user_id"] <= 0
        or not item["artifact_id"]
        or not item["artifact_content_hash"]
        for item in result
    ):
        raise DecisionQualitySnapshotContractError(
            "artifact receipt manifest binding is incomplete"
        )
    return sorted(
        result,
        key=lambda item: (item["artifact_id"], item["receipt_id"]),
    )


def _provider_receipt_manifest_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project provider receipts without raw stdout or parsed adapter output."""

    result: list[dict[str, Any]] = []
    for row in rows:
        receipt = _row_payload(row)
        adapter_output = receipt.get("adapter_output")
        origin_hash = (
            str(adapter_output.get("origin_receipt_hash") or "")
            if isinstance(adapter_output, Mapping)
            else ""
        )
        if not isinstance(adapter_output, Mapping):
            raise DecisionQualitySnapshotContractError(
                "provider receipt manifest has no adapter output"
            )
        try:
            policy = verify_candidate_provider_adapter_policy(adapter_output)
        except CandidateProviderAdapterPolicyError as exc:
            raise DecisionQualitySnapshotContractError(
                "provider receipt manifest adapter policy is invalid"
            ) from exc
        result.append(
            {
                "receipt_id": str(receipt.get("receipt_id") or ""),
                "content_hash": str(receipt.get("content_hash") or ""),
                "provider": str(receipt.get("provider") or ""),
                "operation": str(receipt.get("operation") or ""),
                "capture_mode": str(receipt.get("capture_mode") or ""),
                "request_hash": str(receipt.get("request_hash") or ""),
                "adapter_output_sha256": str(
                    receipt.get("adapter_output_sha256") or ""
                ),
                "normalized_payload_hash": str(
                    receipt.get("normalized_payload_hash") or ""
                ),
                "origin_receipt_hash": origin_hash,
                "adapter_policy_id": policy["adapter_policy_id"],
                "adapter_policy_hash": policy["adapter_policy_hash"],
                "adapter_contract_version": policy["adapter_contract_version"],
                "adapter_script_sha256": policy["adapter_script_sha256"],
                "adapter_policy_script_sha256": policy[
                    "adapter_policy_script_sha256"
                ],
                "adapter_library_name": policy["adapter_library_name"],
                "adapter_library_version": policy["adapter_library_version"],
                "adapter_python_version": policy["adapter_python_version"],
                "origin_fetched_at": _candidate_artifact_timestamp(
                    receipt.get("origin_fetched_at"),
                    "provider receipt origin clock",
                ).isoformat(),
                "completed_at": _candidate_artifact_timestamp(
                    receipt.get("completed_at"),
                    "provider receipt completion clock",
                ).isoformat(),
            }
        )
    if any(
        not item["receipt_id"]
        or not item["content_hash"]
        or not item["provider"]
        or not item["operation"]
        or item["capture_mode"] != "live"
        or not item["request_hash"]
        or not item["adapter_output_sha256"]
        or not item["normalized_payload_hash"]
        or not item["origin_receipt_hash"]
        or not item["adapter_policy_id"]
        or not item["adapter_policy_hash"]
        or not item["adapter_contract_version"]
        or not item["adapter_script_sha256"]
        or not item["adapter_policy_script_sha256"]
        or not item["adapter_library_name"]
        or not item["adapter_library_version"]
        or not item["adapter_python_version"]
        for item in result
    ):
        raise DecisionQualitySnapshotContractError(
            "provider receipt manifest binding is incomplete"
        )
    return sorted(
        result,
        key=lambda item: (
            item["provider"],
            item["operation"],
            item["receipt_id"],
        ),
    )


def _fetch_decision_event_rows(
    *,
    user_id: int,
    window_start: datetime,
    cutoff: datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    where = (
        "userId = ? AND metric_eligible = 1 "
        "AND decision_at >= ? AND decision_at <= ? AND created_at <= ?"
    )
    params = (
        user_id,
        window_start.isoformat(),
        cutoff.isoformat(),
        cutoff.isoformat(),
    )
    rows = _fetch_paginated_rows(
        connection,
        table="decision_events",
        where=where,
        params=params,
        order_columns=("decision_at", "event_id"),
    )
    return [_decode_evidence_row(row) for row in rows]


def _fetch_outcome_observation_rows(
    *,
    user_id: int,
    event_ids: set[str],
    cutoff: datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ordered_ids = sorted(event_ids)
    for start in range(0, len(ordered_ids), _IN_CLAUSE_BATCH_SIZE):
        batch = ordered_ids[start : start + _IN_CLAUSE_BATCH_SIZE]
        placeholders = ", ".join("?" for _ in batch)
        # Nullable receipt columns remain visible so the evaluator can reject
        # a malformed terminal row.  Known future receipts are excluded at SQL
        # time and the payload-level source timestamp is checked again below.
        where = (
            f"userId = ? AND is_terminal = 1 AND decision_event_id IN ({placeholders}) "
            "AND (finalized_at IS NULL OR finalized_at <= ?) "
            "AND (observed_at IS NULL OR observed_at <= ?) "
            "AND (updated_at IS NULL OR updated_at <= ?)"
        )
        params: tuple[Any, ...] = (
            user_id,
            *batch,
            cutoff.isoformat(),
            cutoff.isoformat(),
            cutoff.isoformat(),
        )
        rows = _fetch_paginated_rows(
            connection,
            table="outcome_observations",
            where=where,
            params=params,
            order_columns=("observed_at", "observation_id"),
        )
        result.extend(_decode_evidence_row(row) for row in rows)
    return result


def _fetch_decision_quality_input_artifact_rows(
    *,
    user_id: int,
    window_start: datetime,
    cutoff: datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    # This deliberately reads the complete tenant partition.  The repository
    # verifies every payload/hash/duplicated index and storage clock before the
    # first artifact is selected by type, eligibility, time window, or limit.
    try:
        rows = list_decision_quality_input_artifacts(
            user_id=user_id,
            limit=None,
            connection=connection,
        )
    except DecisionQualityIntegrityError as exc:
        raise DecisionQualitySnapshotContractError(
            "stored decision-quality input artifact failed its integrity contract"
        ) from exc

    selected: list[dict[str, Any]] = []
    for row in rows:
        payload = _row_payload(row)
        is_candidate_input = (
            payload.get("audit_eligible") is True
            or (
                payload.get("artifact_type") == CANDIDATE_AUDIT_ARTIFACT_TYPE
                and payload.get("artifact_schema_version")
                == CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
            )
            or (
                payload.get("artifact_type")
                == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE
                and payload.get("artifact_schema_version")
                == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
            )
            or _is_prompt_shadow_artifact(
                str(payload.get("artifact_type") or ""),
                str(payload.get("artifact_schema_version") or ""),
            )
        )
        if not is_candidate_input:
            continue

        # These are semantic point-in-time filters, so they must use only the
        # normalized payload and the physical clock validated by the repository
        # tenant scan.  Applying them in SQL would let a poisoned duplicate
        # index hide the row before its immutable binding was checked.
        recorded_at = parse_evaluation_as_of(payload.get("recorded_at"))
        created_at = parse_evaluation_as_of(row.get("created_at"))
        if recorded_at > cutoff or created_at > cutoff:
            continue
        decision_value = payload.get("decision_at")
        if decision_value is not None:
            decision_at = parse_evaluation_as_of(decision_value)
            if decision_at < window_start or decision_at > cutoff:
                continue
        selected.append(row)
    return selected


def _fetch_decision_quality_artifact_receipt_rows(
    *,
    user_id: int,
    cutoff: datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    """Read every receipt visible at the cutoff for one isolated tenant."""

    rows = _fetch_paginated_rows(
        connection,
        table="decision_quality_artifact_receipts",
        where="userId = ? AND source_visible_at <= ? AND created_at <= ?",
        params=(user_id, cutoff.isoformat(), cutoff.isoformat()),
        order_columns=("source_visible_at", "artifact_id"),
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        try:
            decoded = _decode_artifact_receipt_row(row)
        except DecisionQualityIntegrityError as exc:
            raise DecisionQualitySnapshotContractError(
                "stored artifact commit receipt failed its integrity contract"
            ) from exc
        receipt = _row_payload(decoded)
        receipt_id = str(receipt.get("receipt_id") or "")
        if not receipt_id or receipt_id in seen:
            raise DecisionQualitySnapshotContractError(
                "artifact commit receipt identity is duplicated or missing"
            )
        if (
            int(decoded.get("userId") or 0) != user_id
            or int(receipt.get("user_id") or 0) != user_id
            or _candidate_artifact_timestamp(
                receipt.get("source_visible_at"),
                "artifact receipt visibility clock",
            )
            > cutoff
        ):
            raise DecisionQualitySnapshotContractError(
                "artifact commit receipt crossed its tenant or cutoff boundary"
            )
        seen.add(receipt_id)
        result.append(decoded)
    return result


def _candidate_provider_receipt_ids(
    rows: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Collect only ids named by native D4 outcome artifacts.

    The outcome validator remains authoritative for the exact nested shape;
    this first pass merely bounds the global receipt read to referenced ids.
    """

    result: set[str] = set()
    for row in rows:
        envelope = _row_payload(row)
        artifact = envelope.get("artifact")
        if not isinstance(artifact, Mapping):
            continue
        if (
            envelope.get("artifact_type") != CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE
            or artifact.get("schema_version")
            != CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
        ):
            continue
        refs = artifact.get("provider_receipt_refs")
        if not isinstance(refs, Mapping):
            continue
        pending: list[object] = [refs]
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                receipt_id = value.get("receipt_id")
                if (
                    isinstance(receipt_id, str)
                    and receipt_id.startswith("dqpr_")
                ):
                    result.add(receipt_id)
                pending.extend(value.values())
            elif isinstance(value, Sequence) and not isinstance(
                value, (str, bytes)
            ):
                pending.extend(value)
    return result


def _fetch_decision_quality_provider_receipt_rows(
    *,
    receipt_ids: set[str],
    cutoff: datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    """Read and cryptographically revalidate referenced global receipts."""

    if not receipt_ids:
        return []
    result: list[dict[str, Any]] = []
    for start in range(0, len(receipt_ids), _IN_CLAUSE_BATCH_SIZE):
        batch = sorted(receipt_ids)[start : start + _IN_CLAUSE_BATCH_SIZE]
        placeholders = ", ".join("?" for _ in batch)
        rows = _fetch_paginated_rows(
            connection,
            table="decision_quality_provider_receipts",
            where=(
                f"receipt_id IN ({placeholders}) AND completed_at <= ? "
                "AND created_at <= ?"
            ),
            params=(*batch, cutoff.isoformat(), cutoff.isoformat()),
            order_columns=("provider", "operation", "receipt_id"),
        )
        for row in rows:
            try:
                decoded = _decode_provider_receipt_row(row)
            except DecisionQualityIntegrityError as exc:
                raise DecisionQualitySnapshotContractError(
                    "stored provider receipt failed its integrity contract"
                ) from exc
            receipt = _row_payload(decoded)
            adapter_output = receipt.get("adapter_output")
            if not isinstance(adapter_output, Mapping):
                raise DecisionQualitySnapshotContractError(
                    "provider receipt adapter output is missing"
                )
            try:
                validate_provider_origin_receipt(adapter_output)
                verify_candidate_provider_adapter_policy(adapter_output)
            except (
                ProviderReceiptValidationError,
                CandidateProviderAdapterPolicyError,
            ) as exc:
                raise DecisionQualitySnapshotContractError(
                    "provider origin receipt failed integrity or adapter-policy validation"
                ) from exc
            response = adapter_output.get("response")
            request = adapter_output.get("request")
            cache = adapter_output.get("cache")
            if not all(
                isinstance(value, Mapping)
                for value in (response, request, cache)
            ):
                raise DecisionQualitySnapshotContractError(
                    "provider origin receipt sections are missing"
                )
            assert isinstance(response, Mapping)
            assert isinstance(request, Mapping)
            assert isinstance(cache, Mapping)
            rebuilt_normalized = _rebuild_candidate_provider_normalized_payload(
                adapter_output
            )
            rebuilt_normalized_hash = canonical_provider_hash(rebuilt_normalized)
            if (
                receipt.get("capture_mode") != "live"
                or receipt.get("provider") != adapter_output.get("provider_id")
                or receipt.get("operation") != adapter_output.get("operation")
                or receipt.get("request_hash") != request.get("request_hash")
                or receipt.get("normalized_payload_hash")
                != response.get("normalized_payload_hash")
                or receipt.get("normalized_payload_hash")
                != rebuilt_normalized_hash
                or receipt.get("origin_fetched_at")
                != cache.get("origin_fetched_at")
                or receipt.get("completed_at") != response.get("completed_at")
                or _candidate_artifact_timestamp(
                    decoded.get("created_at"),
                    "provider receipt storage clock",
                )
                > cutoff
            ):
                raise DecisionQualitySnapshotContractError(
                    "provider receipt storage binding conflicts with origin"
                )
            result.append(decoded)
    by_id = {
        str(_row_payload(row).get("receipt_id") or ""): row for row in result
    }
    if len(by_id) != len(result):
        raise DecisionQualitySnapshotContractError(
            "provider receipt identity is duplicated"
        )
    # A referenced receipt absent at the cutoff is not silently substituted.
    # If its outcome row is formal, partitioning will reject that outcome.
    return result


def _rebuild_candidate_provider_normalized_payload(
    origin_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility wrapper around the central production normalization."""

    try:
        return rebuild_candidate_provider_normalized_payload(origin_receipt)
    except CandidateProviderAdapterPolicyError as exc:
        raise DecisionQualitySnapshotContractError(
            "provider receipt storage binding conflicts with origin normalization"
        ) from exc


def _fetch_paginated_rows(
    connection: Any,
    *,
    table: str,
    where: str,
    params: Sequence[Any],
    order_columns: Sequence[str] | None = None,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    if order_columns is None:
        order_columns = tuple(
            part.strip() for part in (order_by or "").split(",") if part.strip()
        )
    identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not identifier.fullmatch(table) or not order_columns or any(
        not identifier.fullmatch(column) for column in order_columns
    ):
        raise ValueError("keyset pagination requires simple SQL identifiers")

    count_rows = _fetchall(
        connection,
        f"SELECT COUNT(*) AS row_count FROM {table} WHERE {where}",
        params,
    )
    if len(count_rows) != 1:
        raise DecisionQualitySnapshotStorageError(
            f"could not count point-in-time rows from {table}"
        )
    try:
        expected_count = int(count_rows[0]["row_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionQualitySnapshotStorageError(
            f"invalid point-in-time count returned for {table}"
        ) from exc
    # MySQL and SQLite both sort NULL before text in ascending order.  Model
    # each key as (is_not_null, coalesced_value) so keyset traversal preserves
    # that ordering without OFFSET rescans or skipping malformed nullable rows.
    components = [
        component
        for column in order_columns
        for component in (
            f"CASE WHEN {column} IS NULL THEN 0 ELSE 1 END",
            f"COALESCE({column}, '')",
        )
    ]
    order_by = ", ".join(components)
    result: list[dict[str, Any]] = []
    last_key: tuple[Any, ...] | None = None
    while len(result) < expected_count:
        page_where = where
        page_params: list[Any] = list(params)
        if last_key is not None:
            terms: list[str] = []
            for index, expression in enumerate(components):
                equal_prefix = " AND ".join(
                    f"{components[prefix]} = ?" for prefix in range(index)
                )
                term = f"{expression} > ?"
                if equal_prefix:
                    term = f"({equal_prefix} AND {term})"
                terms.append(term)
                page_params.extend(last_key[:index])
                page_params.append(last_key[index])
            page_where = f"({where}) AND ({' OR '.join(terms)})"
        page = _fetchall(
            connection,
            f"SELECT * FROM {table} WHERE {page_where} ORDER BY {order_by} "
            f"LIMIT {_PAGE_SIZE}",
            tuple(page_params),
        )
        if not page:
            raise DecisionQualitySnapshotStorageError(
                f"point-in-time pagination changed while reading {table}"
            )
        result.extend(page)
        last_row = page[-1]
        last_key = tuple(
            component
            for column in order_columns
            for component in (
                0 if last_row.get(column) is None else 1,
                "" if last_row.get(column) is None else last_row.get(column),
            )
        )
    if len(result) != expected_count:
        raise DecisionQualitySnapshotStorageError(
            f"point-in-time pagination count mismatch for {table}"
        )
    return result


def _decode_evidence_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    raw_payload = result.get("payload")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise DecisionQualitySnapshotContractError(
                "stored decision evidence payload is invalid JSON"
            ) from exc
    elif isinstance(raw_payload, Mapping):
        payload = dict(raw_payload)
    else:
        raise DecisionQualitySnapshotContractError(
            "stored decision evidence payload is not an object"
        )
    if not isinstance(payload, Mapping):
        raise DecisionQualitySnapshotContractError(
            "stored decision evidence payload is not an object"
        )
    result["payload"] = dict(payload)
    for key in ("eligible", "is_backfilled", "metric_eligible", "is_terminal"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


def _decode_input_artifact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _decode_evidence_row(row)
    try:
        normalized = normalize_decision_quality_input_artifact(result["payload"])
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualitySnapshotContractError(
            "stored decision-quality input artifact is invalid"
        ) from exc
    if canonical_json(result["payload"]) != canonical_json(normalized):
        raise DecisionQualitySnapshotContractError(
            "stored decision-quality input artifact is not canonical"
        )
    for field in (
        "artifact_id",
        "schema_version",
        "artifact_type",
        "artifact_schema_version",
        "logical_key",
        "source_type",
        "source_report_id",
        "decision_event_id",
        "decision_at",
        "available_at",
        "recorded_at",
        "store_authority",
        "content_hash",
    ):
        if result.get(field) != normalized.get(field):
            raise DecisionQualitySnapshotContractError(
                f"stored input artifact index conflicts with payload: {field}"
            )
    if bool(result.get("audit_eligible")) is not normalized.get("audit_eligible"):
        raise DecisionQualitySnapshotContractError(
            "stored input artifact audit eligibility conflicts with payload"
        )
    created_value = result.get("created_at")
    if created_value is None:
        raise DecisionQualitySnapshotContractError(
            "stored input artifact physical receipt is missing"
        )
    try:
        created_at = parse_evaluation_as_of(created_value)
        recorded_at = parse_evaluation_as_of(normalized.get("recorded_at"))
    except (TypeError, ValueError) as exc:
        raise DecisionQualitySnapshotContractError(
            "stored input artifact receipt clock is invalid"
        ) from exc
    if created_at < recorded_at:
        raise DecisionQualitySnapshotContractError(
            "stored input artifact physical receipt predates recorded_at"
        )
    result["audit_eligible"] = bool(result.get("audit_eligible"))
    result["payload"] = normalized
    return result


def _events_in_window(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_start: datetime,
    cutoff: datetime,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        payload = _row_payload(row)
        decision_at = parse_evaluation_as_of(payload.get("decision_at"))
        created_value = row.get("created_at")
        if created_value is None:
            raise DecisionQualitySnapshotContractError(
                "decision event receipt time is missing"
            )
        created_at = parse_evaluation_as_of(created_value)
        if window_start <= decision_at <= cutoff and created_at <= cutoff:
            selected.append(row)
    return selected


def _validate_event_storage_binding(row: Mapping[str, Any]) -> None:
    payload = _row_payload(row)
    actual_hash = str(row.get("content_hash") or "").strip().lower()
    try:
        expected_hash = decision_event_content_hash(payload)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualitySnapshotContractError(
            "stored decision event cannot be canonically hashed"
        ) from exc
    if actual_hash != expected_hash:
        raise DecisionQualitySnapshotContractError(
            "stored decision event content hash mismatch"
        )
    for key in (
        "event_id",
        "schema_version",
        "event_type",
        "source_type",
        "decision_at",
        "fund_code",
        "final_action",
        "action_category",
        "eligible",
        "is_backfilled",
        "metric_eligible",
    ):
        if key in row and row.get(key) is not None and row.get(key) != payload.get(key):
            raise DecisionQualitySnapshotContractError(
                f"stored decision event index conflicts with payload: {key}"
            )
    created_at = parse_evaluation_as_of(row.get("created_at"))
    decision_at = parse_evaluation_as_of(payload.get("decision_at"))
    if created_at < decision_at:
        raise DecisionQualitySnapshotContractError(
            "stored decision event receipt predates its decision"
        )


def _formal_event_input(row: Mapping[str, Any]) -> bool:
    """Select forward-only, primary-store event inputs for formal evaluation."""

    event = _row_payload(row)
    if (
        event.get("schema_version") != "decision_event.v2"
        or event.get("metric_eligible") is not True
        or event.get("audit_eligible") is not True
        or event.get("store_authority") != "primary"
        or event.get("is_backfilled") is True
        or event.get("backfilled") is True
        or not isinstance(event.get("replay_bundle"), Mapping)
        or not isinstance(event.get("variant_manifest"), Mapping)
        or "replay_bundle_hash" not in event
    ):
        return False
    required_variant_fields = (
        _LEGACY_VARIANT_VERSION_FIELDS | _LEGACY_VARIANT_HASH_FIELDS
    )
    return all(field in event for field in required_variant_fields)


def _partition_event_inputs_by_rollout(
    rows: Sequence[Mapping[str, Any]],
    *,
    rollout_marker: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Allow the legacy/nonformal path only before the storage-owned boundary."""

    boundary = parse_evaluation_as_of(rollout_marker.get("required_from"))
    formal: list[Mapping[str, Any]] = []
    nonformal: list[Mapping[str, Any]] = []
    for row in rows:
        if _formal_event_input(row):
            formal.append(row)
            continue
        created_at = parse_evaluation_as_of(row.get("created_at"))
        event = _row_payload(row)
        if created_at >= boundary:
            event_id = str(event.get("event_id") or "unknown")
            raise DecisionQualitySnapshotContractError(
                "post-rollout decision event is missing the formal replay "
                f"contract: {event_id}"
            )
        declared_fields = sorted(
            field for field in _D2_REPLAY_DECLARATION_FIELDS if field in event
        )
        if declared_fields:
            raise DecisionQualitySnapshotContractError(
                "pre-rollout decision event has a partial or invalid D2 replay "
                "contract: " + ",".join(declared_fields)
            )
        nonformal.append(row)
    return formal, nonformal


def _artifacts_in_window(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_start: datetime,
    cutoff: datetime,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        payload = _row_payload(row)
        available_at = parse_evaluation_as_of(payload.get("available_at"))
        recorded_at = parse_evaluation_as_of(payload.get("recorded_at"))
        created_value = row.get("created_at")
        if created_value is None:
            raise DecisionQualitySnapshotContractError(
                "decision-quality input artifact receipt time is missing"
            )
        created_at = parse_evaluation_as_of(created_value)
        decision_value = payload.get("decision_at")
        if max(available_at, recorded_at, created_at) > cutoff:
            continue
        if decision_value is not None:
            decision_at = parse_evaluation_as_of(decision_value)
            if decision_at < window_start or decision_at > cutoff:
                continue
        selected.append(row)
    return selected


def _terminal_outcome_for_selected_event(
    row: Mapping[str, Any],
    event_ids: set[str],
    *,
    evaluation_as_of: datetime,
) -> bool:
    payload = _row_payload(row)
    event_id = str(
        payload.get("event_id")
        or payload.get("decision_event_id")
        or row.get("decision_event_id")
        or ""
    )
    terminal = payload.get("is_terminal") is True or row.get("is_terminal") is True
    if event_id not in event_ids or not terminal:
        return False
    available_at = _maximum_aware_timestamp(
        (
            payload.get("label_available_at"),
            payload.get("source_available_at"),
            row.get("finalized_at"),
            row.get("observed_at"),
            row.get("updated_at"),
            row.get("recorded_at"),
            row.get("created_at"),
            payload.get("finalized_at"),
            payload.get("observed_at"),
            payload.get("observation_at"),
            payload.get("recorded_at"),
        )
    )
    if available_at is None:
        return True
    return available_at <= evaluation_as_of


def _maximum_aware_timestamp(values: Sequence[Any]) -> datetime | None:
    parsed = [
        parse_evaluation_as_of(value)
        for value in values
        if value is not None
    ]
    return max(parsed) if parsed else None


def _row_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, Mapping):
        return payload
    if "schema_version" in row:
        return row
    raise DecisionQualitySnapshotContractError(
        "stored decision-quality row has no object payload"
    )


def _snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    payload = snapshot.get("payload")
    value = payload if isinstance(payload, Mapping) else snapshot
    if not isinstance(value, Mapping):
        raise DecisionQualitySnapshotContractError("snapshot payload is not an object")
    return dict(value)


def _snapshot_run_row(user_id: int, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    manifest = snapshot.get("input_manifest")
    evaluation = snapshot.get("evaluation")
    overall = evaluation.get("overall") if isinstance(evaluation, Mapping) else None
    return {
        "user_id": user_id,
        "snapshot_id": snapshot.get("snapshot_id"),
        "evaluation_hash": snapshot.get("evaluation_hash"),
        "status": snapshot.get("status"),
        "readiness_status": snapshot.get("readiness_status"),
        "mature_decision_day_count": (
            int(manifest.get("mature_decision_day_count") or 0)
            if isinstance(manifest, Mapping)
            else 0
        ),
        "formal_label_coverage_percent": (
            _optional_number(overall.get("label_coverage_percent"))
            if isinstance(overall, Mapping)
            else None
        ),
        "human_review_status": snapshot.get("human_review_status"),
        "automatic_promotion_allowed": False,
    }


def _list_evidence_user_ids(connection: Any) -> list[int]:
    rows = _fetchall(
        connection,
        """
        SELECT userId FROM (
            SELECT userId FROM decision_events
            UNION
            SELECT userId FROM decision_quality_input_artifacts
        ) AS decision_quality_users
        ORDER BY userId
        """,
    )
    return sorted({_positive_user_id(row.get("userId")) for row in rows})


def _normalize_user_ids(value: Iterable[int] | None) -> list[int] | None:
    if value is None:
        return None
    return sorted({_positive_user_id(item) for item in value})


def _positive_user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("user_id must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError("user_id must be a positive integer")
    return parsed


def _validated_window_days(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("window_days must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("window_days must be a positive integer") from exc
    if parsed < 1 or parsed > MAX_WINDOW_DAYS:
        raise ValueError(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")
    return parsed


def _reject_possible_truncation(rows: Sequence[Any], source: str) -> None:
    if len(rows) >= _INPUT_LIMIT:
        raise DecisionQualitySnapshotStorageError(
            f"{source} reached the safe input limit; refusing a truncated evaluation"
        )


def _require_primary_store(connection: Any) -> None:
    if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
        raise DecisionQualitySnapshotStorageError(
            "MySQL is configured; decision-quality evaluation refuses SQLite fallback"
        )


def _default_connection_factory() -> Any:
    from app.database import _connect

    return _connect()


def _default_read_connection_factory() -> Any:
    """Open the latest-snapshot store without schema DDL or fallback writes."""

    settings = get_settings()
    if settings.uses_mysql:
        import pymysql

        from app.db_connect import DbConnection, _parse_mysql_url

        assert settings.database_url
        raw = pymysql.connect(
            **(
                _parse_mysql_url(settings.database_url)
                | {
                    "connect_timeout": 10,
                    "read_timeout": 30,
                    "write_timeout": 30,
                    "autocommit": False,
                }
            )
        )
        return DbConnection(raw, "mysql")

    from app.db_connect import DbConnection

    configured_path = os.getenv("FUND_AI_DB_PATH")
    path = Path(configured_path) if configured_path else settings.db_path
    uri = path.expanduser().resolve(strict=False).as_uri() + "?mode=ro"
    raw = sqlite3.connect(uri, uri=True)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA query_only = ON")
    return DbConnection(raw, "sqlite")


@contextmanager
def _connection(factory: Any, *, commit_on_success: bool) -> Iterator[Any]:
    connection = factory()
    try:
        yield connection
        if commit_on_success:
            connection.commit()
        else:
            # Ends MySQL REPEATABLE READ/MVCC snapshots.  This is a transaction
            # boundary only; no storage mutation is performed.
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


__all__ = [
    "DECISION_QUALITY_EVALUATOR_VERSION",
    "DECISION_QUALITY_SNAPSHOT_READ_SCHEMA_VERSION",
    "DecisionQualitySnapshotContractError",
    "DecisionQualitySnapshotError",
    "DecisionQualitySnapshotStorageError",
    "READINESS_INSUFFICIENT",
    "READINESS_MANUAL_REVIEW",
    "READINESS_SHADOW",
    "build_decision_quality_snapshot",
    "evaluate_and_persist_decision_quality_snapshots",
    "parse_evaluation_as_of",
    "read_latest_decision_quality_snapshot",
    "redact_decision_quality_snapshot",
    "resolve_decision_quality_readiness",
]
