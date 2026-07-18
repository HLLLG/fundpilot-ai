#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <40-character-main-commit-sha>" >&2
    exit 64
fi

deploy_sha="$1"
if [[ ! "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid deploy SHA: $deploy_sha" >&2
    exit 64
fi

repo_root="/srv/fundpilot/repo"
release_root="/srv/fundpilot/releases/$deploy_sha"
release_web="$release_root/web"
web_root="/srv/fundpilot/web"
lock_file="/srv/fundpilot/deploy.lock"
deployed_sha_file="/srv/fundpilot/DEPLOYED_SHA"
rollback_marker="/tmp/fundpilot-rollback-$deploy_sha"
previous_sha=""
deployment_error_status=""
web_was_activated=false

if [[ ! -d "$repo_root/.git" ]]; then
    echo "repository not found at $repo_root" >&2
    exit 66
fi
if [[ ! -f "$repo_root/.env.production" ]]; then
    echo ".env.production not found in $repo_root" >&2
    exit 66
fi
required_web_files=(
    "index.html"
    "login.html"
    "register.html"
    "settings.html"
    "admin/users.html"
    "reset-password.html"
)
for required_web_file in "${required_web_files[@]}"; do
    if [[ ! -f "$release_web/$required_web_file" ]]; then
        echo "staged frontend is incomplete: $release_web/$required_web_file is missing" >&2
        exit 66
    fi
done

exec 9>"$lock_file"
if ! flock -w 900 9; then
    echo "another FundPilot deployment still holds $lock_file" >&2
    exit 75
fi

cd "$repo_root"

if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
    echo "tracked server checkout changes would be overwritten; deployment stopped" >&2
    git status --short >&2
    exit 65
fi

git fetch --prune origin refs/heads/main:refs/remotes/origin/main
git cat-file -e "$deploy_sha^{commit}"
if ! git merge-base --is-ancestor "$deploy_sha" refs/remotes/origin/main; then
    echo "refusing to deploy a commit that is not on origin/main: $deploy_sha" >&2
    exit 65
fi

if [[ -f "$deployed_sha_file" ]]; then
    previous_sha="$(tr -d '[:space:]' < "$deployed_sha_file")"
    if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]] || ! git cat-file -e "$previous_sha^{commit}" 2>/dev/null; then
        echo "ignoring invalid previous deployed SHA: $previous_sha" >&2
        previous_sha=""
    fi
fi

rollback_release() {
    if [[ -z "$previous_sha" || "$previous_sha" == "$deploy_sha" ]]; then
        echo "no previous healthy release is available for rollback" >&2
        return 1
    fi

    echo "rolling back to previously healthy release $previous_sha" >&2
    git checkout --detach "$previous_sha" || return 1
    export FUND_AI_API_IMAGE="fundpilot-api:$previous_sha"

    local rollback_compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
    local rollback_services=(api)
    local rollback_has_worker=false
    "${rollback_compose[@]}" config -q || return 1
    if "${rollback_compose[@]}" config --services | grep -qx 'worker'; then
        rollback_services+=(worker)
        rollback_has_worker=true
    fi
    "${rollback_compose[@]}" up -d --build --remove-orphans "${rollback_services[@]}" || return 1

    local rollback_api_ready=false
    for attempt in $(seq 1 30); do
        if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
            rollback_api_ready=true
            break
        fi
        echo "waiting for rollback API health ($attempt/30)" >&2
        sleep 5
    done
    if [[ "$rollback_api_ready" != "true" ]]; then
        "${rollback_compose[@]}" logs --tail=150 api >&2 || true
        return 1
    fi

    if [[ "$rollback_has_worker" == "true" ]]; then
        local rollback_worker_ready=false
        for attempt in $(seq 1 30); do
            local rollback_worker_id
            rollback_worker_id="$("${rollback_compose[@]}" ps -q worker)"
            if [[ -n "$rollback_worker_id" ]] && [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$rollback_worker_id")" == "healthy" ]]; then
                rollback_worker_ready=true
                break
            fi
            echo "waiting for rollback worker health ($attempt/30)" >&2
            sleep 5
        done
        if [[ "$rollback_worker_ready" != "true" ]]; then
            "${rollback_compose[@]}" logs --tail=150 worker >&2 || true
            return 1
        fi
    fi

    if [[ "$web_was_activated" == "true" ]]; then
        local previous_web="/srv/fundpilot/releases/$previous_sha/web"
        if [[ ! -d "$previous_web" ]]; then
            echo "previous frontend release is unavailable: $previous_web" >&2
            return 1
        fi
        mkdir -p "$web_root" || return 1
        rsync -a --delete "$previous_web/" "$web_root/" || return 1
        find "$web_root" -type d -exec chmod 755 {} + || return 1
        find "$web_root" -type f -exec chmod 644 {} + || return 1
    fi
    "${rollback_compose[@]}" up -d --no-deps --force-recreate nginx || return 1
    "${rollback_compose[@]}" exec -T nginx nginx -t || return 1
    curl -fsS http://127.0.0.1/ >/dev/null || return 1
    echo "rollback to $previous_sha succeeded" >&2
}

on_deployment_error() {
    local command_status=$?
    local status="${deployment_error_status:-$command_status}"
    trap - ERR
    set +e
    if [[ -e "$rollback_marker" ]]; then
        exit "$status"
    fi
    : > "$rollback_marker"
    rollback_release
    local rollback_status=$?
    if [[ $rollback_status -ne 0 ]]; then
        echo "automatic rollback failed; manual recovery is required" >&2
    fi
    exit "$status"
}

rm -f "$rollback_marker"
trap on_deployment_error ERR
git checkout --detach "$deploy_sha"
export FUND_AI_API_IMAGE="fundpilot-api:$deploy_sha"

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
"${compose[@]}" config -q
"${compose[@]}" build api
"${compose[@]}" up -d mysql

mysql_ready=false
for attempt in $(seq 1 36); do
    mysql_container_id="$("${compose[@]}" ps -q mysql)"
    if [[ -n "$mysql_container_id" ]] && [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$mysql_container_id")" == "healthy" ]]; then
        mysql_ready=true
        break
    fi
    echo "waiting for MySQL health ($attempt/36)"
    sleep 5
done
if [[ "$mysql_ready" != "true" ]]; then
    "${compose[@]}" logs --tail=150 mysql >&2 || true
    echo "MySQL did not become healthy" >&2
    deployment_error_status=70
    false
fi

# Runtime credentials remain least-privilege. This one-shot release process
# owns additive DDL and immutable trigger creation before the API is replaced.
api_image="$FUND_AI_API_IMAGE"
if ! docker image inspect "$api_image" >/dev/null; then
    echo "built API image could not be resolved: $api_image" >&2
    deployment_error_status=70
    false
fi
docker run --rm \
    --network "container:$mysql_container_id" \
    --env-file .env.production \
    -e FUND_AI_MYSQL_ADMIN_HOST=127.0.0.1 \
    "$api_image" \
    python -m app.mysql_admin_bootstrap
"${compose[@]}" up -d --no-deps --force-recreate api worker

api_ready=false
for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
        api_ready=true
        break
    fi
    echo "waiting for API health ($attempt/30)"
    sleep 5
done
if [[ "$api_ready" != "true" ]]; then
    "${compose[@]}" logs --tail=150 api >&2 || true
    echo "API did not become healthy" >&2
    deployment_error_status=70
    false
fi

worker_ready=false
for attempt in $(seq 1 30); do
    worker_container_id="$("${compose[@]}" ps -q worker)"
    if [[ -n "$worker_container_id" ]] && [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$worker_container_id")" == "healthy" ]]; then
        worker_ready=true
        break
    fi
    echo "waiting for background worker health ($attempt/30)"
    sleep 5
done
if [[ "$worker_ready" != "true" ]]; then
    "${compose[@]}" logs --tail=150 worker >&2 || true
    echo "Background worker did not become healthy" >&2
    deployment_error_status=70
    false
fi

# A process-level health endpoint cannot detect a broken evaluation contract.
# Exercise the exact scheduled snapshot command without writing a snapshot
# before making the new release visible.
evaluation_as_of="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
"${compose[@]}" exec -T api \
    python scripts/evaluate_decision_quality.py \
    --all-users \
    --evaluation-as-of "$evaluation_as_of" \
    --window-days 365 \
    --dry-run \
    --format summary

mkdir -p "$web_root"
web_was_activated=true
rsync -a --delete "$release_web/" "$web_root/"
find "$web_root" -type d -exec chmod 755 {} +
find "$web_root" -type f -exec chmod 644 {} +

"${compose[@]}" up -d --no-deps --force-recreate nginx
"${compose[@]}" exec -T nginx nginx -t
curl -fsS http://127.0.0.1/ >/dev/null
curl -fsS http://127.0.0.1/login/ >/dev/null
curl -fsS http://127.0.0.1/register/ >/dev/null
curl -fsS http://127.0.0.1/settings/ >/dev/null
curl -fsS http://127.0.0.1/admin/users/ >/dev/null
curl -fsS http://127.0.0.1/reset-password/ >/dev/null
curl -fsS http://127.0.0.1/api/trading-session >/dev/null

printf '%s\n' "$deploy_sha" > "$deployed_sha_file"
rm -f "$rollback_marker"
trap - ERR
echo "FundPilot deployment succeeded: $deploy_sha"
