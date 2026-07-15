from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.db_migrations import run_migrations
from app.services.prompt_shadow_repository import (
    PromptShadowCasConflict,
    PromptShadowConflict,
    PromptShadowIntegrityError,
    advance_prompt_shadow_budget,
    create_prompt_shadow_run,
    finalize_prompt_shadow_challenger,
    get_prompt_shadow_budget,
    get_prompt_shadow_run,
    lease_prompt_shadow_run,
    list_prompt_shadow_runs,
    transition_prompt_shadow_run,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.commit()
    return connection


def _at(minutes: int = 0) -> str:
    return (
        datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)
        + timedelta(minutes=minutes)
    ).isoformat()


def _create(connection: sqlite3.Connection, *, user_id: int = 7, suffix: str = "1"):
    return create_prompt_shadow_run(
        user_id=user_id,
        run_id=f"psr_{suffix}",
        policy_id="policy_v2",
        policy_hash=_sha("policy"),
        decision_at=_at(),
        registration_artifact_id=f"dqa_registration_{suffix}",
        created_at=_at(1),
        connection=connection,
    )


def _champion_succeeded(
    connection: sqlite3.Connection,
    *,
    user_id: int = 7,
    suffix: str = "1",
):
    run = _create(connection, user_id=user_id, suffix=suffix)
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_attempt_pending_receipt",
        updated_at=_at(2),
        updates={"champion_attempt_artifact_id": f"dqa_champion_attempt_{suffix}"},
        connection=connection,
    )
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_ready",
        updated_at=_at(3),
        connection=connection,
    )
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_call_started",
        updated_at=_at(4),
        updates={"champion_network_started_at": _at(4)},
        connection=connection,
    )
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_output_pending_receipt",
        updated_at=_at(5),
        updates={"champion_output_artifact_id": f"dqa_champion_output_{suffix}"},
        connection=connection,
    )
    return transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_succeeded",
        updated_at=_at(6),
        updates={
            "champion_report_id": f"report-{suffix}",
            "challenger_deadline_at": _at(21),
        },
        connection=connection,
    )


def test_run_create_is_tenant_scoped_and_retry_is_idempotent() -> None:
    connection = _connection()
    created = _create(connection)

    assert get_prompt_shadow_run(
        user_id=7, run_id=created["run_id"], connection=connection
    ) == created
    assert get_prompt_shadow_run(
        user_id=8, run_id=created["run_id"], connection=connection
    ) is None
    assert list_prompt_shadow_runs(user_id=8, connection=connection) == []

    retried = create_prompt_shadow_run(
        user_id=7,
        run_id=created["run_id"],
        policy_id=created["policy_id"],
        policy_hash=created["policy_hash"],
        decision_at=created["decision_at"],
        registration_artifact_id=created["registration_artifact_id"],
        created_at=_at(9),
        connection=connection,
    )
    assert retried == created


def test_run_identity_conflict_fails_closed() -> None:
    connection = _connection()
    created = _create(connection)

    with pytest.raises(PromptShadowConflict):
        create_prompt_shadow_run(
            user_id=7,
            run_id=created["run_id"],
            policy_id=created["policy_id"],
            policy_hash=_sha("different"),
            decision_at=created["decision_at"],
            registration_artifact_id=created["registration_artifact_id"],
            created_at=_at(2),
            connection=connection,
        )


def test_transition_requires_exact_tenant_status_and_state_version() -> None:
    connection = _connection()
    run = _create(connection)
    transitioned = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status="registration_pending_receipt",
        expected_state_version=0,
        new_status="champion_attempt_pending_receipt",
        updated_at=_at(2),
        updates={"champion_attempt_artifact_id": "dqa_attempt"},
        connection=connection,
    )
    assert transitioned["state_version"] == 1

    for user_id, version in ((8, 1), (7, 0)):
        with pytest.raises(PromptShadowCasConflict):
            transition_prompt_shadow_run(
                user_id=user_id,
                run_id=run["run_id"],
                expected_status="champion_attempt_pending_receipt",
                expected_state_version=version,
                new_status="champion_ready",
                updated_at=_at(3),
                connection=connection,
            )


def test_network_start_is_required_before_call_and_cannot_be_rewritten() -> None:
    connection = _connection()
    run = _create(connection)
    run = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_attempt_pending_receipt",
        updated_at=_at(2),
        updates={"champion_attempt_artifact_id": "dqa_attempt"},
        connection=connection,
    )
    run = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_ready",
        updated_at=_at(3),
        connection=connection,
    )

    with pytest.raises(PromptShadowIntegrityError, match="network start"):
        transition_prompt_shadow_run(
            user_id=7,
            run_id=run["run_id"],
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="champion_call_started",
            updated_at=_at(4),
            connection=connection,
        )

    started = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_call_started",
        updated_at=_at(4),
        updates={"champion_network_started_at": _at(4)},
        connection=connection,
    )
    with pytest.raises(PromptShadowIntegrityError, match="immutable"):
        transition_prompt_shadow_run(
            user_id=7,
            run_id=run["run_id"],
            expected_status=started["status"],
            expected_state_version=started["state_version"],
            new_status="champion_output_pending_receipt",
            updated_at=_at(5),
            updates={
                "champion_network_started_at": _at(5),
                "champion_output_artifact_id": "dqa_output",
            },
            connection=connection,
        )


def test_lease_and_budget_reservation_commit_as_one_state_change() -> None:
    connection = _connection()
    run = _champion_succeeded(connection)

    result = lease_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_state_version=run["state_version"],
        lease_owner_hash=_sha("worker"),
        lease_token_hash=_sha("lease"),
        lease_acquired_at=_at(7),
        lease_expires_at=_at(10),
        scope_key="global_default",
        budget_date_local="2026-07-15",
        policy_id=run["policy_id"],
        policy_hash=run["policy_hash"],
        max_calls=2,
        connection=connection,
    )

    assert result["reserved"] is True
    assert result["ordinal"] == 1
    assert result["run"]["status"] == "challenger_leased"
    assert result["run"]["budget_reserved_at"] == _at(7)
    assert result["budget"]["reserved_calls"] == 1


def test_exhausted_budget_terminalizes_run_without_second_reservation() -> None:
    connection = _connection()
    first = _champion_succeeded(connection, suffix="1")
    second = _champion_succeeded(connection, suffix="2")
    common = {
        "lease_owner_hash": _sha("worker"),
        "lease_acquired_at": _at(7),
        "lease_expires_at": _at(10),
        "scope_key": "global_default",
        "budget_date_local": "2026-07-15",
        "policy_id": first["policy_id"],
        "policy_hash": first["policy_hash"],
        "max_calls": 1,
        "connection": connection,
    }
    lease_prompt_shadow_run(
        user_id=7,
        run_id=first["run_id"],
        expected_state_version=first["state_version"],
        lease_token_hash=_sha("lease-1"),
        **common,
    )
    skipped = lease_prompt_shadow_run(
        user_id=7,
        run_id=second["run_id"],
        expected_state_version=second["state_version"],
        lease_token_hash=_sha("lease-2"),
        **common,
    )

    assert skipped["reserved"] is False
    assert skipped["run"]["status"] == "budget_skipped"
    assert skipped["run"]["lease_owner_hash"] is None
    budget = get_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        connection=connection,
    )
    assert budget is not None and budget["reserved_calls"] == 1


def test_budget_counter_enforces_reserved_started_terminal_order() -> None:
    connection = _connection()
    run = _champion_succeeded(connection)
    lease_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_state_version=run["state_version"],
        lease_owner_hash=_sha("worker"),
        lease_token_hash=_sha("lease"),
        lease_acquired_at=_at(7),
        lease_expires_at=_at(10),
        scope_key="global_default",
        budget_date_local="2026-07-15",
        policy_id=run["policy_id"],
        policy_hash=run["policy_hash"],
        max_calls=1,
        connection=connection,
    )

    with pytest.raises(PromptShadowConflict, match="started"):
        advance_prompt_shadow_budget(
            scope_key="global_default",
            budget_date_local="2026-07-15",
            action="completed",
            updated_at=_at(8),
            connection=connection,
        )
    started = advance_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        action="started",
        updated_at=_at(8),
        connection=connection,
    )
    assert started["started_calls"] == 1
    completed = advance_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        action="completed",
        updated_at=_at(9),
        connection=connection,
    )
    assert completed["completed_calls"] == 1
    with pytest.raises(PromptShadowConflict, match="started"):
        advance_prompt_shadow_budget(
            scope_key="global_default",
            budget_date_local="2026-07-15",
            action="failed",
            updated_at=_at(10),
            connection=connection,
        )


def test_challenger_run_and_budget_terminalize_atomically_and_retry_idempotently() -> None:
    connection = _connection()
    run = _champion_succeeded(connection)
    leased = lease_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_state_version=run["state_version"],
        lease_owner_hash=_sha("worker"),
        lease_token_hash=_sha("lease"),
        lease_acquired_at=_at(7),
        lease_expires_at=_at(14),
        scope_key="global_default",
        budget_date_local="2026-07-15",
        policy_id=run["policy_id"],
        policy_hash=run["policy_hash"],
        max_calls=1,
        connection=connection,
    )["run"]
    attempt_pending = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=leased["status"],
        expected_state_version=leased["state_version"],
        new_status="challenger_attempt_pending_receipt",
        updated_at=_at(8),
        updates={"challenger_attempt_artifact_id": "dqa_challenger_attempt"},
        connection=connection,
    )
    ready = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=attempt_pending["status"],
        expected_state_version=attempt_pending["state_version"],
        new_status="challenger_ready",
        updated_at=_at(9),
        connection=connection,
    )
    started = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=ready["status"],
        expected_state_version=ready["state_version"],
        new_status="challenger_call_started",
        updated_at=_at(10),
        updates={"challenger_network_started_at": _at(10)},
        connection=connection,
    )
    advance_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        action="started",
        updated_at=_at(10),
        connection=connection,
    )
    pending = transition_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_status=started["status"],
        expected_state_version=started["state_version"],
        new_status="challenger_output_pending_receipt",
        updated_at=_at(11),
        updates={"challenger_output_artifact_id": "dqa_challenger_output"},
        connection=connection,
    )

    completed = finalize_prompt_shadow_challenger(
        user_id=7,
        run_id=run["run_id"],
        expected_status=pending["status"],
        expected_state_version=pending["state_version"],
        budget_action="completed",
        new_status="completed",
        updated_at=_at(12),
        terminal_reason="paired_output_receipted",
        connection=connection,
    )
    retried = finalize_prompt_shadow_challenger(
        user_id=7,
        run_id=run["run_id"],
        expected_status=pending["status"],
        expected_state_version=pending["state_version"],
        budget_action="completed",
        new_status="completed",
        updated_at=_at(12),
        terminal_reason="paired_output_receipted",
        connection=connection,
    )

    assert retried == completed
    budget = get_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        connection=connection,
    )
    assert budget is not None
    assert budget["started_calls"] == 1
    assert budget["completed_calls"] == 1
    assert budget["failed_calls"] == 0


def test_expired_deadline_never_consumes_budget() -> None:
    connection = _connection()
    run = _champion_succeeded(connection)
    result = lease_prompt_shadow_run(
        user_id=7,
        run_id=run["run_id"],
        expected_state_version=run["state_version"],
        lease_owner_hash=_sha("worker"),
        lease_token_hash=_sha("lease"),
        lease_acquired_at=_at(22),
        lease_expires_at=_at(24),
        scope_key="global_default",
        budget_date_local="2026-07-15",
        policy_id=run["policy_id"],
        policy_hash=run["policy_hash"],
        max_calls=1,
        connection=connection,
    )
    assert result["run"]["status"] == "challenger_timed_out"
    assert result["budget"] is None
    assert get_prompt_shadow_budget(
        scope_key="global_default",
        budget_date_local="2026-07-15",
        connection=connection,
    ) is None
