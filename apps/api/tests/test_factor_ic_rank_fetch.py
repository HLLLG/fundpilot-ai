from __future__ import annotations

from types import SimpleNamespace

from app.services import akshare_subprocess as target

_REAL_FETCH_OPEN_FUND_RANK = target.fetch_open_fund_rank


def test_open_fund_rank_limits_request_and_retries_transient_timeouts(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []
    outcomes = iter(
        [
            None,
            None,
            {"data": [{"fund_code": "000001", "fund_name": "测试基金"}]},
        ]
    )

    def fake_runner(script: str, *, label: str, timeout: int | float):
        calls.append({"script": script, "label": label, "timeout": timeout})
        return next(outcomes)

    monkeypatch.setattr(target, "run_akshare_json_script", fake_runner)
    monkeypatch.setattr(
        target.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy direct subprocess path")
        ),
    )
    monkeypatch.setattr(
        target,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    rows = _REAL_FETCH_OPEN_FUND_RANK(limit=500)

    assert rows == [{"fund_code": "000001", "fund_name": "测试基金"}]
    assert len(calls) == 3
    assert sleeps == [2.0, 5.0]
    script = calls[0]["script"]
    assert '"pn": "500"' in script
    assert "timeout=(5, 20)" in script
    assert "fund_open_fund_rank_em" not in script
    assert all(call["timeout"] == 35 for call in calls)


def test_open_fund_rank_stops_after_three_failures(monkeypatch) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []

    def always_fail(script: str, *, label: str, timeout: int | float):
        calls.append({"script": script, "label": label, "timeout": timeout})
        return None

    monkeypatch.setattr(target, "run_akshare_json_script", always_fail)
    monkeypatch.setattr(
        target.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy direct subprocess path")
        ),
    )
    monkeypatch.setattr(
        target,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    assert _REAL_FETCH_OPEN_FUND_RANK(limit=500) is None
    assert len(calls) == 3
    assert sleeps == [2.0, 5.0]
