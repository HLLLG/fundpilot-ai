from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.config import Settings
from app.models import FundDiscoveryReport, InvestorProfile
from app.services.decision_repository import (
    canonical_json,
    normalize_decision_quality_artifact_receipt,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    build_prompt_shadow_input_artifact,
    normalize_artifact_receipt_ref,
    normalize_prompt_gate_policy,
    normalize_prompt_shadow_attempt,
    normalize_prompt_shadow_registration,
)
from app.services.prompt_shadow_service import (
    PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT,
    build_current_prompt_shadow_policy,
    build_prompt_shadow_projection,
    prepare_prompt_shadow_champion,
)


BASE = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="sk-" + "a" * 40,
        prompt_shadow_enabled=True,
        prompt_shadow_assignment_secret="unit-test-secret-never-persist",
        prompt_shadow_assignment_key_id="test-key-v1",
        prompt_shadow_sample_basis_points=10_000,
        prompt_shadow_max_challenger_calls_per_day=5,
    )


def _receipt_ref(
    *,
    user_id: int,
    envelope: dict[str, Any],
    source_at: datetime,
    visible_at: datetime,
) -> dict[str, Any]:
    receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": user_id,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": envelope["artifact_type"],
            "artifact_content_hash": envelope["content_hash"],
            "source_row_created_at": source_at.isoformat(),
            "source_visible_at": visible_at.isoformat(),
            "store_authority": "primary",
        }
    )
    return normalize_artifact_receipt_ref(
        {
            "user_id": user_id,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": envelope["artifact_type"],
            "artifact_content_hash": envelope["content_hash"],
            "receipt_id": receipt["receipt_id"],
            "receipt_content_hash": receipt["content_hash"],
            "source_row_created_at": receipt["source_row_created_at"],
            "source_visible_at": receipt["source_visible_at"],
        },
        expected_user_id=user_id,
        expected_artifact_type=envelope["artifact_type"],
    )


def test_current_policy_is_strict_preregistered_and_contains_no_secret() -> None:
    settings = _settings()
    policy = build_current_prompt_shadow_policy(settings)

    assert normalize_prompt_gate_policy(policy) == policy
    assert policy["automatic_promotion_allowed"] is False
    assert policy["budget"]["max_challenger_calls_per_day"] == 5
    assert policy["assignment"]["sample_basis_points"] == 10_000
    assert settings.prompt_shadow_assignment_secret not in canonical_json(policy)
    assert PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT in policy["challenger_prompt"][
        "template_snapshot"
    ]


def test_prepare_registers_exact_provider_pair_before_network(monkeypatch) -> None:
    from app.services import prompt_shadow_service as service

    settings = _settings()
    run_state: dict[str, Any] = {}
    stored_artifacts: list[dict[str, Any]] = []

    def put_receipted(*, user_id: int, artifact: dict[str, Any]):
        envelope = build_prompt_shadow_input_artifact(user_id=user_id, artifact=artifact)
        stored_artifacts.append(artifact)
        if envelope["artifact_type"] == PROMPT_GATE_POLICY_ARTIFACT_TYPE:
            source = BASE - timedelta(seconds=20)
        else:
            source = BASE + timedelta(seconds=2)
        return envelope, _receipt_ref(
            user_id=user_id,
            envelope=envelope,
            source_at=source,
            visible_at=source + timedelta(milliseconds=10),
        )

    def create_run(**kwargs):
        run_state.update(
            {
                "userId": kwargs["user_id"],
                "run_id": kwargs["run_id"],
                "status": "registration_pending_receipt",
                "state_version": 0,
            }
        )
        return dict(run_state)

    def transition(**kwargs):
        assert run_state["status"] == kwargs["expected_status"]
        assert run_state["state_version"] == kwargs["expected_state_version"]
        run_state.update(kwargs.get("updates") or {})
        run_state["status"] = kwargs["new_status"]
        run_state["state_version"] += 1
        return dict(run_state)

    monkeypatch.setattr(service, "_put_receipted_artifact", put_receipted)
    monkeypatch.setattr(service, "create_prompt_shadow_run", create_run)
    monkeypatch.setattr(service, "transition_prompt_shadow_run", transition)

    payload = {"budget": 1000, "candidate_codes": ["000001", "000002"]}
    capture = prepare_prompt_shadow_champion(
        user_id=17,
        transport="sync",
        champion_system_prompt="Champion system prompt",
        challenger_system_prompt="Challenger system prompt",
        user_payload=payload,
        model="deepseek-test",
        max_tokens=1800,
        target_sectors=["technology"],
        focus_sectors=["technology"],
        scan_mode="full_market",
        candidate_pool=[{"fund_code": "000001"}, {"fund_code": "000002"}],
        discovery_facts={"market_regime": "neutral"},
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=1000,
        sector_heat=[],
        market_news=[],
        topic_briefs=[],
        analysis_mode="fast",
        decision_at=BASE,
        default_prompt_only=True,
        news_tool_rounds=0,
        settings=settings,
        now=BASE + timedelta(seconds=1),
    )

    assert capture is not None
    assert run_state["status"] == "champion_call_started"
    assert capture.trace_collector.trace is None
    assert capture.registration["prompt_pair"]["champion_provider_payload"][
        "messages"
    ][1]["content"] == canonical_json(payload)
    assert capture.registration["prompt_pair"]["challenger_provider_payload"][
        "messages"
    ][1]["content"] == canonical_json(payload)
    assert (
        capture.registration["prompt_pair"]["champion_provider_payload_hash"]
        == capture.champion_attempt["provider_payload_hash"]
    )
    assert normalize_prompt_shadow_registration(
        capture.registration,
        policy=capture.policy,
        expected_user_id=17,
    ) == capture.registration
    assert normalize_prompt_shadow_attempt(
        capture.champion_attempt,
        registration=capture.registration,
        expected_user_id=17,
    ) == capture.champion_attempt
    assert settings.prompt_shadow_assignment_secret not in canonical_json(stored_artifacts)


def test_policy_first_seen_after_decision_only_warms_next_request(monkeypatch) -> None:
    from app.services import prompt_shadow_service as service

    def late_policy(*, user_id: int, artifact: dict[str, Any]):
        envelope = build_prompt_shadow_input_artifact(user_id=user_id, artifact=artifact)
        source = BASE + timedelta(seconds=2)
        return envelope, _receipt_ref(
            user_id=user_id,
            envelope=envelope,
            source_at=source,
            visible_at=source,
        )

    monkeypatch.setattr(service, "_put_receipted_artifact", late_policy)
    monkeypatch.setattr(
        service,
        "create_prompt_shadow_run",
        lambda **_kwargs: pytest.fail("late policy must not register this decision"),
    )

    capture = prepare_prompt_shadow_champion(
        user_id=17,
        transport="sync",
        champion_system_prompt="Champion",
        challenger_system_prompt="Challenger",
        user_payload={"budget": 1000},
        model="deepseek-test",
        max_tokens=1800,
        target_sectors=[],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=[],
        discovery_facts={},
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=1000,
        sector_heat=[],
        market_news=[],
        topic_briefs=[],
        analysis_mode="fast",
        decision_at=BASE,
        default_prompt_only=True,
        news_tool_rounds=0,
        settings=_settings(),
        now=BASE + timedelta(seconds=1),
    )
    assert capture is None


def test_projection_freezes_only_post_guard_report() -> None:
    report = FundDiscoveryReport(
        id="report-1",
        title="No eligible opportunity",
        recommendations=[],
        eliminated_candidates=[],
        allocation_plan={"summary": "keep cash"},
        discovery_facts={
            "fund_lookthrough_claim_audit": {"status": "sanitized"}
        },
        analysis_mode="fast",
    )

    projection = build_prompt_shadow_projection(
        report=report,
        requested_budget_yuan=1000,
    )

    assert projection["recommendations"] == []
    assert projection["allocations"] == []
    assert projection["unallocated_budget_yuan"] == 1000
    assert projection["claim_audit"]["status"] == "sanitized"
    assert projection["automatic_promotion_allowed"] is False
