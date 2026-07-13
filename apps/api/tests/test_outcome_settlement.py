from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.database import (
    database_file_path,
    delete_report,
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
from app.services.decision_outcome_persistence import OutcomeEvidenceConflict
from app.services.outcome_settlement import (
    OutcomeSettlementConflict,
    settle_pending_outcomes,
    OutcomeSettlementError,
)

_CREATED_AT = datetime(2026, 1, 2, 6, 30, tzinfo=timezone.utc)


def _daily_report() -> Report:
    return Report(
        id="settle-daily",
        created_at=_CREATED_AT,
        title="日报",
        risk=RiskAssessment(
            level="low",
            suggested_action="watch",
            weighted_return_percent=0,
            alerts=[],
        ),
        holdings=[Holding(fund_code="000001", fund_name="日报基金", holding_amount=100)],
        fund_recommendations=[
            FundRecommendation(
                fund_code="000001",
                fund_name="日报基金",
                action="分批加仓",
                amount_yuan=20,
            )
        ],
        summary="摘要",
        recommendations=["分批加仓"],
        caveats=[],
        provider="test",
        analysis_facts={
            "portfolio": {"round_trip_fee_percent": 1.0},
            "data_evidence": {"items": []},
        },
    )


def _discovery_report() -> FundDiscoveryReport:
    return FundDiscoveryReport(
        id="settle-discovery",
        created_at=_CREATED_AT,
        title="荐基",
        summary="摘要",
        recommendations=[
            DiscoveryRecommendation(
                fund_code="000002",
                fund_name="荐基基金",
                sector_name="测试",
                action="分批买入",
                suggested_amount_yuan=100,
            )
        ],
        discovery_facts={
            "profile": {"round_trip_fee_percent": 1.0},
            "data_evidence": {"items": []},
        },
        provider="test",
    )


def _nav(_code: str, *, trading_days: int) -> dict:
    start = date(2026, 1, 2)
    rows = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "nav": round(1 + index * 0.001, 6),
        }
        for index in range(90)
    ]
    return {"data": rows[-max(trading_days, 90) :]}


def _observation_rows() -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_file_path())
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT decision_event_id, horizon_trading_days, status, is_terminal "
            "FROM outcome_observations ORDER BY decision_event_id, horizon_trading_days"
        ).fetchall()
    finally:
        connection.close()


def test_scheduled_settlement_matures_daily_and_discovery_without_get() -> None:
    save_report(_daily_report())
    save_discovery_report(_discovery_report())

    result = settle_pending_outcomes(
        as_of_date="2026-07-13",
        fetch_nav=_nav,
        fetch_benchmark=None,
        trade_dates=frozenset({"2026-01-02"}),
    )

    assert result["status"] == "completed"
    assert result["report_count"] == 2
    assert result["pending_horizon_count"] == 6
    assert result["attempted_count"] == 6
    assert result["terminal_count"] == 6
    rows = _observation_rows()
    assert len(rows) == 6
    assert {
        (
            "discovery"
            if row["decision_event_id"].startswith("discovery:")
            else "daily",
            row["horizon_trading_days"],
            row["status"],
        )
        for row in rows
    } == {
        ("daily", 1, "mature"),
        ("daily", 5, "mature"),
        ("daily", 20, "mature"),
        ("discovery", 5, "hit"),
        ("discovery", 20, "hit"),
        ("discovery", 60, "hit"),
    }
    assert all(row["is_terminal"] == 1 for row in rows)

    retry = settle_pending_outcomes(
        as_of_date="2026-07-13",
        fetch_nav=_nav,
        fetch_benchmark=None,
        trade_dates=frozenset({"2026-01-02"}),
    )
    assert retry["report_count"] == 0
    assert retry["attempted_count"] == 0
    assert len(_observation_rows()) == 6


def test_terminal_race_conflict_fails_closed(monkeypatch) -> None:
    target = {
        "user_id": 1,
        "source_type": "daily",
        "report_id": "r1",
        "pending_event_horizons": {"daily:r1:0:000001": {1}},
        "report": {"id": "r1"},
    }
    monkeypatch.setattr(
        "app.services.outcome_settlement._load_pending_targets",
        lambda **_kwargs: ([target], []),
    )

    def conflict(*_args, **_kwargs):
        raise OutcomeEvidenceConflict("different terminal evidence")

    monkeypatch.setattr("app.services.outcome_settlement._settle_daily", conflict)

    with pytest.raises(OutcomeSettlementConflict, match="terminal outcome conflict"):
        settle_pending_outcomes(
            as_of_date="2026-07-13",
            fetch_nav=_nav,
            fetch_benchmark=None,
            trade_dates=frozenset(),
        )


def test_deleted_visible_report_settles_from_frozen_decision_event() -> None:
    save_report(_daily_report())
    assert delete_report("settle-daily") is True

    result = settle_pending_outcomes(
        as_of_date="2026-07-13",
        fetch_nav=_nav,
        fetch_benchmark=None,
        trade_dates=frozenset({"2026-01-02"}),
    )

    assert result["status"] == "completed"
    assert result["orphaned_count"] == 0
    assert result["terminal_count"] == 3
    rows = _observation_rows()
    assert len(rows) == 3
    assert all(row["is_terminal"] == 1 for row in rows)


def test_settlement_rejects_production_mysql_fallback(monkeypatch) -> None:
    class FallbackConnection:
        dialect = "sqlite"

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(uses_mysql=True),
    )

    with pytest.raises(OutcomeSettlementError, match="拒绝回落 SQLite"):
        settle_pending_outcomes(
            as_of_date="2026-07-13",
            connection_factory=FallbackConnection,
            trade_dates=frozenset(),
        )
