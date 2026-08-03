from __future__ import annotations

import json
import sys

from scripts import capture_factor_ic_universe as capture_module


def test_universe_fetch_retries_once_with_full_timeout(capsys) -> None:
    expected = [{"fund_code": "000001"}]
    calls: list[tuple[int, int]] = []
    sleeps: list[int] = []

    def fetch_universe(*, limit: int, timeout_seconds: int):
        calls.append((limit, timeout_seconds))
        return None if len(calls) == 1 else expected

    result = capture_module._fetch_universe_with_retry(
        fetch_universe,
        sleep=sleeps.append,
    )

    assert result == expected
    assert calls == [(25_000, 150), (25_000, 150)]
    assert sleeps == [5]
    assert "retrying in 5s (1/2)" in capsys.readouterr().err


def test_capture_keeps_universe_when_optional_nav_batch_fails_quality_gate(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    universe_path = tmp_path / "universe.json"
    nav_path = tmp_path / "nav-observations.json"
    payload = {
        "snapshot": {
            "source_share_count": 25_000,
            "sampled_fund_count": 1_500,
        },
        "members": [],
    }
    monkeypatch.setattr(capture_module, "capture_universe", lambda **_kwargs: payload)
    monkeypatch.setattr(
        capture_module,
        "build_nav_observation_batch_from_universe",
        lambda _payload: (_ for _ in ()).throw(ValueError("coverage below 80%")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_factor_ic_universe.py",
            "--out",
            str(universe_path),
            "--nav-observations-out",
            str(nav_path),
            "--source-commit",
            "a" * 40,
            "--source-run-id",
            "run-1",
        ],
    )

    assert capture_module.main() == 0

    assert json.loads(universe_path.read_text(encoding="utf-8")) == payload
    assert not nav_path.exists()
    captured = capsys.readouterr()
    assert "optional NAV observation batch skipped" in captured.err
