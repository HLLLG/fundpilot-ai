"""荐基 LLM 候选/板块瘦身 helper 单测。"""

from unittest.mock import patch

from app.services.discovery_candidate_llm import (
    build_sector_change_index,
    resolve_candidate_daily_estimate,
    slim_candidate_for_llm,
    slim_nav_trend_for_llm,
    trim_sector_heat_for_llm,
)


def test_slim_nav_trend_for_llm_keeps_decision_fields_only():
    full = {
        "trend_label": "震荡",
        "recent_5d_change_percent": 1.2,
        "recent_5d_daily_change_percent": [0.1, -0.2],
        "distance_from_high_percent": -3.5,
        "period_change_percent": 8.0,
        "latest_nav": 1.234,
        "recent_nav_series": [{"date": "2026-06-01", "nav": 1.2}],
        "source": "akshare",
    }
    slim = slim_nav_trend_for_llm(full)
    assert slim == {
        "trend_label": "震荡",
        "recent_5d_change_percent": 1.2,
        "recent_5d_daily_change_percent": [0.1, -0.2],
        "distance_from_high_percent": -3.5,
        "period_change_percent": 8.0,
    }
    assert "latest_nav" not in slim
    assert "source" not in slim


def test_resolve_candidate_daily_estimate_prefers_official_nav():
    with patch(
        "app.services.discovery_candidate_llm.get_cached_official_nav_return",
        return_value=1.23,
    ):
        daily, source = resolve_candidate_daily_estimate(
            fund_code="161725",
            sector_label="白酒",
            sector_change_index={"白酒": 2.0},
            trade_date="2026-06-25",
        )
    assert daily == 1.23
    assert source == "official_nav"


def test_resolve_candidate_daily_estimate_falls_back_to_sector():
    with patch(
        "app.services.discovery_candidate_llm.get_cached_official_nav_return",
        return_value=None,
    ):
        daily, source = resolve_candidate_daily_estimate(
            fund_code="161725",
            sector_label="白酒",
            sector_change_index={"白酒": 2.5},
            trade_date="2026-06-25",
        )
    assert daily == 2.5
    assert source == "sector_estimate"


def test_slim_candidate_for_llm_includes_extended_fields():
    item = {
        "fund_code": "161725",
        "fund_name": "招商中证白酒",
        "sector_label": "白酒",
        "return_1y_percent": 5.0,
        "return_3m_percent": 2.0,
        "return_6m_percent": 3.0,
        "max_drawdown_1y_percent": -12.0,
        "fund_scale_yi": 80.0,
        "dip_drop_percent": -4.2,
        "nav_trend": {
            "trend_label": "回调",
            "period_change_percent": 6.0,
            "latest_nav": 1.1,
        },
    }
    with patch(
        "app.services.discovery_candidate_llm.get_cached_official_nav_return",
        return_value=0.8,
    ):
        row = slim_candidate_for_llm(
            item,
            sector_change_index=build_sector_change_index(
                [{"sector_label": "白酒", "change_1d_percent": 1.0}]
            ),
            trade_date="2026-06-25",
        )
    assert row["return_3m_percent"] == 2.0
    assert row["nav_trend"]["trend_label"] == "回调"
    assert "latest_nav" not in row["nav_trend"]
    assert row["dip_drop_percent"] == -4.2
    assert row["estimated_daily_return_percent"] == 0.8
    assert row["daily_return_source"] == "official_nav"


def test_trim_sector_heat_for_llm_keeps_targets_and_top_heat():
    heat = [
        {"sector_label": f"板块{i}", "heat_score": float(i), "change_1d_percent": 0.1 * i}
        for i in range(1, 21)
    ]
    trimmed = trim_sector_heat_for_llm(
        heat,
        target_sectors=["板块3"],
        focus_sectors=["板块99"],
        top_n=5,
    )
    labels = {row["sector_label"] for row in trimmed}
    assert "板块3" in labels
    assert "板块20" in labels
    assert len(trimmed) <= 5
