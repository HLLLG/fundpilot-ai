#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/apps/api/.venv/Scripts/python.exe"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${ROOT}/apps/api/.venv/bin/python"
fi
exec "$PYTHON" "${ROOT}/apps/api/scripts/diagnose_sector_quotes.py" --pretty "$@"
