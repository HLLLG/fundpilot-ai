from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import us_forex_client
from app.services import us_market_service

_FETCH_USD_CNY = us_forex_client.fetch_usd_cny


def _safe_payload() -> dict:
    return {
        "records": [
            {"日期": 1783987200000, "美元": 679.9},
            {"日期": 1784073600000, "美元": 679.1},
        ]
    }


def _boc_payload() -> dict:
    return {
        "records": [
            {"日期": 1783987200000, "中行折算价": 679.9},
            {"日期": 1784073600000, "中行折算价": 679.1},
        ]
    }


def test_fetch_prefers_reachable_safe_source(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []

    def fake_run(_script: str, *, label: str, timeout_seconds: float) -> dict | None:
        calls.append((label, timeout_seconds))
        return _safe_payload()

    monkeypatch.setattr(us_forex_client, "_run_akshare", fake_run)

    quote = _FETCH_USD_CNY()

    assert quote == {
        "last_price": 6.791,
        "change_percent": -0.12,
        "quote_time": "2026-07-15",
        "source": "currency_boc_safe",
        "stale": False,
        "frequency": "daily",
    }
    assert calls == [
        ("currency_boc_safe", us_forex_client._PRIMARY_SOURCE_TIMEOUT)
    ]


def test_fetch_falls_back_to_boc_without_baidu_attempt(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []

    def fake_run(_script: str, *, label: str, timeout_seconds: float) -> dict | None:
        calls.append((label, timeout_seconds))
        return None if label == "currency_boc_safe" else _boc_payload()

    monkeypatch.setattr(us_forex_client, "_run_akshare", fake_run)

    quote = _FETCH_USD_CNY()

    assert quote is not None
    assert quote["source"] == "currency_boc_sina"
    assert calls == [
        ("currency_boc_safe", us_forex_client._PRIMARY_SOURCE_TIMEOUT),
        ("currency_boc_sina", us_forex_client._FALLBACK_SOURCE_TIMEOUT),
    ]
    assert all(label != "fx_quote_baidu" for label, _timeout in calls)


def test_source_timeouts_fit_shared_market_budget() -> None:
    assert (
        us_forex_client._PRIMARY_SOURCE_TIMEOUT
        + us_forex_client._FALLBACK_SOURCE_TIMEOUT
        < us_market_service._FETCH_BUDGET_SECONDS
    )


def test_run_akshare_honors_per_source_timeout(monkeypatch) -> None:
    captured: dict[str, float] = {}

    def fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            stdout=json.dumps({"columns": [], "records": []}),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(us_forex_client.subprocess, "run", fake_run)

    payload = us_forex_client._run_akshare(
        "print('ok')",
        label="test",
        timeout_seconds=2.5,
    )

    assert payload == {"columns": [], "records": []}
    assert captured["timeout"] == 2.5
