from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_production_images_include_outcome_settlement_cli() -> None:
    cli = (
        PROJECT_ROOT / "apps" / "api" / "scripts" / "settle_pending_outcomes.py"
    ).read_text(encoding="utf-8")
    quality_cli = (
        PROJECT_ROOT / "apps" / "api" / "scripts" / "evaluate_decision_quality.py"
    ).read_text(encoding="utf-8")
    root_dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    root_dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    api_dockerfile = (PROJECT_ROOT / "apps" / "api" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    api_dockerignore = (
        PROJECT_ROOT / "apps" / "api" / ".dockerignore"
    ).read_text(encoding="utf-8")

    assert (
        "COPY apps/api/scripts/settle_pending_outcomes.py "
        "/app/scripts/settle_pending_outcomes.py"
    ) in root_dockerfile
    assert (
        "COPY apps/api/scripts/evaluate_decision_quality.py "
        "/app/scripts/evaluate_decision_quality.py"
    ) in root_dockerfile
    assert (
        "COPY scripts/settle_pending_outcomes.py "
        "/app/scripts/settle_pending_outcomes.py"
    ) in api_dockerfile
    assert (
        "COPY scripts/evaluate_decision_quality.py "
        "/app/scripts/evaluate_decision_quality.py"
    ) in api_dockerfile
    assert "!apps/api/scripts/settle_pending_outcomes.py" in root_dockerignore
    assert "!apps/api/scripts/evaluate_decision_quality.py" in root_dockerignore
    assert "!scripts/settle_pending_outcomes.py" in api_dockerignore
    assert "!scripts/evaluate_decision_quality.py" in api_dockerignore
    assert "settle_candidate_selection_outcomes" in cli
    assert "reconcile_decision_quality_artifact_receipts" in cli
    assert "--max-candidate-cases" in cli
    assert "--max-receipts" in cli
    assert "--evaluation-as-of" in quality_cli
    assert "--all-users" in quality_cli


def test_scheduled_workflow_runs_cli_inside_api_container() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "outcome-settlement.yml"
    ).read_text(encoding="utf-8")

    assert "LIGHTHOUSE_DEPLOY_ENABLED" in workflow
    assert "exec -T api python scripts/settle_pending_outcomes.py" in workflow
    assert "exec -T api python scripts/evaluate_decision_quality.py" in workflow
    assert "--all-users" in workflow
    assert "--evaluation-as-of '$evaluation_as_of'" in workflow
    assert "timezone: \"Asia/Shanghai\"" in workflow
    assert "forward-only candidate T+20 settlement" in workflow
    assert "post-commit receipt reconciliation" in workflow
    assert "live calendar/NAV adapter-output receipts" in workflow
    assert "legacy candidate v3/v2 artifacts" in workflow
    assert "id: settlement" in workflow
    assert "id: quality-snapshot" in workflow
    assert workflow.count("continue-on-error: true") == 2
    assert "if: always()" in workflow
    assert "Fail after isolated settlement and snapshot attempts" in workflow
