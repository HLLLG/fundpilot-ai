from __future__ import annotations

import json

import pytest

from scripts import settle_pending_outcomes as cli

from app.services.candidate_selection_outcomes import (
    CandidateSelectionSettlementError,
)
from app.services.outcome_settlement import OutcomeSettlementError


def _legacy_result() -> dict:
    return {
        "schema_version": "outcome_settlement.v1",
        "status": "completed",
        "as_of_date": "2026-07-14",
        "report_count": 1,
    }


def _candidate_result(
    *,
    status: str = "completed",
    failed_user_ids: list[int] | None = None,
) -> dict:
    return {
        "schema_version": "candidate_selection_settlement.v1",
        "status": status,
        "as_of_date": "2026-07-14",
        "failed_user_ids": failed_user_ids or [],
    }


def _receipt_result(*, failed_count: int = 0) -> dict:
    return {
        "status": "completed" if failed_count == 0 else "completed_with_failures",
        "scanned_count": 0,
        "finalized_count": 0,
        "failed_count": failed_count,
        "finalized_artifact_ids": [],
        "failures": [],
    }


@pytest.fixture(autouse=True)
def _stub_receipt_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "reconcile_decision_quality_artifact_receipts",
        lambda **_kwargs: _receipt_result(),
    )


def test_legacy_failure_does_not_block_candidate_settlement(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []

    def fail_legacy(**_kwargs):
        calls.append("legacy")
        raise OutcomeSettlementError("legacy contract failed")

    candidate = _candidate_result()

    def run_candidate(**_kwargs):
        calls.append("candidate")
        return candidate

    monkeypatch.setattr(cli, "settle_pending_outcomes", fail_legacy)
    monkeypatch.setattr(cli, "settle_candidate_selection_outcomes", run_candidate)

    code = cli.main(["--as-of-date", "2026-07-14"])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert calls == ["legacy", "candidate"]
    assert output["status"] == "failed_closed"
    assert output["error_type"] == "OutcomeSettlementError"
    assert output["candidate_selection"] == candidate


def test_candidate_failure_preserves_legacy_settlement(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []
    legacy = _legacy_result()

    def run_legacy(**_kwargs):
        calls.append("legacy")
        return legacy

    def fail_candidate(**_kwargs):
        calls.append("candidate")
        raise CandidateSelectionSettlementError("candidate contract failed")

    monkeypatch.setattr(cli, "settle_pending_outcomes", run_legacy)
    monkeypatch.setattr(cli, "settle_candidate_selection_outcomes", fail_candidate)

    code = cli.main(["--as-of-date", "2026-07-14"])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert calls == ["legacy", "candidate"]
    assert output["status"] == "completed"
    assert output["report_count"] == 1
    assert output["candidate_selection"]["status"] == "failed_closed"
    assert output["candidate_selection"]["error_type"] == (
        "CandidateSelectionSettlementError"
    )


def test_candidate_failed_users_exit_two_after_both_settlements(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []
    candidate = _candidate_result(
        status="completed_with_failures",
        failed_user_ids=[7],
    )
    monkeypatch.setattr(
        cli,
        "settle_pending_outcomes",
        lambda **_kwargs: calls.append("legacy") or _legacy_result(),
    )
    monkeypatch.setattr(
        cli,
        "settle_candidate_selection_outcomes",
        lambda **_kwargs: calls.append("candidate") or candidate,
    )

    assert cli.main(["--as-of-date", "2026-07-14"]) == 2
    output = json.loads(capsys.readouterr().out)

    assert calls == ["legacy", "candidate"]
    assert output["candidate_selection"]["failed_user_ids"] == [7]


def test_legacy_failed_users_exit_two_after_both_settlements(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []
    legacy = _legacy_result()
    legacy.update(
        {
            "status": "completed_with_failures",
            "failed_target_count": 1,
            "failed_user_ids": [4],
            "failure_reasons": [
                {"reason": "outcome_evaluation_failed", "count": 1}
            ],
        }
    )
    monkeypatch.setattr(
        cli,
        "settle_pending_outcomes",
        lambda **_kwargs: calls.append("legacy") or legacy,
    )
    monkeypatch.setattr(
        cli,
        "settle_candidate_selection_outcomes",
        lambda **_kwargs: calls.append("candidate") or _candidate_result(),
    )

    assert cli.main(["--as-of-date", "2026-07-14"]) == 2
    output = json.loads(capsys.readouterr().out)

    assert calls == ["legacy", "candidate"]
    assert output["failed_user_ids"] == [4]
    assert output["candidate_selection"]["status"] == "completed"


def test_pending_only_is_a_successful_retryable_run(
    monkeypatch,
    capsys,
) -> None:
    candidate = _candidate_result(status="completed_with_pending")
    candidate["pending_case_count"] = 2
    monkeypatch.setattr(
        cli,
        "settle_pending_outcomes",
        lambda **_kwargs: _legacy_result(),
    )
    monkeypatch.setattr(
        cli,
        "settle_candidate_selection_outcomes",
        lambda **_kwargs: candidate,
    )

    assert cli.main(["--as-of-date", "2026-07-14"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["status"] == "completed"
    assert output["candidate_selection"]["status"] == "completed_with_pending"
    assert output["candidate_selection"]["pending_case_count"] == 2


def test_receipt_reconcile_failure_does_not_block_both_settlements(
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []

    def fail_receipts(**_kwargs):
        calls.append("receipts")
        raise RuntimeError("receipt store unavailable")

    monkeypatch.setattr(
        cli,
        "reconcile_decision_quality_artifact_receipts",
        fail_receipts,
    )
    monkeypatch.setattr(
        cli,
        "settle_pending_outcomes",
        lambda **_kwargs: calls.append("legacy") or _legacy_result(),
    )
    monkeypatch.setattr(
        cli,
        "settle_candidate_selection_outcomes",
        lambda **_kwargs: calls.append("candidate") or _candidate_result(),
    )

    assert cli.main(["--as-of-date", "2026-07-14"]) == 2
    output = json.loads(capsys.readouterr().out)

    assert calls == ["receipts", "legacy", "candidate"]
    assert output["decision_quality_artifact_receipts"]["status"] == (
        "failed_closed"
    )


def test_receipts_are_reconciled_for_each_selected_user(
    monkeypatch,
    capsys,
) -> None:
    calls: list[tuple[int | None, int]] = []

    def reconcile(*, user_id=None, limit=0):
        calls.append((user_id, limit))
        result = _receipt_result()
        result["scanned_count"] = 1
        result["finalized_count"] = 1
        result["finalized_artifact_ids"] = [f"dqa_{user_id}"]
        return result

    monkeypatch.setattr(
        cli,
        "reconcile_decision_quality_artifact_receipts",
        reconcile,
    )
    monkeypatch.setattr(cli, "settle_pending_outcomes", lambda **_kwargs: _legacy_result())
    monkeypatch.setattr(
        cli,
        "settle_candidate_selection_outcomes",
        lambda **_kwargs: _candidate_result(),
    )

    assert cli.main(
        ["--user-id", "8", "--user-id", "3", "--max-receipts", "17"]
    ) == 0
    output = json.loads(capsys.readouterr().out)

    assert calls == [(3, 17), (8, 17)]
    receipts = output["decision_quality_artifact_receipts"]
    assert receipts["user_count"] == 2
    assert receipts["scanned_count"] == 2
    assert receipts["finalized_count"] == 2
