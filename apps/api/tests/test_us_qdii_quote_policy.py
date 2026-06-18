"""Tests for Xiaobei-aligned quote policy."""

from __future__ import annotations

from app.services.us_qdii_quote_policy import (
    estimate_basis_suffix,
    quote_mode_for_session,
)


def test_quote_mode_live_for_premarket_and_regular():
    assert quote_mode_for_session("pre_market") == "live"
    assert quote_mode_for_session("regular") == "live"


def test_quote_mode_rth_close_for_after_hours_and_closed():
    assert quote_mode_for_session("after_hours") == "rth_close"
    assert quote_mode_for_session("closed") == "rth_close"


def test_estimate_basis_suffix():
    assert "实时" in estimate_basis_suffix("live")
    assert "收盘" in estimate_basis_suffix("rth_close")
