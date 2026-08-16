#!/usr/bin/env bash
# Open 127.0.0.1:18000 -> Lighthouse 127.0.0.1:8000 and wait until /health answers.
# Prints the tunnel PID on stdout. All diagnostics go to stderr so callers can
# capture the PID with command substitution.
#
# GitHub → Lighthouse TCP can connect on :22 and still never deliver the SSH
# banner (`Connection timed out during banner exchange`, Factor IC Refresh
# #14/#15, same as Decision Outcome Settlement #36). OpenSSH's default DSCP
# markings get dropped on that path; IPQoS=none matches the compose-exec
# helper. Also time out the handshake and reopen the session.
set -euo pipefail

: "${SSH_KEY_PATH:?SSH_KEY_PATH is empty}"
: "${SSH_KNOWN_HOSTS_PATH:?SSH_KNOWN_HOSTS_PATH is empty}"
: "${LIGHTHOUSE_USER:?LIGHTHOUSE_USER is empty}"
: "${LIGHTHOUSE_HOST:?LIGHTHOUSE_HOST is empty}"

LOCAL_PORT="${LIGHTHOUSE_TUNNEL_LOCAL_PORT:-18000}"
REMOTE_PORT="${LIGHTHOUSE_TUNNEL_REMOTE_PORT:-8000}"
CONNECT_ATTEMPTS="${LIGHTHOUSE_TUNNEL_CONNECT_ATTEMPTS:-4}"
HEALTH_ATTEMPTS="${LIGHTHOUSE_TUNNEL_HEALTH_ATTEMPTS:-10}"
HEALTH_URL="http://127.0.0.1:${LOCAL_PORT}/health"
LOG_DIR="${RUNNER_TEMP:-/tmp}"
target="${LIGHTHOUSE_USER}@${LIGHTHOUSE_HOST}"

ssh_options=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH"
  -o ConnectTimeout=15
  -o ConnectionAttempts=1
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
  -o TCPKeepAlive=yes
  -o IPQoS=none
)

tunnel_pid=""

kill_tunnel() {
  if [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" 2>/dev/null; then
    kill "$tunnel_pid" 2>/dev/null || true
    wait "$tunnel_pid" 2>/dev/null || true
  fi
  tunnel_pid=""
}

for connect in $(seq 1 "$CONNECT_ATTEMPTS"); do
  ssh_log="$LOG_DIR/lighthouse-api-tunnel-${connect}.log"
  ssh "${ssh_options[@]}" \
    -E "$ssh_log" \
    -v \
    -T -N \
    -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$target" &
  tunnel_pid=$!
  ready=false
  for health in $(seq 1 "$HEALTH_ATTEMPTS"); do
    if ! kill -0 "$tunnel_pid" 2>/dev/null; then
      wait "$tunnel_pid" 2>/dev/null || true
      echo "SSH tunnel exited during connect ${connect}/${CONNECT_ATTEMPTS}" >&2
      tail -n 50 "$ssh_log" >&2 || true
      tunnel_pid=""
      break
    fi
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null; then
      ready=true
      break
    fi
    echo "waiting for Lighthouse SSH tunnel (connect ${connect}/${CONNECT_ATTEMPTS}, health ${health}/${HEALTH_ATTEMPTS})" >&2
    sleep 2
  done
  if [[ "$ready" == true ]]; then
    echo "$tunnel_pid"
    exit 0
  fi
  echo "connect attempt ${connect}/${CONNECT_ATTEMPTS} did not become healthy; retrying" >&2
  kill_tunnel
  sleep 2
done

echo "::error::Lighthouse API is unavailable through the SSH tunnel" >&2
exit 1
