#!/usr/bin/env bash
# 一次性安装本机 Playwright Chromium，供板块行情浏览器链路使用。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/apps/web"

echo "Installing web deps (playwright)..."
(cd "$WEB" && npm install)

echo "Installing Chromium for sector-quote-browser-command.mjs..."
(cd "$WEB" && npx playwright install chromium)

echo ""
echo "Done. Add to .env (or copy from .env.example):"
echo "  FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true"
echo "  FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND=node scripts/sector-quote-browser-command.mjs"
echo "  FUND_AI_SECTOR_QUOTES_BROWSER_TIMEOUT_SECONDS=20"
echo ""
echo "Then restart: bash scripts/dev.sh"
echo "Probe: bash scripts/diagnose-sector-quotes.sh"
