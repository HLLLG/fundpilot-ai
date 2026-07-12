from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

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
