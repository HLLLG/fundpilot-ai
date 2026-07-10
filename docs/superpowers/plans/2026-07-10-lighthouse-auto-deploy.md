# Lighthouse Automatic Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically deploy each successful `main` CI commit to the FundPilot Lighthouse server while keeping production data and secrets server-local.

**Architecture:** GitHub Actions builds the static Next.js export, stages it over SSH, and invokes a versioned locked deployment script. The server fetches the exact validated commit over its existing GitHub SSH-over-443 connection, rebuilds only the API, publishes the staged frontend, and runs health checks. The existing weekly factor-IC workflow publishes to a production URL stored in the GitHub `production` Environment.

**Tech Stack:** GitHub Actions, Bash/OpenSSH/rsync, Docker Compose, Nginx, FastAPI, Next.js static export, pytest contract tests.

---

### Task 1: Add deployment asset contract tests

**Files:**
- Create: `apps/api/tests/test_lighthouse_deployment_assets.py`

- [ ] **Step 1: Write failing contracts for the production assets**

Create tests that resolve the repository root with `Path(__file__).resolve().parents[3]` and assert these exact behaviors:

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_root_dockerfile_uses_reachable_package_mirrors() -> None:
    dockerfile = _text("Dockerfile")
    assert "mirrors.tuna.tsinghua.edu.cn/debian" in dockerfile
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in dockerfile


def test_production_compose_keeps_state_on_host_and_ports_private() -> None:
    compose = yaml.safe_load(_text("docker-compose.production.yml"))
    services = compose["services"]
    assert services["mysql"]["ports"] == ["127.0.0.1:13306:3306"]
    assert "/srv/fundpilot/mysql:/var/lib/mysql" in services["mysql"]["volumes"]
    assert services["api"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "/srv/fundpilot/data:/app/data" in services["api"]["volumes"]
    assert "/srv/fundpilot/uploads:/app/uploads" in services["api"]["volumes"]
    assert "/srv/fundpilot/web:/usr/share/nginx/html:ro" in services["nginx"]["volumes"]


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
    assert "find \"$web_root\" -type d -exec chmod 755" in script
    assert "find \"$web_root\" -type f -exec chmod 644" in script


def test_deploy_workflow_only_deploys_successful_main_ci_commit() -> None:
    workflow = _text(".github/workflows/deploy-lighthouse.yml")
    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "branches: [main]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "NEXT_PUBLIC_API_BASE_URL: \"\"" in workflow
    assert "environment: production" in workflow
    assert "LIGHTHOUSE_SSH_PRIVATE_KEY" in workflow
    assert "LIGHTHOUSE_KNOWN_HOSTS" in workflow


def test_factor_ic_refresh_uses_production_environment_url() -> None:
    workflow = _text(".github/workflows/factor-ic-refresh.yml")
    assert "environment: production" in workflow
    assert "${{ vars.FACTOR_IC_PUBLISH_URL }}" in workflow
    assert "fundpilot-api-269544-5-1392809852.sh.run.tcloudbase.com" not in workflow
```

- [ ] **Step 2: Verify the contracts fail for missing deployment assets**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py -q
```

Expected: failures for the missing Compose, Nginx, deploy workflow/script, old factor URL, and missing Dockerfile mirrors.

- [ ] **Step 3: Commit the red tests**

```powershell
git add apps/api/tests/test_lighthouse_deployment_assets.py
git commit -m "test: define lighthouse deployment contracts"
```

### Task 2: Version the production runtime configuration

**Files:**
- Modify: `Dockerfile`
- Create: `docker-compose.production.yml`
- Create: `deploy/nginx/fundpilot.conf`

- [ ] **Step 1: Update the root Dockerfile package sources**

Before `apt-get update`, replace `deb.debian.org/debian` in `/etc/apt/sources.list.d/debian.sources` with `mirrors.tuna.tsinghua.edu.cn/debian`. Set `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` and pass `--index-url "$PIP_INDEX_URL"` to both requirements installs.

- [ ] **Step 2: Add the production Compose topology**

Create `docker-compose.production.yml` with `mysql`, `api`, and `nginx`. Use host mounts under `/srv/fundpilot`, bind MySQL only to `127.0.0.1:13306`, bind API only to `127.0.0.1:8000`, use one Uvicorn worker, and load `.env.production` for MySQL and API.

- [ ] **Step 3: Add the HTTP Nginx configuration**

Create `deploy/nginx/fundpilot.conf` with `server_name _`, static root `/usr/share/nginx/html`, ACME challenge support, same-origin `/api/` proxying, disabled proxy buffering, 3600-second streaming timeouts, and SPA fallback to `/index.html`.

- [ ] **Step 4: Verify the runtime configuration contracts pass**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py::test_root_dockerfile_uses_reachable_package_mirrors apps/api/tests/test_lighthouse_deployment_assets.py::test_production_compose_keeps_state_on_host_and_ports_private apps/api/tests/test_lighthouse_deployment_assets.py::test_nginx_preserves_same_origin_sse_proxying -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the runtime assets**

```powershell
git add Dockerfile docker-compose.production.yml deploy/nginx/fundpilot.conf
git commit -m "build: version lighthouse production runtime"
```

### Task 3: Implement the locked server deployment script

**Files:**
- Create: `deploy/lighthouse/deploy.sh`

- [ ] **Step 1: Implement input and state validation**

The script must use `set -Eeuo pipefail`, accept exactly one 40-character lowercase hexadecimal SHA, confirm `/srv/fundpilot/repo/.env.production` and the staged `/srv/fundpilot/releases/<sha>/web/index.html` exist, and acquire `/srv/fundpilot/deploy.lock` with `flock -w 900`.

- [ ] **Step 2: Implement exact-commit checkout and API deployment**

Refuse tracked local changes, fetch `origin main`, verify the SHA is an ancestor of `origin/main`, check it out detached, validate Compose, build/restart only `api`, and poll `http://127.0.0.1:8000/health` up to 30 times with five-second intervals.

- [ ] **Step 3: Publish the staged frontend and verify Nginx**

Use `rsync -a --delete` from the SHA-specific staging directory into `/srv/fundpilot/web/`, set directories to `755` and files to `644`, validate Nginx through Compose, start/recreate Nginx, and require `curl -fsS http://127.0.0.1/` to succeed.

- [ ] **Step 4: Verify the deploy-script contract**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py::test_deploy_script_locks_validates_and_checks_health -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit the deployment script**

```powershell
git add deploy/lighthouse/deploy.sh
git commit -m "feat: add locked lighthouse deploy script"
```

### Task 4: Replace CloudBase deployment with Lighthouse deployment

**Files:**
- Create: `.github/workflows/deploy-lighthouse.yml`
- Delete: `.github/workflows/deploy-web.yml`

- [ ] **Step 1: Add the guarded workflow trigger**

Use `workflow_run` for completed `CI` runs on `main` and `workflow_dispatch`. Grant only `contents: read`, reference the `production` Environment, add a single deployment concurrency group with `cancel-in-progress: false`, and deploy only successful CI runs or manual runs.

- [ ] **Step 2: Build the exact commit's frontend**

Checkout the exact `workflow_run.head_sha` or manual `github.sha`, use Node 22 with npm caching, run `npm ci`, and run the static build with `NEXT_PUBLIC_API_BASE_URL` explicitly empty.

- [ ] **Step 3: Stage and execute over native OpenSSH**

Write `LIGHTHOUSE_SSH_PRIVATE_KEY` and `LIGHTHOUSE_KNOWN_HOSTS` into mode-600 files, create `/srv/fundpilot/releases/<sha>/web`, rsync `apps/web/out/`, copy `deploy/lighthouse/deploy.sh` to a SHA-specific `/tmp` file, execute it with the SHA, and remove only that temporary script afterward.

- [ ] **Step 4: Remove the CloudBase deploy workflow**

Delete `.github/workflows/deploy-web.yml`, eliminating `TCB_ENV_ID`, the old API URL, CloudBase CLI installation/login, and `tcb app deploy`.

- [ ] **Step 5: Verify the workflow contract**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py::test_deploy_workflow_only_deploys_successful_main_ci_commit -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit the workflow migration**

```powershell
git add .github/workflows/deploy-lighthouse.yml .github/workflows/deploy-web.yml
git commit -m "ci: deploy successful main builds to lighthouse"
```

### Task 5: Migrate the scheduled factor-IC publisher

**Files:**
- Modify: `.github/workflows/factor-ic-refresh.yml`

- [ ] **Step 1: Use the production Environment target**

Add `environment: production` to the refresh job. Replace the hard-coded CloudBase URL with `${{ vars.FACTOR_IC_PUBLISH_URL }}` and add an early shell check that accepts only an HTTPS URL ending in `/api/internal/factor-ic-snapshots`.

- [ ] **Step 2: Keep the token secret and computation unchanged**

Continue passing `FACTOR_IC_PUBLISH_TOKEN` only to the publish step. Do not change the schedule, factor computation parameters, or generated artifact path.

- [ ] **Step 3: Verify the factor workflow contract**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py::test_factor_ic_refresh_uses_production_environment_url -q
```

Expected: `1 passed`.

- [ ] **Step 4: Commit the factor publisher migration**

```powershell
git add .github/workflows/factor-ic-refresh.yml
git commit -m "ci: publish factor IC snapshots to lighthouse"
```

### Task 6: Document one-time production setup and verify the branch

**Files:**
- Create: `docs/deploy/lighthouse-cicd.md`

- [ ] **Step 1: Document server prerequisites**

Describe the dedicated GitHub Actions SSH key, `/srv/fundpilot/releases`, Docker-group access, clean tracked server checkout, GitHub SSH-over-443 remote, `.env.production`, and matching `FUND_AI_FACTOR_IC_PUBLISH_TOKEN`.

- [ ] **Step 2: Document GitHub Environment configuration**

List `LIGHTHOUSE_HOST`, `LIGHTHOUSE_USER`, `LIGHTHOUSE_SSH_PRIVATE_KEY`, `LIGHTHOUSE_KNOWN_HOSTS`, `FACTOR_IC_PUBLISH_TOKEN`, and the `FACTOR_IC_PUBLISH_URL` Environment variable. Require HTTPS for the factor publisher and explain the first manual workflow dispatch verification.

- [ ] **Step 3: Run complete verification**

Run:

```powershell
python -m pytest apps/api/tests/test_lighthouse_deployment_assets.py -q
python -m pytest apps/api/tests/test_dockerfile_factor_ic_packaging.py apps/api/tests/test_factor_ic_workflow_contract.py -q
git diff --check
```

Expected: all deployment and existing factor workflow tests pass, and `git diff --check` exits zero.

- [ ] **Step 4: Commit the deployment documentation**

```powershell
git add docs/deploy/lighthouse-cicd.md
git commit -m "docs: add lighthouse CI/CD setup runbook"
```
