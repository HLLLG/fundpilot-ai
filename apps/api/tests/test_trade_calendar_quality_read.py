from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from app.services import trade_calendar_cache as calendar


@pytest.fixture(autouse=True)
def _clear_process_quality_cache():
    calendar.clear_trade_calendar_quality_cache()
    yield
    calendar.clear_trade_calendar_quality_cache()


def _origin_read(
    *,
    started_at: str = "2026-07-15T00:00:00+00:00",
    completed_at: str = "2026-07-15T00:00:01+00:00",
    dates: list[str] | None = None,
):
    values = dates or ["2026-07-14", "2026-07-15"]
    raw = json.dumps(values, ensure_ascii=False).encode("utf-8") + b"\n"
    return calendar._build_quality_origin_read(
        started_at=started_at,
        completed_at=completed_at,
        stdout=raw,
        parsed_payload=values,
        normalized_payload={"dates": values},
        status="success",
    )


def test_live_calendar_capture_preserves_complete_stdout_bytes(monkeypatch) -> None:
    raw = b'provider diagnostic\n["2026-07-14", "2026-07-15"]\r\n'
    monkeypatch.setattr(
        calendar.subprocess,
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
    monkeypatch.setattr(calendar, "_utc_now", lambda: next(clocks))

    read = calendar._fetch_trade_calendar_quality_origin()

    assert read.ok is True
    assert read.normalized_payload == {
        "dates": ["2026-07-14", "2026-07-15"]
    }
    response = read.origin_receipt["response"]
    assert base64.b64decode(response["stdout_base64"], validate=True) == raw
    assert response["stdout_size_bytes"] == len(raw)
    assert read.origin_receipt["adapter"]["contract_version"] == (
        calendar._QUALITY_ADAPTER_CONTRACT_VERSION
    )
    assert read.origin_receipt["upstream_raw_available"] is False


def test_quality_cache_returns_one_origin_with_distinct_deliveries(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "trade_dates.json"
    monkeypatch.setattr(calendar, "_cache_path", lambda: path)
    origin = _origin_read()
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        return deepcopy(origin)

    monkeypatch.setattr(calendar, "_fetch_trade_calendar_quality_origin", capture)
    clocks = iter(
        [
            "2026-07-15T00:00:02+00:00",
            "2026-07-15T00:00:03+00:00",
            "2026-07-15T00:00:04+00:00",
        ]
    )
    monkeypatch.setattr(calendar, "_utc_now", lambda: next(clocks))

    first = calendar.get_trade_calendar_quality_read()
    second = calendar.get_trade_calendar_quality_read()
    calendar.clear_trade_calendar_quality_cache()
    third = calendar.get_trade_calendar_quality_read()

    assert calls == 1
    assert first.delivery["cache_status"] == "miss"
    assert second.delivery["cache_status"] == "hit"
    assert second.delivery["cache_layer"] == "process"
    assert third.delivery["cache_status"] == "hit"
    assert third.delivery["cache_layer"] == "disk"
    assert {
        first.origin_receipt["origin_receipt_hash"],
        second.origin_receipt["origin_receipt_hash"],
        third.origin_receipt["origin_receipt_hash"],
    } == {origin.origin_receipt["origin_receipt_hash"]}
    assert all(
        read.origin_receipt["response"]["completed_at"]
        == "2026-07-15T00:00:01+00:00"
        for read in (first, second, third)
    )


def test_legacy_calendar_cache_is_usable_but_cannot_create_formal_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "trade_dates.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": date.today().isoformat(),
                "dates": ["2026-07-14"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(calendar, "_cache_path", lambda: path)
    captured = _origin_read(dates=["2026-07-15"])
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        return deepcopy(captured)

    monkeypatch.setattr(calendar, "_fetch_trade_calendar_quality_origin", capture)
    monkeypatch.setattr(
        calendar,
        "_utc_now",
        lambda: "2026-07-15T00:00:02+00:00",
    )

    assert calendar.get_trade_date_set() == frozenset({"2026-07-14"})
    quality = calendar.get_trade_calendar_quality_read()

    assert calls == 1
    assert quality.delivery["cache_status"] == "miss"
    assert quality.normalized_payload == {"dates": ["2026-07-15"]}


def test_expired_process_origin_is_refetched_instead_of_living_forever(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(calendar, "_cache_path", lambda: tmp_path / "missing.json")
    old_day = date(2026, 7, 1)
    old = _origin_read(
        started_at=f"{old_day.isoformat()}T00:00:00+00:00",
        completed_at=f"{old_day.isoformat()}T00:00:01+00:00",
        dates=["2026-07-01"],
    )
    calendar._QUALITY_PROCESS_ORIGIN = (
        deepcopy(old.origin_receipt),
        deepcopy(old.normalized_payload),
    )
    fresh_day = old_day + timedelta(days=8)
    fresh = _origin_read(
        started_at=f"{fresh_day.isoformat()}T00:00:00+00:00",
        completed_at=f"{fresh_day.isoformat()}T00:00:01+00:00",
        dates=[fresh_day.isoformat()],
    )
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        return deepcopy(fresh)

    monkeypatch.setattr(calendar, "_fetch_trade_calendar_quality_origin", capture)
    monkeypatch.setattr(
        calendar,
        "_utc_now",
        lambda: f"{fresh_day.isoformat()}T00:00:02+00:00",
    )

    read = calendar.get_trade_calendar_quality_read()

    assert calls == 1
    assert read.normalized_payload == {"dates": [fresh_day.isoformat()]}
    assert read.delivery["cache_status"] == "miss"
