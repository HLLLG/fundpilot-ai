#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="/srv/fundpilot/repo"
cd "$repo_root"

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T nginx nginx -s reload
curl -fsS \
    --resolve "www.hllingxi.cn:443:127.0.0.1" \
    https://www.hllingxi.cn/ >/dev/null
