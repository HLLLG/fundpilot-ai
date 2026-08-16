"""Factor IC 定时任务必须能从 GitHub runner 重试打通 Lighthouse 隧道。"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TUNNEL_SCRIPT = REPO_ROOT / "scripts" / "ci" / "open-lighthouse-api-tunnel.sh"
REFRESH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factor-ic-refresh.yml"
CAPTURE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factor-ic-universe-capture.yml"


def test_lighthouse_tunnel_helper_times_out_and_retries_hung_handshakes() -> None:
    text = TUNNEL_SCRIPT.read_text(encoding="utf-8")
    assert "ConnectTimeout=15" in text
    assert "TCPKeepAlive=yes" in text
    assert "ServerAliveCountMax=10" in text
    assert "IPQoS=none" in text
    assert "CONNECT_ATTEMPTS" in text
    assert 'HEALTH_URL="http://127.0.0.1:${LOCAL_PORT}/health"' in text


def test_lighthouse_compose_exec_helper_detaches_and_retries_transport_errors() -> None:
    script = REPO_ROOT / "scripts" / "ci" / "run-lighthouse-compose-exec.sh"
    text = script.read_text(encoding="utf-8")
    assert "ConnectTimeout=15" in text
    assert "IPQoS=none" in text
    assert "st -ne 255" in text or '"$st" -ne 255' in text
    assert "setsid --fork" in text
    assert 'ssh_once "$@" < "$stdin_file" || st=$?' in text
    assert "deploy.lock" in text
    assert '"${1:-}" == "--host"' in text


def test_lighthouse_deploy_detaches_the_long_remote_script() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "deploy-lighthouse.yml"
    ).read_text(encoding="utf-8")
    assert "run-lighthouse-compose-exec.sh --host lighthouse-deploy" in workflow
    assert "IPQoS=none" in workflow
    assert "chmod 700 '$remote_script' && { '$remote_script'" not in workflow


def test_factor_ic_workflows_open_the_tunnel_through_the_retry_helper() -> None:
    helper = "scripts/ci/open-lighthouse-api-tunnel.sh"
    refresh = REFRESH_WORKFLOW.read_text(encoding="utf-8")
    capture = CAPTURE_WORKFLOW.read_text(encoding="utf-8")
    assert helper in refresh
    assert helper in capture
    assert refresh.count(helper) == 2
    assert "T -N -L 127.0.0.1:18000:127.0.0.1:8000" not in refresh
    assert "T -N -L 127.0.0.1:18000:127.0.0.1:8000" not in capture
