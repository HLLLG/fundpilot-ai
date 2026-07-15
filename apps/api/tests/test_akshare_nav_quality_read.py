from __future__ import annotations

import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services import akshare_subprocess as nav


@pytest.fixture(autouse=True)
def _clear_quality_cache():
    nav.clear_fund_nav_quality_cache()
    yield
    nav.clear_fund_nav_quality_cache()


def _origin_read(
    *,
    code: str = "000001",
    days: int = 90,
    indicator: str = nav._FUND_NAV_INDICATOR,
    completed_at: str = "2026-07-15T00:00:01+00:00",
    cache_hour: int = 10,
):
    payload = {
        "data": [
            {"date": "2026-07-14", "nav": 1.0, "daily_growth": 0.1},
            {"date": "2026-07-15", "nav": 1.01, "daily_growth": 1.0},
        ]
    }
    stdout = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    script = nav._fund_nav_history_script(code, days, indicator)
    return nav._build_fund_nav_origin_read(
        fund_code=code,
        trading_days=days,
        indicator=indicator,
        script=script,
        started_at="2026-07-15T00:00:00+00:00",
        completed_at=completed_at,
        stdout=stdout,
        parsed_payload=payload,
        normalized_payload=payload,
        status="success",
        cache_hour=cache_hour,
    )


def test_live_nav_capture_freezes_stdout_request_and_runtime_versions(monkeypatch) -> None:
    payload = {
        "data": [
            {"date": "2026-07-15", "nav": 1.01, "daily_growth": 1.0}
        ]
    }
    raw = b"adapter diagnostic\n" + json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8") + b"\r\n"
    monkeypatch.setattr(
        nav.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=raw,
            stderr=b"",
        ),
    )
    clocks = iter(
        [
            "2026-07-15T00:00:00+00:00",
            "2026-07-15T00:00:01+00:00",
        ]
    )
    monkeypatch.setattr(nav, "_utc_now", lambda: next(clocks))

    read = nav._capture_fund_nav_quality_origin(
        "000001",
        trading_days=123,
        indicator=nav._FUND_NAV_INDICATOR,
        cache_hour=10,
    )

    assert read.ok is True
    assert read.normalized_payload == payload
    receipt = read.origin_receipt
    assert receipt["request"]["parameters"] == {
        "fund_code": "000001",
        "trading_days": 123,
        "indicator": nav._FUND_NAV_INDICATOR,
    }
    assert receipt["adapter"]["contract_version"] == (
        nav._FUND_NAV_ADAPTER_CONTRACT_VERSION
    )
    assert receipt["adapter"]["library_name"] == "akshare"
    assert receipt["adapter"]["library_version"]
    assert receipt["adapter"]["python_version"]
    assert base64.b64decode(
        receipt["response"]["stdout_base64"],
        validate=True,
    ) == raw
    assert receipt["response"]["stdout_size_bytes"] == len(raw)
    assert receipt["upstream_raw_available"] is False


def test_nav_hour_cache_keeps_origin_and_changes_only_delivery(monkeypatch) -> None:
    origin = _origin_read()
    calls = 0

    def capture(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return deepcopy(origin)

    monkeypatch.setattr(nav, "_capture_fund_nav_quality_origin", capture)
    clocks = iter(
        [
            "2026-07-15T00:00:02+00:00",
            "2026-07-15T00:00:03+00:00",
            "2026-07-15T00:00:04+00:00",
        ]
    )
    monkeypatch.setattr(nav, "_utc_now", lambda: next(clocks))

    first = nav._fetch_fund_nav_history_quality_read_for_hour(
        "000001",
        trading_days=90,
        indicator=nav._FUND_NAV_INDICATOR,
        cache_hour=10,
    )
    second = nav._fetch_fund_nav_history_quality_read_for_hour(
        "000001",
        trading_days=90,
        indicator=nav._FUND_NAV_INDICATOR,
        cache_hour=10,
    )
    next_hour = nav._fetch_fund_nav_history_quality_read_for_hour(
        "000001",
        trading_days=90,
        indicator=nav._FUND_NAV_INDICATOR,
        cache_hour=11,
    )

    assert calls == 2
    assert first.delivery["cache_status"] == "miss"
    assert second.delivery["cache_status"] == "hit"
    assert second.delivery["cache_layer"] == "process"
    assert next_hour.delivery["cache_status"] == "miss"
    assert first.origin_receipt["origin_receipt_hash"] == second.origin_receipt[
        "origin_receipt_hash"
    ]
    assert first.origin_receipt["response"]["completed_at"] == second.origin_receipt[
        "response"
    ]["completed_at"]
    assert second.delivery["served_at"] == "2026-07-15T00:00:03+00:00"


def test_nav_cache_identity_includes_days_indicator_and_adapter_version(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int, str, str]] = []

    def capture(
        code: str,
        *,
        trading_days: int,
        indicator: str,
        cache_hour: int,
    ):
        calls.append(
            (
                code,
                trading_days,
                indicator,
                nav._FUND_NAV_ADAPTER_CONTRACT_VERSION,
            )
        )
        return _origin_read(
            code=code,
            days=trading_days,
            indicator=indicator,
            cache_hour=cache_hour,
        )

    monkeypatch.setattr(nav, "_capture_fund_nav_quality_origin", capture)
    monkeypatch.setattr(
        nav,
        "_utc_now",
        lambda: "2026-07-15T00:00:02+00:00",
    )

    for days, indicator in (
        (90, nav._FUND_NAV_INDICATOR),
        (91, nav._FUND_NAV_INDICATOR),
        (90, "累计净值走势"),
    ):
        nav._fetch_fund_nav_history_quality_read_for_hour(
            "000001",
            trading_days=days,
            indicator=indicator,
            cache_hour=10,
        )
    monkeypatch.setattr(
        nav,
        "_FUND_NAV_ADAPTER_CONTRACT_VERSION",
        "decision_quality_fund_nav_adapter.v2-test",
    )
    nav._fetch_fund_nav_history_quality_read_for_hour(
        "000001",
        trading_days=90,
        indicator=nav._FUND_NAV_INDICATOR,
        cache_hour=10,
    )

    assert len(calls) == 4
    assert calls[0][:3] == ("000001", 90, nav._FUND_NAV_INDICATOR)
    assert calls[1][1] == 91
    assert calls[2][2] == "累计净值走势"
    assert calls[3][3] == "decision_quality_fund_nav_adapter.v2-test"


def test_quality_payload_projection_does_not_expose_receipt(
    monkeypatch,
) -> None:
    read = _origin_read()
    monkeypatch.setattr(
        nav,
        "_fetch_fund_nav_history_quality_read_for_hour",
        lambda *_args, **_kwargs: deepcopy(read),
    )

    payload = nav._fund_nav_payload_from_quality_read("000001", 90, 10)

    assert payload == read.normalized_payload
    assert "origin_receipt" not in payload
