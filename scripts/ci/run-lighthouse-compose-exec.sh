#!/usr/bin/env bash
# Run `docker compose exec` on Lighthouse without keeping one long SSH session.
#
# Production failure modes on GitHub → Lighthouse:
# 1. Handshake hang / RST (`kex_exchange_identification`, run #36). Time out
#    the handshake, disable DSCP (IPQoS=none), and reopen the session.
# 2. Idle session drop after the remote command has started (`Broken pipe`,
#    runs #31–#35). docker exec is killed by SIGHUP. Spawn off the SSH session.
# 3. Start SSH returns `started pid=…` then every later SSH from the same
#    runner dies at banner exchange (run #37). docker exec kept the sshd
#    session open via inherited fds. Double-fork, close extra fds, poll once
#    per interval instead of hammering sshd.
# 4. Handshake retries must snapshot the heredoc (run #38). `set -e` plus
#    consuming stdin on the first 255 would skip retries or send an empty
#    remote script. Snapshot stdin and keep looping on transport errors.
#
# Settlement is idempotent; the poll loop never relaunches a live job.
set -euo pipefail

: "${SSH_KEY_PATH:?SSH_KEY_PATH is empty}"
: "${SSH_KNOWN_HOSTS_PATH:?SSH_KNOWN_HOSTS_PATH is empty}"
: "${LIGHTHOUSE_USER:?LIGHTHOUSE_USER is empty}"
: "${LIGHTHOUSE_HOST:?LIGHTHOUSE_HOST is empty}"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <job-name> <remote-argv...>" >&2
  echo "example: $0 outcome-settlement python -u scripts/settle_pending_outcomes.py" >&2
  exit 64
fi

job_name="$1"
shift
if [[ ! "$job_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "invalid job name: $job_name" >&2
  exit 64
fi
if [[ $# -lt 1 ]]; then
  echo "missing remote command" >&2
  exit 64
fi

CONNECT_ATTEMPTS="${LIGHTHOUSE_EXEC_CONNECT_ATTEMPTS:-8}"
POLL_SECONDS="${LIGHTHOUSE_EXEC_POLL_SECONDS:-30}"
MAX_WAIT_SECONDS="${LIGHTHOUSE_EXEC_MAX_WAIT_SECONDS:-2400}"
DEPLOY_LOCK_ATTEMPTS="${LIGHTHOUSE_EXEC_DEPLOY_LOCK_ATTEMPTS:-60}"
target="${LIGHTHOUSE_USER}@${LIGHTHOUSE_HOST}"
remote_dir="/tmp/fundpilot-ci/${job_name}"
remote_log="${remote_dir}/job.log"
remote_exit="${remote_dir}/job.exit"
remote_pid="${remote_dir}/job.pid"
deploy_lock="/srv/fundpilot/deploy.lock"

inner_cmd="cd /srv/fundpilot/repo && docker compose --env-file .env.production -f docker-compose.production.yml exec -T -e PYTHONUNBUFFERED=1 api"
for arg in "$@"; do
  inner_cmd+=" $(printf '%q' "$arg")"
done
inner_b64="$(printf '%s' "$inner_cmd" | base64 -w0)"

ssh_options=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH"
  -o ConnectTimeout=15
  -o ConnectionAttempts=1
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
  -o TCPKeepAlive=yes
  -o IPQoS=none
  -o ControlMaster=no
  -T
)

ssh_once() {
  ssh "${ssh_options[@]}" "$target" "$@"
}

ssh_call() {
  # Snapshot stdin once. The remote script is a heredoc; the first failed
  # ssh would otherwise consume it and every retry would send an empty
  # command. `|| st=$?` is required so `set -e` does not abort the loop.
  local attempt st=255 stdin_file
  stdin_file="$(mktemp "${TMPDIR:-/tmp}/lighthouse-ssh-stdin.XXXXXX")"
  cat > "$stdin_file"
  for attempt in $(seq 1 "$CONNECT_ATTEMPTS"); do
    st=0
    ssh_once "$@" < "$stdin_file" || st=$?
    if [[ "$st" -ne 255 ]]; then
      rm -f "$stdin_file"
      return "$st"
    fi
    echo "SSH transport failed with 255 (attempt ${attempt}/${CONNECT_ATTEMPTS})" >&2
    if [[ "$attempt" -lt "$CONNECT_ATTEMPTS" ]]; then
      sleep $((attempt * 5))
    fi
  done
  rm -f "$stdin_file"
  return 255
}

start_remote_job() {
  ssh_call env \
    FUNDPILOT_JOB_DIR="$remote_dir" \
    FUNDPILOT_LOG="$remote_log" \
    FUNDPILOT_EXIT="$remote_exit" \
    FUNDPILOT_PID="$remote_pid" \
    FUNDPILOT_LOCK="$deploy_lock" \
    FUNDPILOT_LOCK_ATTEMPTS="$DEPLOY_LOCK_ATTEMPTS" \
    FUNDPILOT_INNER_B64="$inner_b64" \
    bash -s <<'REMOTE'
set -euo pipefail
mkdir -p "$FUNDPILOT_JOB_DIR"

if [[ -e "$FUNDPILOT_LOCK" ]]; then
  for i in $(seq 1 "$FUNDPILOT_LOCK_ATTEMPTS"); do
    if flock -n "$FUNDPILOT_LOCK" -c true; then
      break
    fi
    echo "waiting for $FUNDPILOT_LOCK ($i/$FUNDPILOT_LOCK_ATTEMPTS)" >&2
    sleep 15
  done
  if ! flock -n "$FUNDPILOT_LOCK" -c true; then
    echo "deploy still holds $FUNDPILOT_LOCK" >&2
    exit 1
  fi
fi

if [[ -f "$FUNDPILOT_PID" ]]; then
  old_pid="$(tr -d '[:space:]' < "$FUNDPILOT_PID" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "reusing live job pid=$old_pid"
    exit 0
  fi
fi

rm -f "$FUNDPILOT_EXIT" "$FUNDPILOT_PID"
: > "$FUNDPILOT_LOG"
inner="$(printf '%s' "$FUNDPILOT_INNER_B64" | base64 -d)"
printf '%s\n' "$inner" > "$FUNDPILOT_JOB_DIR/cmd.sh"
cat > "$FUNDPILOT_JOB_DIR/run.sh" <<RUN
#!/bin/bash
echo \$\$ > $(printf '%q' "$FUNDPILOT_PID")
exec </dev/null >>$(printf '%q' "$FUNDPILOT_LOG") 2>&1
if [[ -d /proc/self/fd ]]; then
  for fd in /proc/self/fd/*; do
    fdnum="\${fd##*/}"
    case "\$fdnum" in
      0|1|2) continue ;;
      *) eval "exec \${fdnum}>&-" 2>/dev/null || true ;;
    esac
  done
fi
bash $(printf '%q' "$FUNDPILOT_JOB_DIR/cmd.sh")
echo \$? > $(printf '%q' "$FUNDPILOT_EXIT")
RUN
chmod 700 "$FUNDPILOT_JOB_DIR/run.sh" "$FUNDPILOT_JOB_DIR/cmd.sh"
setsid --fork /bin/bash "$FUNDPILOT_JOB_DIR/run.sh"
for i in \$(seq 1 10); do
  if [[ -s "$FUNDPILOT_PID" ]]; then
    break
  fi
  sleep 0.2
done
echo "started pid=\$(tr -d '[:space:]' < \"\$FUNDPILOT_PID\")"
REMOTE
}

poll_remote_job() {
  local skip_lines="$1"
  ssh_once env \
    FUNDPILOT_LOG="$remote_log" \
    FUNDPILOT_EXIT="$remote_exit" \
    FUNDPILOT_PID="$remote_pid" \
    FUNDPILOT_SKIP_LINES="$skip_lines" \
    bash -s <<'REMOTE' || return $?
set -euo pipefail
if [[ -f "$FUNDPILOT_PID" && ! -f "$FUNDPILOT_EXIT" ]]; then
  pid="$(tr -d '[:space:]' < "$FUNDPILOT_PID" || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && ! kill -0 "$pid" 2>/dev/null; then
    echo "1" > "$FUNDPILOT_EXIT"
  fi
fi
if [[ -f "$FUNDPILOT_EXIT" ]]; then
  echo "STATUS=done"
  echo "EXIT=$(tr -d '[:space:]' < "$FUNDPILOT_EXIT")"
else
  echo "STATUS=running"
fi
echo "LOG"
if [[ -f "$FUNDPILOT_LOG" ]]; then
  tail -n +"$FUNDPILOT_SKIP_LINES" "$FUNDPILOT_LOG"
fi
REMOTE
}

start_remote_job
sleep 2

deadline=$((SECONDS + MAX_WAIT_SECONDS))
next_line=1
while (( SECONDS < deadline )); do
  poll_out=""
  if poll_out="$(poll_remote_job "$next_line")"; then
    status="$(printf '%s\n' "$poll_out" | awk 'NR==1 {print}')"
    exit_line="$(printf '%s\n' "$poll_out" | awk 'NR==2 {print}')"
    log_body="$(printf '%s\n' "$poll_out" | awk 'BEGIN{p=0} /^LOG$/{p=1; next} p{print}')"
    if [[ -n "$log_body" ]]; then
      printf '%s\n' "$log_body"
      next_line=$((next_line + $(printf '%s\n' "$log_body" | wc -l)))
    fi
    if [[ "$status" == "STATUS=done" ]]; then
      exit_code="${exit_line#EXIT=}"
      if [[ ! "$exit_code" =~ ^[0-9]+$ ]]; then
        echo "::error::remote job finished without a numeric exit code" >&2
        exit 1
      fi
      exit "$exit_code"
    fi
  else
    echo "poll SSH failed; will retry" >&2
  fi
  sleep "$POLL_SECONDS"
done

echo "::error::timed out waiting for remote job ${job_name} after ${MAX_WAIT_SECONDS}s" >&2
exit 1
