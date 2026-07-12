from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from scripts.publish_factor_ic import (
    PUBLISH_TIMEOUT_SECONDS,
    RETRY_DELAYS,
    _required_env,
    _write_actions_summary,
    publish_summary,
)
from tests.test_factor_ic_snapshot import valid_payload


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(
            self.status_code,
            request=request,
            text=self.text,
        )
        response.raise_for_status()


class FakeClient:
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _summary_path(tmp_path, generated_at: str = "2026-07-10T08:00:00+00:00"):
    raw = valid_payload(generated_at)
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(raw["summary"]), encoding="utf-8")
    return path


def _publish(tmp_path, client: FakeClient, sleeps: list[float]) -> str:
    return publish_summary(
        summary_path=_summary_path(tmp_path),
        url="https://example.test/api/internal/factor-ic-snapshots",
        token="secret-token-value",
        source_commit="a" * 40,
        source_run_id="12345",
        client=client,
        sleep=sleeps.append,
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
    )


def test_retries_5xx_and_never_places_token_in_body(tmp_path) -> None:
    client = FakeClient(
        [FakeResponse(503), FakeResponse(200, {"created": True})]
    )
    sleeps: list[float] = []

    result = _publish(tmp_path, client, sleeps)

    assert result == "created"
    assert sleeps == [5]
    assert len(client.calls) == 2
    assert "secret-token-value" not in json.dumps(client.calls[0]["json"])
    assert (
        client.calls[0]["headers"]["X-Factor-IC-Publish-Token"]
        == "secret-token-value"
    )
    assert client.calls[0]["timeout"] == PUBLISH_TIMEOUT_SECONDS == 90.0


def test_four_total_attempts_use_bounded_backoff(tmp_path) -> None:
    client = FakeClient([FakeResponse(503) for _ in range(4)])
    sleeps: list[float] = []

    with pytest.raises(httpx.HTTPStatusError):
        _publish(tmp_path, client, sleeps)

    assert RETRY_DELAYS == (5, 15, 45)
    assert sleeps == [5, 15, 45]
    assert len(client.calls) == 4


def test_network_errors_retry_and_can_return_duplicate(tmp_path) -> None:
    request = httpx.Request("POST", "https://example.test")
    client = FakeClient(
        [
            httpx.ConnectError("offline", request=request),
            FakeResponse(200, {"created": False}),
        ]
    )
    sleeps: list[float] = []

    result = _publish(tmp_path, client, sleeps)

    assert result == "duplicate"
    assert sleeps == [5]


@pytest.mark.parametrize("status_code", [401, 422])
def test_client_errors_do_not_retry(status_code: int, tmp_path) -> None:
    client = FakeClient([FakeResponse(status_code)])
    sleeps: list[float] = []

    with pytest.raises(httpx.HTTPStatusError):
        _publish(tmp_path, client, sleeps)

    assert sleeps == []
    assert len(client.calls) == 1


def test_conflict_is_a_safe_newer_snapshot_skip(tmp_path) -> None:
    client = FakeClient([FakeResponse(409, {"detail": "newer exists"})])
    sleeps: list[float] = []
    assert _publish(tmp_path, client, sleeps) == "newer_exists"
    assert sleeps == []


def test_invalid_summary_is_rejected_before_http(tmp_path) -> None:
    path = _summary_path(tmp_path)
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["universe_size"] = 239
    path.write_text(json.dumps(summary), encoding="utf-8")
    client = FakeClient([])

    with pytest.raises(ValueError, match="有效基金数不足"):
        publish_summary(
            summary_path=path,
            url="https://example.test/api/internal/factor-ic-snapshots",
            token="secret-token-value",
            source_commit="a" * 40,
            source_run_id="12345",
            client=client,
            now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        )

    assert client.calls == []


def test_actions_summary_contains_metrics_but_not_token(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "actions-summary.md"
    token = "actions-secret-token-value"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    monkeypatch.setenv("FACTOR_IC_PUBLISH_TOKEN", token)
    summary = valid_payload("2026-07-10T08:00:00+00:00")["summary"]

    _write_actions_summary(summary, "created")

    text = target.read_text(encoding="utf-8")
    assert "Factor IC Refresh" in text
    assert "universe_size: 300" in text
    assert "momentum: IC +0.0100, n=34" in text
    assert token not in text


def test_required_env_strips_values_and_rejects_missing(monkeypatch) -> None:
    monkeypatch.setenv("EXAMPLE_REQUIRED", "  value  ")
    assert _required_env("EXAMPLE_REQUIRED") == "value"
    monkeypatch.delenv("EXAMPLE_REQUIRED")
    with pytest.raises(RuntimeError, match="EXAMPLE_REQUIRED"):
        _required_env("EXAMPLE_REQUIRED")
