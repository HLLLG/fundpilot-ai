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


def test_local_compose_shares_worker_heartbeat_with_request_api() -> None:
    compose = (REPO_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

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
        assert f"https://www.hllingxi.cn/{route}/" in deploy
        assert f"location = /{route}/" in nginx
        assert f"try_files /{route}.html =404;" in nginx


def test_production_nginx_enforces_canonical_https_and_managed_tls() -> None:
    nginx = (REPO_ROOT / "deploy" / "nginx" / "fundpilot.conf").read_text(
        encoding="utf-8"
    )
    deploy = (REPO_ROOT / "deploy" / "lighthouse" / "deploy.sh").read_text(
        encoding="utf-8"
    )
    renewal_hook = (
        REPO_ROOT
        / "deploy"
        / "lighthouse"
        / "reload-nginx-after-certificate-renewal.sh"
    ).read_text(encoding="utf-8")

    assert "listen 80 default_server;" in nginx
    assert "listen 443 ssl default_server;" in nginx
    assert "server_name hllingxi.cn;" in nginx
    assert "server_name www.hllingxi.cn;" in nginx
    assert "return 308 https://www.hllingxi.cn$request_uri;" in nginx
    assert "/etc/letsencrypt/live/hllingxi.cn/fullchain.pem" in nginx
    assert "/etc/letsencrypt/live/hllingxi.cn/privkey.pem" in nginx
    assert 'Strict-Transport-Security "max-age=31536000"' in nginx

    assert "certbot.timer" in deploy
    assert "fundpilot-nginx-reload" in deploy
    assert "unexpected HTTP redirect" in deploy
    assert "unexpected apex HTTPS redirect" in deploy
    assert "粤ICP备2026100543号-1" in deploy

    assert "nginx -t" in renewal_hook
    assert "nginx -s reload" in renewal_hook
    assert 'www.hllingxi.cn:443:127.0.0.1' in renewal_hook


def test_production_nginx_separates_streaming_and_buffered_api_policies() -> None:
    nginx = (REPO_ROOT / "deploy" / "nginx" / "fundpilot.conf").read_text(
        encoding="utf-8"
    )

    assert "upstream fundpilot_api {" in nginx
    assert "keepalive 32;" in nginx
    assert "location = /api/analyze/stream {" in nginx
    assert "location = /api/fund-discovery/stream {" in nginx
    assert r"location ~ ^/api/(?:fund-discovery/)?reports/[^/]+/chat$ {" in nginx
    assert "location /api/ {" in nginx
    assert nginx.count("proxy_buffering off;") == 3
    assert "proxy_buffering on;" in nginx
    assert "proxy_read_timeout 120s;" in nginx
    assert "proxy_set_header Connection \"\";" in nginx


def test_production_nginx_compresses_json_and_immutably_caches_hashed_assets() -> None:
    nginx = (REPO_ROOT / "deploy" / "nginx" / "fundpilot.conf").read_text(
        encoding="utf-8"
    )

    assert "gzip on;" in nginx
    assert "application/json" in nginx
    assert "text/event-stream" not in nginx
    assert "location ^~ /_next/static/ {" in nginx
    assert 'Cache-Control "public, max-age=31536000, immutable"' in nginx
    assert "expires -1;" in nginx
