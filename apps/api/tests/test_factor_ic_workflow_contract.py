from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "factor-ic-refresh.yml"


def test_factor_ic_workflow_is_read_only_and_uses_fixed_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert 'cron: "23 3 * * 0"' in text
    assert 'timezone: "Asia/Shanghai"' in text
    assert "contents: read" in text
    assert "group: factor-ic-refresh" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 45" in text
    assert "working-directory: apps/api" in text
    assert "--universe-mode sampled" in text
    assert "--sample-pool-size 500" in text
    assert "--universe-size 300" in text
    assert "--nav-days 750" in text
    assert "--rebalance-step 21" in text
    assert "--forward-days 20" in text
    assert "--factor-lookback 250" in text
    assert "--max-workers 8" in text
    assert '--out-dir "$RUNNER_TEMP/factor-ic"' in text
    assert "secrets.FACTOR_IC_PUBLISH_TOKEN" in text
    assert "secrets.LIGHTHOUSE_SSH_PRIVATE_KEY" in text
    assert "secrets.LIGHTHOUSE_KNOWN_HOSTS" in text
    assert "ExitOnForwardFailure=yes" in text
    assert "127.0.0.1:18000:127.0.0.1:8000" in text
    assert "curl -fsS --max-time 2 http://127.0.0.1:18000/health" in text
    assert "scripts/publish_factor_ic.py" in text
    assert "requirements-ocr" not in text
    assert "git push" not in text
    assert "--token" not in text
