from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_daily_capture_is_lightweight_and_uses_existing_refresh_gate() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "factor-ic-universe-capture.yml"
    ).read_text(encoding="utf-8")

    assert "FACTOR_IC_REFRESH_ENABLED" in workflow
    assert 'timezone: "Asia/Shanghai"' in workflow
    assert "capture_factor_ic_universe.py" in workflow
    assert "publish_factor_ic_universe.py" in workflow
    assert "run_factor_ic.py" not in workflow
    assert "FACTOR_IC_PUBLISH_TOKEN" in workflow
    assert "X-Factor-IC-Publish-Token" not in workflow  # Added by the CLI, never a query argument.


def test_weekly_refresh_fetches_pit_history_before_generation() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "factor-ic-refresh.yml"
    ).read_text(encoding="utf-8")

    fetch_at = workflow.index("fetch_factor_ic_universe.py")
    generate_at = workflow.index("run_factor_ic.py")
    publish_at = workflow.index("publish_factor_ic.py")
    assert fetch_at < generate_at < publish_at
    assert '--pit-history "$RUNNER_TEMP/factor-ic-pit.json"' in workflow
    assert "--pit-history-days 1600" in workflow
    assert "--pit-embargo-trading-days 20" in workflow
    assert "--max-snapshots 180" in workflow
