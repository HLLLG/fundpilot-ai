from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_production_compose_runs_two_configurable_workers() -> None:
    compose = (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "WEB_CONCURRENCY: ${WEB_CONCURRENCY:-2}" in compose
    assert "FUND_AI_RUNTIME_ROLE: api" in compose
    assert '"--workers", "1"' not in compose


def test_production_compose_has_one_supervised_background_worker() -> None:
    compose = (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "  worker:" in compose
    assert "FUND_AI_RUNTIME_ROLE: worker" in compose
    assert '["python", "-m", "app.background_worker"]' in compose
    assert '["CMD", "python", "-m", "app.background_worker", "--healthcheck"]' in compose
    assert compose.count(
        "FUND_AI_BACKGROUND_WORKER_HEARTBEAT_PATH: /app/data/background-worker-heartbeat.json"
    ) == 2


def test_cloud_compose_shares_worker_heartbeat_with_request_api() -> None:
    compose = (REPO_ROOT / "docker-compose.cloud.yml").read_text(encoding="utf-8")

    assert compose.count(
        "FUND_AI_BACKGROUND_WORKER_HEARTBEAT_PATH: /app/data/background-worker-heartbeat.json"
    ) == 2


def test_both_api_images_default_to_two_workers_without_hardcoding_cli_flag() -> None:
    dockerfiles = [
        (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"),
        (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8"),
    ]

    for dockerfile in dockerfiles:
        assert "ENV WEB_CONCURRENCY=2" in dockerfile
        assert '"--workers"' not in dockerfile


def test_lighthouse_deploy_waits_for_worker_and_runs_quality_smoke() -> None:
    deploy = (REPO_ROOT / "deploy" / "lighthouse" / "deploy.sh").read_text(
        encoding="utf-8"
    )

    assert "--force-recreate api worker" in deploy
    assert "waiting for background worker health" in deploy
    assert "evaluate_decision_quality.py" in deploy
    assert "--dry-run" in deploy
    assert "rollback_has_worker" in deploy
    assert "--remove-orphans" in deploy


def test_admin_static_routes_are_required_and_support_trailing_slashes() -> None:
    deploy = (REPO_ROOT / "deploy" / "lighthouse" / "deploy.sh").read_text(
        encoding="utf-8"
    )
    nginx = (REPO_ROOT / "deploy" / "nginx" / "fundpilot.conf").read_text(
        encoding="utf-8"
    )

    for route in ("admin/users", "reset-password"):
        assert f'"{route}.html"' in deploy
        assert f"curl -fsS http://127.0.0.1/{route}/" in deploy
        assert f"location = /{route}/" in nginx
        assert f"try_files /{route}.html =404;" in nginx
