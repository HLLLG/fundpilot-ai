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
    "login/index.html"
    "register/index.html"
    "settings/index.html"
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
git checkout --detach "$deploy_sha"

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
"${compose[@]}" config -q
"${compose[@]}" run --rm --no-deps nginx nginx -t
"${compose[@]}" up -d --build api

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
    "${compose[@]}" logs --tail=150 api >&2
    echo "API did not become healthy" >&2
    exit 70
fi

mkdir -p "$web_root"
rsync -a --delete "$release_web/" "$web_root/"
find "$web_root" -type d -exec chmod 755 {} +
find "$web_root" -type f -exec chmod 644 {} +

"${compose[@]}" up -d --no-deps --force-recreate nginx
"${compose[@]}" exec -T nginx nginx -t
curl -fsS http://127.0.0.1/ >/dev/null
curl -fsS http://127.0.0.1/login/ >/dev/null
curl -fsS http://127.0.0.1/register/ >/dev/null
curl -fsS http://127.0.0.1/settings/ >/dev/null
curl -fsS http://127.0.0.1/api/trading-session >/dev/null

printf '%s\n' "$deploy_sha" > /srv/fundpilot/DEPLOYED_SHA
echo "FundPilot deployment succeeded: $deploy_sha"
