from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.models import InvestorProfile
from app.services.decision_repository import (
    canonical_json,
    normalize_decision_quality_artifact_receipt,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    build_prompt_shadow_input_artifact,
    normalize_artifact_receipt_ref,
)
from app.services.prompt_shadow_service import prepare_prompt_shadow_champion


BASE = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="sk-" + "b" * 40,
        prompt_shadow_enabled=True,
        prompt_shadow_assignment_secret="worker-test-secret",
        prompt_shadow_assignment_key_id="worker-test-key",
        prompt_shadow_sample_basis_points=10_000,
        prompt_shadow_max_challenger_calls_per_day=5,
        prompt_shadow_lease_seconds=180,
    )


def _receipt_ref(
    *,
    user_id: int,
    envelope: dict[str, Any],
    source_at: datetime,
) -> dict[str, Any]:
    receipt = normalize_decision_quality_artifact_receipt(
        {
            "user_id": user_id,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": envelope["artifact_type"],
            "artifact_content_hash": envelope["content_hash"],
            "source_row_created_at": source_at.isoformat(),
            "source_visible_at": (source_at + timedelta(milliseconds=1)).isoformat(),
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


def _capture(monkeypatch):
    from app.services import prompt_shadow_service as service

    state: dict[str, Any] = {}

    def put(*, user_id: int, artifact: dict[str, Any]):
        envelope = build_prompt_shadow_input_artifact(user_id=user_id, artifact=artifact)
        source = (
            BASE - timedelta(seconds=10)
            if envelope["artifact_type"] == PROMPT_GATE_POLICY_ARTIFACT_TYPE
            else BASE + timedelta(seconds=2)
        )
        return envelope, _receipt_ref(
            user_id=user_id,
            envelope=envelope,
            source_at=source,
        )

    def create(**kwargs):
        state.update(
            {
                "userId": kwargs["user_id"],
                "run_id": kwargs["run_id"],
                "status": "registration_pending_receipt",
                "state_version": 0,
            }
        )
        return dict(state)

    def transition(**kwargs):
        state.update(kwargs.get("updates") or {})
        state["status"] = kwargs["new_status"]
        state["state_version"] = int(state.get("state_version", 0)) + 1
        return dict(state)

    monkeypatch.setattr(service, "_put_receipted_artifact", put)
    monkeypatch.setattr(service, "create_prompt_shadow_run", create)
    monkeypatch.setattr(service, "transition_prompt_shadow_run", transition)
    capture = prepare_prompt_shadow_champion(
        user_id=17,
        transport="sync",
        champion_system_prompt="Champion system",
        challenger_system_prompt="Challenger system",
        user_payload={"budget": 1000, "candidate_codes": ["000001"]},
        model="deepseek-test",
        max_tokens=1800,
        target_sectors=["technology"],
        focus_sectors=["technology"],
        scan_mode="full_market",
        candidate_pool=[{"fund_code": "000001"}],
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
        settings=_settings(),
        now=BASE + timedelta(seconds=1),
    )
    assert capture is not None
    return capture


def test_worker_commits_attempt_and_budget_start_before_exact_provider_call(
    monkeypatch,
) -> None:
    from app.services import prompt_shadow_worker as worker

    capture = _capture(monkeypatch)
    settings = _settings()
    state: dict[str, Any] = {
        "userId": 17,
        "run_id": capture.run_id,
        "registration_artifact_id": capture.registration_ref["artifact_id"],
        "policy_id": capture.policy["policy_id"],
        "policy_hash": capture.policy["policy_hash"],
        "champion_report_id": "report-1",
        "challenger_deadline_at": (BASE + timedelta(minutes=10)).isoformat(),
        "status": "champion_succeeded",
        "state_version": 5,
    }
    budget_actions: list[str] = []
    transition_order: list[str] = []

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "_now", lambda: BASE + timedelta(seconds=4))
    monkeypatch.setattr(worker, "get_prompt_shadow_run", lambda **_kwargs: dict(state))
    monkeypatch.setattr(
        worker,
        "_inner_artifact",
        lambda *, artifact_id, **_kwargs: (
            capture.registration
            if artifact_id == capture.registration_ref["artifact_id"]
            else capture.policy
        ),
    )
    registration_receipt = {
        key: value
        for key, value in capture.registration_ref.items()
        if key != "registration_hash"
    }
    monkeypatch.setattr(worker, "_receipt_ref", lambda **_kwargs: registration_receipt)

    def lease(**_kwargs):
        state.update(
            {
                "status": "challenger_leased",
                "state_version": 6,
                "lease_owner_hash": "1" * 64,
                "lease_token_hash": "2" * 64,
                "lease_acquired_at": (BASE + timedelta(seconds=3)).isoformat(),
                "lease_expires_at": (BASE + timedelta(minutes=2)).isoformat(),
                "budget_scope_key": "global",
                "budget_date_local": "2026-07-15",
                "budget_reserved_at": (BASE + timedelta(seconds=3)).isoformat(),
            }
        )
        return {"reserved": True, "run": dict(state), "ordinal": 1, "budget": {}}

    monkeypatch.setattr(worker, "lease_prompt_shadow_run", lease)

    def put(*, user_id: int, artifact: dict[str, Any]):
        envelope = build_prompt_shadow_input_artifact(user_id=user_id, artifact=artifact)
        return envelope, _receipt_ref(
            user_id=user_id,
            envelope=envelope,
            source_at=BASE + timedelta(seconds=4),
        )

    monkeypatch.setattr(worker, "_put_receipted_artifact", put)

    def transition(**kwargs):
        transition_order.append(kwargs["new_status"])
        state.update(kwargs.get("updates") or {})
        state["status"] = kwargs["new_status"]
        state["state_version"] = int(state["state_version"]) + 1
        return dict(state)

    monkeypatch.setattr(worker, "transition_prompt_shadow_run", transition)
    monkeypatch.setattr(
        worker,
        "advance_prompt_shadow_budget",
        lambda *, action, **_kwargs: budget_actions.append(action) or {},
    )

    class FakeClient:
        def __init__(self):
            self._last_report_raw_content = None

        def _call_model(
            self,
            system_prompt,
            user_payload,
            model,
            *,
            trace_collector,
            exact_provider_payload,
        ):
            assert state["status"] == "challenger_call_started"
            assert budget_actions == ["started"]
            assert exact_provider_payload == capture.registration["prompt_pair"][
                "challenger_provider_payload"
            ]
            assert exact_provider_payload["messages"][1]["content"] == canonical_json(
                user_payload
            )
            raw = '{"title":"t","recommendations":[]}'
            trace_collector.start_request(exact_provider_payload)
            trace_collector.mark_response_started(http_status=200)
            trace_collector.observe_sync_envelope(b"{}")
            trace_collector.observe_content(raw)
            trace_collector.finish_success()
            self._last_report_raw_content = raw
            return {"title": "t", "recommendations": []}

    monkeypatch.setattr(worker, "DiscoveryClient", FakeClient)
    monkeypatch.setattr(worker, "_build_challenger_report", lambda **_kwargs: object())
    monkeypatch.setattr(
        worker,
        "_persist_challenger_output",
        lambda **_kwargs: {**state, "status": "challenger_output_pending_receipt"},
    )
    monkeypatch.setattr(
        worker,
        "_finalize_output_pending",
        lambda **_kwargs: budget_actions.append("completed")
        or {**state, "status": "completed", "terminal_reason": "paired"},
    )

    result = worker.process_prompt_shadow_run(
        user_id=17,
        run_id=capture.run_id,
        worker_id="worker-1",
        now=BASE + timedelta(seconds=3),
    )

    assert result["status"] == "completed"
    assert transition_order == [
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
    ]
    assert budget_actions == ["started", "completed"]


def test_reconcile_never_retries_expired_network_started_run(monkeypatch) -> None:
    from app.services import prompt_shadow_worker as worker

    run = {
        "userId": 17,
        "run_id": "dqsr_" + "a" * 64,
        "status": "challenger_call_started",
        "state_version": 9,
        "lease_expires_at": (BASE - timedelta(seconds=1)).isoformat(),
        "budget_scope_key": "global",
        "budget_date_local": "2026-07-15",
    }
    terminalizations: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker,
        "list_prompt_shadow_worker_candidates",
        lambda **_kwargs: [run],
    )
    monkeypatch.setattr(
        worker,
        "finalize_prompt_shadow_challenger",
        lambda **kwargs: terminalizations.append(
            (kwargs["new_status"], kwargs["budget_action"])
        )
        or {},
    )

    changed = worker.reconcile_prompt_shadow_stale_runs(now=BASE)

    assert changed == 1
    assert terminalizations == [("challenger_indeterminate", "failed")]


def test_reconcile_finishes_receipted_output_without_provider_replay(monkeypatch) -> None:
    from app.services import prompt_shadow_worker as worker

    run = {
        "userId": 17,
        "run_id": "dqsr_" + "1" * 64,
        "status": "challenger_output_pending_receipt",
        "state_version": 9,
        "challenger_output_artifact_id": "dqa_" + "2" * 64,
        "lease_expires_at": (BASE + timedelta(minutes=2)).isoformat(),
        "updated_at": BASE.isoformat(),
    }
    recovered: list[str] = []
    monkeypatch.setattr(
        worker,
        "list_prompt_shadow_worker_candidates",
        lambda **_kwargs: [dict(run)],
    )
    monkeypatch.setattr(
        worker,
        "_recover_output_pending",
        lambda *, run, **_kwargs: recovered.append(run["run_id"])
        or {**run, "status": "completed"},
    )
    monkeypatch.setattr(
        worker,
        "transition_prompt_shadow_run",
        lambda **_kwargs: pytest.fail("output recovery must not use stale transition"),
    )
    monkeypatch.setattr(
        worker,
        "stream_chat_completion",
        lambda **_kwargs: pytest.fail("output recovery must not replay provider"),
    )

    changed = worker.reconcile_prompt_shadow_stale_runs(
        now=BASE + timedelta(seconds=1)
    )

    assert changed == 1
    assert recovered == [run["run_id"]]
