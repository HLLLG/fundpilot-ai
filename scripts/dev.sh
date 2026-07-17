#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
API_PYTHON="$API_DIR/.venv/Scripts/python.exe"

port_is_listening() {
  local port="$1"
  "$API_PYTHON" - "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.socket() as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
}

stop_process_tree() {
  local pid="$1"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      taskkill.exe //PID "$pid" //T //F >/dev/null 2>&1 || true
      ;;
    *)
      kill "$pid" 2>/dev/null || true
      ;;
  esac
}

if [[ ! -f "$API_PYTHON" ]]; then
  echo "Creating backend virtual environment..."
  python -m venv "$API_DIR/.venv"
  "$API_PYTHON" -m pip install -r "$API_DIR/requirements.txt"
fi

# A node_modules directory can exist while its package binaries are missing
# (for example after an interrupted install). Check the actual Next.js launcher
# instead of only checking for the directory.
if [[ ! -x "$WEB_DIR/node_modules/.bin/next" && ! -f "$WEB_DIR/node_modules/.bin/next.cmd" ]]; then
  echo "Installing frontend dependencies..."
  (cd "$WEB_DIR" && npm ci)
fi

cleanup() {
  echo ""
  echo "Stopping FundPilot AI..."
  [[ -n "${API_PID:-}" ]] && stop_process_tree "$API_PID"
  [[ -n "${WEB_PID:-}" ]] && stop_process_tree "$WEB_PID"
}

echo "Starting FundPilot AI..."
export FUND_AI_DB_FALLBACK_SQLITE="${FUND_AI_DB_FALLBACK_SQLITE:-true}"

if port_is_listening 8000; then
  echo "API port 8000 is already in use. Stop the existing FundPilot API before starting another copy." >&2
  exit 1
fi
if port_is_listening 3001; then
  echo "Web port 3001 is already in use. Stop the existing FundPilot Web process before starting another copy." >&2
  exit 1
fi

trap cleanup EXIT INT TERM

API_ARGS=(-m uvicorn app.main:app --host 127.0.0.1 --port 8000)
# WatchFiles/Uvicorn reload uses an extra Python process on Windows. Keep the
# default single-process path stable; opt in only while actively editing API code.
if [[ "${FUND_AI_DEV_RELOAD:-false}" == "true" ]]; then
  API_ARGS+=(--reload)
fi

(cd "$API_DIR" && exec "$API_PYTHON" "${API_ARGS[@]}") &
API_PID=$!

(cd "$WEB_DIR" && exec npm run dev) &
WEB_PID=$!

echo "API: http://127.0.0.1:8000"
echo "Web: http://127.0.0.1:3001"
echo "Press Ctrl+C to stop both servers."

wait
