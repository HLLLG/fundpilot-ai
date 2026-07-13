from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_production_images_include_outcome_settlement_cli() -> None:
    root_dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    api_dockerfile = (PROJECT_ROOT / "apps" / "api" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "COPY apps/api/scripts/settle_pending_outcomes.py "
        "/app/scripts/settle_pending_outcomes.py"
    ) in root_dockerfile
    assert (
        "COPY scripts/settle_pending_outcomes.py "
        "/app/scripts/settle_pending_outcomes.py"
    ) in api_dockerfile


def test_scheduled_workflow_runs_cli_inside_api_container() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "outcome-settlement.yml"
    ).read_text(encoding="utf-8")

    assert "LIGHTHOUSE_DEPLOY_ENABLED" in workflow
    assert "exec -T api python scripts/settle_pending_outcomes.py" in workflow
    assert "timezone: \"Asia/Shanghai\"" in workflow
