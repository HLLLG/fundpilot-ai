from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from app.db_connect import DbConnection
from app.db_migrations import run_migrations
from app.services import candidate_selection_outcomes as outcome_service
from app.services.akshare_subprocess import (
    fund_nav_quality_adapter_policy_material,
)
from app.services.candidate_selection_audit import build_candidate_selection_audit_v2
from app.services.candidate_selection_outcomes import (
    CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
    CandidateAuditCommitReceiptLate,
    CandidateAuditSourceCaptureLate,
    CandidateSelectionSettlementError,
    _calendar_snapshot,
    _fair_due_window,
    _persist_outcome_set,
    _persist_provider_reads,
    build_candidate_outcome_set,
    candidate_preregistered_target_from_artifact,
    candidate_target_from_artifact,
    settle_candidate_selection_outcomes,
    validate_candidate_outcome_set,
)
from app.services.decision_quality_artifacts import (
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3,
    CANDIDATE_AUDIT_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_MODE,
    CANDIDATE_FORMAL_RECEIPT_MAX_DELAY_SECONDS,
    CANDIDATE_FORMAL_SOURCE_CAPTURE_MAX_DELAY_SECONDS,
    build_candidate_label_plan,
    persist_report_decision_quality_artifacts,
)
from app.services.decision_quality_provider_receipts import (
    DecisionQualityProviderRead,
    build_provider_origin_receipt,
    build_provider_read,
)
from app.services.trade_calendar_cache import (
    trade_calendar_quality_adapter_policy_material,
)
from app.services.decision_repository import (
    DecisionQualityIntegrityError,
    canonical_hash,
    finalize_decision_quality_artifact_receipt,
    get_decision_quality_artifact_receipt,
    list_decision_quality_artifact_receipts,
    list_decision_quality_input_artifacts,
    list_decision_quality_provider_receipts,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_input_artifact,
)


_DECISION_AT = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
_AUDIT_RECORDED_AT = "2026-06-01T06:01:00+00:00"
_AUDIT_ROW_CREATED_AT = "2026-06-01T06:01:01+00:00"
_PROVIDER_COMPLETED_AT = "2026-06-22T08:00:00+00:00"
_PROVIDER_SERVED_AT = "2026-06-22T08:00:01+00:00"
_SETTLED_AT = "2026-06-22T08:01:00+00:00"


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _candidate(code: str, stage: str, rank: int, score: float) -> dict[str, Any]:
    return {
        "fund_code": code,
        "fund_name": f"基金{code}",
        "sector_label": "科技",
        f"{stage}_rank": rank,
        f"{stage}_score": score,
        "score_components": {"quality": score, "fit": score / 2},
        "gates": {"quality": {"status": "pass"}},
        "reason_codes": [f"{stage}:{code}"],
    }


def _context(stage: str) -> dict[str, Any]:
    ref_id = f"source:{stage}"
    result: dict[str, Any] = {
        "version": f"{stage}.v1",
        "source_refs": [
            {
                "ref_id": ref_id,
                "source": f"fixture_{stage}",
                "version": "2026-06-01",
                "snapshot_hash": _digest(f"source:{stage}"),
            }
        ],
        "pit_refs": [
            {
                "fact_id": f"fact:{stage}",
                "source_ref_id": ref_id,
                "available_at": "2026-06-01T05:30:00+00:00",
                "snapshot_hash": _digest(f"fact:{stage}"),
            }
        ],
    }
    if stage == "recall":
        result["scope"] = {
            "definition": "complete fixture recall",
            "complete": True,
            "candidate_count_total": 3,
            "candidate_count_retained": 3,
            "catalogue_rows_embedded": False,
        }
    return result


def _audit() -> dict[str, Any]:
    stages = {
        stage: [
            _candidate("100001", stage, 1, 90.0),
            _candidate("100002", stage, 2, 80.0),
            _candidate("100003", stage, 3, 70.0),
        ]
        for stage in ("recall", "gate", "prescreen", "final")
    }
    return build_candidate_selection_audit_v2(
        decision_at=_DECISION_AT,
        recall_candidates=stages["recall"],
        gate_candidates=stages["gate"],
        prescreen_candidates=stages["prescreen"],
        final_candidates=stages["final"],
        versions={"selection_policy": "candidate_policy.test.v1"},
        stage_contexts={stage: _context(stage) for stage in stages},
    )


def _dates() -> list[str]:
    start = date(2026, 6, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(22)]


def _nav_payload(code: str, *, missing_exit: bool = False) -> dict[str, Any]:
    growth = {"100001": 1.0, "100002": -0.5, "100003": 0.2}[code]
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(_dates()):
        if missing_exit and day == _dates()[-1]:
            continue
        nav = (
            2.0
            if index == 0
            else 1.0 + index / 100
            if code == "100001"
            else 1.0
        )
        rows.append({"date": day, "nav": nav, "daily_growth": growth})
    return {"data": rows}


def _provider_read(
    *,
    provider: str,
    operation: str,
    parameters: dict[str, Any],
    normalized_payload: object,
    completed_at: str = _PROVIDER_COMPLETED_AT,
    served_at: str = _PROVIDER_SERVED_AT,
    cache_status: str = "miss",
    cache_layer: str = "live",
    adapter_script_override: str | None = None,
) -> DecisionQualityProviderRead:
    completed = datetime.fromisoformat(completed_at)
    started = completed - timedelta(seconds=1)
    if provider == outcome_service._CALENDAR_SOURCE:
        material = trade_calendar_quality_adapter_policy_material()
        parsed_payload = normalized_payload["dates"]
    else:
        material = fund_nav_quality_adapter_policy_material(
            fund_code=str(parameters["fund_code"]),
            trading_days=int(parameters["trading_days"]),
            cache_hour=int(started.timestamp() // 3600),
        )
        parsed_payload = normalized_payload
    stdout = json.dumps(
        parsed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    origin = build_provider_origin_receipt(
        provider_id=provider,
        operation=operation,
        request_parameters=parameters,
        request_started_at=started,
        response_completed_at=completed,
        response_status="success",
        adapter_contract_version=str(material["adapter_contract_version"]),
        adapter_script=(
            adapter_script_override
            if adapter_script_override is not None
            else str(material["adapter_script"])
        ),
        library_name="akshare",
        library_version="fixture",
        python_version="3.12.fixture",
        cache_policy=str(material["cache_policy"]),
        cache_key_material=material["cache_key_material"],
        stdout_bytes=stdout,
        parsed_payload=parsed_payload,
        normalized_payload=normalized_payload,
        upstream_raw_unavailable_reason=(
            "fixture captures exact adapter stdout, not upstream HTTP bytes"
        ),
    )
    return build_provider_read(
        origin_receipt=origin,
        normalized_payload=normalized_payload,
        cache_status=cache_status,
        cache_layer=cache_layer,
        served_at=served_at,
    )


def _calendar_read(
    *,
    cache_status: str = "miss",
    cache_layer: str = "live",
    origin: DecisionQualityProviderRead | None = None,
    served_at: str = _PROVIDER_SERVED_AT,
) -> DecisionQualityProviderRead:
    if origin is not None:
        return build_provider_read(
            origin_receipt=origin.origin_receipt,
            normalized_payload=origin.normalized_payload,
            cache_status=cache_status,
            cache_layer=cache_layer,
            served_at=served_at,
        )
    return _provider_read(
        provider=outcome_service._CALENDAR_SOURCE,
        operation=outcome_service._CALENDAR_OPERATION,
        parameters={},
        normalized_payload={"dates": _dates()},
        cache_status=cache_status,
        cache_layer=cache_layer,
        served_at=served_at,
    )


def _nav_read(
    code: str,
    *,
    trading_days: int = 90,
    missing_exit: bool = False,
    completed_at: str = _PROVIDER_COMPLETED_AT,
    cache_status: str = "miss",
    cache_layer: str = "live",
    served_at: str = _PROVIDER_SERVED_AT,
    origin: DecisionQualityProviderRead | None = None,
) -> DecisionQualityProviderRead:
    if origin is not None:
        return build_provider_read(
            origin_receipt=origin.origin_receipt,
            normalized_payload=origin.normalized_payload,
            cache_status=cache_status,
            cache_layer=cache_layer,
            served_at=served_at,
        )
    return _provider_read(
        provider=outcome_service._NAV_SOURCE,
        operation=outcome_service._NAV_OPERATION,
        parameters={
            "fund_code": code,
            "trading_days": trading_days,
            "indicator": "单位净值走势",
        },
        normalized_payload=_nav_payload(code, missing_exit=missing_exit),
        completed_at=completed_at,
        served_at=served_at,
        cache_status=cache_status,
        cache_layer=cache_layer,
    )


def _audit_row(
    *,
    user_id: int = 1,
    receipt_delay_seconds: float = 59.0,
    schema_version: str = CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = _audit()
    plan = build_candidate_label_plan(
        decision_at=_DECISION_AT,
        registered_at=_AUDIT_RECORDED_AT,
        decision_eligible=True,
    )
    report_id = f"candidate-report-{user_id}"
    wrapper = {
        "schema_version": schema_version,
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
        "capture_status": "eligible",
        "capture_reason": "eligible",
        "source_report_id": report_id,
        "decision_at": _DECISION_AT.isoformat(),
        "recorded_at": _AUDIT_RECORDED_AT,
        "audit_snapshot_hash": audit["snapshot_hash"],
        "audit": audit,
        "capture_validation": outcome_service.validate_candidate_selection_audit(
            audit
        ),
        "label_plan": plan,
    }
    envelope = normalize_decision_quality_input_artifact(
        {
            "artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
            "artifact_schema_version": schema_version,
            "logical_key": f"candidate_audit:{report_id}",
            "source_type": "discovery",
            "source_report_id": report_id,
            "decision_event_id": None,
            "decision_at": _DECISION_AT.isoformat(),
            "available_at": _AUDIT_RECORDED_AT,
            "recorded_at": _AUDIT_RECORDED_AT,
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": wrapper,
        }
    )
    row = {
        "user_id": user_id,
        "userId": user_id,
        "payload": envelope,
        "created_at": _AUDIT_ROW_CREATED_AT,
    }
    visible = datetime.fromisoformat(_AUDIT_ROW_CREATED_AT) + timedelta(
        seconds=receipt_delay_seconds
    )
    receipt_payload = normalize_decision_quality_artifact_receipt(
        {
            "user_id": user_id,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
            "artifact_content_hash": envelope["content_hash"],
            "source_row_created_at": _AUDIT_ROW_CREATED_AT,
            "source_visible_at": visible.isoformat(),
            "store_authority": "primary",
        }
    )
    receipt = {
        "userId": user_id,
        "payload": receipt_payload,
        "created_at": receipt_payload["source_visible_at"],
    }
    return row, receipt


def _target(
    *,
    calendar_read: DecisionQualityProviderRead | None = None,
) -> dict[str, Any]:
    row, receipt = _audit_row()
    target = candidate_target_from_artifact(row, artifact_receipt=receipt)
    assert target is not None
    read = calendar_read or _calendar_read()
    calendar = _calendar_snapshot(read, as_of_date="2026-06-22")
    assert calendar is not None
    dates = outcome_service._resolve_plan_dates(
        target["label_plan"],
        calendar,
        entry_not_before_date=target["entry_not_before_date"],
    )
    assert dates == {"entry_date": "2026-06-02", "exit_date": "2026-06-22"}
    return {
        **target,
        **dates,
        "calendar": calendar,
        "calendar_provider_read": read,
    }


def _nav_reads(target: dict[str, Any]) -> dict[str, DecisionQualityProviderRead]:
    return {code: _nav_read(code) for code in target["universe_codes"]}


def _built_outcome(
    target: dict[str, Any],
    *,
    nav_reads: dict[str, object] | None = None,
    settled_at: str = _SETTLED_AT,
) -> dict[str, Any]:
    outcome, reason = build_candidate_outcome_set(
        target,
        nav_payloads=nav_reads or _nav_reads(target),
        settled_at=settled_at,
    )
    assert reason is None and outcome is not None
    return outcome


def _database_factory(path) -> Any:
    initial = sqlite3.connect(path, timeout=20)
    initial.row_factory = sqlite3.Row
    run_migrations(initial)
    initial.commit()
    initial.close()

    def factory() -> DbConnection:
        connection = sqlite3.connect(path, timeout=20)
        connection.row_factory = sqlite3.Row
        return DbConnection(connection, "sqlite")

    return factory


def test_formal_path_rejects_plain_calendar_and_nav_dicts() -> None:
    with pytest.raises(CandidateSelectionSettlementError, match="ProviderRead"):
        _calendar_snapshot(frozenset(_dates()), as_of_date="2026-06-22")

    target = _target()
    with pytest.raises(CandidateSelectionSettlementError, match="ProviderRead"):
        build_candidate_outcome_set(
            target,
            nav_payloads={code: _nav_payload(code) for code in target["universe_codes"]},
            settled_at=_SETTLED_AT,
        )


def test_outcome_rejects_self_consistent_nonproduction_calendar_script() -> None:
    forged = _provider_read(
        provider=outcome_service._CALENDAR_SOURCE,
        operation=outcome_service._CALENDAR_OPERATION,
        parameters={},
        normalized_payload={"dates": _dates()},
        adapter_script_override="print('attacker-controlled calendar')",
    )
    with pytest.raises(CandidateSelectionSettlementError, match="adapter policy"):
        _calendar_snapshot(forged, as_of_date="2026-06-22")


def test_legacy_audit_is_ignored_and_never_upgraded() -> None:
    row, receipt = _audit_row(
        schema_version=CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3
    )
    assert candidate_preregistered_target_from_artifact(row) is None
    assert candidate_target_from_artifact(row, artifact_receipt=receipt) is None
    assert outcome_service.CANDIDATE_OUTCOME_SET_SCHEMA_VERSION_V2.endswith(".v2")
    assert outcome_service.CANDIDATE_OUTCOME_SET_SCHEMA_VERSION.endswith(".v3")


def test_preregistration_parser_keeps_missing_receipt_in_denominator() -> None:
    row, _ = _audit_row()
    base = candidate_preregistered_target_from_artifact(row)
    assert base is not None
    assert "entry_not_before_date" not in base
    assert base["universe_codes"] == ["100001", "100002", "100003"]
    with pytest.raises(CandidateSelectionSettlementError, match="receipt is required"):
        candidate_target_from_artifact(row)


def test_audit_receipt_binding_cross_tenant_tamper_and_300_second_boundary() -> None:
    boundary_row, boundary_receipt = _audit_row(receipt_delay_seconds=300)
    boundary = candidate_target_from_artifact(
        boundary_row,
        artifact_receipt=boundary_receipt,
    )
    assert boundary is not None
    assert boundary["entry_not_before_date"] == "2026-06-02"
    assert boundary["audit_storage_cutoff"] == boundary_receipt["created_at"]

    late_row, late_receipt = _audit_row(receipt_delay_seconds=300.001)
    with pytest.raises(CandidateAuditCommitReceiptLate, match="delay policy") as late:
        candidate_target_from_artifact(late_row, artifact_receipt=late_receipt)
    assert late.value.receipt == late_receipt["payload"]

    cross_tenant = deepcopy(boundary_receipt)
    cross_tenant["userId"] = 2
    with pytest.raises(CandidateSelectionSettlementError, match="immutable source"):
        candidate_target_from_artifact(
            boundary_row,
            artifact_receipt=cross_tenant,
        )

    tampered = deepcopy(boundary_receipt)
    tampered["payload"]["artifact_content_hash"] = "0" * 64
    with pytest.raises(CandidateSelectionSettlementError, match="integrity"):
        candidate_target_from_artifact(boundary_row, artifact_receipt=tampered)

    tampered_late = deepcopy(late_receipt)
    tampered_late["payload"]["artifact_content_hash"] = "0" * 64
    with pytest.raises(
        CandidateSelectionSettlementError, match="integrity"
    ) as invalid:
        candidate_target_from_artifact(
            late_row,
            artifact_receipt=tampered_late,
        )
    assert not isinstance(invalid.value, CandidateAuditCommitReceiptLate)


def test_storage_owned_source_capture_delay_blocks_target_before_provider_reads() -> None:
    row, _ = _audit_row()
    source_created_at = _DECISION_AT + timedelta(seconds=301)
    row["created_at"] = source_created_at.isoformat()
    envelope = row["payload"]
    receipt_payload = normalize_decision_quality_artifact_receipt(
        {
            "user_id": 1,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": CANDIDATE_AUDIT_ARTIFACT_TYPE,
            "artifact_content_hash": envelope["content_hash"],
            "source_row_created_at": source_created_at.isoformat(),
            "source_visible_at": (
                source_created_at + timedelta(seconds=1)
            ).isoformat(),
            "store_authority": "primary",
        }
    )
    receipt = {
        "userId": 1,
        "payload": receipt_payload,
        "created_at": receipt_payload["source_visible_at"],
    }

    with pytest.raises(CandidateAuditSourceCaptureLate, match="source capture"):
        candidate_target_from_artifact(row, artifact_receipt=receipt)


def test_full_outcome_freezes_exact_provider_refs_delivery_and_projection() -> None:
    calendar_origin = _calendar_read()
    calendar_hit = _calendar_read(
        origin=calendar_origin,
        cache_status="hit",
        cache_layer="process",
        served_at="2026-06-22T08:00:20+00:00",
    )
    assert (
        outcome_service._provider_receipt_ref(calendar_origin)
        == outcome_service._provider_receipt_ref(calendar_hit)
    )
    assert calendar_origin.delivery != calendar_hit.delivery

    target = _target(calendar_read=calendar_hit)
    nav_origin = _nav_read("100001")
    nav_hit = _nav_read(
        "100001",
        origin=nav_origin,
        cache_status="hit",
        cache_layer="process",
        served_at="2026-06-22T08:00:30+00:00",
    )
    reads = _nav_reads(target)
    reads["100001"] = nav_hit
    outcome = _built_outcome(target, nav_reads=reads)
    validate_candidate_outcome_set(outcome, target=target)

    label = outcome["outcome_labels"]["100001"]
    ref = outcome["provider_receipt_refs"]["nav_by_code"]["100001"]
    assert ref == label["source_ref"]["provider_receipt_ref"]
    assert label["source_ref"]["delivery"]["cache_status"] == "hit"
    assert ref["completed_at"] == _PROVIDER_COMPLETED_AT
    assert ref["origin_receipt_hash"] == nav_origin.origin_receipt[
        "origin_receipt_hash"
    ]
    assert label["evidence"]["observation_count"] == 21
    assert label["evidence"]["provider_normalized_payload_hash"] == ref[
        "normalized_payload_hash"
    ]
    assert label["availability_basis"] == (
        "requires_post_commit_artifact_receipt"
    )
    assert label["label_available_at"] is None
    assert outcome["label_available_at"] is None
    assert outcome["automatic_promotion_allowed"] is False


def test_missing_common_date_and_pre_exit_origin_remain_pending() -> None:
    target = _target()
    reads = _nav_reads(target)
    reads["100003"] = _nav_read("100003", missing_exit=True)
    outcome, reason = build_candidate_outcome_set(
        target,
        nav_payloads=reads,
        settled_at=_SETTLED_AT,
    )
    assert outcome is None
    assert reason == "candidate_common_date_nav_missing"

    reads["100003"] = _nav_read(
        "100003",
        completed_at="2026-06-22T06:59:59+00:00",
        served_at="2026-06-22T07:00:00+00:00",
    )
    outcome, reason = build_candidate_outcome_set(
        target,
        nav_payloads=reads,
        settled_at=_SETTLED_AT,
    )
    assert outcome is None
    assert reason == "candidate_nav_origin_before_exit_close"


def test_resigned_label_cannot_disagree_with_frozen_return_evidence() -> None:
    target = _target()
    outcome = _built_outcome(target)
    tampered = deepcopy(outcome)
    label = tampered["outcome_labels"]["100001"]
    label["return_percent"] = 999.0
    label["label_hash"] = canonical_hash(
        {key: value for key, value in label.items() if key != "label_hash"}
    )
    tampered["outcome_set_hash"] = canonical_hash(
        {
            key: value
            for key, value in tampered.items()
            if key != "outcome_set_hash"
        }
    )
    with pytest.raises(CandidateSelectionSettlementError, match="return conflicts"):
        validate_candidate_outcome_set(tampered, target=target)


def test_outcome_adapter_stratum_cannot_diverge_from_provider_refs() -> None:
    target = _target()
    tampered = deepcopy(_built_outcome(target))
    tampered["provider_adapter_stratum"][0]["adapter_library_version"] = (
        "forged-runtime"
    )
    tampered["outcome_set_hash"] = canonical_hash(
        {
            key: value
            for key, value in tampered.items()
            if key != "outcome_set_hash"
        }
    )
    with pytest.raises(CandidateSelectionSettlementError, match="stratum conflicts"):
        validate_candidate_outcome_set(tampered, target=target)


def test_provider_receipts_must_commit_before_outcome_transaction(tmp_path) -> None:
    target = _target()
    reads = _nav_reads(target)
    outcome = _built_outcome(target, nav_reads=reads)
    factory = _database_factory(tmp_path / "provider-order.db")

    with pytest.raises(DecisionQualityIntegrityError, match="commit-visible"):
        _persist_outcome_set(
            target=target,
            outcome_set=outcome,
            connection_factory=factory,
        )
    with factory() as connection:
        assert list_decision_quality_input_artifacts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        ) == []

    persisted = _persist_provider_reads(
        [target["calendar_provider_read"], *reads.values()],
        connection_factory=factory,
    )
    assert len(persisted) == 4
    saved, inserted = _persist_outcome_set(
        target=target,
        outcome_set=outcome,
        connection_factory=factory,
    )
    assert inserted is True
    with factory() as connection:
        receipts = list_decision_quality_artifact_receipts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        )
        provider_rows = list_decision_quality_provider_receipts(
            limit=20,
            connection=connection,
        )
    assert len(receipts) == 1
    assert receipts[0]["payload"]["artifact_id"] == saved["payload"]["artifact_id"]
    assert len(provider_rows) == 4


def test_store_reread_rejects_cross_fund_provider_receipt_substitution(
    tmp_path,
) -> None:
    target = _target()
    reads = _nav_reads(target)
    outcome = _built_outcome(target, nav_reads=reads)
    factory = _database_factory(tmp_path / "cross-fund-provider.db")
    _persist_provider_reads(
        [target["calendar_provider_read"], *reads.values()],
        connection_factory=factory,
    )
    substituted = deepcopy(outcome)
    substituted["provider_receipt_refs"]["nav_by_code"]["100002"] = deepcopy(
        substituted["provider_receipt_refs"]["nav_by_code"]["100001"]
    )
    substituted["provider_deliveries"]["nav_by_code"]["100002"] = deepcopy(
        substituted["provider_deliveries"]["nav_by_code"]["100001"]
    )
    with factory() as connection, pytest.raises(
        DecisionQualityIntegrityError,
        match="evidence identity",
    ):
        outcome_service._verify_outcome_provider_receipts_in_store(
            substituted,
            target=target,
            connection=connection,
        )


def test_store_reread_rejects_self_consistent_nav_projection_detached_from_stdout(
    tmp_path,
) -> None:
    target = _target()
    reads = _nav_reads(target)
    detached = deepcopy(_built_outcome(target, nav_reads=reads))
    code = "100001"
    plan = target["label_plan"]
    forged_rows = deepcopy(
        detached["outcome_labels"][code]["evidence"]["observations"]
    )
    for row in forged_rows:
        row["daily_growth"] = 2.0
    forged_evidence, reason = outcome_service._return_evidence(
        code,
        {"data": forged_rows},
        entry_date=detached["entry_date"],
        exit_date=detached["exit_date"],
        expected_dates=outcome_service._expected_trade_dates(target) or [],
        horizon=int(plan["horizon_trading_days"]),
        minimum_daily_growth_coverage=float(
            plan["minimum_daily_growth_coverage"]
        ),
        minimum_observation_ratio=float(plan["minimum_observation_ratio"]),
        provider_normalized_payload_hash=detached["provider_receipt_refs"][
            "nav_by_code"
        ][code]["normalized_payload_hash"],
    )
    assert reason is None and forged_evidence is not None
    forged_label = detached["outcome_labels"][code]
    forged_label["evidence"] = forged_evidence
    forged_label["return_percent"] = forged_evidence["return_percent"]
    forged_label["binary_relevance"] = forged_evidence["return_percent"] > 0
    forged_label["source_ref"]["content_hash"] = forged_evidence[
        "evidence_hash"
    ]
    forged_label["source_ref"]["normalized_payload_projection_hash"] = (
        forged_evidence["normalized_payload_projection_hash"]
    )
    returns = {
        fund_code: float(label["evidence"]["return_percent"])
        for fund_code, label in detached["outcome_labels"].items()
    }
    relevance = outcome_service._cross_sectional_relevance(returns)
    for fund_code, label in detached["outcome_labels"].items():
        label["relevance"] = relevance[fund_code]
        label["label_hash"] = canonical_hash(
            {
                key: value
                for key, value in label.items()
                if key != "label_hash"
            }
        )
    detached["semantic_hash"] = canonical_hash(
        {
            "audit_artifact_id": detached["audit_artifact_id"],
            "audit_content_hash": detached["audit_content_hash"],
            "audit_snapshot_hash": detached["audit_snapshot_hash"],
            "audit_artifact_receipt_ref": detached[
                "audit_artifact_receipt_ref"
            ],
            "plan_hash": detached["plan_hash"],
            "calendar_semantic_hash": detached["calendar"][
                "calendar_semantic_hash"
            ],
            "entry_date": detached["entry_date"],
            "exit_date": detached["exit_date"],
            "labels": {
                fund_code: outcome_service._label_semantic_material(label)
                for fund_code, label in sorted(
                    detached["outcome_labels"].items()
                )
            },
        }
    )
    detached["outcome_set_hash"] = canonical_hash(
        {
            key: value
            for key, value in detached.items()
            if key != "outcome_set_hash"
        }
    )

    # The document is deliberately self-consistent; only a fresh projection
    # from the stored adapter stdout can expose the hybrid evidence.
    validate_candidate_outcome_set(detached, target=target)
    factory = _database_factory(tmp_path / "detached-nav-projection.db")
    _persist_provider_reads(
        [target["calendar_provider_read"], *reads.values()],
        connection_factory=factory,
    )
    with factory() as connection, pytest.raises(
        DecisionQualityIntegrityError,
        match="origin projection conflicts",
    ):
        outcome_service._verify_outcome_provider_receipts_in_store(
            detached,
            target=target,
            connection=connection,
        )


def test_outcome_commit_receipt_crash_gap_is_repaired_idempotently(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    reads = _nav_reads(target)
    outcome = _built_outcome(target, nav_reads=reads)
    factory = _database_factory(tmp_path / "outcome-crash-gap.db")
    _persist_provider_reads(
        [target["calendar_provider_read"], *reads.values()],
        connection_factory=factory,
    )
    real_finalizer = outcome_service.finalize_decision_quality_artifact_receipt

    def interrupted_finalizer(**_kwargs):
        raise DecisionQualityIntegrityError("simulated post-commit crash")

    monkeypatch.setattr(
        outcome_service,
        "finalize_decision_quality_artifact_receipt",
        interrupted_finalizer,
    )
    with pytest.raises(DecisionQualityIntegrityError, match="post-commit crash"):
        _persist_outcome_set(
            target=target,
            outcome_set=outcome,
            connection_factory=factory,
        )
    with factory() as connection:
        rows = list_decision_quality_input_artifacts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        )
        receipts = list_decision_quality_artifact_receipts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        )
    assert len(rows) == 1
    assert receipts == []

    monkeypatch.setattr(
        outcome_service,
        "finalize_decision_quality_artifact_receipt",
        real_finalizer,
    )
    saved, inserted = _persist_outcome_set(
        target=target,
        outcome_set=outcome,
        connection_factory=factory,
    )
    assert inserted is False
    with factory() as connection:
        receipt = get_decision_quality_artifact_receipt(
            user_id=1,
            artifact_id=saved["payload"]["artifact_id"],
            connection=connection,
        )
    assert receipt is not None


def test_concurrent_outcome_winner_and_loser_share_one_terminal_receipt(
    tmp_path,
) -> None:
    target = _target()
    reads = _nav_reads(target)
    outcome = _built_outcome(target, nav_reads=reads)
    factory = _database_factory(tmp_path / "outcome-race.db")
    _persist_provider_reads(
        [target["calendar_provider_read"], *reads.values()],
        connection_factory=factory,
    )

    def persist() -> tuple[dict[str, Any], bool]:
        return _persist_outcome_set(
            target=target,
            outcome_set=outcome,
            connection_factory=factory,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: persist(), range(2)))
    assert len({result[0]["payload"]["artifact_id"] for result in results}) == 1
    with factory() as connection:
        rows = list_decision_quality_input_artifacts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        )
        receipts = list_decision_quality_artifact_receipts(
            user_id=1,
            artifact_type=CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            connection=connection,
        )
    assert len(rows) == 1
    assert len(receipts) == 1


def test_missing_audit_receipt_is_one_stable_pending_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, _ = _audit_row()
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: ([row], {}, {}, set()),
    )
    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert result["status"] == "completed_with_pending"
    assert result["formal_audit_count"] == 1
    assert result["missing_audit_commit_receipt_count"] == 1
    assert result["pending_case_count"] == 1
    assert result["pending_reasons"] == [
        {"reason": "candidate_audit_commit_receipt_pending", "count": 1}
    ]


def test_legacy_audit_is_counted_as_ignored_not_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, receipt = _audit_row(
        schema_version=CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION_V3
    )
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: (
            [row],
            {},
            {(1, row["payload"]["artifact_id"]): receipt},
            set(),
        ),
    )
    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert result["status"] == "completed"
    assert result["legacy_ignored_audit_count"] == 1
    assert result["formal_audit_count"] == 0
    assert result["pending_case_count"] == 0


def test_calendar_provider_failure_counts_each_case_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_and_receipts = [_audit_row(user_id=user_id) for user_id in (1, 2)]
    rows = [item[0] for item in rows_and_receipts]
    receipts = {
        (row["user_id"], row["payload"]["artifact_id"]): receipt
        for row, receipt in rows_and_receipts
    }
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: (rows, {}, receipts, set()),
    )
    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        fetch_calendar=lambda: {"dates": _dates()},
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert result["pending_case_count"] == 2
    assert result["pending_reasons"] == [
        {"reason": "trade_calendar_provider_receipt_invalid", "count": 2}
    ]


def test_incomplete_case_writes_no_provider_receipt_or_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, receipt = _audit_row()
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: (
            [row],
            {},
            {(1, row["payload"]["artifact_id"]): receipt},
            set(),
        ),
    )
    monkeypatch.setattr(
        outcome_service,
        "_persist_provider_reads",
        lambda *_args, **_kwargs: pytest.fail(
            "incomplete case must not persist provider receipts"
        ),
    )
    monkeypatch.setattr(
        outcome_service,
        "_persist_outcome_set",
        lambda **_kwargs: pytest.fail("incomplete case must not persist outcome"),
    )

    def fetcher(code: str, *, trading_days: int) -> DecisionQualityProviderRead:
        return _nav_read(
            code,
            trading_days=trading_days,
            missing_exit=code == "100003",
        )

    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        fetch_calendar=_calendar_read,
        fetch_nav=fetcher,
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert result["status"] == "completed_with_pending"
    assert result["persisted_case_count"] == 0
    assert result["provider_receipt_count"] == 0
    assert result["outcome_commit_receipt_count"] == 0
    assert result["pending_reasons"] == [
        {"reason": "candidate_common_date_nav_missing", "count": 1}
    ]


def test_malformed_and_persistence_failure_tenants_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_and_receipts = [_audit_row(user_id=user_id) for user_id in (1, 2)]
    rows = [item[0] for item in rows_and_receipts]
    receipts = {
        (row["user_id"], row["payload"]["artifact_id"]): receipt
        for row, receipt in rows_and_receipts
    }
    targets = {
        user_id: {**_target(), "user_id": user_id}
        for user_id in (1, 2)
    }
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: (rows, {}, receipts, set()),
    )

    def parse_target(row, *, artifact_receipt):
        del artifact_receipt
        user_id = int(row["user_id"])
        if user_id == 1:
            raise CandidateSelectionSettlementError("malformed tenant")
        return targets[user_id]

    monkeypatch.setattr(outcome_service, "candidate_target_from_artifact", parse_target)
    monkeypatch.setattr(
        outcome_service,
        "build_candidate_outcome_set",
        lambda target, **_kwargs: (
            {"case_id": f"user-{target['user_id']}-case"},
            None,
        ),
    )
    monkeypatch.setattr(outcome_service, "_persist_provider_reads", lambda *_args, **_kwargs: [])

    def persist(*, target, outcome_set, connection_factory):
        del connection_factory
        if target["user_id"] == 1:
            raise DecisionQualityIntegrityError("tenant integrity failure")
        return {"payload": {"artifact": outcome_set}}, True

    monkeypatch.setattr(outcome_service, "_persist_outcome_set", persist)
    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        fetch_calendar=_calendar_read,
        fetch_nav=lambda code, *, trading_days: _nav_read(
            code,
            trading_days=trading_days,
        ),
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert result["status"] == "completed_with_failures"
    assert result["failed_user_ids"] == [1]
    assert result["persisted_case_count"] == 1
    assert result["completed_case_ids"] == ["user-2-case"]


def test_persistence_time_integrity_error_does_not_block_healthy_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_and_receipts = [_audit_row(user_id=user_id) for user_id in (1, 2)]
    rows = [item[0] for item in rows_and_receipts]
    receipts = {
        (row["user_id"], row["payload"]["artifact_id"]): receipt
        for row, receipt in rows_and_receipts
    }
    targets: dict[int, dict[str, Any]] = {}
    for row, receipt in rows_and_receipts:
        target = candidate_target_from_artifact(row, artifact_receipt=receipt)
        assert target is not None
        targets[int(row["user_id"])] = target
    monkeypatch.setattr(
        outcome_service,
        "_load_candidate_artifacts",
        lambda **_kwargs: (rows, {}, receipts, set()),
    )
    monkeypatch.setattr(
        outcome_service,
        "candidate_target_from_artifact",
        lambda row, *, artifact_receipt: targets[int(row["user_id"])],
    )
    monkeypatch.setattr(
        outcome_service,
        "build_candidate_outcome_set",
        lambda target, **_kwargs: (
            {"case_id": f"user-{target['user_id']}-case"},
            None,
        ),
    )
    monkeypatch.setattr(outcome_service, "_persist_provider_reads", lambda *_args, **_kwargs: [])
    attempts: list[int] = []

    def persist(*, target, outcome_set, connection_factory):
        del connection_factory
        user_id = int(target["user_id"])
        attempts.append(user_id)
        if user_id == 1:
            raise DecisionQualityIntegrityError("persistence-time corruption")
        return {"payload": {"artifact": outcome_set}}, True

    monkeypatch.setattr(outcome_service, "_persist_outcome_set", persist)
    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        observed_at=_SETTLED_AT,
        fetch_calendar=_calendar_read,
        fetch_nav=lambda code, *, trading_days: _nav_read(
            code,
            trading_days=trading_days,
        ),
        connection_factory=lambda: pytest.fail("load was stubbed"),
    )
    assert sorted(attempts) == [1, 2]
    assert result["status"] == "completed_with_failures"
    assert result["failed_user_ids"] == [1]
    assert result["failure_reasons"] == [
        {"reason": "DecisionQualityIntegrityError", "count": 1}
    ]
    assert result["persisted_case_count"] == 1


def test_fair_due_window_rotates_retryable_cases() -> None:
    targets = [{"audit_artifact_id": f"audit-{index}"} for index in range(21)]
    attempted: set[str] = set()
    start = date(2026, 7, 1)
    for offset in range(3):
        selected = _fair_due_window(
            targets,
            limit=20,
            as_of_date=(start + timedelta(days=offset)).isoformat(),
        )
        attempted.update(str(row["audit_artifact_id"]) for row in selected)
    assert attempted == {f"audit-{index}" for index in range(21)}


def test_quality_ledger_settlement_and_existing_receipt_recovery_are_idempotent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _database_factory(tmp_path / "candidate-ledger.db")
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: _AUDIT_ROW_CREATED_AT,
    )
    with factory() as connection:
        persisted = persist_report_decision_quality_artifacts(
            user_id=1,
            report={
                "id": "candidate-ledger-report",
                "created_at": _DECISION_AT.isoformat(),
                "recommendations": [],
                "discovery_facts": {"candidate_selection_audit": _audit()},
            },
            saved_events=[],
            source_type="discovery",
            store_authority="primary",
            report_recorded_at=_AUDIT_RECORDED_AT,
            connection=connection,
        )
    assert len(persisted) == 1
    audit_artifact_id = persisted[0]["payload"]["artifact_id"]
    monkeypatch.setattr(
        "app.services.decision_repository._decision_quality_database_utc_now",
        lambda _connection: datetime.fromisoformat(
            "2026-06-01T06:02:00+00:00"
        ),
    )
    finalize_decision_quality_artifact_receipt(
        user_id=1,
        artifact_id=audit_artifact_id,
        connection_factory=factory,
    )
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(
        "app.services.decision_repository._decision_quality_database_utc_now",
        lambda _connection: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(uses_mysql=False),
    )
    calls: list[str] = []

    def fetcher(code: str, *, trading_days: int) -> DecisionQualityProviderRead:
        calls.append(code)
        return _nav_read(code, trading_days=trading_days)

    result = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        fetch_calendar=_calendar_read,
        fetch_nav=fetcher,
        observed_at=_SETTLED_AT,
        connection_factory=factory,
    )
    assert result["status"] == "completed"
    assert result["audit_count"] == 1
    assert result["formal_audit_count"] == 1
    assert result["audit_commit_receipt_count"] == 1
    assert result["persisted_case_count"] == 1
    assert result["provider_receipt_count"] == 4
    assert result["outcome_commit_receipt_count"] == 1
    assert sorted(calls) == ["100001", "100002", "100003"]

    calls.clear()
    retry = settle_candidate_selection_outcomes(
        as_of_date="2026-06-22",
        fetch_calendar=lambda: pytest.fail("existing outcome must skip providers"),
        fetch_nav=fetcher,
        observed_at="2026-06-22T08:02:00+00:00",
        connection_factory=factory,
    )
    assert retry["existing_case_count"] == 1
    assert retry["persisted_case_count"] == 0
    assert retry["outcome_commit_receipt_count"] == 1
    assert calls == []


def test_calendar_contract_rejects_noncanonical_dates() -> None:
    target = _target()
    calendar = deepcopy(target["calendar"])
    calendar["dates"] = [20260714, "2026-07-15"]
    with pytest.raises(CandidateSelectionSettlementError, match="calendar contract"):
        outcome_service._validate_calendar_snapshot(
            calendar,
            settled_at=_SETTLED_AT,
        )
