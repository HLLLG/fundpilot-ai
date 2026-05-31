#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
API_PYTHON="$API_DIR/.venv/Scripts/python.exe"

if [[ ! -f "$API_PYTHON" ]]; then
  echo "Creating backend virtual environment..."
  python -m venv "$API_DIR/.venv"
  "$API_PYTHON" -m pip install -r "$API_DIR/requirements.txt"
fi

cleanup() {
  echo ""
  echo "Stopping FundPilot AI..."
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Starting FundPilot AI..."
(cd "$API_DIR" && "$API_PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
API_PID=$!

(cd "$WEB_DIR" && npm run dev) &
WEB_PID=$!

echo "API: http://127.0.0.1:8000"
echo "Web: http://127.0.0.1:3000"
echo "Press Ctrl+C to stop both servers."

wait
