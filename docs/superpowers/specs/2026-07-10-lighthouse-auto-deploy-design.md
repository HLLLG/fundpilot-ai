# Lighthouse Automatic Deployment Design

## Goal

After the existing `CI` workflow succeeds for a commit on `main`, automatically deploy that exact commit to the FundPilot Lighthouse server. A manual dispatch remains available for an operator-triggered redeploy.

## Scope and non-goals

The deployment updates the API container and static frontend only. MySQL data, uploaded files, `/srv/fundpilot/data`, `/srv/fundpilot/uploads`, `/srv/fundpilot/mysql`, and `.env.production` remain server-local and are never transferred to GitHub Actions.

The first version does not use GHCR, a self-hosted runner, blue-green containers, or automated database migrations. The current single-server Docker Compose topology remains unchanged.

## Trigger and security boundary

`deploy-lighthouse.yml` uses the existing `workflow_run` trigger for completed `CI` runs on `main`, plus `workflow_dispatch`. The deploy job proceeds only for a successful CI conclusion. It checks out `github.event.workflow_run.head_sha` for CI-triggered runs, avoiding a race where a later main commit is deployed accidentally.

The job uses a single `production` GitHub Environment containing `LIGHTHOUSE_HOST`, `LIGHTHOUSE_USER`, `LIGHTHOUSE_SSH_PRIVATE_KEY`, and `LIGHTHOUSE_KNOWN_HOSTS`. The action SSH key is dedicated to deployment and differs from personal workstation keys. The server authorizes its public key for the deployment user, which needs access to `/srv/fundpilot` and Docker but has no MySQL public port.

The workflow has one deployment concurrency group with `cancel-in-progress: false`, so complete deployments run serially rather than interrupting a server build or file sync.

## Build and deployment flow

1. GitHub Actions checks out the exact successful commit and builds `apps/web` on the hosted runner with `NEXT_PUBLIC_API_BASE_URL` explicitly set to an empty string. The static export therefore calls same-origin `/api/*` through Nginx.
2. The action opens an SSH connection to the Lighthouse server. The server uses its already configured GitHub SSH-over-443 path to fetch `origin/main` and checks out the requested commit in `/srv/fundpilot/repo`.
3. A versioned server deployment script holds `/srv/fundpilot/deploy.lock`, validates the checked-out commit, validates Nginx configuration, and rebuilds/restarts the API with `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build api`.
4. The action synchronizes the runner-built `apps/web/out/` into `/srv/fundpilot/web/`. The deployment script applies world-readable static-file permissions only under that directory, then verifies Nginx and the API health endpoint.
5. The workflow reports failure if any SSH command, Docker operation, static sync, or health check fails. Existing MySQL and host-mounted application data are untouched. A failure before the API restart leaves the old API running; a post-restart failure is surfaced for operator investigation rather than performing an unsafe data rollback.

## Versioned deployment assets

The repository will hold the production Compose file, Nginx configuration, deployment script, and deploy workflow. The root Dockerfile will include the verified domestic Debian and PyPI mirrors, so future server builds do not depend on the manually modified server copy.

`.env.production` remains ignored and is created only on the server. It contains all database and provider secrets. The production Compose configuration binds MySQL only to `127.0.0.1:13306` for the administrator's SSH-tunneled IDEA connection; port 3306 remains unavailable from the public network.

## Factor IC scheduled workflow

`factor-ic-refresh.yml` continues to run the existing weekly computation on GitHub-hosted runners. Its target changes from the CloudBase API URL to the Lighthouse production `/api/internal/factor-ic-snapshots` URL. `FUND_AI_FACTOR_IC_PUBLISH_TOKEN` on the server and the `FACTOR_IC_PUBLISH_TOKEN` GitHub Environment secret must have the same value.

## Validation

The deployment workflow runs the existing CI first, validates Compose syntax, checks Nginx configuration, waits for `http://127.0.0.1:8000/health`, and verifies the Nginx homepage. A manual dispatch is used once after setup to verify the complete path before relying on a regular `main` push.
