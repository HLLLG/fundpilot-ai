"""Tests for QDII holdings penetration valuation."""

from __future__ import annotations

from app.services.us_qdii_valuation_service import (
    compute_holdings_reference,
    is_index_like_qdii,
    merge_qdii_references,
)


def test_is_index_like_qdii():
    assert is_index_like_qdii(
        {"fund_name": "华宝纳斯达克精选", "tracking_target": "纳斯达克", "tracking_factor": 1.0}
    )
    assert not is_index_like_qdii(
        {"fund_name": "易方达全球成长精选", "tracking_target": "全球成长", "tracking_factor": 0.75}
    )


    holdings = [
        {"code": "NVDA", "market": "us", "weight": 10.0},
        {"code": "AAPL", "market": "us", "weight": 10.0},
    ]
    quotes = {"us:NVDA": -2.0, "us:AAPL": 4.0}
    assert compute_holdings_reference(holdings, quotes) == 1.0


def test_compute_holdings_reference_insufficient_coverage():
    holdings = [
        {"code": "NVDA", "market": "us", "weight": 2.0},
        {"code": "MSFT", "market": "us", "weight": 20.0},
    ]
    quotes = {"us:NVDA": -2.0}
    assert compute_holdings_reference(holdings, quotes) is None


def test_merge_index_like_prefers_index_over_holdings():
    seeds = [
        {
            "fund_code": "017436",
            "fund_name": "华宝纳斯达克精选",
            "tracking_target": "纳斯达克",
            "tracking_symbol": "NASDAQ_FUT",
            "tracking_factor": 1.0,
            "estimate_basis": "基于纳斯达克盘前/收盘涨跌估算，非实时净值/承诺收益",
        }
    ]
    merged = merge_qdii_references(
        seeds,
        {},
        holdings_refs={"017436": 0.44},
        change_map={"NASDAQ_FUT": -1.34},
        quote_mode="live",
    )
    assert merged[0]["reference_change_percent"] == -1.34
    assert merged[0]["estimate_basis"] == seeds[0]["estimate_basis"]


def test_merge_active_prefers_holdings_over_fundgz():
    seeds = [
        {
            "fund_code": "012920",
            "fund_name": "易方达全球成长精选",
            "tracking_target": "全球成长",
            "tracking_symbol": "NASDAQ_FUT",
            "tracking_factor": 0.75,
            "estimate_basis": "基于全球指数涨跌综合估算，非实时净值/承诺收益",
        }
    ]
    merged = merge_qdii_references(
        seeds,
        fundgz_refs={"012920": -0.47},
        holdings_refs={"012920": 0.44},
        change_map={"NASDAQ_FUT": -1.34},
        quote_mode="rth_close",
    )
    assert merged[0]["reference_change_percent"] == 0.44
    assert "穿透" in merged[0]["estimate_basis"]


def test_merge_prefers_fundgz_over_holdings_and_index():
    seeds = [
        {
            "fund_code": "017436",
            "fund_name": "华宝纳斯达克精选",
            "tracking_target": "纳斯达克",
            "tracking_symbol": "NASDAQ_FUT",
            "tracking_factor": 1.0,
            "estimate_basis": "基于纳斯达克盘前/收盘涨跌估算，非实时净值/承诺收益",
        }
    ]
    merged = merge_qdii_references(
        seeds,
        fundgz_refs={"017436": 0.22},
        holdings_refs={"017436": 0.44},
        change_map={"NASDAQ_FUT": -1.34},
    )
    assert merged[0]["reference_change_percent"] == 0.22
    assert "天天基金" in merged[0]["estimate_basis"]


def test_merge_falls_back_to_index_factor():
    seeds = [
        {
            "fund_code": "017436",
            "fund_name": "华宝纳斯达克精选",
            "tracking_target": "纳斯达克",
            "tracking_symbol": "NASDAQ_FUT",
            "tracking_factor": 1.0,
            "estimate_basis": "基于纳斯达克盘前/收盘涨跌估算，非实时净值/承诺收益",
        }
    ]
    merged = merge_qdii_references(seeds, {}, {}, {"NASDAQ_FUT": -1.34})
    assert merged[0]["reference_change_percent"] == -1.34
