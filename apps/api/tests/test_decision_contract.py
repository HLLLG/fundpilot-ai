from __future__ import annotations

import copy

from app.services.decision_contract import (
    ANALYSIS_PROMPT_VERSION,
    DECISION_EVENT_SCHEMA_VERSION,
    DECISION_QUALITY_CONTRACT_VERSION,
    DECISION_REPLAY_BUNDLE_SCHEMA_VERSION,
    POLICY_VERSION,
    build_report_decision_bundle,
    decision_replay_bundle_error,
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
    assert [row["horizon_trading_days"] for row in first["observations"]] == [5, 20, 60]
    assert event["policy_version"] == "decision_policy.2026-07.v5"
    assert event["strategy_version"] == "decision_strategy.post_guard.v2"
    assert event["strategy_evaluation_policy"]["mode"] == "shadow_record_only"
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


def test_prompt_contract_is_frozen_per_event_and_drives_prompt_version(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    prompt_contract = {
        "schema_version": "prompt_contract.v1",
        "template_version": "analysis_prompt.test.v99",
        "contract_hash": "stable-contract-hash",
        "runtime": {"temperature": 0.2, "rounds": [0]},
    }
    report = {
        "id": "prompt-contract-report",
        "created_at": "2026-07-12T08:00:00+00:00",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "A", "action": "buy"},
            {"fund_code": "000002", "fund_name": "B", "action": "buy"},
        ],
        "analysis_facts": {
            "pipeline": {"prompt_contract": prompt_contract},
        },
    }

    first = build_report_decision_bundle(report, decision_kind="daily")
    second = build_report_decision_bundle(report, decision_kind="daily")

    assert first == second
    first_event, second_event = first["events"]
    assert first_event["schema_version"] == "decision_event.v2"
    assert first_event["prompt_version"] == "analysis_prompt.test.v99"
    assert first_event["policy_version"] == "decision_policy.2026-07.v5"
    assert first_event["prompt_contract"] == prompt_contract
    assert first_event["prompt_contract"] is not prompt_contract
    assert first_event["prompt_contract"]["runtime"] is not prompt_contract["runtime"]
    assert first_event["prompt_contract"] is not second_event["prompt_contract"]
    assert first_event["payload_hash"] == second["events"][0]["payload_hash"]

    prompt_contract["runtime"]["rounds"].append(1)
    first_event["prompt_contract"]["runtime"]["rounds"].append(2)
    assert second_event["prompt_contract"]["runtime"]["rounds"] == [0]
    assert second["events"][0]["prompt_contract"]["runtime"]["rounds"] == [0]


def test_legacy_report_without_prompt_contract_keeps_v2_shape(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    report = {
        "id": "legacy-no-prompt-contract",
        "created_at": "2026-07-12T08:00:00+00:00",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "A", "action": "buy"},
        ],
        "analysis_facts": {},
    }

    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]

    assert event["schema_version"] == DECISION_EVENT_SCHEMA_VERSION
    assert event["prompt_version"] == ANALYSIS_PROMPT_VERSION
    assert event["policy_version"] == POLICY_VERSION
    assert "prompt_contract" not in event


def test_top_level_prompt_contract_remains_readable_for_a2_compatibility(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-13",
    )
    report = {
        "id": "top-level-prompt-contract",
        "created_at": "2026-07-12T08:00:00+00:00",
        "recommendations": [
            {"fund_code": "000001", "fund_name": "A", "action": "buy"},
        ],
        "discovery_facts": {
            "prompt_contract": {
                "schema_version": "prompt_contract.v1",
                "template_version": "discovery_prompt.test.v88",
            }
        },
    }

    event = build_report_decision_bundle(report, decision_kind="discovery")["events"][0]

    assert event["prompt_version"] == "discovery_prompt.test.v88"
    assert event["prompt_contract"]["schema_version"] == "prompt_contract.v1"


def test_replay_bundle_binds_full_evidence_refs_inputs_and_variant(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_contract.resolve_confirm_date",
        lambda _trade_time: "2026-07-14",
    )
    evidence = {
        "schema_version": "1.0",
        "generated_at": "2026-07-14T10:03:00+00:00",
        "decision_ready": True,
        "items": [
            {
                "fact_id": "holdings.000001.daily_return_percent",
                "source": "official_nav",
                "source_type": "official",
                "as_of_date": "2026-07-13",
                "available_at": "2026-07-14T09:58:00+00:00",
                "fetched_at": "2026-07-14T10:02:00+00:00",
                "freshness": "fresh",
                "confidence": "high",
                "is_estimate": False,
            }
        ],
    }
    report = {
        "id": "replay-report",
        "created_at": "2026-07-14T10:00:00+00:00",
        "provider": "deepseek-reasoner",
        "fund_recommendations": [
            {
                "fund_code": "000001",
                "fund_name": "A",
                "fund_type": "equity",
                "action": "buy",
            }
        ],
        "analysis_facts": {
            "market_regime": "risk_on",
            "data_evidence": evidence,
        },
    }

    event = build_report_decision_bundle(report, decision_kind="daily")["events"][0]
    replay = event["replay_bundle"]

    assert event["quality_contract_version"] == DECISION_QUALITY_CONTRACT_VERSION
    assert event["replay_contract_required"] is True
    assert replay["schema_version"] == DECISION_REPLAY_BUNDLE_SCHEMA_VERSION
    assert replay["recorded_at"] == "2026-07-14T10:03:00+00:00"
    assert replay["data_evidence_snapshot"] == evidence
    assert replay["facts_snapshot"]["data_evidence"] == evidence
    assert event["replay_refs"] == replay["replay_refs"]
    assert event["replay_bundle_hash"] == replay["bundle_hash"]
    assert event["variant_manifest"] == replay["variant_manifest"]
    assert event["fund_type"] == "equity"
    assert event["market_regime"] == "risk_on"
    assert event["data_completeness"] == "complete"
    assert decision_replay_bundle_error(replay) is None

    tampered = copy.deepcopy(replay)
    tampered["replay_refs"][0]["ref_id"] = "data_evidence:placeholder"
    assert decision_replay_bundle_error(tampered) == "replay_bundle_refs_mismatch"
