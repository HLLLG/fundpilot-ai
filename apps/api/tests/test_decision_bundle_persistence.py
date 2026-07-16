from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from app.config import refresh_settings
from app.database import (
    database_file_path,
    get_discovery_report,
    get_report,
    save_discovery_report,
    save_report,
)
from app.models import (
    DiscoveryRecommendation,
    FundDiscoveryReport,
    FundRecommendation,
    Holding,
    Report,
    RiskAssessment,
)
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.candidate_selection_audit import (
    build_candidate_selection_audit_v2,
)
from app.services.decision_repository import (
    canonical_hash,
    get_decision_quality_artifact_receipt,
    list_decision_quality_input_artifacts,
    list_decision_quality_evaluation_snapshots,
)
from app.services.decision_quality_snapshot import (
    evaluate_and_persist_decision_quality_snapshots,
)
from app.services.decision_outcome_persistence import (
    persist_daily_outcome_result,
    persist_discovery_outcome_result,
)
from app.services.discovery_outcomes import build_discovery_outcomes
from app.services.mainline_regime import build_mainline_regime_snapshot
from app.services.mainline_snapshot_repository import MAINLINE_SNAPSHOT_ARTIFACT_TYPE
from app.services.recommendation_outcomes import build_recommendation_outcomes


_CREATED_AT = datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc)


def _position_snapshot(snapshot_id: str, fund_code: str = "000001") -> dict:
    return {
        "schema_version": "portfolio_position_snapshot.v1",
        "snapshot_id": snapshot_id,
        "snapshot_at": _CREATED_AT.isoformat(),
        "snapshot_date": "2026-07-10",
        "source_type": "confirmed_ledger",
        "truth_status": "confirmed",
        "authoritative": True,
        "ledger_version": 3,
        "position_complete": True,
        "completeness": {"position_truth_status": "confirmed"},
        "cash": {"balance_cny": None, "status": "unknown"},
        "positions": [
            {
                "fund_code": fund_code,
                "confirmed_shares": "100.0000",
                "unit_cost_yuan": "1.200000",
            }
        ],
    }


def _daily_report(
    report_id: str = "daily-report-1",
    *,
    snapshot_id: str = "daily-snapshot-1",
    fund_code: str = "000001",
) -> Report:
    return Report(
        id=report_id,
        created_at=_CREATED_AT,
        title="日报测试",
        risk=RiskAssessment(
            level="low",
            suggested_action="watch",
            weighted_return_percent=0,
            alerts=[],
        ),
        holdings=[
            Holding(
                fund_code=fund_code,
                fund_name="测试基金",
                holding_amount=120,
            )
        ],
        fund_recommendations=[
            FundRecommendation(
                fund_code=fund_code,
                fund_name="测试基金",
                action="分批加仓",
                amount_yuan=20,
            )
        ],
        summary="测试摘要",
        recommendations=["分批加仓"],
        caveats=[],
        provider="offline-test",
        analysis_facts={
            "portfolio_position_snapshot": _position_snapshot(snapshot_id, fund_code),
            "portfolio": {"round_trip_fee_percent": 1.0},
            "data_evidence": {"schema_version": "1.0", "items": []},
        },
    )


def _clean_claim_audit() -> dict:
    audit = {
        "schema_version": "fund_lookthrough_claim_audit.v1",
        "status": "clean",
        "facts_status": "available",
        "scanned_field_count": 1,
        "lookthrough_field_count": 1,
        "changed_field_count": 0,
        "change_count": 0,
        "reason_counts": {},
        "changes": [],
        "hash_algorithm": "sha256",
    }
    audit["audit_hash"] = canonical_hash(audit)
    return audit


def _empty_valid_candidate_audit(decision_at: datetime) -> dict:
    return build_candidate_selection_audit_v2(
        decision_at=decision_at,
        recall_candidates=[],
        gate_candidates=[],
        prescreen_candidates=[],
        final_candidates=[],
        versions={"selection_policy": "pytest-empty-selection.v1"},
        stage_contexts={
            stage: {
                "version": f"pytest-{stage}.v1",
                "scope": (
                    {
                        "definition": "complete empty candidate recall",
                        "complete": True,
                        "candidate_count_total": 0,
                        "candidate_count_retained": 0,
                        "catalogue_rows_embedded": False,
                    }
                    if stage == "recall"
                    else {"definition": "complete empty stage", "complete": True}
                ),
            }
            for stage in ("recall", "gate", "prescreen", "final")
        },
    )


def _discovery_report(
    report_id: str = "discovery-report-1",
    *,
    snapshot_id: str = "discovery-snapshot-1",
    fund_code: str = "000002",
) -> FundDiscoveryReport:
    return FundDiscoveryReport(
        id=report_id,
        created_at=_CREATED_AT,
        title="荐基测试",
        summary="测试摘要",
        recommendations=[
            DiscoveryRecommendation(
                fund_code=fund_code,
                fund_name="候选基金",
                sector_name="测试板块",
                action="分批买入",
                suggested_amount_yuan=100,
            )
        ],
        discovery_facts={
            "portfolio_position_snapshot": _position_snapshot(snapshot_id, fund_code),
            "profile": {"round_trip_fee_percent": 1.2},
            "data_evidence": {"schema_version": "1.0", "items": []},
        },
        provider="offline-test",
    )


def _fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_file_path())
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _count(table: str) -> int:
    # Test-owned table names only; keeping the SQL literal makes accidental use
    # with request data impossible.
    allowed = {
        "reports",
        "fund_discovery_reports",
        "decision_portfolio_snapshots",
        "decision_events",
        "outcome_observations",
        "outcome_observation_revisions",
        "decision_quality_input_artifacts",
        "decision_quality_artifact_receipts",
    }
    assert table in allowed
    return int(_fetchall(f"SELECT COUNT(*) AS value FROM {table}")[0]["value"])


def test_save_report_atomically_persists_daily_decision_bundle() -> None:
    saved = save_report(_daily_report())

    assert saved.decision_contract["persistence"] == "persisted"
    assert saved.decision_contract["event_count"] == 1
    assert saved.decision_contract["observation_count"] == 3
    assert len(saved.decision_events) == 1
    assert _count("reports") == 1
    assert _count("decision_portfolio_snapshots") == 1
    assert _count("decision_events") == 1
    assert _count("outcome_observations") == 3
    assert _count("outcome_observation_revisions") == 3

    report_payload = json.loads(_fetchall("SELECT payload FROM reports")[0]["payload"])
    event = _fetchall("SELECT * FROM decision_events")[0]
    observations = _fetchall(
        "SELECT horizon_trading_days, status FROM outcome_observations "
        "ORDER BY horizon_trading_days"
    )
    assert report_payload["decision_contract"] == saved.decision_contract
    assert report_payload["decision_events"] == saved.decision_events
    assert event["source_type"] == "daily"
    assert event["source_report_id"] == saved.id
    assert event["portfolio_snapshot_id"] == "daily-snapshot-1"
    assert [(row["horizon_trading_days"], row["status"]) for row in observations] == [
        (1, "pending"),
        (5, "pending"),
        (20, "pending"),
    ]


def test_discovery_mainline_snapshot_is_frozen_once_in_append_only_ledger() -> None:
    report = _discovery_report(report_id="discovery-mainline-snapshot")
    snapshot = build_mainline_regime_snapshot(
        [{"sector_label": "CPO", "change_1d_percent": 1.2}],
        sector_labels=["CPO"],
        decision_at=_CREATED_AT,
        captured_at=_CREATED_AT + timedelta(seconds=1),
    )
    report.discovery_facts["mainline_snapshot"] = snapshot

    save_discovery_report(report)
    save_discovery_report(report)

    rows = list_decision_quality_input_artifacts(
        user_id=1,
        artifact_type=MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
        source_type="discovery",
        source_report_id=report.id,
        limit=10,
    )
    assert len(rows) == 1
    envelope = rows[0]["payload"]
    assert envelope["logical_key"] == f"mainline_snapshot:{report.id}"
    assert envelope["audit_eligible"] is False
    wrapper = envelope["artifact"]
    assert wrapper["snapshot_hash"] == snapshot["snapshot_hash"]
    assert wrapper["snapshot"]["execution_gate_changed"] is False


def test_saved_d2_report_is_formally_replayable_and_snapshot_is_idempotent() -> None:
    decision_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    evidence_observed_at = decision_at + timedelta(seconds=2)
    report = _daily_report(report_id="d2-production-replay-round-trip")
    report.created_at = decision_at
    report.analysis_facts["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": evidence_observed_at.isoformat(),
        "decision_ready": True,
        "items": [
            {
                "fact_id": "holdings.000001.daily_return_percent",
                "source": "official_nav",
                "source_type": "official",
                "available_at": (decision_at - timedelta(minutes=1)).isoformat(),
                "fetched_at": evidence_observed_at.isoformat(),
                "freshness": "fresh",
                "confidence": "high",
                "is_estimate": False,
            }
        ],
    }

    saved = save_report(report)
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=1)
    first = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of=cutoff,
        user_ids=[1],
        window_days=365,
    )
    retry = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of=cutoff,
        user_ids=[1],
        window_days=365,
    )

    event = saved.decision_events[0]
    assert event["recorded_at"] == evidence_observed_at.isoformat()
    assert event["replay_bundle"]["recorded_at"] == evidence_observed_at.isoformat()
    assert first["snapshots"][0]["snapshot_id"] == retry["snapshots"][0][
        "snapshot_id"
    ]
    rows = list_decision_quality_evaluation_snapshots(user_id=1)
    assert len(rows) == 1
    snapshot = rows[0]["payload"]
    assert snapshot["input_manifest"]["decision_event_count"] == 1
    assert snapshot["input_manifest"]["nonformal_decision_event_count"] == 0
    assert snapshot["evaluation"]["input_audit"]["valid_event_count"] == 1
    assert snapshot["evaluation"]["input_audit"]["formal_event_horizon_count"] == 3
    assert snapshot["evaluation"]["overall"]["replay"] == {
        "eligible_count": 3,
        "ineligible_count": 0,
        "coverage_percent": 100.0,
    }


def test_real_daily_settlement_without_invented_source_time_scores_in_snapshot() -> None:
    report = _daily_report(report_id="d2-real-mature-settlement")
    evidence_observed_at = _CREATED_AT + timedelta(seconds=2)
    report.analysis_facts["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": evidence_observed_at.isoformat(),
        "decision_ready": True,
        "items": [
            {
                "fact_id": "holdings.000001.daily_return_percent",
                "source": "official_nav",
                "source_type": "official",
                "available_at": (_CREATED_AT - timedelta(minutes=1)).isoformat(),
                "fetched_at": evidence_observed_at.isoformat(),
                "freshness": "fresh",
                "confidence": "high",
                "is_estimate": False,
            }
        ],
    }
    saved = save_report(report)
    report_payload = saved.model_dump(mode="json")
    result = build_recommendation_outcomes(
        report_payload,
        None,
        horizons=(1,),
        fetch_nav=lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-07-10", "nav": 1.0},
                {"date": "2026-07-11", "nav": 1.02},
                {"date": "2026-07-12", "nav": 1.03},
            ]
        },
        trade_dates=frozenset({"2026-07-10", "2026-07-11", "2026-07-12"}),
        fetch_benchmark=None,
        formal_v2_only=True,
    )
    observation = result["items"][0]["by_horizon"]["T+1"][
        "outcome_observation"
    ]
    assert observation["status"] == "mature"
    assert observation["source_available_at"] is None
    assert observation["observation_at"] is None

    persisted = persist_daily_outcome_result(report_payload, result)
    assert persisted["outcome_evidence"]["terminal_count"] == 1
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=1)
    batch = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of=cutoff,
        user_ids=[1],
        window_days=365,
    )

    assert batch["snapshots"][0]["status"] in {"available", "unavailable"}
    snapshot = list_decision_quality_evaluation_snapshots(user_id=1)[0]["payload"]
    audit = snapshot["evaluation"]["input_audit"]
    assert audit["outcome_exclusions"] == []
    match = next(
        row
        for row in audit["event_horizon_matches"]
        if row["horizon_trading_days"] == 1
    )
    assert match["match_status"] == "matched_terminal"
    assert match["formal_score_status"] == "included"
    assert match["label_source_available_at"] is None
    assert match["label_availability_basis"] == "storage_terminal_receipt"
    manifested = next(
        row
        for row in snapshot["input_manifest"]["terminal_outcomes"]
        if row["observation_id"] == observation["observation_id"]
    )
    assert manifested["first_observed_at"] <= manifested["label_recorded_at"]
    assert manifested["finalized_at"] == manifested["label_recorded_at"]


def test_real_discovery_settlement_without_source_time_scores_in_snapshot() -> None:
    report = _discovery_report(report_id="d2-real-discovery-settlement")
    evidence_observed_at = _CREATED_AT + timedelta(seconds=2)
    report.discovery_facts["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": evidence_observed_at.isoformat(),
        "decision_ready": True,
        "items": [
            {
                "fact_id": "candidate.000002.rank_score",
                "source": "candidate_pool",
                "source_type": "computed",
                "available_at": (_CREATED_AT - timedelta(minutes=1)).isoformat(),
                "fetched_at": evidence_observed_at.isoformat(),
                "freshness": "fresh",
                "confidence": "high",
                "is_estimate": False,
            }
        ],
    }
    saved = save_discovery_report(report)
    report_payload = saved.model_dump(mode="json")
    result = build_discovery_outcomes(
        report_payload,
        days=5,
        fetch_nav=lambda *_args, **_kwargs: {
            "data": [
                {"date": f"2026-07-{day:02d}", "nav": 1.0 + index / 100}
                for index, day in enumerate(range(10, 16))
            ]
        },
        fetch_benchmark=None,
    )
    observation = result["outcome_observations"][0]
    assert observation["mature"] is True
    assert observation["source_available_at"] is None
    assert observation["observation_at"] is None

    persisted = persist_discovery_outcome_result(report_payload, result)
    assert persisted["outcome_evidence"]["terminal_count"] == 1
    evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of=datetime.now(timezone.utc) + timedelta(minutes=1),
        user_ids=[1],
        window_days=365,
    )

    snapshot = list_decision_quality_evaluation_snapshots(user_id=1)[0]["payload"]
    audit = snapshot["evaluation"]["input_audit"]
    assert audit["outcome_exclusions"] == []
    match = next(
        row
        for row in audit["event_horizon_matches"]
        if row["horizon_trading_days"] == 5
    )
    assert match["match_status"] == "matched_terminal"
    assert match["formal_score_status"] == "included"
    assert match["label_source_available_at"] is None
    assert match["label_availability_basis"] == "storage_terminal_receipt"
    manifested = next(
        row
        for row in snapshot["input_manifest"]["terminal_outcomes"]
        if row["observation_id"] == observation["observation_id"]
    )
    assert manifested["first_observed_at"] <= manifested["label_recorded_at"]
    assert manifested["finalized_at"] == manifested["label_recorded_at"]


def test_save_discovery_report_atomically_persists_decision_bundle() -> None:
    saved = save_discovery_report(_discovery_report())

    assert saved.decision_contract["persistence"] == "persisted"
    assert saved.decision_contract["event_count"] == 1
    assert saved.decision_contract["observation_count"] == 3
    assert _count("fund_discovery_reports") == 1
    assert _count("decision_portfolio_snapshots") == 1
    assert _count("decision_events") == 1
    assert _count("outcome_observations") == 3

    event = _fetchall("SELECT * FROM decision_events")[0]
    observations = _fetchall(
        "SELECT horizon_trading_days, status FROM outcome_observations "
        "ORDER BY horizon_trading_days"
    )
    assert event["source_type"] == "discovery"
    assert event["source_report_id"] == saved.id
    assert event["portfolio_snapshot_id"] == "discovery-snapshot-1"
    assert [(row["horizon_trading_days"], row["status"]) for row in observations] == [
        (5, "pending"),
        (20, "pending"),
        (60, "pending"),
    ]


def test_report_quality_artifacts_are_append_only_and_claim_is_counted_once() -> None:
    report = _daily_report(report_id="daily-quality-artifacts")
    report.fund_recommendations.append(
        FundRecommendation(
            fund_code="000002",
            fund_name="second fixture fund",
            action="观察",
            amount_yuan=None,
        )
    )
    report.analysis_facts["fund_lookthrough_claim_audit"] = _clean_claim_audit()

    first = save_report(report)
    second = save_report(report)

    assert len(first.decision_events) == 2
    assert first.decision_events == second.decision_events
    assert _count("decision_quality_input_artifacts") == 1
    artifact_row = _fetchall(
        "SELECT artifact_type, decision_event_id, available_at, recorded_at, "
        "created_at, payload "
        "FROM decision_quality_input_artifacts"
    )[0]
    payload = json.loads(artifact_row["payload"])
    wrapper = payload["artifact"]
    event_rows = _fetchall(
        "SELECT event_id, created_at FROM decision_events "
        "WHERE source_report_id = ? ORDER BY event_id",
        (report.id,),
    )
    assert artifact_row["artifact_type"] == "claim_audit_wrapper"
    assert artifact_row["decision_event_id"] == event_rows[0]["event_id"]
    event_receipt = datetime.fromisoformat(event_rows[0]["created_at"])
    expected_artifact_time = (event_receipt + timedelta(microseconds=1)).isoformat()
    assert artifact_row["available_at"] == expected_artifact_time
    assert artifact_row["recorded_at"] == expected_artifact_time
    assert datetime.fromisoformat(artifact_row["created_at"]) >= event_receipt
    assert wrapper["event_id"] == event_rows[0]["event_id"]
    assert wrapper["available_at"] == expected_artifact_time
    assert wrapper["recorded_at"] == expected_artifact_time
    assert wrapper["audit"]["status"] == "clean"

    connection = sqlite3.connect(database_file_path())
    try:
        connection.execute("DELETE FROM reports WHERE id = ?", (report.id,))
        connection.commit()
    finally:
        connection.close()
    assert _count("decision_quality_input_artifacts") == 1


def test_saved_clean_claim_audit_is_accepted_by_snapshot_evaluator() -> None:
    decision_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    evidence_observed_at = decision_at + timedelta(seconds=2)
    report = _daily_report(report_id="daily-clean-claim-snapshot")
    report.created_at = decision_at
    report.analysis_facts["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": evidence_observed_at.isoformat(),
        "decision_ready": True,
        "items": [
            {
                "fact_id": "holdings.000001.daily_return_percent",
                "source": "official_nav",
                "source_type": "official",
                "available_at": (decision_at - timedelta(minutes=1)).isoformat(),
                "fetched_at": evidence_observed_at.isoformat(),
                "freshness": "fresh",
                "confidence": "high",
                "is_estimate": False,
            }
        ],
    }
    report.analysis_facts["fund_lookthrough_claim_audit"] = _clean_claim_audit()

    save_report(report)
    batch = evaluate_and_persist_decision_quality_snapshots(
        evaluation_as_of=datetime.now(timezone.utc) + timedelta(minutes=1),
        user_ids=[1],
        window_days=365,
    )

    assert batch["snapshots"][0]["status"] in {"available", "unavailable"}
    snapshot = list_decision_quality_evaluation_snapshots(user_id=1)[0]["payload"]
    claims = snapshot["evaluation"]["claim_audits"]
    assert claims["status"] == "available"
    assert claims["audit_count"] == 1
    assert claims["classified_count"] == 1
    assert claims["clean_count"] == 1
    assert claims["exclusion_reasons"] == []
    assert claims["records"][0]["status"] == "accepted"


def test_discovery_candidate_audit_is_frozen_even_when_not_metric_eligible() -> None:
    report = _discovery_report(report_id="discovery-quality-artifacts")
    report.discovery_facts["candidate_selection_audit"] = {
        "schema_version": "discovery_candidate_selection_audit.v1",
        "rows": [],
    }

    save_discovery_report(report)

    row = _fetchall(
        "SELECT artifact_type, audit_eligible, decision_event_id, recorded_at, payload "
        "FROM decision_quality_input_artifacts"
    )[0]
    event_receipt = datetime.fromisoformat(
        _fetchall("SELECT created_at FROM decision_events")[0]["created_at"]
    )
    payload = json.loads(row["payload"])
    assert row["artifact_type"] == "candidate_selection_audit"
    assert row["audit_eligible"] == 0
    assert row["decision_event_id"] is None
    assert row["recorded_at"] == (
        event_receipt + timedelta(microseconds=1)
    ).isoformat()
    assert payload["artifact"]["audit"]["schema_version"] == (
        "discovery_candidate_selection_audit.v1"
    )
    assert payload["artifact"]["capture_validation"]["decision_eligible"] is False


def test_zero_recommendation_discovery_freezes_audit_without_fake_event() -> None:
    report = _discovery_report(report_id="discovery-zero-selection")
    live_decision_at = datetime.now(timezone.utc)
    report.created_at = live_decision_at
    report.recommendations = []
    report.discovery_facts["candidate_selection_audit"] = (
        _empty_valid_candidate_audit(live_decision_at)
    )
    report.discovery_facts["fund_lookthrough_claim_audit"] = _clean_claim_audit()
    before_save = datetime.now(timezone.utc)

    first = save_discovery_report(report)
    second = save_discovery_report(report)
    after_save = datetime.now(timezone.utc)

    assert first.decision_contract["event_count"] == 0
    assert first.decision_contract["observation_count"] == 0
    assert first.decision_events == []
    assert second.decision_events == []
    assert _count("fund_discovery_reports") == 1
    assert _count("decision_portfolio_snapshots") == 1
    assert _count("decision_events") == 0
    assert _count("outcome_observations") == 0
    assert _count("decision_quality_input_artifacts") == 1

    rows = list_decision_quality_input_artifacts(
        user_id=1,
        source_report_id=report.id,
    )
    assert len(rows) == 1
    envelope = rows[0]["payload"]
    recorded_at = datetime.fromisoformat(envelope["recorded_at"])
    assert before_save <= recorded_at <= after_save
    assert envelope["artifact_type"] == "candidate_selection_audit"
    assert envelope["source_type"] == "discovery"
    assert envelope["source_report_id"] == report.id
    assert envelope["decision_event_id"] is None
    assert envelope["decision_at"] == live_decision_at.isoformat()
    assert envelope["available_at"] == envelope["recorded_at"]
    assert envelope["audit_eligible"] is True
    assert envelope["artifact"]["recorded_at"] == envelope["recorded_at"]
    assert envelope["artifact"]["recorded_at_source"] == (
        "report_transaction_post_insert"
    )
    assert envelope["artifact"]["audit"]["schema_version"] == (
        "discovery_candidate_selection_audit.v2"
    )
    assert envelope["artifact"]["schema_version"] == (
        "decision_quality_candidate_audit_artifact.v4"
    )
    assert envelope["artifact"]["label_plan"]["status"] == "preregistered"
    assert envelope["artifact"]["label_plan"]["preregistered_at"] == (
        envelope["recorded_at"]
    )
    assert envelope["artifact"]["label_plan"]["provider_receipt_required"] is True
    assert envelope["artifact"]["label_plan"]["capture_mode"] == (
        "live_only_no_backfill"
    )
    assert envelope["artifact"]["label_plan"]["post_commit_receipt_required"] is True
    assert envelope["artifact"]["label_plan"][
        "formal_receipt_max_delay_seconds"
    ] == 300
    assert "entry_not_before_date" not in envelope["artifact"]["label_plan"]
    assert envelope["artifact"]["label_plan"][
        "automatic_promotion_allowed"
    ] is False
    # The claim audit was present on the report, but no DecisionEvent exists to
    # bind its event id and immutable payload hash.
    assert all(row["artifact_type"] != "claim_audit_wrapper" for row in rows)


def test_concurrent_zero_recommendation_retries_share_first_report_receipt() -> None:
    report = _discovery_report(report_id="discovery-zero-selection-concurrent")
    report.recommendations = []
    report.discovery_facts["candidate_selection_audit"] = (
        _empty_valid_candidate_audit(_CREATED_AT)
    )
    ready = Barrier(2)

    def save_same_report() -> FundDiscoveryReport:
        token = set_request_user_id(1)
        try:
            ready.wait(timeout=5)
            return save_discovery_report(report.model_copy(deep=True))
        finally:
            reset_request_user_id(token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = list(executor.map(lambda _index: save_same_report(), range(2)))

    assert all(item.decision_events == [] for item in saved)
    assert _count("fund_discovery_reports") == 1
    assert _count("decision_events") == 0
    assert _count("outcome_observations") == 0
    assert _count("decision_quality_input_artifacts") == 1
    rows = list_decision_quality_input_artifacts(
        user_id=1,
        source_report_id=report.id,
    )
    assert len(rows) == 1
    assert rows[0]["payload"]["artifact"]["recorded_at_source"] == (
        "report_transaction_post_insert"
    )


def test_discovery_save_finalizes_post_commit_candidate_receipt() -> None:
    report = _discovery_report(report_id="candidate-post-commit-receipt")
    report.recommendations = []
    report.discovery_facts["candidate_selection_audit"] = (
        _empty_valid_candidate_audit(_CREATED_AT)
    )

    save_discovery_report(report)

    artifact_row = list_decision_quality_input_artifacts(
        user_id=1,
        source_report_id=report.id,
    )[0]
    artifact = artifact_row["payload"]
    receipt_row = get_decision_quality_artifact_receipt(
        user_id=1,
        artifact_id=artifact["artifact_id"],
    )
    assert receipt_row is not None
    receipt = receipt_row["payload"]
    assert receipt["artifact_id"] == artifact["artifact_id"]
    assert receipt["artifact_type"] == "candidate_selection_audit"
    assert receipt["artifact_content_hash"] == artifact["content_hash"]
    assert receipt["source_row_created_at"] == artifact_row["created_at"]
    assert datetime.fromisoformat(receipt["source_visible_at"]) >= (
        datetime.fromisoformat(receipt["source_row_created_at"])
    )
    assert receipt["store_authority"] == "primary"


def test_post_commit_receipt_failure_leaves_committed_report_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _discovery_report(report_id="candidate-receipt-retryable")
    report.recommendations = []
    report.discovery_facts["candidate_selection_audit"] = (
        _empty_valid_candidate_audit(_CREATED_AT)
    )

    def fail_finalize(**_kwargs):
        raise RuntimeError("injected post-commit receipt failure")

    monkeypatch.setattr(
        "app.services.decision_repository.finalize_decision_quality_artifact_receipt",
        fail_finalize,
    )
    saved = save_discovery_report(report)

    assert saved.id == report.id
    assert _count("fund_discovery_reports") == 1
    assert _count("decision_quality_input_artifacts") == 1
    assert _count("decision_quality_artifact_receipts") == 0


def test_quality_artifact_failure_rolls_back_report_and_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _daily_report(report_id="quality-artifact-rollback")
    report.analysis_facts["fund_lookthrough_claim_audit"] = _clean_claim_audit()

    def fail_write(**_kwargs):
        raise RuntimeError("injected quality artifact failure")

    monkeypatch.setattr(
        "app.services.decision_quality_artifacts.put_decision_quality_input_artifact",
        fail_write,
    )
    with pytest.raises(RuntimeError, match="injected quality artifact failure"):
        save_report(report)

    assert _count("reports") == 0
    assert _count("decision_events") == 0
    assert _count("outcome_observations") == 0
    assert _count("decision_quality_input_artifacts") == 0


def test_prompt_contract_survives_report_and_event_database_round_trip() -> None:
    report = _daily_report(report_id="prompt-contract-round-trip")
    prompt_contract = {
        "schema_version": "prompt_contract.v1",
        "template_version": "analysis_prompt.2026-07.v4",
        "effective_messages_hash": "messages-sha256",
    }
    prompt_contract["contract_hash"] = canonical_hash(prompt_contract)
    report.analysis_facts["pipeline"] = {
        "model": "deepseek-chat",
        "prompt_contract": prompt_contract,
    }

    saved = save_report(report)

    report_payload = json.loads(
        _fetchall(
            "SELECT payload FROM reports WHERE id = ?",
            ("prompt-contract-round-trip",),
        )[0]["payload"]
    )
    event_payload = json.loads(
        _fetchall(
            "SELECT payload FROM decision_events WHERE source_report_id = ?",
            ("prompt-contract-round-trip",),
        )[0]["payload"]
    )
    assert saved.decision_events[0]["prompt_contract"] == prompt_contract
    assert report_payload["decision_events"][0]["prompt_contract"] == prompt_contract
    assert event_payload["prompt_contract"] == prompt_contract
    assert event_payload["prompt_version"] == "analysis_prompt.2026-07.v4"
    assert event_payload["schema_version"] == "decision_event.v2"


def test_repeated_report_and_discovery_saves_are_idempotent() -> None:
    daily = _daily_report()
    discovery = _discovery_report()

    first_daily = save_report(daily)
    second_daily = save_report(daily)
    first_discovery = save_discovery_report(discovery)
    second_discovery = save_discovery_report(discovery)

    assert first_daily.decision_events == second_daily.decision_events
    assert first_discovery.decision_events == second_discovery.decision_events
    assert _count("reports") == 1
    assert _count("fund_discovery_reports") == 1
    assert _count("decision_portfolio_snapshots") == 2
    assert _count("decision_events") == 2
    assert _count("outcome_observations") == 6
    # A retry with identical initial evidence must not manufacture a revision.
    assert _count("outcome_observation_revisions") == 6


@pytest.mark.parametrize(
    "failing_repository_write",
    [
        "put_decision_portfolio_snapshot",
        "put_decision_event",
        "upsert_outcome_observation",
    ],
)
def test_repository_failure_rolls_back_report_and_entire_bundle(
    monkeypatch: pytest.MonkeyPatch,
    failing_repository_write: str,
) -> None:
    def fail_write(**_kwargs):
        raise RuntimeError("injected decision repository failure")

    monkeypatch.setattr(
        f"app.services.decision_repository.{failing_repository_write}",
        fail_write,
    )

    with pytest.raises(RuntimeError, match="injected decision repository failure"):
        save_report(_daily_report())

    assert _count("reports") == 0
    assert _count("decision_portfolio_snapshots") == 0
    assert _count("decision_events") == 0
    assert _count("outcome_observations") == 0
    assert _count("outcome_observation_revisions") == 0


def test_discovery_repository_failure_rolls_back_report_and_entire_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(**_kwargs):
        raise RuntimeError("injected discovery observation failure")

    monkeypatch.setattr(
        "app.services.decision_repository.upsert_outcome_observation",
        fail_write,
    )

    with pytest.raises(RuntimeError, match="injected discovery observation failure"):
        save_discovery_report(_discovery_report())

    assert _count("fund_discovery_reports") == 0
    assert _count("decision_portfolio_snapshots") == 0
    assert _count("decision_events") == 0
    assert _count("outcome_observations") == 0
    assert _count("outcome_observation_revisions") == 0


def test_fallback_store_is_persisted_but_excluded_from_audit_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import db_connect

    def unavailable_mysql():
        raise RuntimeError("injected MySQL outage")

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://test:test@127.0.0.1:3306/fundpilot_test",
    )
    monkeypatch.setenv("FUND_AI_DB_FALLBACK_SQLITE", "true")
    monkeypatch.setattr(db_connect, "_open_mysql", unavailable_mysql)
    db_connect.reset_mysql_fallback_cache()
    refresh_settings()
    try:
        saved = save_report(_daily_report())

        assert saved.decision_contract["store_authority"] == "fallback_non_audited"
        assert saved.decision_contract["audit_eligible"] is False
        # This also proves a fresh fallback file was bootstrapped with the report
        # and v10 decision tables before the atomic bundle write was attempted.
        assert _count("reports") == 1
        event = _fetchall("SELECT metric_eligible, payload FROM decision_events")[0]
        event_payload = json.loads(event["payload"])
        assert event["metric_eligible"] == 0
        assert event_payload["store_authority"] == "fallback_non_audited"
        assert event_payload["metric_eligible"] is False
    finally:
        db_connect.reset_mysql_fallback_cache()
        monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
        refresh_settings()


def test_bundle_rows_and_report_reads_are_isolated_by_user() -> None:
    save_report(
        _daily_report(
            report_id="user-1-report",
            snapshot_id="shared-snapshot-id",
            fund_code="000001",
        )
    )

    user_two = set_request_user_id(2)
    try:
        save_report(
            _daily_report(
                report_id="user-2-report",
                snapshot_id="shared-snapshot-id",
                fund_code="000002",
            )
        )
        assert get_report("user-1-report") is None
        assert get_report("user-2-report") is not None
    finally:
        reset_request_user_id(user_two)

    assert get_report("user-1-report") is not None
    assert get_report("user-2-report") is None
    assert get_discovery_report("discovery-report-1") is None

    snapshot_users = _fetchall(
        "SELECT userId, snapshot_id FROM decision_portfolio_snapshots ORDER BY userId"
    )
    event_users = _fetchall(
        "SELECT userId, source_report_id FROM decision_events ORDER BY userId"
    )
    observation_users = _fetchall(
        "SELECT DISTINCT userId FROM outcome_observations ORDER BY userId"
    )
    assert [(row["userId"], row["snapshot_id"]) for row in snapshot_users] == [
        (1, "shared-snapshot-id"),
        (2, "shared-snapshot-id"),
    ]
    assert [(row["userId"], row["source_report_id"]) for row in event_users] == [
        (1, "user-1-report"),
        (2, "user-2-report"),
    ]
    assert [row["userId"] for row in observation_users] == [1, 2]
