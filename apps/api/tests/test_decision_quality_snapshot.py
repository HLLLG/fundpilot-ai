from __future__ import annotations

import json
import hashlib
import sqlite3
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import decision_quality_snapshot as snapshot_service
from app.services import candidate_selection_outcomes as outcome_service
from app.services import akshare_subprocess as nav_adapter
from app.services import trade_calendar_cache as calendar_adapter
from app.services.akshare_subprocess import (
    fund_nav_quality_adapter_policy_material,
)
from app.config import refresh_settings
from app.db_connect import DbConnection
from app.db_migrations import run_migrations
from app.services.candidate_selection_audit import (
    build_candidate_selection_audit_v2,
)
from app.services.candidate_selection_outcomes import (
    CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
    CANDIDATE_OUTCOME_SET_SCHEMA_VERSION,
    settle_candidate_selection_outcomes,
)
from app.services.decision_quality_artifacts import (
    CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_AUDIT_ARTIFACT_TYPE,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
    persist_report_decision_quality_artifacts,
)
from app.services.decision_quality_provider_receipts import (
    DecisionQualityProviderRead,
    build_provider_origin_receipt,
    build_provider_read,
)
from app.services.decision_quality_provider_policy import (
    verify_candidate_provider_adapter_policy,
)
from app.services.trade_calendar_cache import (
    trade_calendar_quality_adapter_policy_material,
)
from app.services.decision_quality_snapshot import (
    DecisionQualitySnapshotContractError,
    DecisionQualitySnapshotStorageError,
    READINESS_INSUFFICIENT,
    READINESS_MANUAL_REVIEW,
    READINESS_SHADOW,
    _events_in_window,
    _fetch_decision_quality_input_artifact_rows,
    _fetch_decision_quality_provider_receipt_rows,
    _validate_provider_receipt_ref_binding,
    _fetch_paginated_rows,
    _legacy_event_exclusion_allowed,
    _redacted_candidate_selection,
    _raise_for_evaluation_contract_failures,
    _source_verified_selected_count_below_k,
    _terminal_outcome_for_selected_event,
    _validate_artifact_receipt_binding,
    build_decision_quality_snapshot,
    evaluate_and_persist_decision_quality_snapshots,
    parse_evaluation_as_of,
    read_latest_decision_quality_snapshot,
    resolve_decision_quality_readiness,
)
from app.services.decision_repository import (
    canonical_hash,
    canonical_json,
    finalize_decision_quality_artifact_receipt,
    list_decision_quality_evaluation_snapshots,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_input_artifact,
    normalize_decision_quality_provider_receipt,
    put_decision_quality_provider_receipt,
    put_decision_quality_input_artifact,
    reconcile_decision_quality_artifact_receipts,
)


def _snapshot_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    return connection


def _clone_input_artifact_with_poison(
    connection: sqlite3.Connection,
    *,
    source_user_id: int,
    target_user_id: int,
    poison: dict[str, object],
) -> None:
    source = connection.execute(
        "SELECT * FROM decision_quality_input_artifacts WHERE userId = ?",
        (source_user_id,),
    ).fetchone()
    assert source is not None
    row = dict(source)
    row["userId"] = target_user_id
    row.update(poison)
    columns = tuple(row)
    connection.execute(
        "INSERT INTO decision_quality_input_artifacts "
        f"({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _formal_candidate_audit(
    decision_at: str,
    *,
    universe_codes: tuple[str, ...] = ("100001", "100002", "100003"),
    final_codes: tuple[str, ...] | None = None,
) -> dict:
    def candidate(stage: str, code: str, rank: int) -> dict:
        return {
            "fund_code": code,
            "fund_name": f"fixture-{code}",
            "sector_label": "technology",
            f"{stage}_rank": rank,
            f"{stage}_score": 91.0 - rank,
            "score_components": {"quality": 91.0 - rank},
            "gates": {"quality": {"status": "pass"}},
            "reason_codes": [f"{stage}:{code}"],
        }

    stage_contexts: dict[str, dict] = {}
    stages = ("recall", "gate", "prescreen", "final")
    for stage in stages:
        ref_id = f"source:{stage}"
        stage_contexts[stage] = {
            "version": f"{stage}.v1",
            "source_refs": [
                {
                    "ref_id": ref_id,
                    "source": "snapshot_fixture",
                    "version": "v1",
                    "snapshot_hash": _sha(ref_id),
                }
            ],
            "pit_refs": [
                {
                    "fact_id": f"fact:{stage}",
                    "source_ref_id": ref_id,
                    "available_at": "2026-01-02T06:58:00+00:00",
                    "snapshot_hash": _sha(f"fact:{stage}"),
                }
            ],
        }
    stage_contexts["recall"]["scope"] = {
        "definition": "complete snapshot fixture",
        "complete": True,
        "candidate_count_total": len(universe_codes),
        "candidate_count_retained": len(universe_codes),
        "catalogue_rows_embedded": False,
    }
    selected_codes = final_codes if final_codes is not None else universe_codes
    candidates = [
        candidate(stage, code, rank)
        for stage in ("recall",)
        for rank, code in enumerate(universe_codes, start=1)
    ]
    return build_candidate_selection_audit_v2(
        decision_at=decision_at,
        recall_candidates=candidates,
        gate_candidates=[
            candidate("gate", code, rank)
            for rank, code in enumerate(universe_codes, start=1)
        ],
        prescreen_candidates=[
            candidate("prescreen", code, rank)
            for rank, code in enumerate(universe_codes, start=1)
        ],
        final_candidates=[
            candidate("final", code, rank)
            for rank, code in enumerate(selected_codes, start=1)
        ],
        versions={"selection_policy": "candidate_policy.snapshot.v1"},
        stage_contexts=stage_contexts,
    )


def _persist_formal_candidate_audit(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    universe_codes: tuple[str, ...] = ("100001", "100002", "100003"),
    final_codes: tuple[str, ...] | None = None,
) -> dict:
    decision_at = "2026-01-02T06:59:00+00:00"
    rows = persist_report_decision_quality_artifacts(
        user_id=user_id,
        report={
            "id": f"snapshot-receipt-{user_id}",
            "created_at": decision_at,
            "recommendations": [],
            "discovery_facts": {
                "candidate_selection_audit": _formal_candidate_audit(
                    decision_at,
                    universe_codes=universe_codes,
                    final_codes=final_codes,
                )
            },
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-01-02T07:00:00+00:00",
        connection=connection,
    )
    assert len(rows) == 1
    return rows[0]


def _insert_artifact_receipt(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    artifact_row: dict,
    source_visible_at: str,
) -> dict:
    payload = artifact_row["payload"]
    receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": user_id,
            "artifact_id": payload["artifact_id"],
            "artifact_type": payload["artifact_type"],
            "artifact_content_hash": payload["content_hash"],
            "source_row_created_at": artifact_row["created_at"],
            "source_visible_at": source_visible_at,
            "store_authority": "primary",
        }
    )
    connection.execute(
        "INSERT INTO decision_quality_artifact_receipts "
        "(userId, receipt_id, schema_version, receipt_policy, artifact_id, "
        "artifact_type, artifact_content_hash, source_row_created_at, "
        "source_visible_at, store_authority, content_hash, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            receipt["receipt_id"],
            receipt["schema_version"],
            receipt["receipt_policy"],
            receipt["artifact_id"],
            receipt["artifact_type"],
            receipt["artifact_content_hash"],
            receipt["source_row_created_at"],
            receipt["source_visible_at"],
            receipt["store_authority"],
            receipt["content_hash"],
            canonical_json(receipt),
            receipt["source_visible_at"],
        ),
    )
    return receipt


def _quality_provider_read(
    *,
    provider: str,
    operation: str,
    parameters: dict,
    parsed_payload: object,
    normalized_payload: object,
    completed_at: str,
    served_at: str,
) -> DecisionQualityProviderRead:
    completed = datetime.fromisoformat(completed_at)
    started = completed - timedelta(seconds=1)
    if provider == "akshare.tool_trade_date_hist_sina":
        material = trade_calendar_quality_adapter_policy_material()
    else:
        material = fund_nav_quality_adapter_policy_material(
            fund_code=str(parameters["fund_code"]),
            trading_days=int(parameters["trading_days"]),
            cache_hour=int(started.timestamp() // 3600),
        )
    stdout = json.dumps(
        parsed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if provider == "akshare.tool_trade_date_hist_sina":
        origin_read = calendar_adapter._build_quality_origin_read(
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            stdout=stdout,
            parsed_payload=parsed_payload,
            normalized_payload=normalized_payload,
            status="success",
        )
    else:
        origin_read = nav_adapter._build_fund_nav_origin_read(
            fund_code=str(parameters["fund_code"]),
            trading_days=int(parameters["trading_days"]),
            indicator=str(parameters["indicator"]),
            script=str(material["adapter_script"]),
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            stdout=stdout,
            parsed_payload=parsed_payload,
            normalized_payload=normalized_payload,
            status="success",
            cache_hour=int(started.timestamp() // 3600),
        )
    return build_provider_read(
        origin_receipt=origin_read.origin_receipt,
        normalized_payload=normalized_payload,
        cache_status="miss",
        cache_layer="live",
        served_at=served_at,
    )


def _settle_source_verified_fixture(
    monkeypatch: pytest.MonkeyPatch,
    path,
    *,
    user_id: int,
    universe_codes: tuple[str, ...],
    final_codes: tuple[str, ...],
):
    def factory() -> DbConnection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return DbConnection(connection, "sqlite")

    initial = sqlite3.connect(path)
    initial.row_factory = sqlite3.Row
    run_migrations(initial)
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(
        initial,
        user_id=user_id,
        universe_codes=universe_codes,
        final_codes=final_codes,
    )
    initial.commit()
    initial.close()
    database_clock = {
        "value": datetime.fromisoformat("2026-01-02T07:00:02+00:00")
    }
    monkeypatch.setattr(
        "app.services.decision_repository._decision_quality_database_utc_now",
        lambda _connection: database_clock["value"],
    )
    finalize_decision_quality_artifact_receipt(
        user_id=user_id,
        artifact_id=audit_row["artifact_id"],
        connection_factory=factory,
    )
    trade_dates = [
        (date(2026, 1, 3) + timedelta(days=index)).isoformat()
        for index in range(22)
    ]
    provider_completed = "2026-01-24T08:00:00+00:00"
    provider_served = "2026-01-24T08:01:00+00:00"
    calendar_read = _quality_provider_read(
        provider="akshare.tool_trade_date_hist_sina",
        operation="tool_trade_date_hist_sina",
        parameters={},
        parsed_payload=trade_dates,
        normalized_payload={"dates": trade_dates},
        completed_at=provider_completed,
        served_at=provider_served,
    )

    def nav_read(code: str, *, trading_days: int) -> DecisionQualityProviderRead:
        rows = [
            {
                "date": day,
                "nav": 1.0 + index / 100,
                "daily_growth": 0.1,
            }
            for index, day in enumerate(trade_dates)
        ]
        payload = {"data": rows}
        return _quality_provider_read(
            provider="akshare.fund_open_fund_info_em",
            operation="fund_open_fund_info_em",
            parameters={
                "fund_code": code,
                "trading_days": trading_days,
                "indicator": "单位净值走势",
            },
            parsed_payload=payload,
            normalized_payload=payload,
            completed_at=provider_completed,
            served_at=provider_served,
        )

    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-24T08:02:30+00:00",
    )
    database_clock["value"] = datetime.fromisoformat(
        "2026-01-24T08:03:00+00:00"
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("Settings", (), {"uses_mysql": False})(),
    )
    result = settle_candidate_selection_outcomes(
        user_ids=[user_id],
        as_of_date="2026-01-24",
        max_cases=5,
        fetch_calendar=lambda: calendar_read,
        fetch_nav=nav_read,
        observed_at="2026-01-24T08:02:00+00:00",
        connection_factory=factory,
    )
    assert result["persisted_case_count"] == 1
    assert result["provider_receipt_count"] == len(universe_codes) + 1
    assert result["outcome_commit_receipt_count"] == 1
    return factory


@pytest.mark.parametrize(
    ("mature_days", "coverage", "expected"),
    [
        (19, None, READINESS_INSUFFICIENT),
        (20, None, READINESS_SHADOW),
        (59, 100.0, READINESS_SHADOW),
        (60, 79.99, READINESS_SHADOW),
        (60, 80.0, READINESS_MANUAL_REVIEW),
    ],
)
def test_readiness_ladder_is_manual_review_only(
    mature_days: int,
    coverage: float | None,
    expected: str,
) -> None:
    assert (
        resolve_decision_quality_readiness(
            mature_decision_day_count=mature_days,
            formal_label_coverage_percent=coverage,
        )
        == expected
    )


def test_evaluation_as_of_requires_an_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        parse_evaluation_as_of("2026-07-14T10:00:00")
    assert parse_evaluation_as_of("2026-07-14T10:00:00+08:00").isoformat() == (
        "2026-07-14T02:00:00+00:00"
    )


def test_insufficient_sample_is_persisted_idempotently_and_redacted() -> None:
    first = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of="2026-07-14T00:00:00Z",
        user_ids=[7],
        window_days=365,
    )
    retry = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of="2026-07-14T00:00:00Z",
        user_ids=[7],
        window_days=365,
    )

    assert first["snapshots"][0]["readiness_status"] == READINESS_INSUFFICIENT
    assert first["snapshots"][0]["snapshot_id"] == retry["snapshots"][0][
        "snapshot_id"
    ]
    rows = list_decision_quality_evaluation_snapshots(user_id=7)
    assert len(rows) == 1

    redacted = read_latest_decision_quality_snapshot(user_id=7)
    assert redacted is not None
    assert redacted["automatic_promotion_allowed"] is False
    serialized = json.dumps(redacted, ensure_ascii=False)
    for forbidden in (
        '"evaluation"',
        '"input_manifest"',
        '"event_records"',
        '"outcome_records"',
        '"raw_claims"',
    ):
        assert forbidden not in serialized

def test_candidate_redaction_uses_nested_allowlists() -> None:
    secret = "RAW-NAV-SECRET-000001"
    redacted = _redacted_candidate_selection(
        {
            "status": "available",
            "case_count": 1,
            "aggregate": {
                "case_count": 1,
                "pit_eligible_case_count": 1,
                "precision_at_k": {
                    "status": "available",
                    "case_count": 1,
                    "macro_average": 1.0,
                    "fund_code": "000001",
                    "raw_evidence": secret,
                },
                "coverage": {"universe_count": 3, "outcome_labels": secret},
                "raw_nav": secret,
            },
            "readiness": {
                "status": "insufficient_data",
                "mature_decision_day_count": 1,
                "case_ids": [secret],
            },
            "evaluations": [{"outcome_labels": secret}],
        }
    )
    serialized = json.dumps(redacted, ensure_ascii=False)
    assert secret not in serialized
    assert "000001" not in serialized
    assert "outcome_labels" not in serialized
    assert "raw_nav" not in serialized


def test_d4_audit_without_post_commit_receipt_remains_formal_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(connection, user_id=41)
    connection.commit()

    snapshot = build_decision_quality_snapshot(
        user_id=41,
        evaluation_as_of="2026-01-03T00:00:00+00:00",
        window_days=365,
        connection=connection,
    )

    candidate = snapshot["evaluation"]["candidate_selection"]
    assert candidate["formal_case_count"] == 1
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["evaluations"][0]["formal_status"] == "receipt_pending"
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_audit_commit_receipt_pending"
    )
    manifest = snapshot["input_manifest"]
    assert manifest["schema_version"] == "decision_quality_input_manifest.v4"
    assert manifest["consumed_input_artifact_count"] == 1
    assert manifest["artifact_receipt_count"] == 0
    assert manifest["provider_receipt_count"] == 0
    assert manifest["input_artifacts"][0]["artifact_id"] == (
        audit_row["artifact_id"]
    )


def test_storage_owned_source_clock_over_300_seconds_is_capture_late_with_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    decision_at = "2026-07-15T08:00:00+00:00"
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-07-15T08:05:01+00:00",
    )
    rows = persist_report_decision_quality_artifacts(
        user_id=61,
        report={
            "id": "physical-source-late",
            "created_at": decision_at,
            "recommendations": [],
            "discovery_facts": {
                "candidate_selection_audit": _formal_candidate_audit(decision_at)
            },
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        # The pre-insert application clock is still inside the window; only the
        # immutable source row proves that live capture actually missed it.
        report_recorded_at="2026-07-15T08:04:59+00:00",
        connection=connection,
    )
    audit_row = rows[0]
    assert audit_row["payload"]["audit_eligible"] is True
    _insert_artifact_receipt(
        connection,
        user_id=61,
        artifact_row=audit_row,
        source_visible_at="2026-07-15T08:05:02+00:00",
    )
    connection.commit()

    snapshot = build_decision_quality_snapshot(
        user_id=61,
        evaluation_as_of="2026-07-15T09:00:00+00:00",
        window_days=365,
        connection=connection,
    )
    candidate = snapshot["evaluation"]["candidate_selection"]
    assert candidate["formal_case_count"] == 1
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["capture_coverage"]["capture_late_count"] == 1
    assert candidate["evaluations"][0]["formal_status"] == "capture_late"
    assert candidate["evaluations"][0]["reason"] == (
        "candidate_selection_capture_late"
    )
    assert candidate["readiness"]["mature_decision_day_count"] == 0
    manifest = snapshot["input_manifest"]
    assert manifest["candidate_capture_status_counts"] == {"capture_late": 1}
    assert manifest["candidate_capture_reason_counts"] == {
        "source_capture_delay_exceeded": 1
    }
    assert manifest["artifact_receipt_count"] == 1
    assert manifest["provider_receipt_count"] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM decision_quality_input_artifacts "
        "WHERE artifact_type = 'candidate_selection_outcome_set'"
    ).fetchone()[0] == 0


def test_invalid_native_audit_and_missing_capture_sentinel_are_denominators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-07-15T08:00:01+00:00",
    )
    decision_at = "2026-07-15T07:59:00+00:00"
    invalid = persist_report_decision_quality_artifacts(
        user_id=62,
        report={
            "id": "invalid-native-capture",
            "created_at": decision_at,
            "discovery_facts": {
                "candidate_selection_audit": {
                    "schema_version": "discovery_candidate_selection_audit.v1",
                    "rows": [],
                }
            },
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T08:00:00+00:00",
        connection=connection,
    )[0]
    missing = persist_report_decision_quality_artifacts(
        user_id=62,
        report={
            "id": "missing-native-capture",
            "created_at": decision_at,
            "discovery_facts": {},
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T08:00:00+00:00",
        connection=connection,
    )[0]
    assert invalid["payload"]["audit_eligible"] is False
    assert missing["payload"]["audit_eligible"] is False
    connection.commit()

    snapshot = build_decision_quality_snapshot(
        user_id=62,
        evaluation_as_of="2026-07-15T09:00:00+00:00",
        window_days=365,
        connection=connection,
    )
    candidate = snapshot["evaluation"]["candidate_selection"]
    assert candidate["formal_case_count"] == 2
    assert candidate["formal_pit_eligible_case_count"] == 2
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["capture_coverage"]["capture_ineligible_count"] == 2
    assert {
        row["formal_status"] for row in candidate["evaluations"]
    } == {"capture_ineligible"}
    assert snapshot["input_manifest"]["candidate_capture_reason_counts"] == {
        "candidate_audit_not_decision_eligible": 1,
        "candidate_selection_audit_missing": 1,
    }
    assert snapshot["input_manifest"]["consumed_input_artifact_count"] == 2


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "audit_eligible", "poison"),
    (
        (
            CANDIDATE_AUDIT_ARTIFACT_TYPE,
            CANDIDATE_AUDIT_ARTIFACT_SCHEMA_VERSION,
            True,
            {
                "audit_eligible": 0,
                "artifact_schema_version": "poisoned.audit.schema",
            },
        ),
        (
            CANDIDATE_OUTCOME_SET_ARTIFACT_TYPE,
            CANDIDATE_OUTCOME_SET_SCHEMA_VERSION,
            True,
            {"audit_eligible": 0},
        ),
        (
            CANDIDATE_CAPTURE_FAILURE_ARTIFACT_TYPE,
            CANDIDATE_CAPTURE_FAILURE_ARTIFACT_SCHEMA_VERSION,
            False,
            {"artifact_schema_version": "poisoned.failure.schema"},
        ),
    ),
)
def test_snapshot_rejects_artifact_index_poison_before_denominator_selection(
    artifact_type: str,
    schema_version: str,
    audit_eligible: bool,
    poison: dict[str, object],
) -> None:
    connection = _snapshot_connection()
    source_user_id = 700
    target_user_id = 701
    put_decision_quality_input_artifact(
        user_id=source_user_id,
        artifact={
            "artifact_type": artifact_type,
            "source_type": "discovery",
            "source_report_id": "poison-source",
            "decision_event_id": None,
            "decision_at": "2026-01-02T06:59:00+00:00",
            "available_at": "2026-01-02T07:00:00+00:00",
            "recorded_at": "2026-01-02T07:00:01+00:00",
            "store_authority": "primary",
            "audit_eligible": audit_eligible,
            "artifact": {"schema_version": schema_version},
        },
        connection=connection,
    )
    _clone_input_artifact_with_poison(
        connection,
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        poison=poison,
    )
    connection.commit()

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="input artifact failed its integrity contract",
    ):
        build_decision_quality_snapshot(
            user_id=target_user_id,
            evaluation_as_of="2026-12-31T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )

    # Tenant-wide validation remains strictly tenant scoped.
    with pytest.raises(DecisionQualitySnapshotContractError):
        # The source row is intentionally only envelope-valid, not a complete
        # native semantic payload; reaching partitioning proves it was decoded.
        build_decision_quality_snapshot(
            user_id=source_user_id,
            evaluation_as_of="2026-12-31T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )


def test_verified_artifact_rows_restore_point_in_time_window_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    cutoff = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    window_start = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    artifact_ids: dict[str, str] = {}

    def persist(
        name: str,
        *,
        decision_at: str | None,
        available_at: str,
        recorded_at: str,
        created_at: str,
    ) -> None:
        monkeypatch.setattr(
            "app.services.decision_repository._utc_now",
            lambda: created_at,
        )
        row = put_decision_quality_input_artifact(
            user_id=702,
            artifact={
                "artifact_type": "claim_audit_wrapper",
                "source_type": "daily",
                "source_report_id": f"window-{name}",
                "decision_event_id": None,
                "decision_at": decision_at,
                "available_at": available_at,
                "recorded_at": recorded_at,
                "store_authority": "primary",
                "audit_eligible": True,
                "artifact": {
                    "schema_version": "window_filter_fixture.v1",
                    "name": name,
                },
            },
            connection=connection,
        )
        artifact_ids[name] = str(row["artifact_id"])

    persist(
        "in_window",
        decision_at="2026-05-20T00:00:00+00:00",
        available_at="2026-05-20T00:00:00+00:00",
        recorded_at="2026-05-20T00:01:00+00:00",
        created_at="2026-05-20T00:02:00+00:00",
    )
    persist(
        "future_recorded_at",
        decision_at="2026-05-20T00:00:00+00:00",
        available_at="2026-05-20T00:00:00+00:00",
        recorded_at="2026-06-11T00:00:00+00:00",
        created_at="2026-06-11T00:01:00+00:00",
    )
    persist(
        "future_created_at",
        decision_at="2026-05-21T00:00:00+00:00",
        available_at="2026-05-21T00:00:00+00:00",
        recorded_at="2026-05-21T00:01:00+00:00",
        created_at="2026-06-11T00:00:00+00:00",
    )
    persist(
        "before_window",
        decision_at="2026-05-01T00:00:00+00:00",
        available_at="2026-05-01T00:00:00+00:00",
        recorded_at="2026-05-01T00:01:00+00:00",
        created_at="2026-05-01T00:02:00+00:00",
    )
    persist(
        "after_window",
        decision_at="2026-06-11T00:00:00+00:00",
        available_at="2026-06-11T00:00:00+00:00",
        recorded_at="2026-06-11T00:01:00+00:00",
        created_at="2026-06-11T00:02:00+00:00",
    )
    persist(
        "null_decision_at",
        decision_at=None,
        available_at="2026-05-01T00:00:00+00:00",
        recorded_at="2026-05-01T00:01:00+00:00",
        created_at="2026-05-01T00:02:00+00:00",
    )

    rows = _fetch_decision_quality_input_artifact_rows(
        user_id=702,
        window_start=window_start,
        cutoff=cutoff,
        connection=connection,
    )

    assert {row["artifact_id"] for row in rows} == {
        artifact_ids["in_window"],
        artifact_ids["null_decision_at"],
    }


def test_capture_failure_then_native_audit_for_same_report_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-07-15T08:00:01+00:00",
    )
    decision_at = "2026-07-15T08:00:00+00:00"
    base = {
        "id": "retry-cannot-erase-capture-failure",
        "created_at": decision_at,
        "discovery_facts": {},
    }
    persist_report_decision_quality_artifacts(
        user_id=63,
        report=base,
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T08:00:00.500000+00:00",
        connection=connection,
    )
    persist_report_decision_quality_artifacts(
        user_id=63,
        report={
            **base,
            "discovery_facts": {
                "candidate_selection_audit": _formal_candidate_audit(decision_at)
            },
        },
        saved_events=[],
        source_type="discovery",
        store_authority="primary",
        report_recorded_at="2026-07-15T08:00:00.500000+00:00",
        connection=connection,
    )
    connection.commit()
    assert connection.execute(
        "SELECT COUNT(*) FROM decision_quality_input_artifacts WHERE userId = 63"
    ).fetchone()[0] == 2
    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="native candidate capture identity is duplicated",
    ):
        build_decision_quality_snapshot(
            user_id=63,
            evaluation_as_of="2026-07-15T09:00:00+00:00",
            window_days=365,
            connection=connection,
        )


def test_sixty_historical_decision_dates_written_today_cannot_unlock_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-07-15T08:00:01+00:00",
    )
    for index in range(60):
        # The first declared decision is 193 days old at the storage receipt;
        # sixty distinct historical declarations must still count as zero live
        # cohort days.
        decision = datetime(2026, 1, 3, 7, 0, tzinfo=timezone.utc) + timedelta(
            days=index
        )
        decision_at = decision.isoformat()
        persist_report_decision_quality_artifacts(
            user_id=64,
            report={
                "id": f"historical-capture-{index:02d}",
                "created_at": decision_at,
                "discovery_facts": {
                    "candidate_selection_audit": _formal_candidate_audit(
                        decision_at
                    )
                },
            },
            saved_events=[],
            source_type="discovery",
            store_authority="primary",
            report_recorded_at="2026-07-15T08:00:00+00:00",
            connection=connection,
        )
    connection.commit()

    snapshot = build_decision_quality_snapshot(
        user_id=64,
        evaluation_as_of="2026-07-15T09:00:00+00:00",
        window_days=365,
        connection=connection,
    )
    candidate = snapshot["evaluation"]["candidate_selection"]
    assert candidate["formal_case_count"] == 60
    assert candidate["capture_coverage"]["capture_late_count"] == 60
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["readiness"]["status"] == "insufficient_data"
    assert candidate["readiness"]["mature_decision_day_count"] == 0
    assert candidate["readiness"]["declared_mature_decision_day_count"] == 0
    assert snapshot["input_manifest"]["candidate_capture_status_counts"] == {
        "capture_late": 60
    }


def test_artifact_receipt_after_cutoff_is_pending_without_lookahead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "snapshot-receipt-cutoff.db"
    initial = sqlite3.connect(path)
    initial.row_factory = sqlite3.Row
    run_migrations(initial)
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(initial, user_id=42)
    initial.commit()
    initial.close()

    source_visible_at = "2026-01-02T07:00:02+00:00"
    normalized_receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": 42,
            "artifact_id": audit_row["artifact_id"],
            "artifact_type": audit_row["payload"]["artifact_type"],
            "artifact_content_hash": audit_row["content_hash"],
            "source_row_created_at": audit_row["created_at"],
            "source_visible_at": source_visible_at,
            "store_authority": "primary",
        }
    )
    insert_connection = sqlite3.connect(path)
    insert_connection.execute(
        "INSERT INTO decision_quality_artifact_receipts "
        "(userId, receipt_id, schema_version, receipt_policy, artifact_id, "
        "artifact_type, artifact_content_hash, source_row_created_at, "
        "source_visible_at, store_authority, content_hash, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            42,
            normalized_receipt["receipt_id"],
            normalized_receipt["schema_version"],
            normalized_receipt["receipt_policy"],
            normalized_receipt["artifact_id"],
            normalized_receipt["artifact_type"],
            normalized_receipt["artifact_content_hash"],
            normalized_receipt["source_row_created_at"],
            normalized_receipt["source_visible_at"],
            normalized_receipt["store_authority"],
            normalized_receipt["content_hash"],
            canonical_json(normalized_receipt),
            source_visible_at,
        ),
    )
    insert_connection.commit()
    insert_connection.close()

    def factory() -> DbConnection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return DbConnection(connection, "sqlite")

    with factory() as connection:
        before = build_decision_quality_snapshot(
            user_id=42,
            evaluation_as_of="2026-01-02T07:00:01.500000+00:00",
            window_days=365,
            connection=connection,
        )
    before_case = before["evaluation"]["candidate_selection"]["evaluations"][0]
    assert before_case["reason"] == (
        "candidate_selection_audit_commit_receipt_pending"
    )
    assert before["input_manifest"]["artifact_receipt_count"] == 0

    with factory() as connection:
        after = build_decision_quality_snapshot(
            user_id=42,
            evaluation_as_of="2026-01-03T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )
    after_case = after["evaluation"]["candidate_selection"]["evaluations"][0]
    assert after_case["reason"] == (
        "candidate_selection_outcome_artifact_absent"
    )
    manifest = after["input_manifest"]
    assert manifest["artifact_receipt_count"] == 1
    assert manifest["artifact_receipts"][0]["artifact_id"] == (
        audit_row["artifact_id"]
    )
    assert manifest["artifact_receipts"][0]["source_visible_at"] == (
        "2026-01-02T07:00:02+00:00"
    )


def test_late_reconciled_audit_is_a_stable_coverage_gap_not_tenant_poison(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "snapshot-late-audit-receipt.db"

    def factory() -> DbConnection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return DbConnection(connection, "sqlite")

    initial = sqlite3.connect(path)
    initial.row_factory = sqlite3.Row
    run_migrations(initial)
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(initial, user_id=46)
    initial.commit()
    initial.close()

    # Simulate the crash-recovery path: reconciliation first observes the
    # committed audit ten minutes after its source-row storage receipt.
    monkeypatch.setattr(
        "app.services.decision_repository._decision_quality_database_utc_now",
        lambda _connection: datetime.fromisoformat(
            "2026-01-02T07:10:01+00:00"
        ),
    )
    reconciled = reconcile_decision_quality_artifact_receipts(
        user_id=46,
        connection_factory=factory,
    )
    assert reconciled == {
        "status": "completed",
        "scanned_count": 1,
        "finalized_count": 1,
        "failed_count": 0,
        "finalized_artifact_ids": [audit_row["artifact_id"]],
        "failures": [],
    }

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("Settings", (), {"uses_mysql": False})(),
    )

    def provider_fetch_forbidden(*_args, **_kwargs):
        pytest.fail("a late audit must not pull calendar or NAV providers")

    settlement_runs = [
        settle_candidate_selection_outcomes(
            user_ids=[46],
            as_of_date="2026-01-24",
            fetch_calendar=provider_fetch_forbidden,
            fetch_nav=provider_fetch_forbidden,
            observed_at="2026-01-24T08:00:00+00:00",
            connection_factory=factory,
        )
        for _ in range(2)
    ]
    for result in settlement_runs:
        assert result["status"] == "completed_with_pending"
        assert result["formal_audit_count"] == 1
        assert result["audit_commit_receipt_count"] == 1
        assert result["late_audit_commit_receipt_count"] == 1
        assert result["pending_case_count"] == 1
        assert result["pending_reasons"] == [
            {"reason": "candidate_audit_commit_receipt_late", "count": 1}
        ]
        assert result["failed_user_ids"] == []
        assert result["due_case_count"] == 0
        assert result["provider_receipt_count"] == 0
        assert result["outcome_commit_receipt_count"] == 0

    captured_cases: list[dict] = []
    real_evaluator = snapshot_service.evaluate_decision_quality

    def capture_cases(*args, **kwargs):
        captured_cases.extend(
            deepcopy(kwargs.get("candidate_selection_cases") or [])
        )
        return real_evaluator(*args, **kwargs)

    monkeypatch.setattr(
        snapshot_service,
        "evaluate_decision_quality",
        capture_cases,
    )
    snapshots: list[dict] = []
    for _ in range(2):
        with factory() as connection:
            snapshots.append(
                build_decision_quality_snapshot(
                    user_id=46,
                    evaluation_as_of="2026-01-03T00:00:00+00:00",
                    window_days=365,
                    connection=connection,
                )
            )

    assert len(captured_cases) == 2
    for case in captured_cases:
        assert case["audit_commit_receipt_status"] == "late"
        assert case["outcome_commit_receipt_status"] == "absent"
        assert case["outcome_labels"] == {}
        assert case["provider_receipt_refs"] == []
        assert case["provider_receipt_count"] == 0
    for snapshot in snapshots:
        candidate = snapshot["evaluation"]["candidate_selection"]
        assert candidate["formal_case_count"] == 1
        assert candidate["formal_invalid_case_count"] == 0
        assert candidate["formal_metric_available_case_count"] == 0
        assert candidate["evaluations"][0]["formal_status"] == (
            "receipt_policy_gap"
        )
        assert candidate["evaluations"][0]["reason"] == (
            "candidate_selection_audit_commit_receipt_late"
        )
        manifest = snapshot["input_manifest"]
        assert manifest["consumed_input_artifact_count"] == 1
        assert manifest["artifact_receipt_count"] == 1
        assert manifest["provider_receipt_count"] == 0
        assert manifest["artifact_receipts"][0]["artifact_id"] == (
            audit_row["artifact_id"]
        )
        assert manifest["artifact_receipts"][0]["source_visible_at"] == (
            "2026-01-02T07:10:01+00:00"
        )
    assert snapshots[0] == snapshots[1]

    with factory() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM decision_quality_provider_receipts"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM decision_quality_input_artifacts "
            "WHERE artifact_type = 'candidate_selection_outcome_set'"
        ).fetchone()[0] == 0

    # A late classification never weakens immutable binding.  Corrupting the
    # stored index (after deliberately disabling its test-only guard) moves the
    # same tenant back to the fatal integrity path, not another pending result.
    corrupt = sqlite3.connect(path)
    corrupt.execute(
        "DROP TRIGGER decision_quality_artifact_receipts_no_update"
    )
    corrupt.execute(
        "UPDATE decision_quality_artifact_receipts "
        "SET artifact_content_hash = ? WHERE artifact_id = ?",
        ("f" * 64, audit_row["artifact_id"]),
    )
    corrupt.commit()
    corrupt.close()

    failed = settle_candidate_selection_outcomes(
        user_ids=[46],
        as_of_date="2026-01-24",
        fetch_calendar=provider_fetch_forbidden,
        fetch_nav=provider_fetch_forbidden,
        observed_at="2026-01-24T08:00:00+00:00",
        connection_factory=factory,
    )
    assert failed["status"] == "completed_with_failures"
    assert failed["failed_user_ids"] == [46]
    assert failed["pending_reasons"] == []
    with factory() as connection:
        with pytest.raises(
            DecisionQualitySnapshotContractError,
            match="stored artifact commit receipt failed",
        ):
            build_decision_quality_snapshot(
                user_id=46,
                evaluation_as_of="2026-01-03T00:00:00+00:00",
                window_days=365,
                connection=connection,
            )


@pytest.mark.parametrize("tamper", ["tenant", "content_hash", "source_clock"])
def test_artifact_receipt_binding_rejects_cross_tenant_and_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    connection = _snapshot_connection()
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(connection, user_id=44)
    receipt_user_id = 45 if tamper == "tenant" else 44
    receipt_payload = normalize_decision_quality_artifact_receipt(
        {
            "user_id": receipt_user_id,
            "artifact_id": audit_row["artifact_id"],
            "artifact_type": audit_row["payload"]["artifact_type"],
            "artifact_content_hash": (
                "f" * 64
                if tamper == "content_hash"
                else audit_row["content_hash"]
            ),
            "source_row_created_at": (
                "2026-01-02T07:00:00+00:00"
                if tamper == "source_clock"
                else audit_row["created_at"]
            ),
            "source_visible_at": "2026-01-02T07:00:02+00:00",
            "store_authority": "primary",
        }
    )
    receipt_row = {
        "userId": receipt_user_id,
        "payload": receipt_payload,
        "created_at": receipt_payload["source_visible_at"],
    }

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="conflicts with its source artifact",
    ):
        _validate_artifact_receipt_binding(
            receipt_row=receipt_row,
            artifact_row=audit_row,
            evaluation_as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )


def test_cross_tenant_receipt_failure_does_not_rollback_healthy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "snapshot-tenant-isolation.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    _persist_formal_candidate_audit(connection, user_id=51)
    bad_source = _persist_formal_candidate_audit(connection, user_id=52)
    bad_receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": 99,
            "artifact_id": bad_source["artifact_id"],
            "artifact_type": bad_source["payload"]["artifact_type"],
            "artifact_content_hash": bad_source["content_hash"],
            "source_row_created_at": bad_source["created_at"],
            "source_visible_at": "2026-01-02T07:00:02+00:00",
            "store_authority": "primary",
        }
    )
    connection.execute(
        "INSERT INTO decision_quality_artifact_receipts "
        "(userId, receipt_id, schema_version, receipt_policy, artifact_id, "
        "artifact_type, artifact_content_hash, source_row_created_at, "
        "source_visible_at, store_authority, content_hash, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            52,
            bad_receipt["receipt_id"],
            bad_receipt["schema_version"],
            bad_receipt["receipt_policy"],
            bad_receipt["artifact_id"],
            bad_receipt["artifact_type"],
            bad_receipt["artifact_content_hash"],
            bad_receipt["source_row_created_at"],
            bad_receipt["source_visible_at"],
            bad_receipt["store_authority"],
            bad_receipt["content_hash"],
            canonical_json(bad_receipt),
            bad_receipt["source_visible_at"],
        ),
    )
    connection.commit()
    connection.close()

    def factory() -> DbConnection:
        value = sqlite3.connect(path)
        value.row_factory = sqlite3.Row
        return DbConnection(value, "sqlite")

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="isolated user ids: 52",
    ):
        evaluate_and_persist_decision_quality_snapshots(
            evaluation_as_of="2026-01-03T00:00:00+00:00",
            user_ids=[51, 52],
            connection_factory=factory,
        )
    with factory() as stored_connection:
        healthy = list_decision_quality_evaluation_snapshots(
            user_id=51,
            connection=stored_connection,
        )
        failed = list_decision_quality_evaluation_snapshots(
            user_id=52,
            connection=stored_connection,
        )
    assert len(healthy) == 1
    assert failed == []


def test_provider_receipt_recomputes_normalization_from_adapter_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    parsed = {
        "data": [
            {"date": "2026-01-02", "nav": 1.0, "daily_growth": 0.1}
        ]
    }
    policy_material = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=int(
            datetime.fromisoformat("2026-01-02T08:00:00+00:00").timestamp()
            // 3600
        ),
    )
    # This origin is internally signed, but it dishonestly claims that the
    # adapter payload normalized to a different empty data set.
    origin = build_provider_origin_receipt(
        provider_id="akshare.fund_open_fund_info_em",
        operation="fund_open_fund_info_em",
        request_parameters={
            "fund_code": "100001",
            "trading_days": 90,
            "indicator": "单位净值走势",
        },
        request_started_at="2026-01-02T08:00:00+00:00",
        response_completed_at="2026-01-02T08:00:01+00:00",
        response_status="success",
        adapter_contract_version=str(policy_material["adapter_contract_version"]),
        adapter_script=str(policy_material["adapter_script"]),
        library_name="akshare",
        library_version="fixture",
        python_version="3.11",
        cache_policy=str(policy_material["cache_policy"]),
        cache_key_material=policy_material["cache_key_material"],
        stdout_bytes=json.dumps(parsed).encode(),
        parsed_payload=parsed,
        normalized_payload={"data": []},
        upstream_raw_unavailable_reason="adapter boundary only",
    )
    repository_receipt = normalize_decision_quality_provider_receipt(
        {
            "provider": origin["provider_id"],
            "operation": origin["operation"],
            "capture_mode": origin["capture_mode"],
            "request_hash": origin["request"]["request_hash"],
            "adapter_output": origin,
            "normalized_payload_hash": origin["response"][
                "normalized_payload_hash"
            ],
            "origin_fetched_at": origin["cache"]["origin_fetched_at"],
            "completed_at": origin["response"]["completed_at"],
        }
    )
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T08:00:02+00:00",
    )
    stored = put_decision_quality_provider_receipt(
        receipt=repository_receipt,
        connection=connection,
    )

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="storage binding conflicts",
    ):
        _fetch_decision_quality_provider_receipt_rows(
            receipt_ids={stored["receipt_id"]},
            cutoff=datetime(2026, 1, 3, tzinfo=timezone.utc),
            connection=connection,
        )


def test_nav_provider_receipt_cannot_be_substituted_across_fund_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _snapshot_connection()
    parsed = {
        "data": [
            {"date": "2026-01-02", "nav": 1.0, "daily_growth": 0.1}
        ]
    }
    policy_material = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=int(
            datetime.fromisoformat("2026-01-02T08:00:00+00:00").timestamp()
            // 3600
        ),
    )
    origin = build_provider_origin_receipt(
        provider_id="akshare.fund_open_fund_info_em",
        operation="fund_open_fund_info_em",
        request_parameters={
            "fund_code": "100001",
            "trading_days": 90,
            "indicator": "单位净值走势",
        },
        request_started_at="2026-01-02T08:00:00+00:00",
        response_completed_at="2026-01-02T08:00:01+00:00",
        response_status="success",
        adapter_contract_version=str(policy_material["adapter_contract_version"]),
        adapter_script=str(policy_material["adapter_script"]),
        library_name="akshare",
        library_version="fixture",
        python_version="3.11",
        cache_policy=str(policy_material["cache_policy"]),
        cache_key_material=policy_material["cache_key_material"],
        stdout_bytes=json.dumps(parsed).encode(),
        parsed_payload=parsed,
        normalized_payload=parsed,
        upstream_raw_unavailable_reason="adapter boundary only",
    )
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T08:00:02+00:00",
    )
    stored = put_decision_quality_provider_receipt(
        receipt={
            "provider": origin["provider_id"],
            "operation": origin["operation"],
            "capture_mode": origin["capture_mode"],
            "request_hash": origin["request"]["request_hash"],
            "adapter_output": origin,
            "normalized_payload_hash": origin["response"][
                "normalized_payload_hash"
            ],
            "origin_fetched_at": origin["cache"]["origin_fetched_at"],
            "completed_at": origin["response"]["completed_at"],
        },
        connection=connection,
    )
    payload = stored["payload"]
    policy = verify_candidate_provider_adapter_policy(origin)
    ref = {
        "receipt_id": payload["receipt_id"],
        "content_hash": payload["content_hash"],
        "provider": payload["provider"],
        "operation": payload["operation"],
        "capture_mode": payload["capture_mode"],
        "request_hash": payload["request_hash"],
        "adapter_output_sha256": payload["adapter_output_sha256"],
        "normalized_payload_hash": payload["normalized_payload_hash"],
        "origin_fetched_at": payload["origin_fetched_at"],
        "completed_at": payload["completed_at"],
        "origin_receipt_hash": origin["origin_receipt_hash"],
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

    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="conflicts with stored origin",
    ):
        _validate_provider_receipt_ref_binding(
            ref=ref,
            receipt_row=stored,
            expected_binding=("nav", "100002"),
            outcome_storage_created_at=datetime(
                2026, 1, 2, 9, tzinfo=timezone.utc
            ),
            evaluation_as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )


def test_outcome_labels_require_visible_commit_receipt_and_manifest_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "snapshot-source-verified.db"

    def factory() -> DbConnection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return DbConnection(connection, "sqlite")

    initial = sqlite3.connect(path)
    initial.row_factory = sqlite3.Row
    run_migrations(initial)
    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-02T07:00:01+00:00",
    )
    audit_row = _persist_formal_candidate_audit(initial, user_id=43)
    initial.commit()
    initial.close()
    database_clock = {"value": datetime.fromisoformat("2026-01-02T07:00:02+00:00")}
    monkeypatch.setattr(
        "app.services.decision_repository._decision_quality_database_utc_now",
        lambda _connection: database_clock["value"],
    )
    finalize_decision_quality_artifact_receipt(
        user_id=43,
        artifact_id=audit_row["artifact_id"],
        connection_factory=factory,
    )

    trade_dates = [
        (date(2026, 1, 3) + timedelta(days=index)).isoformat()
        for index in range(22)
    ]
    provider_completed = "2026-01-24T08:00:00+00:00"
    provider_served = "2026-01-24T08:01:00+00:00"
    calendar_read = _quality_provider_read(
        provider="akshare.tool_trade_date_hist_sina",
        operation="tool_trade_date_hist_sina",
        parameters={},
        parsed_payload=trade_dates,
        normalized_payload={"dates": trade_dates},
        completed_at=provider_completed,
        served_at=provider_served,
    )

    def nav_read(code: str, *, trading_days: int) -> DecisionQualityProviderRead:
        rows = [
            {"date": day, "nav": 1.0 + index / 100, "daily_growth": 0.1}
            for index, day in enumerate(trade_dates)
        ]
        payload = {"data": rows}
        return _quality_provider_read(
            provider="akshare.fund_open_fund_info_em",
            operation="fund_open_fund_info_em",
            parameters={
                "fund_code": code,
                "trading_days": trading_days,
                "indicator": "单位净值走势",
            },
            parsed_payload=payload,
            normalized_payload=payload,
            completed_at=provider_completed,
            served_at=provider_served,
        )

    monkeypatch.setattr(
        "app.services.decision_repository._utc_now",
        lambda: "2026-01-24T08:02:30+00:00",
    )
    database_clock["value"] = datetime.fromisoformat(
        "2026-01-24T08:03:00+00:00"
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("Settings", (), {"uses_mysql": False})(),
    )
    settled = settle_candidate_selection_outcomes(
        user_ids=[43],
        as_of_date="2026-01-24",
        max_cases=5,
        fetch_calendar=lambda: calendar_read,
        fetch_nav=nav_read,
        observed_at="2026-01-24T08:02:00+00:00",
        connection_factory=factory,
    )
    assert settled["persisted_case_count"] == 1
    assert settled["provider_receipt_count"] == 4
    assert settled["outcome_commit_receipt_count"] == 1

    with factory() as connection:
        pending = build_decision_quality_snapshot(
            user_id=43,
            evaluation_as_of="2026-01-24T08:02:45+00:00",
            window_days=365,
            connection=connection,
        )
    pending_evaluation = pending["evaluation"]["candidate_selection"]
    assert pending_evaluation["formal_case_count"] == 1
    assert pending_evaluation["formal_metric_available_case_count"] == 0
    assert pending_evaluation["evaluations"][0]["reason"] == (
        "candidate_selection_outcome_commit_receipt_pending"
    )
    assert pending["input_manifest"]["artifact_receipt_count"] == 1
    assert pending["input_manifest"]["provider_receipt_count"] == 4

    with factory() as connection:
        verified = build_decision_quality_snapshot(
            user_id=43,
            evaluation_as_of="2026-01-25T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )
    candidate = verified["evaluation"]["candidate_selection"]
    assert candidate["formal_case_count"] == 1
    assert candidate["formal_metric_available_case_count"] == 1
    assert candidate["evaluations"][0]["formal_status"] == "source_verified"
    assert len(candidate["stratified"]) == 1
    provider_stratum = candidate["stratified"][0]["dimensions"][
        "provider_adapter_stratum"
    ]
    assert {row["adapter_policy_id"] for row in provider_stratum} == {
        "candidate_provider_adapter_policy.akshare_trade_calendar.v1",
        "candidate_provider_adapter_policy.akshare_fund_nav.v1",
    }
    manifest = verified["input_manifest"]
    assert manifest["artifact_receipt_count"] == 2
    assert manifest["provider_receipt_count"] == 4
    assert manifest["provider_receipts"] == sorted(
        manifest["provider_receipts"],
        key=lambda row: (row["provider"], row["operation"], row["receipt_id"]),
    )
    assert all(
        row["adapter_policy_hash"]
        and row["adapter_contract_version"]
        and row["adapter_script_sha256"]
        and row["adapter_policy_script_sha256"]
        and row["adapter_library_version"]
        and row["adapter_python_version"]
        for row in manifest["provider_receipts"]
    )
    serialized = json.dumps(verified, ensure_ascii=False)
    for forbidden in (
        '"stdout_base64"',
        '"parsed_payload"',
        '"adapter_output":',
    ):
        assert forbidden not in serialized

    corrupt = sqlite3.connect(path)
    corrupt.execute(
        "DROP TRIGGER decision_quality_provider_receipts_no_delete"
    )
    missing_receipt_id = corrupt.execute(
        "SELECT receipt_id FROM decision_quality_provider_receipts "
        "ORDER BY receipt_id LIMIT 1"
    ).fetchone()[0]
    corrupt.execute(
        "DELETE FROM decision_quality_provider_receipts WHERE receipt_id = ?",
        (missing_receipt_id,),
    )
    corrupt.commit()
    corrupt.close()
    with factory() as connection:
        with pytest.raises(
            DecisionQualitySnapshotContractError,
            match="missing provider receipt",
        ):
            build_decision_quality_snapshot(
                user_id=43,
                evaluation_as_of="2026-01-25T00:00:00+00:00",
                window_days=365,
                connection=connection,
            )


def test_snapshot_rejects_self_consistent_nav_projection_detached_from_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "snapshot-detached-nav-projection.db"
    factory = _settle_source_verified_fixture(
        monkeypatch,
        path,
        user_id=63,
        universe_codes=("100001", "100002", "100003"),
        final_codes=("100001", "100002", "100003"),
    )
    corrupt = sqlite3.connect(path)
    corrupt.row_factory = sqlite3.Row
    source_row = corrupt.execute(
        "SELECT * FROM decision_quality_input_artifacts "
        "WHERE userId = 63 AND artifact_type = "
        "'candidate_selection_outcome_set'"
    ).fetchone()
    assert source_row is not None
    old_envelope = json.loads(source_row["payload"])
    detached = deepcopy(old_envelope["artifact"])
    code = "100001"
    label = detached["outcome_labels"][code]
    forged_rows = deepcopy(label["evidence"]["observations"])
    for row in forged_rows:
        row["daily_growth"] = 2.0
    calendar_dates = detached["calendar"]["dates"]
    entry_index = calendar_dates.index(detached["entry_date"])
    exit_index = calendar_dates.index(detached["exit_date"])
    evidence, reason = outcome_service._return_evidence(
        code,
        {"data": forged_rows},
        entry_date=detached["entry_date"],
        exit_date=detached["exit_date"],
        expected_dates=calendar_dates[entry_index : exit_index + 1],
        horizon=int(detached["horizon_trading_days"]),
        minimum_daily_growth_coverage=0.0,
        minimum_observation_ratio=0.0,
        provider_normalized_payload_hash=detached["provider_receipt_refs"][
            "nav_by_code"
        ][code]["normalized_payload_hash"],
    )
    assert reason is None and evidence is not None
    label["evidence"] = evidence
    label["return_percent"] = evidence["return_percent"]
    label["binary_relevance"] = evidence["return_percent"] > 0
    label["source_ref"]["content_hash"] = evidence["evidence_hash"]
    label["source_ref"]["normalized_payload_projection_hash"] = evidence[
        "normalized_payload_projection_hash"
    ]
    returns = {
        fund_code: float(value["evidence"]["return_percent"])
        for fund_code, value in detached["outcome_labels"].items()
    }
    relevance = outcome_service._cross_sectional_relevance(returns)
    for fund_code, value in detached["outcome_labels"].items():
        value["relevance"] = relevance[fund_code]
        value["label_hash"] = canonical_hash(
            {
                key: item
                for key, item in value.items()
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
                fund_code: outcome_service._label_semantic_material(value)
                for fund_code, value in sorted(
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
    envelope_input = dict(old_envelope)
    envelope_input.pop("artifact_id")
    envelope_input.pop("content_hash")
    envelope_input["artifact"] = detached
    forged_envelope = normalize_decision_quality_input_artifact(
        envelope_input
    )
    receipt_row = corrupt.execute(
        "SELECT * FROM decision_quality_artifact_receipts "
        "WHERE userId = 63 AND artifact_id = ?",
        (old_envelope["artifact_id"],),
    ).fetchone()
    assert receipt_row is not None
    old_receipt = json.loads(receipt_row["payload"])
    forged_receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": 63,
            "artifact_id": forged_envelope["artifact_id"],
            "artifact_type": forged_envelope["artifact_type"],
            "artifact_content_hash": forged_envelope["content_hash"],
            "source_row_created_at": old_receipt["source_row_created_at"],
            "source_visible_at": old_receipt["source_visible_at"],
            "store_authority": "primary",
        }
    )
    corrupt.execute("DROP TRIGGER decision_quality_artifacts_no_update")
    corrupt.execute(
        "DROP TRIGGER decision_quality_artifact_receipts_no_update"
    )
    corrupt.execute(
        "UPDATE decision_quality_input_artifacts "
        "SET artifact_id = ?, content_hash = ?, payload = ? "
        "WHERE userId = 63 AND artifact_id = ?",
        (
            forged_envelope["artifact_id"],
            forged_envelope["content_hash"],
            canonical_json(forged_envelope),
            old_envelope["artifact_id"],
        ),
    )
    corrupt.execute(
        "UPDATE decision_quality_artifact_receipts "
        "SET artifact_id = ?, receipt_id = ?, artifact_content_hash = ?, "
        "content_hash = ?, payload = ? "
        "WHERE userId = 63 AND artifact_id = ?",
        (
            forged_receipt["artifact_id"],
            forged_receipt["receipt_id"],
            forged_receipt["artifact_content_hash"],
            forged_receipt["content_hash"],
            canonical_json(forged_receipt),
            old_receipt["artifact_id"],
        ),
    )
    corrupt.commit()
    corrupt.close()

    with factory() as connection, pytest.raises(
        DecisionQualitySnapshotContractError,
        match="projection conflicts with stored provider origin",
    ):
        build_decision_quality_snapshot(
            user_id=63,
            evaluation_as_of="2026-01-25T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )


def _assert_source_verified_selected_below_k(
    snapshot: dict,
    *,
    universe_count: int,
    selected_count: int,
) -> None:
    candidate = snapshot["evaluation"]["candidate_selection"]
    assert candidate["status"] == "unavailable"
    assert candidate["formal_case_count"] == 1
    assert candidate["formal_pit_eligible_case_count"] == 1
    assert candidate["formal_metric_available_case_count"] == 0
    assert candidate["formal_fully_available_case_count"] == 0
    assert candidate["readiness"]["status"] != "eligible_for_human_review"
    assert candidate["automatic_promotion_allowed"] is False
    row = candidate["evaluations"][0]
    assert row["formal_status"] == "source_verified"
    assert row["reason"] == "outcome_labels_incomplete"
    evaluation = row["evaluation"]
    assert evaluation["selected_count"] == selected_count
    assert evaluation["coverage"]["mature_label_count"] == universe_count
    assert evaluation["coverage"]["universe_count"] == universe_count
    assert evaluation["coverage"]["top_k_count"] == 3
    for metric_name in ("precision_at_k", "ndcg_at_k", "regret_at_k"):
        metric = evaluation[metric_name]
        assert metric["status"] == "unavailable"
        assert metric["reason"] == "selected_count_below_k"
        assert metric["selected_count"] == selected_count
        assert metric["required_k"] == 3
    assert snapshot["input_manifest"]["provider_receipt_count"] == (
        universe_count + 1
    )
    assert snapshot["automatic_promotion_allowed"] is False


def test_source_verified_universe_below_k_keeps_labels_and_denominator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    universe = ("100001", "100002")
    factory = _settle_source_verified_fixture(
        monkeypatch,
        tmp_path / "snapshot-universe-below-k.db",
        user_id=61,
        universe_codes=universe,
        final_codes=universe,
    )
    with factory() as connection:
        snapshot = build_decision_quality_snapshot(
            user_id=61,
            evaluation_as_of="2026-01-25T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )
    _assert_source_verified_selected_below_k(
        snapshot,
        universe_count=2,
        selected_count=2,
    )


def test_source_verified_final_selection_below_k_keeps_full_universe_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    factory = _settle_source_verified_fixture(
        monkeypatch,
        tmp_path / "snapshot-final-below-k.db",
        user_id=62,
        universe_codes=("100001", "100002", "100003"),
        final_codes=("100001", "100002"),
    )
    with factory() as connection:
        snapshot = build_decision_quality_snapshot(
            user_id=62,
            evaluation_as_of="2026-01-25T00:00:00+00:00",
            window_days=365,
            connection=connection,
        )
    _assert_source_verified_selected_below_k(
        snapshot,
        universe_count=3,
        selected_count=2,
    )


def test_other_source_verified_unavailable_reason_remains_fail_closed() -> None:
    metrics = {
        name: {
            "status": "unavailable",
            "reason": "selected_count_below_k",
            "selected_count": 2,
            "required_k": 3,
        }
        for name in ("precision_at_k", "ndcg_at_k", "regret_at_k")
    }
    row = {
        "status": "unavailable",
        "formal_status": "source_verified",
        "reason": "outcome_labels_incomplete",
        "evaluation": {
            "status": "unavailable",
            "reason": "outcome_labels_incomplete",
            "k": 3,
            "selected_count": 2,
            "coverage": {
                "mature_label_count": 3,
                "universe_count": 3,
                "top_k_count": 3,
            },
            **metrics,
        },
    }
    assert _source_verified_selected_count_below_k(row)
    tampered = deepcopy(row)
    tampered["reason"] = "candidate_selection_provider_receipt_time_invalid"
    tampered["evaluation"]["reason"] = tampered["reason"]
    assert not _source_verified_selected_count_below_k(tampered)
    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="rejected stored candidate evidence",
    ):
        _raise_for_evaluation_contract_failures(
            {"candidate_selection": {"evaluations": [tampered]}},
            events=[],
        )


def test_expected_pending_outcomes_do_not_fail_the_daily_quality_snapshot() -> None:
    evaluation = {
        "input_audit": {
            "outcome_exclusions": [
                {
                    "reason": "outcome_observation_not_terminal_mature",
                    "count": 24,
                }
            ],
            "shadow_label_exclusions": [],
        }
    }

    _raise_for_evaluation_contract_failures(evaluation, events=[])

    tampered = deepcopy(evaluation)
    tampered["input_audit"]["outcome_exclusions"][0]["reason"] = (
        "outcome_observation_payload_hash_mismatch"
    )
    with pytest.raises(
        DecisionQualitySnapshotContractError,
        match="outcome_exclusions",
    ):
        _raise_for_evaluation_contract_failures(tampered, events=[])


def test_decision_time_candidate_audit_is_manifested_but_not_used_as_a_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Freeze the storage receipt inside the requested PIT cutoff.  Without this
    # explicit clock the test changes meaning after its fixture date passes.
    with monkeypatch.context() as clock:
        clock.setattr(
            "app.services.decision_repository._utc_now",
            lambda: "2026-07-01T02:00:03+00:00",
        )
        old_audit = put_decision_quality_input_artifact(
        user_id=9,
        artifact={
            "artifact_type": "candidate_selection_audit",
            "artifact_schema_version": "decision_quality_candidate_audit_artifact.v3",
            "source_type": "discovery",
            "source_report_id": "report-1",
            "decision_event_id": None,
            "decision_at": "2026-07-01T02:00:00+00:00",
            "available_at": "2026-07-01T02:00:01+00:00",
            "recorded_at": "2026-07-01T02:00:01+00:00",
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": {
                "schema_version": "decision_quality_candidate_audit_artifact.v3",
                "audit": {"schema_version": "discovery_candidate_selection_audit.v2"},
                "label_plan": {"schema_version": "candidate_label_plan.v2"},
            },
        },
    )
        put_decision_quality_input_artifact(
        user_id=9,
        artifact={
            "artifact_type": "candidate_selection_outcome_set",
            "artifact_schema_version": "decision_quality_candidate_outcome_set.v2",
            "source_type": "discovery",
            "source_report_id": "report-1",
            "decision_event_id": None,
            "decision_at": "2026-07-01T02:00:00+00:00",
            "available_at": "2026-07-01T02:00:02+00:00",
            "recorded_at": "2026-07-01T02:00:02+00:00",
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": {
                "schema_version": "decision_quality_candidate_outcome_set.v2",
                "audit_artifact_id": old_audit["artifact_id"],
            },
        },
    )

    evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of="2026-07-14T23:00:00Z",
        user_ids=[9],
    )
    snapshot = list_decision_quality_evaluation_snapshots(user_id=9, limit=1)[0][
        "payload"
    ]

    assert snapshot["evaluation"]["candidate_selection"]["status"] == "unavailable"
    assert snapshot["input_manifest"]["consumed_input_artifact_count"] == 0
    assert snapshot["input_manifest"]["ignored_artifact_count"] == 2
    ignored = snapshot["input_manifest"]["ignored_input_artifacts"]
    assert len(ignored) == 2
    assert all(row["artifact_id"].startswith("dqa_") for row in ignored)
    assert all(len(row["content_hash"]) == 64 for row in ignored)
    assert {row["recorded_at"] for row in ignored} == {
        "2026-07-01T02:00:01+00:00",
        "2026-07-01T02:00:02+00:00",
    }
    assert all(row["created_at"] >= row["recorded_at"] for row in ignored)


def test_direct_self_declared_formal_candidate_case_is_never_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as clock:
        clock.setattr(
            "app.services.decision_repository._utc_now",
            lambda: "2026-07-01T02:01:01+00:00",
        )
        put_decision_quality_input_artifact(
        user_id=11,
        artifact={
            "artifact_type": "candidate_selection_case",
            "artifact_schema_version": "decision_quality_candidate_selection_case.v1",
            "source_type": "discovery",
            "source_report_id": "forged-report",
            "decision_event_id": None,
            "decision_at": "2026-07-01T02:00:00+00:00",
            "available_at": "2026-07-01T02:01:00+00:00",
            "recorded_at": "2026-07-01T02:01:00+00:00",
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": {
                "schema_version": "decision_quality_candidate_selection_case.v1",
                "audit": {"decision_at": "2026-07-01T02:00:00+00:00"},
                "outcome_labels": {},
                "automatic_promotion_allowed": False,
            },
        },
    )
    evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of="2026-07-14T23:00:00Z",
        user_ids=[11],
    )
    snapshot = list_decision_quality_evaluation_snapshots(user_id=11, limit=1)[0][
        "payload"
    ]
    assert snapshot["evaluation"]["candidate_selection"]["formal_case_count"] == 0
    assert snapshot["input_manifest"]["consumed_input_artifact_count"] == 0
    assert snapshot["input_manifest"]["ignored_artifact_count"] == 1


def test_late_inserted_artifact_cannot_enter_an_earlier_cutoff() -> None:
    put_decision_quality_input_artifact(
        user_id=10,
        artifact={
            "artifact_type": "candidate_selection_audit",
            "artifact_schema_version": "decision_quality_candidate_audit_artifact.v1",
            "source_type": "discovery",
            "source_report_id": "report-late",
            "decision_event_id": None,
            "decision_at": "2026-07-01T02:00:00+00:00",
            "available_at": "2026-07-01T02:00:01+00:00",
            "recorded_at": "2026-07-01T02:00:01+00:00",
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": {
                "schema_version": "decision_quality_candidate_audit_artifact.v1",
                "audit": {"schema_version": "discovery_candidate_selection_audit.v2"},
            },
        },
    )

    evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of="2026-07-02T00:00:00Z",
        user_ids=[10],
    )
    snapshot = list_decision_quality_evaluation_snapshots(user_id=10, limit=1)[0][
        "payload"
    ]
    assert snapshot["input_manifest"]["input_artifact_count"] == 0
    assert snapshot["input_manifest"]["ignored_input_artifacts"] == []


def test_point_in_time_filters_use_receipt_and_maximum_label_time() -> None:
    cutoff = datetime(2026, 7, 14, tzinfo=timezone.utc)
    event = {
        "created_at": "2026-07-14T00:00:01+00:00",
        "payload": {
            "schema_version": "decision_event.v2",
            "event_id": "event-1",
            "decision_at": "2026-07-01T00:00:00+00:00",
        },
    }
    assert _events_in_window(
        [event],
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cutoff=cutoff,
    ) == []

    outcome = {
        "is_terminal": True,
        "finalized_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-14T00:00:01+00:00",
        "payload": {
            "event_id": "event-1",
            "is_terminal": True,
            "label_available_at": "2026-07-12T00:00:00+00:00",
        },
    }
    assert not _terminal_outcome_for_selected_event(
        outcome,
        {"event-1"},
        evaluation_as_of=cutoff,
    )


def test_only_precisely_absent_legacy_replay_fields_are_grandfathered() -> None:
    assert _legacy_event_exclusion_allowed(
        "decision_event_replay_bundle_missing_or_invalid",
        {},
    )
    assert not _legacy_event_exclusion_allowed(
        "decision_event_replay_bundle_missing_or_invalid",
        {"replay_bundle": {"schema_version": "tampered"}},
    )
    assert not _legacy_event_exclusion_allowed(
        "decision_event_content_hash_mismatch",
        {},
    )


def test_mysql_to_sqlite_fallback_fails_closed(monkeypatch) -> None:
    class FallbackConnection:
        dialect = "sqlite"

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_test",
    )
    refresh_settings()
    try:
        with pytest.raises(DecisionQualitySnapshotStorageError, match="fallback"):
            evaluate_and_persist_decision_quality_snapshots(
                evaluation_as_of="2026-07-14T00:00:00Z",
                user_ids=[1],
                connection_factory=FallbackConnection,
            )
    finally:
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()


def test_latest_snapshot_read_does_not_commit(monkeypatch) -> None:
    class TrackingConnection:
        dialect = "sqlite"

        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

        def close(self) -> None:
            self.closed += 1

    connection = TrackingConnection()
    monkeypatch.setattr(
        "app.services.decision_quality_snapshot.list_decision_quality_evaluation_snapshots",
        lambda **_kwargs: [],
    )

    assert read_latest_decision_quality_snapshot(
        user_id=1,
        connection_factory=lambda: connection,
    ) is None
    assert (connection.commits, connection.rollbacks, connection.closed) == (0, 1, 1)


def test_read_only_factory_does_not_create_or_migrate_a_missing_database(
    monkeypatch,
    tmp_path,
) -> None:
    path = tmp_path / "must-remain-missing.db"
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(path))
    refresh_settings()

    with pytest.raises(DecisionQualitySnapshotStorageError):
        read_latest_decision_quality_snapshot(user_id=1)
    assert not path.exists()


def test_window_query_paginates_beyond_the_legacy_ten_thousand_cap() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE evidence_rows (row_id INTEGER PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO evidence_rows (row_id) VALUES (?)",
        ((index,) for index in range(10_001)),
    )

    rows = _fetch_paginated_rows(
        connection,
        table="evidence_rows",
        where="row_id >= ?",
        params=(0,),
        order_by="row_id",
    )

    assert len(rows) == 10_001
    assert rows[0]["row_id"] == 0
    assert rows[-1]["row_id"] == 10_000


def test_all_users_attempts_other_tenants_after_one_contract_failure(
    monkeypatch,
) -> None:
    calls: list[int] = []

    def evaluate_one(**kwargs):
        user_id = kwargs["user_id"]
        calls.append(user_id)
        if user_id == 2:
            raise DecisionQualitySnapshotContractError("tenant evidence invalid")
        return {"user_id": user_id}

    monkeypatch.setattr(
        "app.services.decision_quality_snapshot._evaluate_one_user_snapshot",
        evaluate_one,
    )

    with pytest.raises(DecisionQualitySnapshotContractError, match="isolated user ids: 2"):
        evaluate_and_persist_decision_quality_snapshots(
            evaluation_as_of="2026-07-14T00:00:00Z",
            user_ids=[1, 2, 3],
        )
    assert calls == [1, 2, 3]
