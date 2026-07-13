from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import httpx
import pytest

from scripts.capture_factor_ic_universe import capture_universe
from scripts.fetch_factor_ic_universe import fetch_universe_history
from scripts.publish_factor_ic_universe import publish_universe
from app.services import akshare_subprocess
from tests.test_factor_ic_universe_snapshot import valid_universe_payload


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        request = httpx.Request("GET", "https://example.test")
        httpx.Response(self.status_code, request=request).raise_for_status()


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.response

    def get(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.response


def _rank_row(*, nav: str = "1.2345", growth: str = "0.67") -> str:
    parts = [""] * 17
    parts[0] = "123456"
    parts[1] = "测试基金A"
    parts[3] = "2026-07-13"
    parts[4] = nav
    parts[6] = growth
    parts[9] = "3.0"
    parts[10] = "6.0"
    parts[11] = "12.0"
    parts[16] = "2020-01-01"
    return ",".join(parts)


def _execute_universe_parser(script: str, raw: str) -> dict:
    start = script.index("def number(parts, index):")
    end = script.index("try:\n    first_pages")
    namespace = {"math": math}
    exec(script[start:end], namespace)  # noqa: S102 - executes generated local code only
    try:
        rows = namespace["parse_rows"]({"datas": [raw]}, "gp")
    except ValueError as exc:
        return {"error": str(exc)}
    return {"data": rows}


def test_open_fund_universe_parses_observed_nav_fields_from_rank_datas(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    def runner(script: str, **_kwargs):
        captured["script"] = script
        return _execute_universe_parser(script, _rank_row())

    monkeypatch.setattr(akshare_subprocess, "run_akshare_json_script", runner)

    rows = akshare_subprocess.fetch_open_fund_universe(limit=300)

    assert rows == [
        {
            "fund_code": "123456",
            "fund_name": "测试基金A",
            "fund_type": "gp",
            "nav_date": "2026-07-13",
            "latest_nav": 1.2345,
            "daily_growth_percent": 0.67,
            "established_date": "2020-01-01",
            "return_1y_percent": 12.0,
            "return_6m_percent": 6.0,
            "return_3m_percent": 3.0,
            "max_drawdown_1y_percent": None,
            "fund_scale_yi": None,
        }
    ]
    assert 'observed_number(parts, 4, "latest_nav")' in captured["script"]
    assert 'observed_number(parts, 6, "daily_growth_percent")' in captured["script"]


@pytest.mark.parametrize(
    ("nav", "growth"),
    [
        ("0", "0.1"),
        ("-1", "0.1"),
        ("nan", "0.1"),
        ("inf", "0.1"),
        ("not-a-number", "0.1"),
        ("1.0", "inf"),
    ],
)
def test_open_fund_universe_rank_observation_abnormal_values_fail_closed(
    monkeypatch, nav: str, growth: str
) -> None:
    monkeypatch.setattr(
        akshare_subprocess,
        "run_akshare_json_script",
        lambda script, **_kwargs: _execute_universe_parser(
            script,
            _rank_row(nav=nav, growth=growth),
        ),
    )

    assert akshare_subprocess.fetch_open_fund_universe(limit=300) is None

def test_capture_script_builds_validated_artifact_from_full_catalogue() -> None:
    instant = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    calls: list[dict] = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return [
            {
                "fund_code": f"{index:06d}",
                "fund_name": f"组合{index}A",
                "fund_type": ("gp", "hh", "zq", "zs", "qdii")[index % 5],
                "established_date": "2020-01-01",
                "return_1y_percent": index % 100,
            }
            for index in range(1, 5_001)
        ]

    artifact = capture_universe(
        source_commit="a" * 40,
        source_run_id="run-1",
        fetch_universe=fetch,
        now=instant,
    )

    assert calls == [{"limit": 25_000, "timeout_seconds": 90}]
    assert artifact["snapshot"]["sampled_fund_count"] == 1_500
    assert datetime.fromisoformat(
        artifact["snapshot"]["available_at"].replace("Z", "+00:00")
    ) == instant
    assert all(
        member["metadata"]["snapshot_available_at"] == instant.isoformat()
        for member in artifact["members"]
    )
    assert all("latest_nav" not in member["metadata"] for member in artifact["members"])


def test_publish_script_validates_before_post_and_keeps_token_in_header(tmp_path) -> None:
    instant = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    artifact_path = tmp_path / "universe.json"
    artifact_path.write_text(
        json.dumps(valid_universe_payload(instant)),
        encoding="utf-8",
    )
    client = FakeClient(FakeResponse(200, {"created": True}))
    result = publish_universe(
        artifact_path=artifact_path,
        url="https://example.test/api/internal/factor-ic-universe-snapshots",
        token="secret-token",
        client=client,
        now=instant,
    )
    assert result == "created"
    assert client.calls[0]["headers"] == {
        "X-Factor-IC-Publish-Token": "secret-token"
    }
    assert "secret-token" not in json.dumps(client.calls[0]["json"])


def test_fetch_script_sends_bounded_query_and_rejects_oversized_response() -> None:
    client = FakeClient(
        FakeResponse(200, {"snapshot_count": 1, "snapshots": [{"snapshot_id": "x"}]})
    )
    result = fetch_universe_history(
        url="https://example.test/api/internal/factor-ic-universe-snapshots",
        token="secret-token",
        days=365,
        max_snapshots=52,
        stride_days=7,
        client=client,
    )
    assert result["snapshot_count"] == 1
    assert client.calls[0]["params"] == {
        "days": 365,
        "max_snapshots": 52,
        "stride_days": 7,
        "include_members": "true",
    }

    oversized = FakeClient(
        FakeResponse(200, {"snapshot_count": 2, "snapshots": [{}, {}]})
    )
    with pytest.raises(ValueError, match="有界契约"):
        fetch_universe_history(
            url="https://example.test",
            token="secret-token",
            days=30,
            max_snapshots=1,
            stride_days=7,
            client=oversized,
        )
