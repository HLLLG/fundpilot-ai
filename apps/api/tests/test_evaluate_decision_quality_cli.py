from __future__ import annotations

from scripts import evaluate_decision_quality as cli

from app.services.decision_quality_snapshot import DecisionQualitySnapshotStorageError


def _result() -> dict:
    return {
        "schema_version": "decision_quality_snapshot_run.v1",
        "status": "completed",
        "evaluation_as_of": "2026-07-14T00:00:00+00:00",
        "window_days": 365,
        "persisted": True,
        "automatic_promotion_allowed": False,
        "user_count": 1,
        "snapshots": [
            {
                "user_id": 3,
                "snapshot_id": "dqs_" + "a" * 64,
                "status": "unavailable",
                "readiness_status": "insufficient_data",
                "mature_decision_day_count": 0,
                "formal_label_coverage_percent": None,
                "automatic_promotion_allowed": False,
            }
        ],
    }


def test_cli_persists_by_default_and_insufficient_sample_exits_zero(
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict] = []

    def run(**kwargs):
        calls.append(kwargs)
        return _result()

    monkeypatch.setattr(cli, "evaluate_and_persist_decision_quality_snapshots", run)
    code = cli.main(
        [
            "--user-id",
            "3",
            "--evaluation-as-of",
            "2026-07-14T00:00:00Z",
        ]
    )

    assert code == 0
    assert calls == [
        {
            "evaluation_as_of": "2026-07-14T00:00:00Z",
            "user_ids": [3],
            "window_days": 365,
            "persist": True,
        }
    ]
    assert "readiness=insufficient_data" in capsys.readouterr().out


def test_cli_all_users_dry_run_json(monkeypatch, capsys) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        cli,
        "evaluate_and_persist_decision_quality_snapshots",
        lambda **kwargs: calls.append(kwargs) or _result(),
    )

    assert (
        cli.main(
            [
                "--all-users",
                "--evaluation-as-of",
                "2026-07-14T00:00:00+00:00",
                "--window-days",
                "90",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        == 0
    )
    assert calls[0]["user_ids"] is None
    assert calls[0]["persist"] is False
    assert '"automatic_promotion_allowed": false' in capsys.readouterr().out


def test_cli_contract_or_storage_error_exits_two(monkeypatch, capsys) -> None:
    def unavailable(**_kwargs):
        raise DecisionQualitySnapshotStorageError("primary unavailable")

    monkeypatch.setattr(
        cli,
        "evaluate_and_persist_decision_quality_snapshots",
        unavailable,
    )
    code = cli.main(
        [
            "--all-users",
            "--evaluation-as-of",
            "2026-07-14T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "failed_closed" in captured.err
    assert "automatic_promotion_allowed" in captured.err


def test_cli_naive_cutoff_validation_failure_exits_two(monkeypatch, capsys) -> None:
    def reject(**_kwargs):
        raise ValueError("evaluation_as_of must include a timezone offset")

    monkeypatch.setattr(
        cli,
        "evaluate_and_persist_decision_quality_snapshots",
        reject,
    )
    assert (
        cli.main(
            [
                "--user-id",
                "1",
                "--evaluation-as-of",
                "2026-07-14T00:00:00",
            ]
        )
        == 2
    )
    assert "timezone" in capsys.readouterr().err
