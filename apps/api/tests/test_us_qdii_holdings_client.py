"""Tests for holding market classification."""

from __future__ import annotations

from app.services.us_qdii_holdings_client import (
    classify_holding_market,
    normalize_holding_code,
)


def test_classify_us_ticker():
    assert classify_holding_market("NVDA") == "us"
    assert classify_holding_market("BRK.B") == "us"


def test_classify_hk_code():
    assert classify_holding_market("00700") == "hk"
    assert normalize_holding_code("700", "hk") == "00700"


def test_classify_a_share():
    assert classify_holding_market("300502") == "cn"
    assert normalize_holding_code("300502", "cn") == "300502"
