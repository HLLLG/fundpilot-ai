"""Tests for targeted stock quote client."""

from __future__ import annotations

from app.services.us_stock_quote_client import (
    collect_quote_targets,
    eastmoney_secid_candidates,
    to_eastmoney_secid,
)


def test_to_eastmoney_secid_mapping():
    assert to_eastmoney_secid("us", "NVDA") == "105.NVDA"
    assert to_eastmoney_secid("hk", "700") == "116.00700"
    assert to_eastmoney_secid("cn", "300502") == "0.300502"
    assert to_eastmoney_secid("cn", "600519") == "1.600519"


def test_eastmoney_secid_candidates_us_multi_prefix():
    assert eastmoney_secid_candidates("us", "TSM") == [
        "105.TSM",
        "106.TSM",
        "107.TSM",
    ]


def test_collect_quote_targets_dedupes():
    holdings = {
        "017436": {
            "holdings": [
                {"market": "us", "code": "NVDA", "weight": 9.0},
                {"market": "us", "code": "AAPL", "weight": 8.0},
            ]
        },
        "012920": {
            "holdings": [
                {"market": "us", "code": "NVDA", "weight": 5.0},
                {"market": "cn", "code": "300502", "weight": 6.0},
            ]
        },
    }
    targets = collect_quote_targets(holdings)
    assert targets == {("us", "NVDA"), ("us", "AAPL"), ("cn", "300502")}
