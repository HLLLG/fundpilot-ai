from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_production_compose_runs_two_configurable_workers() -> None:
    compose = (REPO_ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "WEB_CONCURRENCY: ${WEB_CONCURRENCY:-2}" in compose
    assert '"--workers", "1"' not in compose


def test_both_api_images_default_to_two_workers_without_hardcoding_cli_flag() -> None:
    dockerfiles = [
        (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"),
        (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8"),
    ]

    for dockerfile in dockerfiles:
        assert "ENV WEB_CONCURRENCY=2" in dockerfile
        assert '"--workers"' not in dockerfile
