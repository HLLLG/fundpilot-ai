"""Freeze report-level quality evidence in the append-only input ledger.

The visible report tables are replaceable product read models.  Claim and
candidate-selection evidence used by the D1 evaluator therefore has to be
copied into the append-only decision-quality artifact ledger in the same
transaction as the saved report and any DecisionEvent bundle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.candidate_selection_audit import (
    validate_candidate_selection_audit,
)
from app.services.decision_quality_evaluation import (
    CLAIM_AUDIT_SCHEMA_VERSION,
    CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION,
)
from app.services.decision_repository import (
    ImmutableRecordConflict,
    canonical_hash,
    list_decision_quality_input_artifacts,
    put_decision_quality_input_artifact,
)


CLAIM_AUDIT_ARTIFACT_TYPE = "claim_audit_wrapper"
CANDIDATE_AUDIT_ARTIFACT_TYPE = "candidate_selection_audit"
CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE = "candidate_selection_capture_failure"
CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION = (
    "decision_quality_candidate_capture_failure.v1"
)
CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3 = (
    "decision_quality_candidate_audit_artifact.v3"
)
CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION = (
    "decision_quality_candidate_audit_artifact.v4"
)
CANDIDATE_LABEL_PLAN_SCHEMA_VERSION_V2 = "candidate_label_plan.v2"
CANDIDATE_LABEL_PLAN_SCHEMA_VERSION = "candidate_label_plan.v3"
CANDIDATE_LABEL_POLICY_VERSION_V2 = "candidate_label_policy.2026-07.v2"
CANDIDATE_LABEL_POLICY_VERSION = "candidate_label_policy.2026-07.v3"
CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS = 300
CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS = 300
CANDIDATE_CAPTURE_MODE = "live_only_no_backfill"
_CANDIDATE_LABEL_HORIZON = 20
_CANDIDATE_LABEL_K = 3
_CANDIDATE_LABEL_UNIVERSE_STAGE = "prescreen"
_CN_TZ = ZoneInfo("Asia/Shanghai")


def persist_report_decision_quality_artifacts(
    *,
    user_id: int,
    report: Mapping[str, Any],
    saved_events: Sequence[Mapping[str, Any]],
    source_type: str,
    store_authority: str,
    report_recorded_at: str | datetime,
    connection: Any,
) -> list[dict[str, Any]]:
    """Append decision-time claim/candidate evidence for one saved report.

    A report-level claim audit is attached exactly once to a deterministic
    anchor event.  It must not be copied to every recommendation, because that
    would overweight multi-recommendation reports in the quality metrics.

    A discovery report may validly abstain and therefore have no DecisionEvent.
    Its candidate-selection audit is still decision evidence.  In that case the
    report transaction's post-insert receipt is the earliest honest availability
    clock; claim evidence remains event-bound and is deliberately not persisted.
    """

    if source_type not in {"daily", "discovery"}:
        raise ValueError("source_type must be daily or discovery")
    facts_key = "analysis_facts" if source_type == "daily" else "discovery_facts"
    facts = report.get(facts_key)
    facts_map = facts if isinstance(facts, Mapping) else {}
    anchor = _anchor_event(saved_events)
    candidate_audit = facts_map.get("candidate_selection_audit")
    candidate_audit_present = "candidate_selection_audit" in facts_map
    if anchor is None and source_type != "discovery":
        return []
    event: Mapping[str, Any] | None = None
    if anchor is not None:
        event = anchor["event"]
        receipt_at = _aware_utc_text(anchor["created_at"], "event.created_at")
        decision_at = _aware_utc_text(event.get("decision_at"), "event.decision_at")
        report_id = str(
            event.get("source_report_id")
            or event.get("report_id")
            or report.get("id")
            or ""
        ).strip()
        primary_eligible = bool(
            store_authority == "primary"
            and event.get("store_authority") == "primary"
            and event.get("audit_eligible") is True
        )
    else:
        # Candidate-selection is a report-level decision, so an abstention must
        # not be erased merely because no per-fund event exists.  The caller
        # captures this receipt only after the report row has been written in
        # the same transaction.
        receipt_at = _aware_utc_text(
            report_recorded_at,
            "report_recorded_at",
        )
        decision_at = _aware_utc_text(report.get("created_at"), "report.created_at")
        report_id = str(report.get("id") or "").strip()
        primary_eligible = store_authority == "primary"
    if _as_datetime(receipt_at) < _as_datetime(decision_at):
        subject = "decision event" if event is not None else "report"
        raise ValueError(f"{subject} cannot be recorded before its decision time")
    # Claim/candidate audits are written after their anchor event in the same
    # transaction.  A deterministic one-microsecond Lamport successor preserves
    # that strict storage order even when two Python clock reads share a tick,
    # while the ledger row's own created_at remains the physical receipt.
    recorded_at = (
        _strict_timestamp_successor(receipt_at) if event is not None else receipt_at
    )

    if not report_id:
        raise ValueError("source report id is required for quality artifacts")
    persisted: list[dict[str, Any]] = []

    claim_audit = facts_map.get("fund_lookthrough_claim_audit")
    if event is not None and isinstance(claim_audit, Mapping):
        wrapper = _claim_audit_wrapper(
            event=event,
            audit=claim_audit,
            recorded_at=recorded_at,
        )
        persisted.append(
            put_decision_quality_input_artifact(
                user_id=user_id,
                artifact={
                    "artifact_type": CLAIM_AUDIT_ARTIFACT_TYPE,
                    "artifact_schema_version": CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION,
                    "source_type": source_type,
                    "source_report_id": report_id,
                    "decision_event_id": str(event["event_id"]),
                    "decision_at": decision_at,
                    "available_at": recorded_at,
                    "recorded_at": recorded_at,
                    "store_authority": store_authority,
                    "audit_eligible": primary_eligible,
                    "artifact": wrapper,
                },
                connection=connection,
            )
        )

    if source_type == "discovery" and isinstance(candidate_audit, Mapping):
        validation = validate_candidate_selection_audit(candidate_audit)
        audit_decision_at = candidate_audit.get("decision_at")
        if validation.get("decision_eligible") is True and (
            audit_decision_at is None
            or _aware_utc_text(
                audit_decision_at, "candidate_audit.decision_at"
            )
            != decision_at
        ):
            raise ValueError(
                "candidate audit decision_at conflicts with its report/event anchor"
            )
        source_capture_delay_seconds = (
            _as_datetime(recorded_at) - _as_datetime(decision_at)
        ).total_seconds()
        source_capture_timely = (
            source_capture_delay_seconds
            <= CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        )
        capture_eligible = bool(
            primary_eligible
            and validation.get("decision_eligible") is True
            and source_capture_timely
        )
        capture_status = (
            "capture_late"
            if not source_capture_timely
            else "eligible"
            if capture_eligible
            else "capture_ineligible"
        )
        capture_reason = (
            "source_capture_delay_exceeded"
            if capture_status == "capture_late"
            else "eligible"
            if capture_status == "eligible"
            else "candidate_audit_not_decision_eligible"
            if validation.get("decision_eligible") is not True
            else "anchor_event_not_audit_eligible"
            if event is not None
            else "primary_store_required"
        )
        label_plan = build_candidate_label_plan(
            decision_at=decision_at,
            registered_at=recorded_at,
            decision_eligible=capture_eligible,
        )
        candidate_artifact = {
            "schema_version": CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
            "registration_phase": "phase1_preregistered",
            "provider_receipt_required": True,
            "capture_mode": CANDIDATE_CAPTURE_MODE,
            "post_commit_receipt_required": True,
            "formal_receipt_max_delay_seconds": (
                CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
            ),
            "formal_source_capture_max_delay_seconds": (
                CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
            ),
            "formal_source_capture_delay_basis": (
                "candidate_audit_source_row_created_at_minus_decision_at"
            ),
            "capture_status": capture_status,
            "capture_reason": capture_reason,
            "source_report_id": report_id,
            "decision_at": decision_at,
            "recorded_at": recorded_at,
            "audit_snapshot_hash": candidate_audit.get("snapshot_hash"),
            "audit": deepcopy(dict(candidate_audit)),
            "capture_validation": deepcopy(validation),
            "label_plan": label_plan,
        }
        if event is None:
            candidate_artifact["recorded_at_source"] = "report_transaction_post_insert"
            existing = _matching_report_candidate_artifact(
                user_id=user_id,
                report_id=report_id,
                decision_at=decision_at,
                store_authority=store_authority,
                audit_eligible=bool(
                    capture_eligible
                ),
                candidate_artifact=candidate_artifact,
                connection=connection,
            )
            if existing is not None:
                # The same report/audit retry is not a new observation.  Reuse
                # its first immutable receipt instead of manufacturing a second
                # content id solely because wall-clock time advanced.
                return [existing]
        try:
            saved_candidate_artifact = put_decision_quality_input_artifact(
                user_id=user_id,
                artifact={
                    "artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
                    "artifact_schema_version": (
                        CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
                    ),
                    "logical_key": f"candidate_audit:{report_id}",
                    "source_type": source_type,
                    "source_report_id": report_id,
                    # Candidate ranking is report-level evidence, not one
                    # recommendation's evidence.
                    "decision_event_id": None,
                    "decision_at": decision_at,
                    "available_at": recorded_at,
                    "recorded_at": recorded_at,
                    "store_authority": store_authority,
                    "audit_eligible": bool(
                        capture_eligible
                    ),
                    "artifact": candidate_artifact,
                },
                connection=connection,
            )
        except ImmutableRecordConflict:
            existing = _matching_report_candidate_artifact(
                user_id=user_id,
                report_id=report_id,
                decision_at=decision_at,
                store_authority=store_authority,
                audit_eligible=bool(
                    capture_eligible
                ),
                candidate_artifact=candidate_artifact,
                connection=connection,
            )
            if existing is None:
                raise
            saved_candidate_artifact = existing
        _require_candidate_preregistration(saved_candidate_artifact)
        persisted.append(saved_candidate_artifact)
    elif source_type == "discovery":
        persisted.append(
            _persist_candidate_capture_failure(
                user_id=user_id,
                report_id=report_id,
                decision_at=decision_at,
                recorded_at=recorded_at,
                store_authority=store_authority,
                reason=(
                    "candidate_selection_audit_not_mapping"
                    if candidate_audit_present
                    else "candidate_selection_audit_missing"
                ),
                connection=connection,
            )
        )
    return persisted


def _matching_report_candidate_artifact(
    *,
    user_id: int,
    report_id: str,
    decision_at: str,
    store_authority: str,
    audit_eligible: bool,
    candidate_artifact: Mapping[str, Any],
    connection: Any,
) -> dict[str, Any] | None:
    expected_semantic_identity = _candidate_artifact_semantic_identity(
        candidate_artifact
    )
    rows = list_decision_quality_input_artifacts(
        user_id=user_id,
        artifact_type=CANDIDATE_AUDIT_ARTIFACT_TYPE,
        source_type="discovery",
        source_report_id=report_id,
        limit=10_000,
        connection=connection,
    )
    for row in rows:
        envelope = row.get("payload")
        if not isinstance(envelope, Mapping):
            continue
        artifact = envelope.get("artifact")
        if not isinstance(artifact, Mapping):
            continue
        existing_semantic_identity = _candidate_artifact_semantic_identity(artifact)
        if (
            envelope.get("decision_event_id") is None
            and envelope.get("decision_at") == decision_at
            and envelope.get("available_at") == envelope.get("recorded_at")
            and envelope.get("recorded_at") == artifact.get("recorded_at")
            and envelope.get("store_authority") == store_authority
            and canonical_hash(existing_semantic_identity)
            == canonical_hash(expected_semantic_identity)
        ):
            return dict(row)
    return None


def _require_candidate_preregistration(
    row: Mapping[str, Any],
) -> None:
    envelope = row.get("payload")
    artifact = envelope.get("artifact") if isinstance(envelope, Mapping) else None
    label_plan = artifact.get("label_plan") if isinstance(artifact, Mapping) else None
    artifact_schema = (
        artifact.get("schema_version") if isinstance(artifact, Mapping) else None
    )
    if (
        artifact_schema != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
        or not isinstance(label_plan, Mapping)
    ):
        raise ValueError("candidate audit preregistration contract is missing")
    if label_plan.get("status") not in {"preregistered", "ineligible"}:
        raise ValueError("candidate audit preregistration status is invalid")
    expected_contract = {
        "provider_receipt_required": True,
        "capture_mode": CANDIDATE_CAPTURE_MODE,
        "post_commit_receipt_required": True,
        "formal_receipt_max_delay_seconds": (
            CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        ),
        "formal_source_capture_max_delay_seconds": (
            CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        ),
        "formal_source_capture_delay_basis": (
            "candidate_audit_source_row_created_at_minus_decision_at"
        ),
    }
    if any(
        artifact.get(key) != value or label_plan.get(key) != value
        for key, value in expected_contract.items()
    ):
        raise ValueError("candidate audit receipt policy is invalid")
    if label_plan.get("preregistered_at") != envelope.get("recorded_at"):
        raise ValueError("candidate audit preregistration clock is invalid")
    if any(
        key in label_plan
        for key in ("entry_date", "actual_entry_date", "entry_not_before_date")
    ):
        raise ValueError("candidate audit phase1 plan cannot contain an entry date")


def build_candidate_label_plan(
    *,
    decision_at: str | datetime,
    registered_at: str | datetime,
    decision_eligible: bool,
) -> dict[str, Any]:
    """Pre-register one conservative, forward-only candidate ranking label.

    The exact common entry/exit market dates are resolved later from a frozen
    exchange-calendar snapshot.  Phase 1 deliberately has no entry date: a
    formal plan first needs the audit artifact's storage-owned post-commit
    receipt and the label provider's live capture receipt.  Their selection
    rule, horizon, metric basis, and minimum evidence coverage are fixed before
    any outcome is known.
    """

    decision_text = _aware_utc_text(decision_at, "decision_at")
    registered_text = _aware_utc_text(registered_at, "registered_at")
    decision_value = _as_datetime(decision_text)
    registered_value = _as_datetime(registered_text)
    if registered_value < decision_value:
        raise ValueError("candidate label plan cannot predate its decision")
    status = "preregistered" if decision_eligible else "ineligible"
    plan: dict[str, Any] = {
        "schema_version": CANDIDATE_LABEL_PLAN_SCHEMA_VERSION,
        "policy_version": CANDIDATE_LABEL_POLICY_VERSION,
        "status": status,
        "reason": (
            "awaiting_post_commit_receipt"
            if decision_eligible
            else "candidate_audit_not_decision_eligible"
        ),
        "registration_phase": "phase1_preregistered",
        "decision_at": decision_text,
        "decision_date_local": decision_value.astimezone(_CN_TZ).date().isoformat(),
        "market_timezone": "Asia/Shanghai",
        "decision_cutoff_local": "15:00:00",
        "same_day_entry_allowed": False,
        "provider_receipt_required": True,
        "capture_mode": CANDIDATE_CAPTURE_MODE,
        "post_commit_receipt_required": True,
        "formal_receipt_max_delay_seconds": (
            CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        ),
        "formal_source_capture_max_delay_seconds": (
            CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        ),
        "formal_source_capture_delay_basis": (
            "candidate_audit_source_row_created_at_minus_decision_at"
        ),
        "formal_receipt_delay_basis": (
            "audit_post_commit_receipt_source_visible_at_minus_source_row_created_at"
        ),
        "commit_visibility_policy": "post_commit_receipt_required",
        "entry_anchor_basis": (
            "later_of_decision_at_and_audit_post_commit_receipt_source_visible_at"
        ),
        "entry_calendar_day_rule": "next_asia_shanghai_calendar_day",
        "entry_date_status": (
            "pending_post_commit_receipt" if decision_eligible else "not_applicable"
        ),
        "date_resolution_rule": (
            "first_cn_market_trade_date_on_or_after_derived_entry_calendar_day_"
            "then_20_transitions"
        ),
        "horizon_trading_days": _CANDIDATE_LABEL_HORIZON,
        "k": _CANDIDATE_LABEL_K,
        "universe_stage": _CANDIDATE_LABEL_UNIVERSE_STAGE,
        "utility_basis": "total_return_percent_before_costs",
        "return_series_policy": "daily_growth_preferred_nav_ratio_fallback_v1",
        "minimum_daily_growth_coverage": 1.0,
        "minimum_observation_ratio": 1.0,
        "automatic_promotion_allowed": False,
    }
    plan["plan_hash"] = canonical_hash(plan)
    # This is the Phase 1 request clock, not proof that the surrounding
    # transaction was externally visible.  A Phase 2 finalizer must bind a
    # storage-owned receipt.source_visible_at before resolving an entry date.
    plan["preregistered_at"] = registered_text
    return plan


def _persist_candidate_capture_failure(
    *,
    user_id: int,
    report_id: str,
    decision_at: str,
    recorded_at: str,
    store_authority: str,
    reason: str,
    connection: Any,
) -> dict[str, Any]:
    """Freeze a report-level sentinel when candidate capture never materialized."""

    reason_material = {"reason": reason}
    sentinel = {
        "schema_version": CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION,
        "source_report_id": report_id,
        "decision_at": decision_at,
        "recorded_at": recorded_at,
        "capture_mode": CANDIDATE_CAPTURE_MODE,
        "capture_status": "capture_ineligible",
        "capture_reason": reason,
        "capture_reason_hash": canonical_hash(reason_material),
        "formal_source_capture_max_delay_seconds": (
            CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        ),
        "formal_receipt_max_delay_seconds": (
            CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        ),
        "post_commit_receipt_required": True,
        "automatic_promotion_allowed": False,
    }
    try:
        return put_decision_quality_input_artifact(
            user_id=user_id,
            artifact={
                "artifact_type": CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
                "artifact_schema_version": (
                    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
                ),
                "logical_key": f"candidate_capture_failure:{report_id}",
                "source_type": "discovery",
                "source_report_id": report_id,
                "decision_event_id": None,
                "decision_at": decision_at,
                "available_at": recorded_at,
                "recorded_at": recorded_at,
                "store_authority": store_authority,
                "audit_eligible": False,
                "artifact": sentinel,
            },
            connection=connection,
        )
    except ImmutableRecordConflict:
        rows = list_decision_quality_input_artifacts(
            user_id=user_id,
            artifact_type=CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
            source_type="discovery",
            source_report_id=report_id,
            limit=10_000,
            connection=connection,
        )
        for row in rows:
            envelope = row.get("payload")
            if (
                isinstance(envelope, Mapping)
                and envelope.get("logical_key")
                == f"candidate_capture_failure:{report_id}"
                and envelope.get("artifact_schema_version")
                == CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION
            ):
                return dict(row)
        raise


def candidate_label_entry_not_before_date_from_post_commit_receipt(
    *,
    decision_at: str | datetime,
    audit_receipt_source_visible_at: str | datetime,
) -> str:
    """Derive Phase 2's earliest entry day from an actual visibility receipt."""

    decision_value = _as_datetime(_aware_utc_text(decision_at, "decision_at"))
    source_visible_value = _as_datetime(
        _aware_utc_text(
            audit_receipt_source_visible_at,
            "audit_receipt_source_visible_at",
        )
    )
    local_anchor = max(decision_value, source_visible_value).astimezone(_CN_TZ)
    return (local_anchor.date() + timedelta(days=1)).isoformat()


def candidate_label_entry_not_before_date(
    *,
    decision_at: str | datetime,
    registered_at: str | datetime,
) -> str:
    """Resolve the legacy v2 entry anchor from its registration clock.

    New v3 plans must use
    :func:`candidate_label_entry_not_before_date_from_post_commit_receipt` and
    must not write the returned date during Phase 1.
    """

    decision_value = _as_datetime(
        _aware_utc_text(decision_at, "decision_at")
    )
    registered_value = _as_datetime(
        _aware_utc_text(registered_at, "registered_at")
    )
    if registered_value < decision_value:
        raise ValueError("candidate label plan cannot predate its decision")
    local_anchor = max(decision_value, registered_value).astimezone(_CN_TZ)
    # Application timestamps are sampled before the surrounding database
    # transaction commits.  Same-day close can therefore become observable
    # while the row is still lock-waiting or the process is paused.  D3 never
    # uses same-day NAV: the earliest admissible market date is the following
    # local calendar day, later resolved to the first market trading date.
    return (local_anchor.date() + timedelta(days=1)).isoformat()


def _candidate_artifact_semantic_identity(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Return report-decision evidence that cannot legitimately change on retry."""

    return {
        key: deepcopy(artifact.get(key))
        for key in (
            "schema_version",
            "source_report_id",
            "decision_at",
            "audit_snapshot_hash",
            "audit",
            "capture_validation",
        )
    }


def _anchor_event(
    saved_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in saved_events:
        payload = row.get("payload")
        event = dict(payload) if isinstance(payload, Mapping) else dict(row)
        if (
            event.get("schema_version") != "decision_event.v2"
            or not str(event.get("event_id") or "").strip()
            or row.get("created_at") is None
        ):
            continue
        index = event.get("recommendation_index")
        candidates.append(
            {
                "event": event,
                "created_at": row.get("created_at"),
                "sort_key": (
                    int(index)
                    if isinstance(index, int) and not isinstance(index, bool)
                    else 10**9,
                    str(event["event_id"]),
                ),
            }
        )
    return min(candidates, key=lambda item: item["sort_key"]) if candidates else None


def _claim_audit_wrapper(
    *,
    event: Mapping[str, Any],
    audit: Mapping[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    if audit.get("schema_version") != CLAIM_AUDIT_SCHEMA_VERSION:
        raise ValueError("claim audit schema is unsupported")
    event_hash = str(event.get("payload_hash") or "").strip().lower()
    if len(event_hash) != 64 or any(ch not in "0123456789abcdef" for ch in event_hash):
        raise ValueError("decision event payload_hash is invalid")
    wrapper: dict[str, Any] = {
        "schema_version": CLAIM_AUDIT_WRAPPER_SCHEMA_VERSION,
        "event_id": str(event["event_id"]),
        "decision_at": _aware_utc_text(event.get("decision_at"), "event.decision_at"),
        "decision_event_payload_hash": event_hash,
        # The post-generation validator is persisted immediately after the
        # event.  ``recorded_at`` is the deterministic strict successor of the
        # immutable event receipt, never the earlier logical request clock.
        "available_at": recorded_at,
        "recorded_at": recorded_at,
        "audit": deepcopy(dict(audit)),
    }
    wrapper["content_hash"] = canonical_hash(wrapper)
    return wrapper


def _aware_utc_text(value: object, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _strict_timestamp_successor(value: str) -> str:
    try:
        return (_as_datetime(value) + timedelta(microseconds=1)).isoformat()
    except OverflowError as exc:
        raise ValueError("event receipt has no representable strict successor") from exc


__all__ = [
    "CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION",
    "CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3",
    "CANDIDATE_AUDIT_ARTIFACT_TYPE",
    "CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION",
    "CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE",
    "CANDIDATE_CAPTURE_MODE",
    "CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS",
    "CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS",
    "CANDIDATE_LABEL_PLAN_SCHEMA_VERSION",
    "CANDIDATE_LABEL_PLAN_SCHEMA_VERSION_V2",
    "CANDIDATE_LABEL_POLICY_VERSION",
    "CANDIDATE_LABEL_POLICY_VERSION_V2",
    "CLAIM_AUDIT_ARTIFACT_TYPE",
    "build_candidate_label_plan",
    "candidate_label_entry_not_before_date",
    "candidate_label_entry_not_before_date_from_post_commit_receipt",
    "persist_report_decision_quality_artifacts",
]
