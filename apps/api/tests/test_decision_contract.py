from __future__ import annotations

from app.services.decision_contract import (
    DECISION_EVENT_SCHEMA_VERSION,
    build_report_decision_bundle,
)


def _position_snapshot() -> dict:
    return {
        "schema_version": "portfolio_position_snapshot.v1",
        "snapshot_id": "pps-1",
        "ledger_version": "pl1:3:abc",
        "position_complete": True,
        "positions": [],
        "cash": {"balance_cny": None, "status": "unknown"},
    }


def test_daily_bundle_freezes_post_guard_action_fee_and_position(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    report = {
        "id": "report-1",
        "created_at": "2026-07-12T08:00:00+00:00",
        "provider": "deepseek-chat",
        "fund_recommendations": [
            {
                "fund_code": "161725",
                "fund_name": "招商中证白酒指数C",
                "action": "分批加仓",
                "validation_notes": ["最终 guard 已校验"],
            }
        ],
        "analysis_facts": {
            "portfolio_position_snapshot": _position_snapshot(),
            "portfolio": {"round_trip_fee_percent": 1.25},
            "pipeline": {"model": "deepseek-reasoner"},
            "data_evidence": {"schema_version": "1.0", "items": []},
        },
    }

    first = build_report_decision_bundle(report, decision_kind="daily")
    second = build_report_decision_bundle(report, decision_kind="daily")

    assert first == second
    event = first["events"][0]
    assert event["schema_version"] == DECISION_EVENT_SCHEMA_VERSION
    assert event["event_id"] == "daily:report-1:0:161725"
    assert event["action"] == "分批加仓"
    assert event["action_source"] == "post_guard_final"
    assert event["evaluation_class"] == "bullish"
    assert event["portfolio_snapshot_id"] == "pps-1"
    assert event["ledger_version"] == "pl1:3:abc"
    assert event["position_complete"] is True
    assert event["model_version"] == "deepseek-reasoner"
    assert event["fee_policy"] == {
        "model_version": "fee_assumption.initial_principal_haircut.v1",
        "status": "available",
        "fee_source": "user_assumption",
        "round_trip_fee_percent": 1.25,
        "fee_calculation": "initial_principal_haircut",
        "is_actual_cost": False,
        "recurring_fund_expenses": "already_embedded_in_nav",
    }
    assert [row["horizon_trading_days"] for row in first["observations"]] == [1, 5, 20]
    assert all(row["status"] == "pending" for row in first["observations"])


def test_discovery_observation_actions_never_enter_buy_denominator(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    report = {
        "id": "discovery-1",
        "created_at": "2026-07-12T08:00:00+00:00",
        "provider": "deepseek",
        "recommendations": [
            {"fund_code": "008586", "fund_name": "示例基金", "action": "建议关注"},
        ],
        "discovery_facts": {
            "portfolio_position_snapshot": _position_snapshot(),
            "profile": {"round_trip_fee_percent": 1.5},
        },
    }

    bundle = build_report_decision_bundle(report, decision_kind="discovery")

    event = bundle["events"][0]
    assert event["event_id"] == "discovery:discovery-1:0:008586"
    assert event["evaluation_class"] == "watch_only"
    assert event["eligible"] is False
    assert [row["horizon_trading_days"] for row in bundle["observations"]] == [5, 20, 60]
    assert all(row["status"] == "observation" for row in bundle["observations"])


def test_batch_two_snapshot_is_traceable_but_not_promoted_to_complete(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-11",
    )
    report = {
        "id": "legacy-position-report",
        "created_at": "2026-07-11T05:00:00+00:00",
        "fund_recommendations": [],
        "analysis_facts": {
            "portfolio_snapshot": {
                "snapshot_id": "batch-two-id",
                "as_of_date": "2026-07-11",
                "captured_at": "2026-07-11T04:00:00+00:00",
                "source": "snapshot",
                "authoritative": True,
                "holdings_fingerprint": "codes-only",
            }
        },
    }

    bundle = build_report_decision_bundle(report, decision_kind="daily")

    snapshot = bundle["position_snapshot"]
    assert snapshot["snapshot_id"] == "batch-two-id"
    assert snapshot["position_complete"] is False
    assert snapshot["ledger_version"] is None
    assert snapshot["cash"] == {"balance_cny": None, "status": "unknown"}


def test_mysql_fallback_bundle_is_not_audit_eligible(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    report = {
        "id": "fallback-report",
        "created_at": "2026-07-12T08:00:00+00:00",
        "fund_recommendations": [],
        "analysis_facts": {},
    }

    bundle = build_report_decision_bundle(
        report,
        decision_kind="daily",
        store_authority="fallback_non_audited",
    )

    assert bundle["contract"]["audit_eligible"] is False
    assert bundle["contract"]["store_authority"] == "fallback_non_audited"
