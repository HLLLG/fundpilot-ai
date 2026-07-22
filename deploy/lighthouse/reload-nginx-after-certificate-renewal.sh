#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="/srv/fundpilot/repo"
cd "$repo_root"

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
if ! nginx_test_output="$("${compose[@]}" exec -T nginx nginx -t 2>&1)"; then
    printf '%s\n' "$nginx_test_output" >&2
    exit 1
fi
if ! nginx_reload_output="$("${compose[@]}" exec -T nginx nginx -s reload 2>&1)"; then
    printf '%s\n' "$nginx_reload_output" >&2
    exit 1
fi
curl -fsS \
    --resolve "www.hllingxi.cn:443:127.0.0.1" \
    https://www.hllingxi.cn/ >/dev/null
