"""Forward-only T+20 labels for the frozen discovery candidate funnel.

This module evaluates the deterministic prescreen -> final candidate policy.
It does not evaluate the LLM, does not mutate recommendation weights, and never
promotes a strategy.  Every scored fund shares one pre-registered market entry
and exit date, and returns are rebuilt from daily-growth observations whenever
available so cash distributions are not mistaken for losses.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.akshare_subprocess import fetch_fund_nav_history_quality_read
from app.services.candidate_selection_audit import (
    V2_SCHEMA_VERSION,
    normalize_candidate_selection_audit,
    validate_candidate_selection_audit,
)
from app.services.decision_quality_artifacts import (
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_AUDIT_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_MODE,
    CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS,
    CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS,
    CANDIDATE_LABEL_PLAN_SCHEMA_VERSION,
    CANDIDATE_LABEL_POLICY_VERSION,
    build_candidate_label_plan,
    candidate_label_entry_not_before_date_from_post_commit_receipt,
)
from app.services.decision_quality_provider_receipts import (
    DecisionQualityProviderRead,
    ProviderReceiptValidationError,
    canonical_provider_hash,
    validate_provider_delivery,
    validate_provider_origin_receipt,
    validate_provider_read,
)
from app.services.decision_quality_provider_policy import (
    CandidateProviderAdapterPolicyError,
    candidate_adapter_policy_is_registered,
    candidate_provider_adapter_stratum,
    candidate_provider_adapter_stratum_hash,
    rebuild_candidate_provider_normalized_payload,
    verify_candidate_provider_adapter_policy,
)
from app.services.decision_repository import (
    DecisionQualityIntegrityError,
    ImmutableRecordConflict,
    canonical_hash,
    finalize_decision_quality_artifact_receipt,
    get_decision_quality_provider_receipt,
    list_decision_quality_artifact_receipts,
    list_decision_quality_input_artifacts,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_input_artifact,
    normalize_decision_quality_provider_receipt,
    put_decision_quality_input_artifact,
    put_decision_quality_provider_receipt,
)
from app.services.fund_factor_nav import build_total_return_index
from app.services.trade_calendar_cache import get_trade_calendar_quality_read


CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE = "candidate_selection_outcome_set"
CANDIDATE_OUTCOME_SET_SCHEMA_VERSION_V2 = (
    "decision_quality_candidate_outcome_set.v2"
)
CANDIDATE_OUTCOME_SET_SCHEMA_VERSION = (
    "decision_quality_candidate_outcome_set.v3"
)
CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION_V2 = (
    "decision_quality_candidate_outcome_label.v2"
)
CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION = (
    "decision_quality_candidate_outcome_label.v3"
)
CANDIDATE_RETURN_EVIDENCE_SCHEMA_VERSION_V1 = (
    "decision_quality_candidate_return_evidence.v1"
)
CANDIDATE_RETURN_EVIDENCE_SCHEMA_VERSION = (
    "decision_quality_candidate_return_evidence.v2"
)
CANDIDATE_CALENDAR_SCHEMA_VERSION_V1 = "decision_quality_trade_calendar.v1"
CANDIDATE_CALENDAR_SCHEMA_VERSION = "decision_quality_trade_calendar.v2"
_NAV_SOURCE = "akshare.fund_open_fund_info_em"
_NAV_OPERATION = "fund_open_fund_info_em"
_NAV_INDICATOR = "单位净值走势"
_CALENDAR_SOURCE = "akshare.tool_trade_date_hist_sina"
_CALENDAR_OPERATION = "tool_trade_date_hist_sina"
_MAX_CASES = 100
_MAX_NAV_POINTS = 800
_MAX_FETCH_WORKERS = 8
_CN_TZ = ZoneInfo("Asia/Shanghai")


class CandidateSelectionSettlementError(RuntimeError):
    """Candidate outcome evidence could not be safely settled."""


class CandidateSelectionSettlementConflict(CandidateSelectionSettlementError):
    """A terminal logical candidate case disagrees with new source evidence."""


class CandidateAuditCommitReceiptLate(CandidateSelectionSettlementError):
    """A valid audit receipt missed the frozen formal-eligibility deadline.

    This is a stable policy classification, not evidence corruption.  Callers
    must retain the audit and receipt in the formal coverage denominator while
    refusing to fetch providers or materialize an outcome for it.
    """

    def __init__(self, receipt: Mapping[str, Any]) -> None:
        super().__init__(
            "candidate audit commit receipt exceeds the formal delay policy"
        )
        self.receipt = deepcopy(dict(receipt))


class CandidateAuditSourceCaptureLate(CandidateSelectionSettlementError):
    """The first immutable audit row was written too long after decision time."""

    def __init__(self, *, delay_seconds: float) -> None:
        super().__init__(
            "candidate audit source capture exceeds the formal delay policy"
        )
        self.delay_seconds = float(delay_seconds)


def settle_candidate_selection_outcomes(
    *,
    user_ids: Iterable[int] | None = None,
    as_of_date: str | None = None,
    max_cases: int = 20,
    fetch_nav: Callable[..., object] = fetch_fund_nav_history_quality_read,
    fetch_calendar: Callable[[], object] | None = None,
    observed_at: str | datetime | None = None,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Settle due D4 audits without letting application clocks self-attest.

    The formal path is deliberately three-phase: provider origins are committed
    first, the outcome transaction re-reads every provider receipt before
    writing, and a fresh transaction then proves that the outcome artifact is
    commit-visible.  Any crash between phases is repaired idempotently.
    """

    anchor = _iso_date(as_of_date or date.today().isoformat())
    if anchor is None:
        raise ValueError("as_of_date must be an ISO date")
    safe_limit = max(1, min(int(max_cases), _MAX_CASES))
    allowed_users = _normalize_user_ids(user_ids)
    run_clock = _aware_utc_text(
        observed_at or datetime.now(timezone.utc),
        "observed_at",
    )
    run_clock_value = _aware_datetime(run_clock)
    assert run_clock_value is not None
    if anchor > run_clock_value.astimezone(_CN_TZ).date().isoformat():
        raise ValueError("as_of_date cannot be after the settlement receipt date")
    factory = connection_factory
    if factory is None:
        from app.database import _connect

        factory = _connect

    (
        audits,
        existing_by_audit,
        artifact_receipts,
        load_failed_user_ids,
    ) = _load_candidate_artifacts(
        allowed_users=allowed_users,
        connection_factory=factory,
    )
    pending_reasons: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()
    failed_user_ids: set[int] = set(load_failed_user_ids)
    if load_failed_user_ids:
        failure_reasons["DecisionQualityIntegrityError"] += len(
            load_failed_user_ids
        )
    existing_count = 0
    legacy_ignored_count = 0
    formal_audit_count = 0
    audit_receipt_count = 0
    missing_audit_receipt_count = 0
    late_audit_receipt_count = 0
    existing_outcome_receipt_recovered_count = 0
    eligible_targets: list[dict[str, Any]] = []
    audits_by_user: dict[int, list[Mapping[str, Any]]] = {}
    for row in audits:
        audits_by_user.setdefault(int(row.get("user_id") or 0), []).append(row)
    for user_id, user_audits in sorted(audits_by_user.items()):
        user_targets: list[dict[str, Any]] = []
        user_existing_count = 0
        try:
            for row in user_audits:
                envelope = row.get("payload")
                artifact = (
                    envelope.get("artifact")
                    if isinstance(envelope, Mapping)
                    else None
                )
                schema = (
                    artifact.get("schema_version")
                    if isinstance(artifact, Mapping)
                    else None
                )
                if schema != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION:
                    # v3/v2 evidence remains immutable historical shadow data.
                    # It is never upgraded into the D4 source-verified stratum.
                    legacy_ignored_count += 1
                    continue
                formal_audit_count += 1
                artifact_id = str(
                    envelope.get("artifact_id")
                    if isinstance(envelope, Mapping)
                    else ""
                )
                audit_receipt = artifact_receipts.get((user_id, artifact_id))
                if audit_receipt is None:
                    pending_reasons[
                        "candidate_audit_commit_receipt_pending"
                    ] += 1
                    missing_audit_receipt_count += 1
                    continue
                audit_receipt_count += 1
                try:
                    target = candidate_target_from_artifact(
                        row,
                        artifact_receipt=audit_receipt,
                    )
                except CandidateAuditCommitReceiptLate:
                    if existing_by_audit.get((user_id, artifact_id)):
                        raise CandidateSelectionSettlementConflict(
                            "late candidate audit receipt has a terminal outcome"
                        )
                    pending_reasons[
                        "candidate_audit_commit_receipt_late"
                    ] += 1
                    late_audit_receipt_count += 1
                    continue
                except CandidateAuditSourceCaptureLate:
                    if existing_by_audit.get((user_id, artifact_id)):
                        raise CandidateSelectionSettlementConflict(
                            "late candidate source capture has a terminal outcome"
                        )
                    pending_reasons[
                        "candidate_audit_source_capture_late"
                    ] += 1
                    late_audit_receipt_count += 1
                    continue
                if target is None:
                    pending_reasons[
                        "candidate_label_plan_not_preregistered"
                    ] += 1
                    continue
                if not target.get("universe_codes"):
                    pending_reasons[
                        "candidate_universe_empty_no_ranking_denominator"
                    ] += 1
                    continue
                audit_id = str(target["audit_artifact_id"])
                existing = existing_by_audit.get((user_id, audit_id), [])
                if len(existing) > 1:
                    raise CandidateSelectionSettlementConflict(
                        "multiple terminal candidate outcome sets share one audit"
                    )
                if existing:
                    outcome_artifact = existing[0].get("payload", {}).get("artifact")
                    if not isinstance(outcome_artifact, Mapping):
                        raise CandidateSelectionSettlementConflict(
                            "terminal candidate outcome set is malformed"
                        )
                    validate_candidate_outcome_set(outcome_artifact, target=target)
                    outcome_id = str(
                        existing[0].get("payload", {}).get("artifact_id") or ""
                    )
                    had_receipt = (
                        artifact_receipts.get((user_id, outcome_id)) is not None
                    )
                    _require_or_finalize_outcome_receipt(
                        existing[0],
                        target=target,
                        connection_factory=factory,
                    )
                    if not had_receipt:
                        existing_outcome_receipt_recovered_count += 1
                    user_existing_count += 1
                    continue
                user_targets.append(target)
        except (
            CandidateSelectionSettlementError,
            DecisionQualityIntegrityError,
            ValueError,
        ) as exc:
            failed_user_ids.add(user_id)
            failure_reasons[type(exc).__name__] += 1
            continue
        eligible_targets.extend(user_targets)
        existing_count += user_existing_count

    calendar_read: DecisionQualityProviderRead | None = None
    calendar: dict[str, Any] | None = None
    calendar_pending_reason: str | None = None
    if eligible_targets:
        try:
            raw_calendar_read = (
                fetch_calendar()
                if fetch_calendar is not None
                else get_trade_calendar_quality_read()
            )
            calendar_read = _require_provider_read(
                raw_calendar_read,
                provider=_CALENDAR_SOURCE,
                operation=_CALENDAR_OPERATION,
            )
            calendar = _calendar_snapshot(
                calendar_read,
                as_of_date=anchor,
            )
        except (CandidateSelectionSettlementError, ProviderReceiptValidationError):
            calendar_pending_reason = "trade_calendar_provider_receipt_invalid"
    mature_targets: list[dict[str, Any]] = []
    for target in sorted(
        eligible_targets,
        key=lambda item: (
            str(item.get("audit_storage_cutoff") or ""),
            str(item.get("audit_artifact_id") or ""),
        ),
    ):
        if calendar is None:
            pending_reasons[
                calendar_pending_reason or "trade_calendar_unavailable"
            ] += 1
            continue
        dates = _resolve_plan_dates(
            target["label_plan"],
            calendar,
            entry_not_before_date=str(target["entry_not_before_date"]),
        )
        if dates is None:
            calendar_dates = [str(value) for value in calendar.get("dates") or []]
            anchor_date = str(
                target.get("entry_not_before_date") or ""
            )
            reason = (
                "trade_calendar_history_insufficient"
                if calendar_dates and anchor_date < calendar_dates[0]
                else "candidate_horizon_not_mature"
            )
            pending_reasons[reason] += 1
            continue
        mature_targets.append(
            {
                **target,
                **dates,
                "calendar": calendar,
                "calendar_provider_read": calendar_read,
            }
        )
    due_targets = _fair_due_window(
        mature_targets,
        limit=safe_limit,
        as_of_date=anchor,
    )
    if len(mature_targets) > safe_limit:
        pending_reasons["case_limit_deferred"] += len(mature_targets) - safe_limit

    fetch_specs: dict[str, int] = {}
    for target in due_targets:
        pull_days = _nav_pull_days(
            entry_date=str(target["entry_date"]),
            as_of_date=anchor,
        )
        for code in target["universe_codes"]:
            fetch_specs[code] = max(fetch_specs.get(code, 0), pull_days)
    nav_payloads = _fetch_nav_payloads(fetch_specs, fetch_nav=fetch_nav)

    persisted_count = 0
    idempotent_count = 0
    provider_receipt_ids: set[str] = set()
    outcome_receipt_count = existing_count
    completed_case_ids: list[str] = []
    for target in due_targets:
        # The label clock is sampled only after all provider reads for this
        # case have completed.  Sampling once at batch start would backdate a
        # slow fetch and overstate point-in-time availability.
        case_receipt = _aware_utc_text(
            observed_at or datetime.now(timezone.utc),
            "observed_at",
        )
        try:
            built, reason = build_candidate_outcome_set(
                target,
                nav_payloads=nav_payloads,
                settled_at=case_receipt,
            )
        except (
            CandidateSelectionSettlementError,
            DecisionQualityIntegrityError,
            ValueError,
        ) as exc:
            user_id = int(target.get("user_id") or 0)
            failed_user_ids.add(user_id)
            failure_reasons[type(exc).__name__] += 1
            continue
        if built is None:
            pending_reasons[reason or "candidate_labels_incomplete"] += 1
            continue
        try:
            case_reads = [target.get("calendar_provider_read")]
            case_reads.extend(
                nav_payloads.get(str(code))
                for code in target.get("universe_codes") or []
            )
            persisted_provider_rows = _persist_provider_reads(
                case_reads,
                connection_factory=factory,
            )
            provider_receipt_ids.update(
                str(row["payload"]["receipt_id"])
                for row in persisted_provider_rows
            )
            saved, inserted = _persist_outcome_set(
                target=target,
                outcome_set=built,
                connection_factory=factory,
            )
        except (
            CandidateSelectionSettlementError,
            DecisionQualityIntegrityError,
            ValueError,
        ) as exc:
            user_id = int(target.get("user_id") or 0)
            failed_user_ids.add(user_id)
            failure_reasons[type(exc).__name__] += 1
            continue
        completed_case_ids.append(str(saved["payload"]["artifact"]["case_id"]))
        outcome_receipt_count += 1
        if inserted:
            persisted_count += 1
        else:
            idempotent_count += 1

    pending_count = sum(pending_reasons.values())
    return {
        "schema_version": "candidate_selection_settlement.v2",
        "status": (
            "completed_with_failures"
            if failed_user_ids
            else "completed"
            if pending_count == 0
            else "completed_with_pending"
        ),
        "as_of_date": anchor,
        "audit_count": len(audits),
        "formal_audit_count": formal_audit_count,
        "legacy_ignored_audit_count": legacy_ignored_count,
        "audit_commit_receipt_count": audit_receipt_count,
        "missing_audit_commit_receipt_count": missing_audit_receipt_count,
        "late_audit_commit_receipt_count": late_audit_receipt_count,
        "registered_audit_count": len(eligible_targets),
        "due_case_count": len(due_targets),
        "existing_case_count": existing_count,
        "persisted_case_count": persisted_count,
        "idempotent_case_count": idempotent_count,
        "provider_receipt_count": len(provider_receipt_ids),
        "outcome_commit_receipt_count": outcome_receipt_count,
        "recovered_outcome_commit_receipt_count": (
            existing_outcome_receipt_recovered_count
        ),
        "pending_case_count": pending_count,
        "pending_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(pending_reasons.items())
        ],
        "failed_user_ids": sorted(failed_user_ids),
        "failure_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(failure_reasons.items())
        ],
        "unique_fund_fetch_count": len(fetch_specs),
        "completed_case_ids": sorted(completed_case_ids),
        "metric_scope": "deterministic_prescreen_to_final_candidate_policy",
        "automatic_promotion_allowed": False,
    }


def build_candidate_outcome_set(
    target: Mapping[str, Any],
    *,
    nav_payloads: Mapping[str, object],
    settled_at: str | datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build a full-universe source-verified label set, or remain pending."""

    receipt = _aware_utc_text(settled_at, "settled_at")
    audit_cutoff = _aware_datetime(target.get("audit_storage_cutoff"))
    if audit_cutoff is None or _aware_datetime(receipt) <= audit_cutoff:
        raise CandidateSelectionSettlementError(
            "candidate label receipt must be strictly after audit storage cutoff"
        )
    entry_date = str(target.get("entry_date") or "")
    exit_date = str(target.get("exit_date") or "")
    plan = target.get("label_plan")
    if not isinstance(plan, Mapping):
        raise CandidateSelectionSettlementError("candidate label plan is missing")
    if not _exit_is_observable_at_receipt(exit_date, receipt):
        raise CandidateSelectionSettlementError(
            "candidate exit date is not observable at settlement receipt"
        )
    expected_dates = _expected_trade_dates(target)
    if expected_dates is None:
        raise CandidateSelectionSettlementError(
            "candidate common-date calendar path is invalid"
        )

    calendar = target.get("calendar")
    if not isinstance(calendar, Mapping):
        raise CandidateSelectionSettlementError("candidate calendar is missing")
    _validate_calendar_snapshot(calendar, settled_at=receipt)
    calendar_read = _require_provider_read(
        target.get("calendar_provider_read"),
        provider=_CALENDAR_SOURCE,
        operation=_CALENDAR_OPERATION,
    )
    _require_snapshot_matches_provider_read(calendar, calendar_read)

    evidence_by_code: dict[str, dict[str, Any]] = {}
    nav_refs: dict[str, dict[str, Any]] = {}
    nav_deliveries: dict[str, dict[str, Any]] = {}
    returns: dict[str, float] = {}
    for code in target.get("universe_codes") or []:
        normalized_code = str(code)
        read = _require_nav_provider_read(
            nav_payloads.get(normalized_code),
            fund_code=normalized_code,
        )
        origin_completed_at = _provider_origin_completed_at(read)
        if not _exit_is_observable_at_receipt(exit_date, origin_completed_at):
            return None, "candidate_nav_origin_before_exit_close"
        delivery_at = _aware_datetime(read.delivery.get("served_at"))
        materialized_at = _aware_datetime(receipt)
        if (
            delivery_at is None
            or materialized_at is None
            or delivery_at > materialized_at
        ):
            raise CandidateSelectionSettlementError(
                "candidate NAV delivery cannot postdate materialization"
            )
        provider_ref = _provider_receipt_ref(read)
        evidence, reason = _return_evidence(
            normalized_code,
            read.normalized_payload,
            entry_date=entry_date,
            exit_date=exit_date,
            expected_dates=expected_dates,
            horizon=int(plan["horizon_trading_days"]),
            minimum_daily_growth_coverage=float(
                plan["minimum_daily_growth_coverage"]
            ),
            minimum_observation_ratio=float(plan["minimum_observation_ratio"]),
            provider_normalized_payload_hash=str(
                provider_ref["normalized_payload_hash"]
            ),
        )
        if evidence is None:
            return None, reason
        evidence_by_code[normalized_code] = evidence
        nav_refs[normalized_code] = provider_ref
        nav_deliveries[normalized_code] = deepcopy(read.delivery)
        returns[normalized_code] = float(evidence["return_percent"])

    if not returns:
        return None, "candidate_universe_empty"
    relevance = _cross_sectional_relevance(returns)
    case_id = candidate_case_id(target)
    labels: dict[str, dict[str, Any]] = {}
    for code in sorted(returns):
        evidence = evidence_by_code[code]
        source_ref = {
            "source": _NAV_SOURCE,
            "ref_id": f"{case_id}:{code}",
            "content_hash": evidence["evidence_hash"],
            "provider_receipt_ref": deepcopy(nav_refs[code]),
            "delivery": deepcopy(nav_deliveries[code]),
            "normalized_payload_projection_hash": evidence[
                "normalized_payload_projection_hash"
            ],
        }
        label: dict[str, Any] = {
            "schema_version": CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION,
            "fund_code": code,
            "horizon_trading_days": int(plan["horizon_trading_days"]),
            "entry_date": entry_date,
            "exit_date": exit_date,
            "mature": True,
            "eligible": True,
            "skipped": False,
            "binary_relevance": returns[code] > 0.0,
            "relevance": relevance[code],
            "return_percent": returns[code],
            "utility_basis": str(plan["utility_basis"]),
            # The label is not formal until the enclosing outcome artifact has
            # a post-commit receipt.  Snapshot materialization derives the
            # eventual label_available_at from that separate receipt.
            "label_available_at": None,
            "availability_basis": "requires_post_commit_artifact_receipt",
            "source_available_at": nav_refs[code]["completed_at"],
            "first_observed_at": nav_deliveries[code]["served_at"],
            "materialized_at": receipt,
            "source_ref": source_ref,
            "evidence": evidence,
            "automatic_promotion_allowed": False,
        }
        label["label_hash"] = canonical_hash(label)
        labels[code] = label

    semantic_labels = {
        code: _label_semantic_material(label) for code, label in labels.items()
    }
    semantic_material = {
        "audit_artifact_id": target["audit_artifact_id"],
        "audit_content_hash": target["audit_content_hash"],
        "audit_snapshot_hash": target["audit_snapshot_hash"],
        "audit_artifact_receipt_ref": target["audit_artifact_receipt_ref"],
        "plan_hash": plan["plan_hash"],
        "calendar_semantic_hash": calendar["calendar_semantic_hash"],
        "entry_date": entry_date,
        "exit_date": exit_date,
        "labels": semantic_labels,
    }
    provider_ref_values = sorted(
        [dict(calendar["provider_receipt_ref"]), *nav_refs.values()],
        key=lambda ref: (
            str(ref["provider"]),
            str(ref["operation"]),
            str(ref["receipt_id"]),
        ),
    )
    provider_adapter_stratum = candidate_provider_adapter_stratum(
        provider_ref_values
    )
    outcome_set: dict[str, Any] = {
        "schema_version": CANDIDATE_OUTCOME_SET_SCHEMA_VERSION,
        "case_id": case_id,
        "audit_artifact_id": target["audit_artifact_id"],
        "audit_content_hash": target["audit_content_hash"],
        "audit_snapshot_hash": target["audit_snapshot_hash"],
        "audit_storage_cutoff": target["audit_storage_cutoff"],
        "source_report_id": target["source_report_id"],
        "decision_at": target["decision_at"],
        "plan_hash": plan["plan_hash"],
        "horizon_trading_days": int(plan["horizon_trading_days"]),
        "k": int(plan["k"]),
        "universe_stage": str(plan["universe_stage"]),
        "utility_basis": str(plan["utility_basis"]),
        "calendar": deepcopy(dict(calendar)),
        "entry_date": entry_date,
        "exit_date": exit_date,
        "settled_at": receipt,
        "materialized_at": receipt,
        "source_available_at": max(
            [calendar["provider_receipt_ref"]["completed_at"]]
            + [ref["completed_at"] for ref in nav_refs.values()]
        ),
        "first_observed_at": max(
            [calendar["delivery"]["served_at"]]
            + [delivery["served_at"] for delivery in nav_deliveries.values()]
        ),
        "label_available_at": None,
        "label_availability_basis": (
            "requires_post_commit_artifact_receipt"
        ),
        "audit_artifact_receipt_ref": deepcopy(
            dict(target["audit_artifact_receipt_ref"])
        ),
        "provider_receipt_refs": {
            "calendar": deepcopy(dict(calendar["provider_receipt_ref"])),
            "nav_by_code": deepcopy(nav_refs),
        },
        "provider_adapter_stratum": provider_adapter_stratum,
        "provider_adapter_stratum_hash": (
            candidate_provider_adapter_stratum_hash(provider_ref_values)
        ),
        "provider_deliveries": {
            "calendar": deepcopy(dict(calendar["delivery"])),
            "nav_by_code": deepcopy(nav_deliveries),
        },
        "universe_count": len(labels),
        "label_count": len(labels),
        "outcome_labels": labels,
        "semantic_hash": canonical_hash(semantic_material),
        "automatic_promotion_allowed": False,
    }
    outcome_set["outcome_set_hash"] = canonical_hash(outcome_set)
    return outcome_set, None


def validate_candidate_outcome_set(
    value: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
) -> None:
    """Recompute labels and verify every frozen receipt reference."""

    expected_fields = {
        "schema_version",
        "case_id",
        "audit_artifact_id",
        "audit_content_hash",
        "audit_snapshot_hash",
        "audit_storage_cutoff",
        "source_report_id",
        "decision_at",
        "plan_hash",
        "horizon_trading_days",
        "k",
        "universe_stage",
        "utility_basis",
        "calendar",
        "entry_date",
        "exit_date",
        "settled_at",
        "materialized_at",
        "source_available_at",
        "first_observed_at",
        "label_available_at",
        "label_availability_basis",
        "audit_artifact_receipt_ref",
        "provider_receipt_refs",
        "provider_adapter_stratum",
        "provider_adapter_stratum_hash",
        "provider_deliveries",
        "universe_count",
        "label_count",
        "outcome_labels",
        "semantic_hash",
        "automatic_promotion_allowed",
        "outcome_set_hash",
    }
    if (
        set(value) != expected_fields
        or value.get("schema_version") != CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
    ):
        raise CandidateSelectionSettlementError("candidate outcome schema is invalid")
    if value.get("outcome_set_hash") != canonical_hash(
        {key: item for key, item in value.items() if key != "outcome_set_hash"}
    ):
        raise CandidateSelectionSettlementError("candidate outcome set hash mismatch")
    for key in (
        "audit_artifact_id",
        "audit_content_hash",
        "audit_snapshot_hash",
        "audit_storage_cutoff",
        "source_report_id",
        "decision_at",
        "audit_artifact_receipt_ref",
    ):
        if value.get(key) != target.get(key):
            raise CandidateSelectionSettlementError(
                f"candidate outcome set {key} conflicts with audit"
            )
    plan = target.get("label_plan")
    if not isinstance(plan, Mapping) or value.get("plan_hash") != plan.get(
        "plan_hash"
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome set conflicts with label plan"
        )
    if (
        value.get("case_id") != candidate_case_id(target)
        or value.get("horizon_trading_days") != plan.get("horizon_trading_days")
        or value.get("k") != plan.get("k")
        or value.get("universe_stage") != plan.get("universe_stage")
        or value.get("utility_basis") != plan.get("utility_basis")
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome identity conflicts with label plan"
        )
    settled_at = _aware_datetime(value.get("settled_at"))
    audit_cutoff = _aware_datetime(target.get("audit_storage_cutoff"))
    if (
        settled_at is None
        or audit_cutoff is None
        or settled_at <= audit_cutoff
        or value.get("materialized_at") != value.get("settled_at")
        or value.get("label_available_at") is not None
        or value.get("label_availability_basis")
        != "requires_post_commit_artifact_receipt"
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome materialization contract is invalid"
        )
    calendar = value.get("calendar")
    if not isinstance(calendar, Mapping):
        raise CandidateSelectionSettlementError("candidate calendar is invalid")
    _validate_calendar_snapshot(calendar, settled_at=value.get("settled_at"))
    resolved_dates = _resolve_plan_dates(
        plan,
        calendar,
        entry_not_before_date=str(target.get("entry_not_before_date") or ""),
    )
    if resolved_dates != {
        "entry_date": value.get("entry_date"),
        "exit_date": value.get("exit_date"),
    }:
        raise CandidateSelectionSettlementError(
            "candidate common dates conflict with frozen calendar"
        )
    if not _exit_is_observable_at_receipt(
        str(value.get("exit_date") or ""), value.get("settled_at")
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome predates its common exit date"
        )
    expected_dates = _expected_trade_dates(
        {
            **target,
            "calendar": calendar,
            "entry_date": value.get("entry_date"),
            "exit_date": value.get("exit_date"),
        }
    )
    if expected_dates is None:
        raise CandidateSelectionSettlementError(
            "candidate outcome calendar path is invalid"
        )

    receipt_refs = value.get("provider_receipt_refs")
    deliveries = value.get("provider_deliveries")
    nav_refs = (
        receipt_refs.get("nav_by_code")
        if isinstance(receipt_refs, Mapping)
        else None
    )
    nav_deliveries = (
        deliveries.get("nav_by_code")
        if isinstance(deliveries, Mapping)
        else None
    )
    universe_codes = [str(code) for code in target.get("universe_codes") or []]
    if (
        not isinstance(receipt_refs, Mapping)
        or not isinstance(deliveries, Mapping)
        or receipt_refs.get("calendar") != calendar.get("provider_receipt_ref")
        or deliveries.get("calendar") != calendar.get("delivery")
        or not isinstance(nav_refs, Mapping)
        or not isinstance(nav_deliveries, Mapping)
        or set(nav_refs) != set(universe_codes)
        or set(nav_deliveries) != set(universe_codes)
    ):
        raise CandidateSelectionSettlementError(
            "candidate provider receipt coverage is invalid"
        )
    for code in universe_codes:
        _validate_provider_ref_and_delivery(
            nav_refs[code],
            nav_deliveries[code],
            provider=_NAV_SOURCE,
            operation=_NAV_OPERATION,
        )
    provider_ref_values = sorted(
        [dict(receipt_refs["calendar"])]
        + [dict(nav_refs[code]) for code in sorted(nav_refs)],
        key=lambda ref: (
            str(ref["provider"]),
            str(ref["operation"]),
            str(ref["receipt_id"]),
        ),
    )
    try:
        expected_provider_stratum = candidate_provider_adapter_stratum(
            provider_ref_values
        )
        expected_provider_stratum_hash = (
            candidate_provider_adapter_stratum_hash(provider_ref_values)
        )
    except CandidateProviderAdapterPolicyError as exc:
        raise CandidateSelectionSettlementError(
            "candidate provider adapter stratum is invalid"
        ) from exc
    if (
        value.get("provider_adapter_stratum") != expected_provider_stratum
        or value.get("provider_adapter_stratum_hash")
        != expected_provider_stratum_hash
    ):
        raise CandidateSelectionSettlementError(
            "candidate provider adapter stratum conflicts with receipt refs"
        )

    labels = value.get("outcome_labels")
    if (
        not isinstance(labels, Mapping)
        or set(labels) != set(universe_codes)
        or value.get("universe_count") != len(universe_codes)
        or value.get("label_count") != len(universe_codes)
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome labels do not cover the frozen universe"
        )
    observed_returns: dict[str, float] = {}
    for code in sorted(universe_codes):
        label = labels.get(code)
        if not isinstance(label, Mapping):
            raise CandidateSelectionSettlementError("candidate label is invalid")
        if set(label) != {
            "schema_version",
            "fund_code",
            "horizon_trading_days",
            "entry_date",
            "exit_date",
            "mature",
            "eligible",
            "skipped",
            "binary_relevance",
            "relevance",
            "return_percent",
            "utility_basis",
            "label_available_at",
            "availability_basis",
            "source_available_at",
            "first_observed_at",
            "materialized_at",
            "source_ref",
            "evidence",
            "automatic_promotion_allowed",
            "label_hash",
        }:
            raise CandidateSelectionSettlementError(
                "candidate label fields are invalid"
            )
        if label.get("label_hash") != canonical_hash(
            {key: item for key, item in label.items() if key != "label_hash"}
        ):
            raise CandidateSelectionSettlementError("candidate label hash mismatch")
        if (
            label.get("schema_version") != CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION
            or label.get("fund_code") != code
            or label.get("horizon_trading_days") != plan.get("horizon_trading_days")
            or label.get("entry_date") != value.get("entry_date")
            or label.get("exit_date") != value.get("exit_date")
            or label.get("mature") is not True
            or label.get("eligible") is not True
            or label.get("skipped") is not False
            or label.get("utility_basis") != plan.get("utility_basis")
            or label.get("availability_basis")
            != "requires_post_commit_artifact_receipt"
            or label.get("label_available_at") is not None
            or label.get("materialized_at") != value.get("settled_at")
            or label.get("source_available_at") != nav_refs[code]["completed_at"]
            or label.get("first_observed_at")
            != nav_deliveries[code]["served_at"]
            or label.get("automatic_promotion_allowed") is not False
        ):
            raise CandidateSelectionSettlementError(
                "candidate label semantic contract is invalid"
            )
        evidence = label.get("evidence")
        if not isinstance(evidence, Mapping):
            raise CandidateSelectionSettlementError(
                "candidate label return evidence is missing"
            )
        recomputed, reason = _return_evidence(
            code,
            {"data": evidence.get("observations")},
            entry_date=str(value.get("entry_date") or ""),
            exit_date=str(value.get("exit_date") or ""),
            expected_dates=expected_dates,
            horizon=int(plan["horizon_trading_days"]),
            minimum_daily_growth_coverage=float(
                plan["minimum_daily_growth_coverage"]
            ),
            minimum_observation_ratio=float(plan["minimum_observation_ratio"]),
            provider_normalized_payload_hash=str(
                nav_refs[code]["normalized_payload_hash"]
            ),
        )
        if recomputed is None or recomputed != evidence:
            raise CandidateSelectionSettlementError(
                f"candidate return evidence cannot be reproduced: {reason}"
            )
        source_ref = label.get("source_ref")
        if (
            not isinstance(source_ref, Mapping)
            or set(source_ref)
            != {
                "source",
                "ref_id",
                "content_hash",
                "provider_receipt_ref",
                "delivery",
                "normalized_payload_projection_hash",
            }
            or source_ref.get("source") != _NAV_SOURCE
            or source_ref.get("ref_id") != f"{value['case_id']}:{code}"
            or source_ref.get("content_hash") != evidence.get("evidence_hash")
            or source_ref.get("provider_receipt_ref") != nav_refs[code]
            or source_ref.get("delivery") != nav_deliveries[code]
            or source_ref.get("normalized_payload_projection_hash")
            != evidence.get("normalized_payload_projection_hash")
        ):
            raise CandidateSelectionSettlementError(
                "candidate label source reference is invalid"
            )
        observed_return = float(evidence["return_percent"])
        if not _same_number(label.get("return_percent"), observed_return):
            raise CandidateSelectionSettlementError(
                "candidate label return conflicts with evidence"
            )
        if label.get("binary_relevance") is not (observed_return > 0.0):
            raise CandidateSelectionSettlementError(
                "candidate binary relevance conflicts with return"
            )
        observed_returns[code] = observed_return
    expected_relevance = _cross_sectional_relevance(observed_returns)
    for code, expected in expected_relevance.items():
        if not _same_number(labels[code].get("relevance"), expected):
            raise CandidateSelectionSettlementError(
                "candidate relevance conflicts with the frozen cross-section"
            )
    all_refs = [receipt_refs["calendar"]] + [
        nav_refs[code] for code in sorted(nav_refs)
    ]
    all_deliveries = [deliveries["calendar"]] + [
        nav_deliveries[code] for code in sorted(nav_deliveries)
    ]
    if (
        value.get("source_available_at")
        != max(str(ref["completed_at"]) for ref in all_refs)
        or value.get("first_observed_at")
        != max(str(item["served_at"]) for item in all_deliveries)
        or _aware_datetime(value.get("first_observed_at")) is None
        or _aware_datetime(value.get("first_observed_at")) > settled_at
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome provider clocks are invalid"
        )
    semantic_material = {
        "audit_artifact_id": value["audit_artifact_id"],
        "audit_content_hash": value["audit_content_hash"],
        "audit_snapshot_hash": value["audit_snapshot_hash"],
        "audit_artifact_receipt_ref": value["audit_artifact_receipt_ref"],
        "plan_hash": value["plan_hash"],
        "calendar_semantic_hash": calendar["calendar_semantic_hash"],
        "entry_date": value["entry_date"],
        "exit_date": value["exit_date"],
        "labels": {
            code: _label_semantic_material(labels[code]) for code in sorted(labels)
        },
    }
    if value.get("semantic_hash") != canonical_hash(semantic_material):
        raise CandidateSelectionSettlementError(
            "candidate outcome semantic hash mismatch"
        )
    if value.get("automatic_promotion_allowed") is not False:
        raise CandidateSelectionSettlementError(
            "candidate outcome must remain shadow-only"
        )


def _load_candidate_artifacts(
    *,
    allowed_users: set[int] | None,
    connection_factory: Callable[[], Any],
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[int, str], list[dict[str, Any]]],
    dict[tuple[int, str], dict[str, Any]],
    set[int],
]:
    with connection_factory() as connection:
        from app.config import get_settings
        from app.services.decision_repository import _fetchall

        if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
            raise CandidateSelectionSettlementError(
                "候选结算主证据库不可用，生产 MySQL 配置下拒绝回落 SQLite"
            )
        user_rows = _fetchall(
            connection,
            "SELECT DISTINCT userId FROM decision_quality_input_artifacts "
            "ORDER BY userId",
        )
        users = [
            int(row.get("userId") or 0)
            for row in user_rows
            if int(row.get("userId") or 0) > 0
            and (
                allowed_users is None
                or int(row.get("userId") or 0) in allowed_users
            )
        ]
        audits: list[dict[str, Any]] = []
        existing: dict[tuple[int, str], list[dict[str, Any]]] = {}
        receipts: dict[tuple[int, str], dict[str, Any]] = {}
        failed_user_ids: set[int] = set()
        for user_id in users:
            try:
                user_audits = list_decision_quality_input_artifacts(
                    user_id=user_id,
                    artifact_type=CANDIDATE_AUDIT_ARTIFACT_TYPE,
                    audit_eligible_only=True,
                    limit=10_000,
                    connection=connection,
                )
                user_outcomes = list_decision_quality_input_artifacts(
                    user_id=user_id,
                    artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
                    audit_eligible_only=True,
                    limit=10_000,
                    connection=connection,
                )
                user_receipts = list_decision_quality_artifact_receipts(
                    user_id=user_id,
                    limit=10_000,
                    connection=connection,
                )
                parsed_receipts: list[tuple[str, dict[str, Any]]] = []
                for receipt_row in user_receipts:
                    receipt_payload = receipt_row.get("payload")
                    if not isinstance(receipt_payload, Mapping):
                        raise DecisionQualityIntegrityError(
                            "artifact commit receipt payload is malformed"
                        )
                    if int(receipt_payload.get("user_id") or 0) != user_id:
                        raise DecisionQualityIntegrityError(
                            "artifact commit receipt crosses tenant boundary"
                        )
                    receipt_artifact_id = str(
                        receipt_payload.get("artifact_id") or ""
                    )
                    if not receipt_artifact_id:
                        raise DecisionQualityIntegrityError(
                            "artifact commit receipt has no source identity"
                        )
                    parsed_receipts.append((receipt_artifact_id, receipt_row))
                parsed_outcomes: list[tuple[str, dict[str, Any]]] = []
                for row in user_outcomes:
                    artifact = row.get("payload", {}).get("artifact")
                    if not isinstance(artifact, Mapping):
                        raise CandidateSelectionSettlementError(
                            "candidate outcome set is malformed"
                        )
                    if artifact.get("schema_version") != (
                        CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
                    ):
                        # D3 terminal artifacts stay immutable and ignored.  A
                        # D4 audit has a new logical identity, so no upgrade or
                        # backfill is attempted here.
                        continue
                    audit_id = (
                        str(artifact.get("audit_artifact_id") or "")
                    )
                    if not audit_id:
                        raise CandidateSelectionSettlementError(
                            "candidate outcome set has no audit identity"
                        )
                    parsed_outcomes.append((audit_id, row))
            except (DecisionQualityIntegrityError, CandidateSelectionSettlementError):
                failed_user_ids.add(user_id)
                continue
            audits.extend({**row, "user_id": user_id} for row in user_audits)
            for artifact_id, receipt_row in parsed_receipts:
                key = (user_id, artifact_id)
                if key in receipts:
                    failed_user_ids.add(user_id)
                    break
                receipts[key] = receipt_row
            if user_id in failed_user_ids:
                audits[:] = [
                    row for row in audits if int(row.get("user_id") or 0) != user_id
                ]
                continue
            for audit_id, row in parsed_outcomes:
                existing.setdefault((user_id, audit_id), []).append(row)
        return audits, existing, receipts, failed_user_ids


def candidate_preregistered_target_from_artifact(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Parse a D4 preregistration without manufacturing commit visibility.

    Snapshot construction uses this view to keep a receipt-missing audit in the
    coverage denominator.  It intentionally has no entry anchor and therefore
    cannot be used to settle a label.
    """

    envelope = row.get("payload")
    if not isinstance(envelope, Mapping):
        raise CandidateSelectionSettlementError("candidate audit envelope is missing")
    try:
        normalized_envelope = normalize_decision_quality_input_artifact(envelope)
    except (TypeError, ValueError) as exc:
        raise CandidateSelectionSettlementError(
            "candidate audit envelope integrity is invalid"
        ) from exc
    if dict(envelope) != normalized_envelope:
        raise CandidateSelectionSettlementError(
            "candidate audit envelope is not canonically normalized"
        )
    artifact = envelope.get("artifact")
    if not isinstance(artifact, Mapping):
        raise CandidateSelectionSettlementError("candidate audit artifact is missing")
    if artifact.get("schema_version") != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION:
        return None
    audit = artifact.get("audit")
    plan = artifact.get("label_plan")
    if not isinstance(audit, Mapping) or not isinstance(plan, Mapping):
        raise CandidateSelectionSettlementError(
            "registered candidate audit or label plan is missing"
        )
    _validate_label_plan(plan, envelope=envelope)
    if plan.get("status") != "preregistered":
        return None
    validation = validate_candidate_selection_audit(audit)
    if (
        audit.get("schema_version") != V2_SCHEMA_VERSION
        or validation.get("status") != "valid"
        or validation.get("decision_eligible") is not True
    ):
        raise CandidateSelectionSettlementError(
            "formal candidate audit is not native-v2 decision eligible"
        )
    if artifact.get("audit_snapshot_hash") != audit.get("snapshot_hash"):
        raise CandidateSelectionSettlementError(
            "candidate audit snapshot hash conflicts with wrapper"
        )
    source_report_id = envelope.get("source_report_id")
    if (
        not isinstance(source_report_id, str)
        or not source_report_id.strip()
        or source_report_id != source_report_id.strip()
        or envelope.get("decision_event_id") is not None
        or envelope.get("artifact_type") != CANDIDATE_AUDIT_ARTIFACT_TYPE
        or envelope.get("artifact_schema_version")
        != CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION
        or envelope.get("logical_key")
        != f"candidate_audit:{source_report_id}"
        or envelope.get("source_type") != "discovery"
        or envelope.get("store_authority") != "primary"
        or envelope.get("audit_eligible") is not True
        or artifact.get("source_report_id") != envelope.get("source_report_id")
        or artifact.get("decision_at") != envelope.get("decision_at")
        or artifact.get("recorded_at") != envelope.get("recorded_at")
        or audit.get("decision_at") != envelope.get("decision_at")
        or artifact.get("capture_validation") != validation
        or artifact.get("registration_phase") != "phase1_preregistered"
        or artifact.get("provider_receipt_required") is not True
        or artifact.get("capture_mode") != CANDIDATE_CAPTURE_MODE
        or artifact.get("post_commit_receipt_required") is not True
        or artifact.get("formal_receipt_max_delay_seconds")
        != CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        or artifact.get("formal_source_capture_max_delay_seconds")
        != CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        or artifact.get("formal_source_capture_delay_basis")
        != "candidate_audit_source_row_created_at_minus_decision_at"
        or artifact.get("capture_status") != "eligible"
        or artifact.get("capture_reason") != "eligible"
    ):
        raise CandidateSelectionSettlementError(
            "candidate audit wrapper conflicts with its immutable envelope"
        )
    artifact_id = str(envelope.get("artifact_id") or "")
    content_hash = str(envelope.get("content_hash") or "")
    if not artifact_id or not content_hash:
        raise CandidateSelectionSettlementError(
            "candidate audit content address is missing"
        )
    decision_at = _aware_datetime(envelope.get("decision_at"))
    available_at = _aware_datetime(envelope.get("available_at"))
    recorded_at = _aware_datetime(envelope.get("recorded_at"))
    storage_created_at = _aware_datetime(row.get("created_at"))
    if None in {decision_at, available_at, recorded_at, storage_created_at}:
        raise CandidateSelectionSettlementError(
            "candidate audit storage clocks are invalid"
        )
    assert decision_at and available_at and recorded_at and storage_created_at
    if not decision_at <= available_at <= recorded_at <= storage_created_at:
        raise CandidateSelectionSettlementError(
            "candidate audit storage clocks are not monotonic"
        )
    source_capture_delay_seconds = (
        storage_created_at - decision_at
    ).total_seconds()
    if str(plan.get("decision_at")) != decision_at.isoformat():
        raise CandidateSelectionSettlementError(
            "candidate label plan decision clock conflicts with audit"
        )
    try:
        normalized = normalize_candidate_selection_audit(audit)
    except (TypeError, ValueError) as exc:
        raise CandidateSelectionSettlementError(
            "candidate audit normalization failed"
        ) from exc
    universe_stage = str(plan["universe_stage"])
    universe_codes = sorted(
        str(candidate.get("fund_code") or "")
        for candidate in normalized.get("rows") or []
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("stage_records"), Mapping)
        and isinstance(candidate["stage_records"].get(universe_stage), Mapping)
        and candidate["stage_records"][universe_stage].get("present") is True
    )
    if any(len(code) != 6 or not code.isdigit() for code in universe_codes):
        raise CandidateSelectionSettlementError(
            "candidate audit universe is invalid"
        )
    user_id = int(row.get("user_id") or row.get("userId") or 0)
    if user_id <= 0:
        raise CandidateSelectionSettlementError(
            "candidate audit tenant identity is missing"
        )
    return {
        "user_id": user_id,
        "audit_artifact_id": artifact_id,
        "audit_content_hash": content_hash,
        "audit_snapshot_hash": str(audit["snapshot_hash"]),
        "audit_phase1_storage_cutoff": max(
            decision_at,
            available_at,
            recorded_at,
            storage_created_at,
        ).isoformat(),
        "source_report_id": source_report_id,
        "decision_at": decision_at.isoformat(),
        "source_capture_delay_seconds": source_capture_delay_seconds,
        "audit": deepcopy(dict(audit)),
        "label_plan": deepcopy(dict(plan)),
        "universe_codes": universe_codes,
    }


def candidate_target_from_artifact(
    row: Mapping[str, Any],
    *,
    artifact_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Bind one D4 preregistration to a primary post-commit receipt."""

    base = candidate_preregistered_target_from_artifact(row)
    if base is None:
        return None
    if artifact_receipt is None:
        raise CandidateSelectionSettlementError(
            "candidate audit commit receipt is required"
        )
    source_capture_delay_seconds = float(
        base.get("source_capture_delay_seconds") or 0.0
    )
    if (
        source_capture_delay_seconds
        > CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
    ):
        raise CandidateAuditSourceCaptureLate(
            delay_seconds=source_capture_delay_seconds
        )
    receipt_ref = _validate_artifact_commit_receipt(
        artifact_receipt,
        source_row=row,
        user_id=int(base["user_id"]),
        expected_artifact_type=CANDIDATE_AUDIT_ARTIFACT_TYPE,
        max_delay_seconds=CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS,
    )
    try:
        entry_anchor = (
            candidate_label_entry_not_before_date_from_post_commit_receipt(
                decision_at=str(base["decision_at"]),
                audit_receipt_source_visible_at=str(
                    receipt_ref["source_visible_at"]
                ),
            )
        )
    except ValueError as exc:
        raise CandidateSelectionSettlementError(
            "candidate audit commit receipt clock is invalid"
        ) from exc
    cutoff_values = [
        _aware_datetime(base["audit_phase1_storage_cutoff"]),
        _aware_datetime(receipt_ref["source_visible_at"]),
        _aware_datetime(artifact_receipt.get("created_at")),
    ]
    if any(value is None for value in cutoff_values):
        raise CandidateSelectionSettlementError(
            "candidate audit commit receipt cutoff is invalid"
        )
    return {
        **base,
        "audit_storage_cutoff": max(
            value for value in cutoff_values if value is not None
        ).isoformat(),
        "audit_artifact_receipt_ref": receipt_ref,
        "entry_not_before_date": entry_anchor,
    }


def _validate_artifact_commit_receipt(
    receipt_row: Mapping[str, Any],
    *,
    source_row: Mapping[str, Any],
    user_id: int,
    expected_artifact_type: str,
    max_delay_seconds: int | None = None,
) -> dict[str, Any]:
    receipt_payload = receipt_row.get("payload")
    source_envelope = source_row.get("payload")
    if not isinstance(receipt_payload, Mapping) or not isinstance(
        source_envelope, Mapping
    ):
        raise CandidateSelectionSettlementError(
            "artifact commit receipt binding is malformed"
        )
    try:
        normalized = normalize_decision_quality_artifact_receipt(receipt_payload)
    except (TypeError, ValueError) as exc:
        raise CandidateSelectionSettlementError(
            "artifact commit receipt integrity is invalid"
        ) from exc
    if dict(receipt_payload) != normalized:
        raise CandidateSelectionSettlementError(
            "artifact commit receipt is not canonically normalized"
        )
    source_created_at = _aware_datetime(source_row.get("created_at"))
    visible_at = _aware_datetime(normalized.get("source_visible_at"))
    receipt_created_at = _aware_datetime(receipt_row.get("created_at"))
    if (
        type(receipt_row.get("userId")) is not int
        or int(receipt_row.get("userId") or 0) != user_id
        or int(normalized.get("user_id") or 0) != user_id
        or normalized.get("artifact_id") != source_envelope.get("artifact_id")
        or normalized.get("artifact_type") != expected_artifact_type
        or source_envelope.get("artifact_type") != expected_artifact_type
        or normalized.get("artifact_content_hash")
        != source_envelope.get("content_hash")
        or normalized.get("source_row_created_at")
        != (
            source_created_at.isoformat()
            if source_created_at is not None
            else None
        )
        or normalized.get("store_authority") != "primary"
        or source_envelope.get("store_authority") != "primary"
        or source_created_at is None
        or visible_at is None
        or receipt_created_at is None
        or receipt_created_at != visible_at
        or visible_at < source_created_at
    ):
        raise CandidateSelectionSettlementError(
            "artifact commit receipt conflicts with its immutable source"
        )
    delay_seconds = (visible_at - source_created_at).total_seconds()
    if max_delay_seconds is not None and delay_seconds > max_delay_seconds:
        raise CandidateAuditCommitReceiptLate(normalized)
    return deepcopy(normalized)


def _validate_label_plan(
    plan: Mapping[str, Any],
    *,
    envelope: Mapping[str, Any],
) -> None:
    try:
        expected_plan = build_candidate_label_plan(
            decision_at=str(envelope.get("decision_at") or ""),
            registered_at=str(envelope.get("recorded_at") or ""),
            decision_eligible=envelope.get("audit_eligible") is True,
        )
    except ValueError as exc:
        raise CandidateSelectionSettlementError(
            "candidate label plan clocks are invalid"
        ) from exc
    if dict(plan) != expected_plan:
        raise CandidateSelectionSettlementError(
            "candidate label plan conflicts with the preregistered policy"
        )
    if (
        plan.get("schema_version") != CANDIDATE_LABEL_PLAN_SCHEMA_VERSION
        or plan.get("policy_version") != CANDIDATE_LABEL_POLICY_VERSION
        or plan.get("status") not in {"preregistered", "ineligible"}
        or plan.get("horizon_trading_days") != 20
        or plan.get("k") != 3
        or plan.get("universe_stage") != "prescreen"
        or plan.get("utility_basis") != "total_return_percent_before_costs"
        or plan.get("market_timezone") != "Asia/Shanghai"
        or plan.get("decision_cutoff_local") != "15:00:00"
        or plan.get("same_day_entry_allowed") is not False
        or plan.get("provider_receipt_required") is not True
        or plan.get("capture_mode") != CANDIDATE_CAPTURE_MODE
        or plan.get("post_commit_receipt_required") is not True
        or plan.get("formal_receipt_max_delay_seconds")
        != CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS
        or plan.get("formal_source_capture_max_delay_seconds")
        != CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS
        or plan.get("formal_source_capture_delay_basis")
        != "candidate_audit_source_row_created_at_minus_decision_at"
        or plan.get("commit_visibility_policy")
        != "post_commit_receipt_required"
        or plan.get("entry_anchor_basis")
        != "later_of_decision_at_and_audit_post_commit_receipt_source_visible_at"
        or plan.get("entry_calendar_day_rule")
        != "next_asia_shanghai_calendar_day"
        or plan.get("date_resolution_rule")
        != "first_cn_market_trade_date_on_or_after_derived_entry_calendar_day_then_20_transitions"
        or plan.get("minimum_daily_growth_coverage") != 1.0
        or plan.get("minimum_observation_ratio") != 1.0
        or plan.get("return_series_policy")
        != "daily_growth_preferred_nav_ratio_fallback_v1"
        or plan.get("automatic_promotion_allowed") is not False
    ):
        raise CandidateSelectionSettlementError(
            "candidate label plan contract is invalid"
        )
    expected_hash = canonical_hash(
        {
            key: value
            for key, value in plan.items()
            if key not in {"plan_hash", "preregistered_at"}
        }
    )
    if plan.get("plan_hash") != expected_hash:
        raise CandidateSelectionSettlementError("candidate label plan hash mismatch")
    if plan.get("preregistered_at") != envelope.get("recorded_at"):
        raise CandidateSelectionSettlementError(
            "candidate label plan receipt conflicts with artifact"
        )
    if plan.get("decision_at") != envelope.get("decision_at"):
        raise CandidateSelectionSettlementError(
            "candidate label plan decision clock conflicts with artifact"
        )
    if any(
        key in plan
        for key in ("entry_not_before_date", "entry_date", "actual_entry_date")
    ):
        raise CandidateSelectionSettlementError(
            "candidate label plan cannot self-attest an entry anchor"
        )


def _require_provider_read(
    value: object,
    *,
    provider: str,
    operation: str,
) -> DecisionQualityProviderRead:
    if not isinstance(value, DecisionQualityProviderRead):
        raise CandidateSelectionSettlementError(
            "formal candidate evidence requires DecisionQualityProviderRead"
        )
    try:
        validate_provider_read(value)
        verify_candidate_provider_adapter_policy(value.origin_receipt)
    except (
        ProviderReceiptValidationError,
        CandidateProviderAdapterPolicyError,
    ) as exc:
        raise CandidateSelectionSettlementError(
            "candidate provider read integrity or adapter policy is invalid"
        ) from exc
    origin = value.origin_receipt
    if (
        not value.ok
        or origin.get("capture_mode") != "live"
        or origin.get("provider_id") != provider
        or origin.get("operation") != operation
        or origin.get("automatic_promotion_allowed") is not False
    ):
        raise CandidateSelectionSettlementError(
            "candidate provider read is not a successful live origin"
        )
    return value


def _require_nav_provider_read(
    value: object,
    *,
    fund_code: str,
) -> DecisionQualityProviderRead:
    read = _require_provider_read(
        value,
        provider=_NAV_SOURCE,
        operation=_NAV_OPERATION,
    )
    request = read.origin_receipt.get("request")
    parameters = request.get("parameters") if isinstance(request, Mapping) else None
    if (
        not isinstance(parameters, Mapping)
        or set(parameters) != {"fund_code", "trading_days", "indicator"}
        or parameters.get("fund_code") != fund_code
        or isinstance(parameters.get("trading_days"), bool)
        or not isinstance(parameters.get("trading_days"), int)
        or int(parameters["trading_days"]) < 1
        or parameters.get("indicator") != _NAV_INDICATOR
    ):
        raise CandidateSelectionSettlementError(
            "candidate NAV provider request conflicts with fund identity"
        )
    return read


def _provider_repository_receipt(
    read: DecisionQualityProviderRead,
) -> dict[str, Any]:
    validate_provider_read(read)
    origin = read.origin_receipt
    verify_candidate_provider_adapter_policy(origin)
    request = origin["request"]
    cache = origin["cache"]
    response = origin["response"]
    assert all(isinstance(item, Mapping) for item in (request, cache, response))
    return normalize_decision_quality_provider_receipt(
        {
            "provider": origin["provider_id"],
            "operation": origin["operation"],
            "capture_mode": origin["capture_mode"],
            "request_hash": request["request_hash"],
            # This is the exact adapter-boundary origin receipt, including the
            # bounded base64 stdout bytes.  It is intentionally not described
            # as the unavailable upstream HTTP response.
            "adapter_output": deepcopy(origin),
            "normalized_payload_hash": response["normalized_payload_hash"],
            "origin_fetched_at": cache["origin_fetched_at"],
            "completed_at": response["completed_at"],
        }
    )


def _provider_receipt_ref(read: DecisionQualityProviderRead) -> dict[str, Any]:
    stored = _provider_repository_receipt(read)
    policy = verify_candidate_provider_adapter_policy(read.origin_receipt)
    return {
        "receipt_id": stored["receipt_id"],
        "content_hash": stored["content_hash"],
        "provider": stored["provider"],
        "operation": stored["operation"],
        "capture_mode": stored["capture_mode"],
        "request_hash": stored["request_hash"],
        "adapter_output_sha256": stored["adapter_output_sha256"],
        "normalized_payload_hash": stored["normalized_payload_hash"],
        "origin_fetched_at": stored["origin_fetched_at"],
        "completed_at": stored["completed_at"],
        "origin_receipt_hash": read.origin_receipt["origin_receipt_hash"],
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


def _provider_origin_completed_at(read: DecisionQualityProviderRead) -> str:
    response = read.origin_receipt.get("response")
    if not isinstance(response, Mapping):
        raise CandidateSelectionSettlementError(
            "candidate provider origin completion is missing"
        )
    return _aware_utc_text(response.get("completed_at"), "provider_completed_at")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _validate_provider_ref_and_delivery(
    ref: object,
    delivery: object,
    *,
    provider: str,
    operation: str,
) -> None:
    expected_ref_fields = {
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
    if (
        not isinstance(ref, Mapping)
        or set(ref) != expected_ref_fields
        or ref.get("provider") != provider
        or ref.get("operation") != operation
        or ref.get("capture_mode") != "live"
        or not isinstance(ref.get("receipt_id"), str)
        or not str(ref["receipt_id"]).startswith("dqpr_")
        or any(
            not _is_sha256(ref.get(field))
            for field in (
                "content_hash",
                "request_hash",
                "adapter_output_sha256",
                "normalized_payload_hash",
                "origin_receipt_hash",
                "adapter_policy_hash",
                "adapter_script_sha256",
                "adapter_policy_script_sha256",
            )
        )
        or ref.get("receipt_id") != f"dqpr_{ref.get('content_hash')}"
        or _aware_datetime(ref.get("origin_fetched_at")) is None
        or _aware_datetime(ref.get("completed_at")) is None
        or ref.get("origin_fetched_at") != ref.get("completed_at")
        or not candidate_adapter_policy_is_registered(
            provider=provider,
            operation=operation,
            contract_version=ref.get("adapter_contract_version"),
            policy_id=ref.get("adapter_policy_id"),
        )
        or ref.get("adapter_library_name") != "akshare"
        or any(
            not isinstance(ref.get(field), str) or not str(ref[field]).strip()
            for field in (
                "adapter_contract_version",
                "adapter_library_version",
                "adapter_python_version",
            )
        )
    ):
        raise CandidateSelectionSettlementError(
            "candidate provider receipt reference is invalid"
        )
    if not isinstance(delivery, Mapping):
        raise CandidateSelectionSettlementError(
            "candidate provider delivery is invalid"
        )
    expected_delivery_hash = canonical_provider_hash(
        {
            key: item
            for key, item in delivery.items()
            if key != "delivery_hash"
        }
    )
    if (
        set(delivery)
        != {
            "schema_version",
            "cache_status",
            "cache_layer",
            "served_at",
            "origin_receipt_hash",
            "cache_key_hash",
            "delivery_hash",
        }
        or delivery.get("schema_version")
        != "decision_quality_provider_delivery.v1"
        or delivery.get("cache_status") not in {"hit", "miss"}
        or not isinstance(delivery.get("cache_layer"), str)
        or not str(delivery["cache_layer"]).strip()
        or delivery.get("origin_receipt_hash") != ref.get("origin_receipt_hash")
        or not _is_sha256(delivery.get("cache_key_hash"))
        or delivery.get("delivery_hash") != expected_delivery_hash
        or _aware_datetime(delivery.get("served_at")) is None
        or _aware_datetime(delivery.get("served_at"))
        < _aware_datetime(ref.get("completed_at"))
    ):
        raise CandidateSelectionSettlementError(
            "candidate provider delivery reference is invalid"
        )


def _require_snapshot_matches_provider_read(
    calendar: Mapping[str, Any],
    read: DecisionQualityProviderRead,
) -> None:
    if (
        calendar.get("provider_receipt_ref") != _provider_receipt_ref(read)
        or calendar.get("delivery") != read.delivery
    ):
        raise CandidateSelectionSettlementError(
            "candidate calendar conflicts with its typed provider read"
        )


def _calendar_snapshot(
    provider_read: object,
    *,
    as_of_date: str,
    source: str | None = None,
    observed_at: str | datetime | None = None,
) -> dict[str, Any] | None:
    """Project a typed calendar read without persisting its origin yet."""

    read = _require_provider_read(
        provider_read,
        provider=_CALENDAR_SOURCE,
        operation=_CALENDAR_OPERATION,
    )
    request = read.origin_receipt.get("request")
    parameters = request.get("parameters") if isinstance(request, Mapping) else None
    if not isinstance(parameters, Mapping) or dict(parameters) != {}:
        raise CandidateSelectionSettlementError(
            "candidate calendar provider request must be parameter-free"
        )
    if source is not None and source != _CALENDAR_SOURCE:
        raise CandidateSelectionSettlementError(
            "candidate calendar source cannot be caller-signed"
        )
    payload = read.normalized_payload
    dates = payload.get("dates") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"dates"}
        or not isinstance(dates, list)
        or not dates
        or any(
            not isinstance(item, str) or _iso_date(item) != item
            for item in dates
        )
        or dates != sorted(set(dates))
    ):
        raise CandidateSelectionSettlementError(
            "candidate calendar normalized payload is invalid"
        )
    delivery_at = str(read.delivery["served_at"])
    if observed_at is not None and _aware_utc_text(
        observed_at, "calendar_observed_at"
    ) != delivery_at:
        raise CandidateSelectionSettlementError(
            "candidate calendar observation must equal provider delivery"
        )
    normalized = sorted(
        value
        for value in set(dates)
        if _iso_date(value) is not None and value <= as_of_date
    )
    normalized = normalized[-_MAX_NAV_POINTS:]
    if not normalized:
        return None
    snapshot: dict[str, Any] = {
        "schema_version": CANDIDATE_CALENDAR_SCHEMA_VERSION,
        "source": _CALENDAR_SOURCE,
        "as_of_date": as_of_date,
        "observed_at": delivery_at,
        "dates": normalized,
        "normalized_payload_projection_hash": canonical_provider_hash(
            {"dates": normalized}
        ),
        "provider_receipt_ref": _provider_receipt_ref(read),
        "delivery": deepcopy(read.delivery),
    }
    snapshot["calendar_semantic_hash"] = canonical_hash(
        {
            key: value
            for key, value in snapshot.items()
            if key not in {"observed_at", "delivery"}
        }
    )
    snapshot["calendar_hash"] = canonical_hash(snapshot)
    return snapshot


def _validate_calendar_snapshot(
    calendar: Mapping[str, Any],
    *,
    settled_at: object,
) -> None:
    dates = calendar.get("dates")
    observed_at = _aware_datetime(calendar.get("observed_at"))
    receipt = _aware_datetime(settled_at)
    raw_as_of_date = calendar.get("as_of_date")
    as_of_date = (
        _iso_date(raw_as_of_date) if isinstance(raw_as_of_date, str) else None
    )
    canonical_dates = (
        isinstance(dates, list)
        and bool(dates)
        and all(
            isinstance(value, str) and value == _iso_date(value)
            for value in dates
        )
    )
    if (
        set(calendar)
        != {
            "schema_version",
            "source",
            "as_of_date",
            "observed_at",
            "dates",
            "normalized_payload_projection_hash",
            "provider_receipt_ref",
            "delivery",
            "calendar_semantic_hash",
            "calendar_hash",
        }
        or calendar.get("schema_version") != CANDIDATE_CALENDAR_SCHEMA_VERSION
        or calendar.get("source") != _CALENDAR_SOURCE
        or as_of_date is None
        or raw_as_of_date != as_of_date
        or not isinstance(dates, list)
        or not dates
        or not canonical_dates
        or dates != sorted(set(dates))
        or any(str(value) > as_of_date for value in dates)
        or observed_at is None
        or receipt is None
        or observed_at > receipt
        or as_of_date > observed_at.astimezone(_CN_TZ).date().isoformat()
    ):
        raise CandidateSelectionSettlementError(
            "candidate calendar contract, dates, or receipt are invalid"
        )
    provider_ref = calendar.get("provider_receipt_ref")
    delivery = calendar.get("delivery")
    _validate_provider_ref_and_delivery(
        provider_ref,
        delivery,
        provider=_CALENDAR_SOURCE,
        operation=_CALENDAR_OPERATION,
    )
    if (
        not isinstance(delivery, Mapping)
        or calendar.get("observed_at") != delivery.get("served_at")
        or calendar.get("normalized_payload_projection_hash")
        != canonical_provider_hash({"dates": dates})
    ):
        raise CandidateSelectionSettlementError(
            "candidate calendar provider projection is invalid"
        )
    if calendar.get("calendar_hash") != canonical_hash(
        {key: item for key, item in calendar.items() if key != "calendar_hash"}
    ):
        raise CandidateSelectionSettlementError("candidate calendar hash mismatch")
    expected_semantic_hash = canonical_hash(
        {
            key: item
            for key, item in calendar.items()
            if key
            not in {
                "calendar_hash",
                "calendar_semantic_hash",
                "observed_at",
                "delivery",
            }
        }
    )
    if calendar.get("calendar_semantic_hash") != expected_semantic_hash:
        raise CandidateSelectionSettlementError(
            "candidate calendar semantic hash mismatch"
        )


def _resolve_plan_dates(
    plan: Mapping[str, Any],
    calendar: Mapping[str, Any],
    *,
    entry_not_before_date: str | None = None,
) -> dict[str, str] | None:
    dates = [str(value) for value in calendar.get("dates") or []]
    anchor = str(
        entry_not_before_date
        if entry_not_before_date is not None
        else plan.get("entry_not_before_date") or ""
    )
    if not dates or anchor < dates[0]:
        return None
    try:
        entry_index = next(index for index, value in enumerate(dates) if value >= anchor)
    except StopIteration:
        return None
    exit_index = entry_index + int(plan["horizon_trading_days"])
    if exit_index >= len(dates):
        return None
    exit_date = dates[exit_index]
    if exit_date > str(calendar.get("as_of_date") or ""):
        return None
    return {"entry_date": dates[entry_index], "exit_date": exit_date}


def _expected_trade_dates(target: Mapping[str, Any]) -> list[str] | None:
    calendar = target.get("calendar")
    plan = target.get("label_plan")
    if not isinstance(calendar, Mapping) or not isinstance(plan, Mapping):
        return None
    dates = [str(value) for value in calendar.get("dates") or []]
    entry_date = str(target.get("entry_date") or "")
    exit_date = str(target.get("exit_date") or "")
    try:
        entry_index = dates.index(entry_date)
        exit_index = dates.index(exit_date)
    except ValueError:
        return None
    expected = dates[entry_index : exit_index + 1]
    if (
        exit_index - entry_index != int(plan.get("horizon_trading_days") or 0)
        or len(expected) != int(plan.get("horizon_trading_days") or 0) + 1
    ):
        return None
    return expected


def _exit_is_observable_at_receipt(
    exit_date: str,
    receipt: object,
) -> bool:
    parsed_exit = _iso_date(exit_date)
    parsed_receipt = _aware_datetime(receipt)
    if parsed_exit is None or parsed_receipt is None:
        return False
    local_receipt = parsed_receipt.astimezone(_CN_TZ)
    exit_value = date.fromisoformat(parsed_exit)
    if local_receipt.date() > exit_value:
        return True
    return (
        local_receipt.date() == exit_value
        and local_receipt.time().replace(tzinfo=None) >= time(15, 0)
    )


def _fetch_nav_payloads(
    fetch_specs: Mapping[str, int],
    *,
    fetch_nav: Callable[..., object],
) -> dict[str, object]:
    if not fetch_specs:
        return {}
    results: dict[str, object] = {}
    worker_count = min(_MAX_FETCH_WORKERS, len(fetch_specs))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(fetch_nav, code, trading_days=days): code
            for code, days in fetch_specs.items()
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception:  # noqa: BLE001 - provider gaps stay retryable
                results[code] = None
    return results


def _fair_due_window(
    targets: Sequence[dict[str, Any]],
    *,
    limit: int,
    as_of_date: str,
) -> list[dict[str, Any]]:
    """Rotate the bounded attempt window so retryable gaps cannot starve peers."""

    rows = list(targets)
    if len(rows) <= limit:
        return rows
    ordinal = date.fromisoformat(as_of_date).toordinal()
    offset = (ordinal * limit) % len(rows)
    rotated = rows[offset:] + rows[:offset]
    return rotated[:limit]


def _return_evidence(
    code: str,
    payload: object,
    *,
    entry_date: str,
    exit_date: str,
    expected_dates: Sequence[str],
    horizon: int,
    minimum_daily_growth_coverage: float,
    minimum_observation_ratio: float,
    provider_normalized_payload_hash: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not _is_sha256(provider_normalized_payload_hash):
        raise CandidateSelectionSettlementError(
            "candidate NAV normalized payload hash is invalid"
        )
    raw_rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        return None, "candidate_nav_history_unavailable"
    by_date: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        day = _iso_date(str(raw.get("date") or "")[:10])
        if day is None or day < entry_date or day > exit_date:
            continue
        nav = _finite_float(raw.get("nav"))
        growth = _finite_float(
            raw.get("daily_growth", raw.get("daily_return_percent"))
        )
        if day in by_date:
            return None, "candidate_nav_duplicate_trade_date"
        by_date[day] = {"date": day, "nav": nav, "daily_growth": growth}
    observations = [by_date[day] for day in sorted(by_date)]
    observed_dates = [row["date"] for row in observations]
    if observed_dates != list(expected_dates):
        return None, "candidate_common_date_nav_missing"
    minimum_points = max(2, math.ceil((horizon + 1) * minimum_observation_ratio))
    if len(observations) < minimum_points:
        return None, "candidate_nav_path_coverage_insufficient"
    series = build_total_return_index(observations)
    if (
        len(series.points) < minimum_points
        or series.points[0][0] != entry_date
        or series.points[-1][0] != exit_date
        or series.return_coverage < minimum_daily_growth_coverage
        or series.invalid_points > 0
    ):
        return None, "candidate_total_return_evidence_insufficient"
    base = float(series.points[0][1])
    target = float(series.points[-1][1])
    if base <= 0 or not math.isfinite(target):
        return None, "candidate_total_return_evidence_invalid"
    return_percent = round((target / base - 1.0) * 100.0, 8)
    if not math.isfinite(return_percent) or not -99.9 < return_percent < 1000.0:
        return None, "candidate_total_return_out_of_range"
    evidence: dict[str, Any] = {
        "schema_version": CANDIDATE_RETURN_EVIDENCE_SCHEMA_VERSION,
        "fund_code": code,
        "source": _NAV_SOURCE,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "observations": observations,
        "provider_normalized_payload_hash": provider_normalized_payload_hash,
        "normalized_payload_projection_hash": canonical_provider_hash(
            {"data": observations}
        ),
        "expected_trade_date_count": len(expected_dates),
        "expected_trade_dates_hash": canonical_hash(list(expected_dates)),
        "observation_count": len(observations),
        "daily_return_points": series.daily_return_points,
        "nav_ratio_points": series.nav_ratio_points,
        "invalid_points": series.invalid_points,
        "daily_growth_coverage": round(series.return_coverage, 8),
        "return_percent": return_percent,
        "return_basis": "daily_growth_preferred_total_return_index",
    }
    evidence["evidence_hash"] = canonical_hash(evidence)
    return evidence, None


def _cross_sectional_relevance(returns: Mapping[str, float]) -> dict[str, float]:
    values = list(returns.values())
    denominator = max(1, len(values) - 1)
    return {
        code: round(
            3.0 * sum(other < value for other in values) / denominator,
            8,
        )
        for code, value in returns.items()
    }


def candidate_case_id(target: Mapping[str, Any]) -> str:
    plan = target["label_plan"]
    digest = canonical_hash(
        {
            "audit_artifact_id": target["audit_artifact_id"],
            "plan_hash": plan["plan_hash"],
            "horizon_trading_days": plan["horizon_trading_days"],
            "k": plan["k"],
            "universe_stage": plan["universe_stage"],
            "policy_version": plan["policy_version"],
        }
    )
    return f"candidate_case_{digest}"


def _persist_provider_reads(
    values: Iterable[object],
    *,
    connection_factory: Callable[[], Any],
) -> list[dict[str, Any]]:
    """Commit each unique origin in its own transaction before the outcome."""

    receipts: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, DecisionQualityProviderRead):
            raise CandidateSelectionSettlementError(
                "complete candidate case contains an untyped provider read"
            )
        try:
            normalized = _provider_repository_receipt(value)
        except (ProviderReceiptValidationError, TypeError, ValueError) as exc:
            raise CandidateSelectionSettlementError(
                "candidate provider receipt cannot be normalized"
            ) from exc
        receipts[str(normalized["receipt_id"])] = normalized
    saved_rows: list[dict[str, Any]] = []
    for receipt_id in sorted(receipts):
        # A fresh owned connection is intentional: the following outcome
        # transaction must prove it can observe this committed origin.
        with connection_factory() as connection:
            saved = put_decision_quality_provider_receipt(
                receipt=receipts[receipt_id],
                connection=connection,
            )
        if saved.get("payload") != receipts[receipt_id]:
            raise DecisionQualityIntegrityError(
                "persisted provider receipt conflicts with captured origin"
            )
        saved_rows.append(saved)
    return saved_rows


def _outcome_provider_ref_pairs(
    outcome_set: Mapping[str, Any],
) -> list[tuple[str, str | None, Mapping[str, Any], Mapping[str, Any]]]:
    refs = outcome_set.get("provider_receipt_refs")
    deliveries = outcome_set.get("provider_deliveries")
    if not isinstance(refs, Mapping) or not isinstance(deliveries, Mapping):
        raise CandidateSelectionSettlementError(
            "candidate outcome provider receipt manifest is missing"
        )
    calendar_ref = refs.get("calendar")
    calendar_delivery = deliveries.get("calendar")
    nav_refs = refs.get("nav_by_code")
    nav_deliveries = deliveries.get("nav_by_code")
    if (
        not isinstance(calendar_ref, Mapping)
        or not isinstance(calendar_delivery, Mapping)
        or not isinstance(nav_refs, Mapping)
        or not isinstance(nav_deliveries, Mapping)
        or set(nav_refs) != set(nav_deliveries)
    ):
        raise CandidateSelectionSettlementError(
            "candidate outcome provider receipt manifest is malformed"
        )
    result = [("calendar", None, calendar_ref, calendar_delivery)]
    for code in sorted(nav_refs):
        ref = nav_refs[code]
        delivery = nav_deliveries[code]
        if not isinstance(ref, Mapping) or not isinstance(delivery, Mapping):
            raise CandidateSelectionSettlementError(
                "candidate outcome NAV provider reference is malformed"
            )
        result.append(("nav", str(code), ref, delivery))
    return result


def _verify_outcome_provider_receipts_in_store(
    outcome_set: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    connection: Any,
) -> None:
    """Re-read exact provider origins inside the outcome write transaction."""

    for kind, fund_code, ref, delivery in _outcome_provider_ref_pairs(outcome_set):
        row = get_decision_quality_provider_receipt(
            receipt_id=str(ref.get("receipt_id") or ""),
            connection=connection,
        )
        if row is None or not isinstance(row.get("payload"), Mapping):
            raise DecisionQualityIntegrityError(
                "referenced provider receipt is not commit-visible"
            )
        payload = row["payload"]
        origin = payload.get("adapter_output")
        if not isinstance(origin, Mapping):
            raise DecisionQualityIntegrityError(
                "provider receipt has no exact adapter origin"
            )
        try:
            validate_provider_origin_receipt(origin)
            validate_provider_delivery(delivery, origin_receipt=origin)
            policy = verify_candidate_provider_adapter_policy(origin)
        except (
            ProviderReceiptValidationError,
            CandidateProviderAdapterPolicyError,
        ) as exc:
            raise DecisionQualityIntegrityError(
                "provider origin, delivery, or adapter policy failed validation"
            ) from exc
        expected = {
            "receipt_id": payload.get("receipt_id"),
            "content_hash": payload.get("content_hash"),
            "provider": payload.get("provider"),
            "operation": payload.get("operation"),
            "capture_mode": payload.get("capture_mode"),
            "request_hash": payload.get("request_hash"),
            "adapter_output_sha256": payload.get("adapter_output_sha256"),
            "normalized_payload_hash": payload.get("normalized_payload_hash"),
            "origin_fetched_at": payload.get("origin_fetched_at"),
            "completed_at": payload.get("completed_at"),
            "origin_receipt_hash": origin.get("origin_receipt_hash"),
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
        if dict(ref) != expected:
            raise DecisionQualityIntegrityError(
                "provider receipt reference conflicts with primary storage"
            )
        request = origin.get("request")
        parameters = request.get("parameters") if isinstance(request, Mapping) else None
        if kind == "calendar":
            request_valid = isinstance(parameters, Mapping) and dict(parameters) == {}
        else:
            request_valid = bool(
                isinstance(parameters, Mapping)
                and parameters.get("fund_code") == fund_code
                and not isinstance(parameters.get("trading_days"), bool)
                and isinstance(parameters.get("trading_days"), int)
                and int(parameters["trading_days"]) >= 1
                and parameters.get("indicator") == _NAV_INDICATOR
            )
        if not request_valid:
            raise DecisionQualityIntegrityError(
                "provider receipt request conflicts with outcome evidence identity"
            )
        try:
            validate_candidate_outcome_provider_projection(
                outcome_set,
                target=target,
                kind=kind,
                fund_code=fund_code,
                origin_receipt=origin,
                delivery=delivery,
            )
        except CandidateSelectionSettlementError as exc:
            raise DecisionQualityIntegrityError(
                "provider origin projection conflicts with outcome evidence"
            ) from exc


def validate_candidate_outcome_provider_projection(
    outcome_set: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    kind: str,
    fund_code: str | None,
    origin_receipt: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> None:
    """Recompute a formal outcome projection from the frozen adapter stdout.

    Content hashes prove that a stored origin is immutable, but cannot prove
    that an independently self-consistent outcome used that origin.  This
    boundary therefore rebuilds the normalized payload from the exact stdout
    bytes and projects it again through the production calendar/NAV logic.
    """

    try:
        normalized = rebuild_candidate_provider_normalized_payload(
            origin_receipt
        )
    except CandidateProviderAdapterPolicyError as exc:
        raise CandidateSelectionSettlementError(
            "candidate provider normalized payload cannot be rebuilt"
        ) from exc
    read = DecisionQualityProviderRead(
        origin_receipt=deepcopy(dict(origin_receipt)),
        normalized_payload=normalized,
        delivery=deepcopy(dict(delivery)),
    )
    if kind == "calendar":
        calendar = outcome_set.get("calendar")
        if not isinstance(calendar, Mapping):
            raise CandidateSelectionSettlementError(
                "candidate outcome calendar projection is missing"
            )
        rebuilt = _calendar_snapshot(
            read,
            as_of_date=str(calendar.get("as_of_date") or ""),
            observed_at=delivery.get("served_at"),
        )
        if rebuilt is None or rebuilt != dict(calendar):
            raise CandidateSelectionSettlementError(
                "candidate calendar is detached from stored adapter stdout"
            )
        return
    if kind != "nav" or not isinstance(fund_code, str) or not fund_code:
        raise CandidateSelectionSettlementError(
            "candidate provider projection identity is invalid"
        )
    _require_nav_provider_read(read, fund_code=fund_code)
    plan = target.get("label_plan")
    calendar = outcome_set.get("calendar")
    labels = outcome_set.get("outcome_labels")
    label = labels.get(fund_code) if isinstance(labels, Mapping) else None
    if (
        not isinstance(plan, Mapping)
        or not isinstance(calendar, Mapping)
        or not isinstance(label, Mapping)
    ):
        raise CandidateSelectionSettlementError(
            "candidate NAV outcome projection context is missing"
        )
    expected_dates = _expected_trade_dates(
        {
            **dict(target),
            "calendar": calendar,
            "entry_date": outcome_set.get("entry_date"),
            "exit_date": outcome_set.get("exit_date"),
        }
    )
    if expected_dates is None:
        raise CandidateSelectionSettlementError(
            "candidate NAV projection calendar path is invalid"
        )
    response = origin_receipt.get("response")
    normalized_hash = (
        response.get("normalized_payload_hash")
        if isinstance(response, Mapping)
        else None
    )
    rebuilt_evidence, reason = _return_evidence(
        fund_code,
        normalized,
        entry_date=str(outcome_set.get("entry_date") or ""),
        exit_date=str(outcome_set.get("exit_date") or ""),
        expected_dates=expected_dates,
        horizon=int(plan["horizon_trading_days"]),
        minimum_daily_growth_coverage=float(
            plan["minimum_daily_growth_coverage"]
        ),
        minimum_observation_ratio=float(plan["minimum_observation_ratio"]),
        provider_normalized_payload_hash=str(normalized_hash or ""),
    )
    if rebuilt_evidence is None or rebuilt_evidence != label.get("evidence"):
        raise CandidateSelectionSettlementError(
            "candidate NAV evidence is detached from stored adapter stdout"
            + (f": {reason}" if reason else "")
        )


def _require_or_finalize_outcome_receipt(
    row: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    connection_factory: Callable[[], Any],
) -> dict[str, Any]:
    """Repair a post-commit crash gap, then verify the unique terminal receipt."""

    envelope = row.get("payload")
    if not isinstance(envelope, Mapping):
        raise CandidateSelectionSettlementError(
            "candidate outcome envelope is malformed"
        )
    artifact_id = str(envelope.get("artifact_id") or "")
    user_id = int(target.get("user_id") or 0)
    # Re-read provider receipts even for an existing outcome before accepting it
    # as terminal.  This catches a corrupt/deleted global evidence dependency.
    with connection_factory() as connection:
        artifact = envelope.get("artifact")
        if not isinstance(artifact, Mapping):
            raise CandidateSelectionSettlementError(
                "candidate outcome artifact is malformed"
            )
        _verify_outcome_provider_receipts_in_store(
            artifact,
            target=target,
            connection=connection,
        )
    receipt = finalize_decision_quality_artifact_receipt(
        user_id=user_id,
        artifact_id=artifact_id,
        connection_factory=connection_factory,
    )
    return _validate_artifact_commit_receipt(
        receipt,
        source_row=row,
        user_id=user_id,
        expected_artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
    )


def _persist_outcome_set(
    *,
    target: Mapping[str, Any],
    outcome_set: Mapping[str, Any],
    connection_factory: Callable[[], Any],
) -> tuple[dict[str, Any], bool]:
    validate_candidate_outcome_set(outcome_set, target=target)
    user_id = int(target["user_id"])
    saved: dict[str, Any]
    inserted = False
    with connection_factory() as connection:
        _verify_outcome_provider_receipts_in_store(
            outcome_set,
            target=target,
            connection=connection,
        )
        existing = list_decision_quality_input_artifacts(
            user_id=user_id,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            source_report_id=str(target["source_report_id"]),
            audit_eligible_only=True,
            limit=10_000,
            connection=connection,
        )
        matches = [
            row
            for row in existing
            if isinstance(row.get("payload", {}).get("artifact"), Mapping)
            and row["payload"]["artifact"].get("audit_artifact_id")
            == target["audit_artifact_id"]
        ]
        if len(matches) > 1:
            raise CandidateSelectionSettlementConflict(
                "multiple candidate outcome sets share one audit artifact"
            )
        if matches:
            prior = matches[0]["payload"]["artifact"]
            validate_candidate_outcome_set(prior, target=target)
            if prior.get("semantic_hash") != outcome_set.get("semantic_hash"):
                raise CandidateSelectionSettlementConflict(
                    "candidate source evidence conflicts with terminal outcome set"
                )
            saved = matches[0]
        else:
            receipt = str(outcome_set["settled_at"])
            try:
                saved = put_decision_quality_input_artifact(
                    user_id=user_id,
                    artifact={
                        "artifact_type": CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
                        "artifact_schema_version": (
                            CANDIDATE_OUTCOME_SET_SCHEMA_VERSION
                        ),
                        "logical_key": (
                            f"candidate_outcome:{target['audit_artifact_id']}"
                        ),
                        "source_type": "discovery",
                        "source_report_id": str(target["source_report_id"]),
                        "decision_event_id": None,
                        "decision_at": str(target["decision_at"]),
                        # This is a materialization clock, not proof of commit.
                        # The Phase 3 receipt below supplies that proof.
                        "available_at": receipt,
                        "recorded_at": receipt,
                        "store_authority": "primary",
                        "audit_eligible": True,
                        "artifact": dict(outcome_set),
                    },
                    connection=connection,
                )
                inserted = True
            except ImmutableRecordConflict as exc:
                raced = list_decision_quality_input_artifacts(
                    user_id=user_id,
                    artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
                    source_report_id=str(target["source_report_id"]),
                    audit_eligible_only=True,
                    limit=10_000,
                    connection=connection,
                )
                matches = [
                    row
                    for row in raced
                    if row.get("payload", {}).get("logical_key")
                    == f"candidate_outcome:{target['audit_artifact_id']}"
                ]
                if len(matches) != 1:
                    raise CandidateSelectionSettlementConflict(
                        "candidate outcome logical-key race could not be resolved"
                    ) from exc
                prior = matches[0].get("payload", {}).get("artifact")
                if not isinstance(prior, Mapping):
                    raise CandidateSelectionSettlementConflict(
                        "candidate outcome logical-key race stored malformed content"
                    ) from exc
                validate_candidate_outcome_set(prior, target=target)
                if prior.get("semantic_hash") != outcome_set.get("semantic_hash"):
                    raise CandidateSelectionSettlementConflict(
                        "candidate outcome logical-key race changed terminal evidence"
                    ) from exc
                saved = matches[0]
                inserted = False

    # The context above has committed and closed.  A fresh transaction now
    # proves visibility, or repairs the exact gap left by a prior crash.
    _require_or_finalize_outcome_receipt(
        saved,
        target=target,
        connection_factory=connection_factory,
    )
    return saved, inserted


def _label_semantic_material(label: Mapping[str, Any]) -> dict[str, Any]:
    material = {
        key: deepcopy(value)
        for key, value in label.items()
        if key
        not in {
            "label_hash",
            "label_available_at",
            "first_observed_at",
            "materialized_at",
            "availability_basis",
        }
    }
    source_ref = material.get("source_ref")
    if isinstance(source_ref, Mapping):
        stable_source_ref = deepcopy(dict(source_ref))
        stable_source_ref.pop("delivery", None)
        material["source_ref"] = stable_source_ref
    return material


def _nav_pull_days(*, entry_date: str, as_of_date: str) -> int:
    age = max(0, (date.fromisoformat(as_of_date) - date.fromisoformat(entry_date)).days)
    return min(_MAX_NAV_POINTS, max(90, age * 2 + 45))


def _normalize_user_ids(values: Iterable[int] | None) -> set[int] | None:
    if values is None:
        return None
    normalized: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            raise ValueError("user_ids must contain positive integers")
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("user_ids must contain positive integers")
        normalized.add(parsed)
    return normalized


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _aware_utc_text(value: object, name: str) -> str:
    parsed = _aware_datetime(value)
    if parsed is None:
        raise ValueError(f"{name} must be an ISO timestamp with timezone")
    return parsed.isoformat()


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _same_number(left: object, right: float) -> bool:
    parsed = _finite_float(left)
    return parsed is not None and math.isclose(parsed, right, abs_tol=1e-10)


__all__ = [
    "CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION",
    "CANDIDATE_OUTCOME_LABEL_SCHEMA_VERSION_V2",
    "CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE",
    "CANDIDATE_OUTCOME_SET_SCHEMA_VERSION",
    "CANDIDATE_OUTCOME_SET_SCHEMA_VERSION_V2",
    "CandidateAuditCommitReceiptLate",
    "CandidateAuditSourceCaptureLate",
    "CandidateSelectionSettlementConflict",
    "CandidateSelectionSettlementError",
    "build_candidate_outcome_set",
    "candidate_case_id",
    "candidate_preregistered_target_from_artifact",
    "candidate_target_from_artifact",
    "settle_candidate_selection_outcomes",
    "validate_candidate_outcome_set",
]
