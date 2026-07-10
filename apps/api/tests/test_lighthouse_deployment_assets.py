from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_production_environment_file_is_ignored() -> None:
    ignored = {line.strip() for line in _text(".gitignore").splitlines()}
    assert ".env.production" in ignored


def test_root_dockerfile_uses_reachable_package_mirrors() -> None:
    dockerfile = _text("Dockerfile")
    assert "mirrors.tuna.tsinghua.edu.cn/debian" in dockerfile
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in dockerfile


def test_production_compose_keeps_state_on_host_and_ports_private() -> None:
    compose = _text("docker-compose.production.yml")
    assert '"127.0.0.1:13306:3306"' in compose
    assert "/srv/fundpilot/mysql:/var/lib/mysql" in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert "/srv/fundpilot/data:/app/data" in compose
    assert "/srv/fundpilot/uploads:/app/uploads" in compose
    assert "/srv/fundpilot/web:/usr/share/nginx/html:ro" in compose


def test_nginx_preserves_same_origin_sse_proxying() -> None:
    nginx = _text("deploy/nginx/fundpilot.conf")
    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "add_header X-Accel-Buffering no;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx


def test_deploy_script_locks_validates_and_checks_health() -> None:
    script = _text("deploy/lighthouse/deploy.sh")
    assert "flock -w" in script
    assert "git merge-base --is-ancestor" in script
    assert "docker compose" in script
    assert "http://127.0.0.1:8000/health" in script
    assert 'find "$web_root" -type d -exec chmod 755' in script
    assert 'find "$web_root" -type f -exec chmod 644' in script


def test_deploy_workflow_only_deploys_successful_main_ci_commit() -> None:
    workflow = _text(".github/workflows/deploy-lighthouse.yml")
    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "branches: [main]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "vars.LIGHTHOUSE_DEPLOY_ENABLED == 'true'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'NEXT_PUBLIC_API_BASE_URL: ""' in workflow
    assert "environment: production" in workflow
    assert "LIGHTHOUSE_SSH_PRIVATE_KEY" in workflow
    assert "LIGHTHOUSE_KNOWN_HOSTS" in workflow


def test_factor_ic_refresh_uses_production_environment_url() -> None:
    workflow = _text(".github/workflows/factor-ic-refresh.yml")
    assert "environment: production" in workflow
    assert "vars.FACTOR_IC_REFRESH_ENABLED == 'true'" in workflow
    assert "${{ vars.FACTOR_IC_PUBLISH_URL }}" in workflow
    assert "fundpilot-api-269544-5-1392809852.sh.run.tcloudbase.com" not in workflow
