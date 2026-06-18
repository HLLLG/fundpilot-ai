"""Unit tests for us_forex_client (USD/CNY 报价解析)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services import us_forex_client as fx

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_boc_sina_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "us_currency_boc_sina.json").read_text(encoding="utf-8"))


def test_parse_boc_sina_latest_row_and_fen_conversion():
    payload = _load_boc_sina_fixture()
    result = fx._parse_boc_sina_payload(payload)

    assert result is not None
    assert result["last_price"] == 7.1771
    assert result["source"] == "currency_boc_sina"
    assert result["frequency"] == "daily"
    assert isinstance(result["quote_time"], str)
    assert len(result["quote_time"]) == 10


def test_parse_boc_safe_latest_row_and_fen_conversion():
    payload = {
        "records": [
            {"日期": "2026-06-16", "美元": 681.08},
            {"日期": "2026-06-17", "美元": 680.96},
        ]
    }
    result = fx._parse_boc_safe_payload(payload)

    assert result is not None
    assert result["last_price"] == 6.8096
    assert result["change_percent"] == pytest.approx(-0.02, abs=0.01)
    assert result["source"] == "currency_boc_safe"
    assert result["quote_time"] == "2026-06-17"


def test_parse_boc_sina_change_percent_from_adjacent_rows():
    payload = _load_boc_sina_fixture()
    result = fx._parse_boc_sina_payload(payload)

    expected = round((717.71 / 717.72 - 1.0) * 100.0, 2)
    assert result["change_percent"] == expected


def test_parse_boc_sina_quote_time_matches_latest_epoch():
    payload = _load_boc_sina_fixture()
    result = fx._parse_boc_sina_payload(payload)

    latest_ms = max(r["日期"] for r in payload["records"])
    expected_date = (
        datetime.fromtimestamp(latest_ms / 1000.0, tz=timezone.utc).date().isoformat()
    )
    assert result["quote_time"] == expected_date


def test_parse_boc_sina_no_placeholder_when_empty():
    assert fx._parse_boc_sina_payload({"columns": [], "records": []}) is None
    assert fx._parse_boc_sina_payload({"records": [{"中行折算价": None}]}) is None


def test_parse_fx_baidu_realtime_no_fen_conversion():
    payload = {
        "records": [{"最新价": 7.2451, "涨跌幅": -0.05, "时间": "2026-06-17 08:12:00"}],
    }
    result = fx._parse_fx_baidu_payload(payload)

    assert result is not None
    assert result["last_price"] == 7.2451
    assert result["change_percent"] == -0.05
    assert result["source"] == "fx_quote_baidu"
    assert result["frequency"] == "realtime"


def test_parse_fx_baidu_missing_price_returns_none():
    payload = {"records": [{"x": "n/a"}]}
    assert fx._parse_fx_baidu_payload(payload) is None
