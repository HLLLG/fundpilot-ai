"""Unit tests for us_index_client."""

from __future__ import annotations

import pytest

from app.services.us_index_client import (
    parse_eastmoney_global_spot,
    parse_us_index_spot,
    parse_us_index_spot_sina,
)


def _ixic_rows() -> list[dict]:
    return [
        {"date": "2026-06-15", "close": 26683.94},
        {"date": "2026-06-16", "close": 26376.34},
    ]


def test_parse_us_index_spot_daily_change():
    payload = {"NASDAQ_FUT": _ixic_rows()}
    quotes = parse_us_index_spot(payload)
    assert len(quotes) == 1
    quote = quotes[0]
    assert quote["symbol"] == "NASDAQ_FUT"
    assert quote["display_name"] == "纳斯达克"
    assert quote["last_price"] == 26376.34
    expected = round((26376.34 - 26683.94) / 26683.94 * 100, 2)
    assert quote["change_percent"] == expected
    assert quote["quote_time"] == "2026-06-16"


def test_parse_us_index_spot_sina_millisecond_date():
    payload = {
        "DOW_FUT": [
            {"date": 1781481600000, "close": 51671.03},
            {"date": 1781568000000, "close": 51999.67},
        ]
    }
    quotes = parse_us_index_spot_sina(payload)
    assert len(quotes) == 1
    assert quotes[0]["quote_time"] == "2026-06-16"
    assert quotes[0]["change_percent"] == pytest.approx(0.64)


def test_parse_eastmoney_global_spot_xiaobei_caliber():
    rows = [
        {
            "f12": "NDX",
            "f14": "纳斯达克",
            "f2": 2602166,
            "f3": -134,
            "f18": 2637634,
            "f124": 1781740800,
        },
        {
            "f12": "SPX",
            "f14": "标普500",
            "f2": 742010,
            "f3": -121,
            "f18": 7511350,
            "f124": 1781740786,
        },
        {
            "f12": "DJIA",
            "f14": "道琼斯",
            "f2": 5149255,
            "f3": -98,
            "f18": 5199967,
            "f124": 1781740782,
        },
    ]
    quotes = parse_eastmoney_global_spot(rows)
    by_symbol = {q["symbol"]: q for q in quotes}
    assert by_symbol["NASDAQ_FUT"]["change_percent"] == pytest.approx(-1.34)
    assert by_symbol["SP500_FUT"]["change_percent"] == pytest.approx(-1.21)
    assert by_symbol["DOW_FUT"]["change_percent"] == pytest.approx(-0.98)
    assert by_symbol["DOW_FUT"]["last_price"] == pytest.approx(51492.55)
    assert by_symbol["DOW_FUT"]["source"] == "eastmoney_global_spot_push2delay"
