from __future__ import annotations

from app.services.analysis_payload import trim_analysis_facts_for_llm


def _base_facts(**overrides) -> dict:
    facts = {
        "holdings": [
            {
                "fund_code": "000001",
                "sector_opportunity": {
                    "sector_label": "半导体",
                    "track": "momentum",
                    "score": 42.5,
                    "confidence": "中",
                    "entry_hint": "可分批关注",
                    "evidence": ["今日主力净流入"],
                    "penalties": [],
                    "change_1d_percent": 1.2,
                    "change_5d_percent": 4.5,
                    "today_main_force_net_yi": 6.0,
                    "cumulative_5d_net_yi": 12.0,
                    "today_available": True,
                    "five_day_available": True,
                    "history_point_count": 8,
                    "pattern_label": "price_flow_aligned_up",
                    "sector_group": "tmt",
                    "opportunity_available": True,
                },
            }
        ],
        "sector_rotation": {
            "available": True,
            "sector_flow_by_label": {"白酒": {"available": True}},
            "market_top": [
                {
                    "sector_label": "白酒",
                    "track": "setup",
                    "score": 55.0,
                    "sector_group": "consumer",
                    "confidence": "中",
                },
                {"sector_label": "证券", "track": "momentum", "score": 40.0, "sector_group": "finance"},
                {"sector_label": "军工", "track": "momentum", "score": 38.0, "sector_group": "军工"},
                {"sector_label": "有色", "track": "setup", "score": 30.0, "sector_group": "cyclical"},
            ],
        },
    }
    facts.update(overrides)
    return facts


def test_deep_mode_keeps_full_sector_opportunity_and_drops_internal_group() -> None:
    trimmed = trim_analysis_facts_for_llm(_base_facts(), analysis_mode="deep", phase=3)
    holding = trimmed["holdings"][0]
    assert "sector_group" not in holding["sector_opportunity"]
    assert holding["sector_opportunity"]["track"] == "momentum"
    assert holding["sector_opportunity"]["evidence"] == ["今日主力净流入"]

    rotation = trimmed["sector_rotation"]
    assert rotation["available"] is True
    assert len(rotation["market_top"]) == 4
    assert all("sector_group" not in item for item in rotation["market_top"])


def test_fast_mode_phase2_compacts_sector_opportunity_and_caps_market_top() -> None:
    trimmed = trim_analysis_facts_for_llm(_base_facts(), analysis_mode="fast", phase=2)
    holding = trimmed["holdings"][0]
    opportunity = holding["sector_opportunity"]
    assert set(opportunity.keys()) <= {
        "track",
        "confidence",
        "opportunity_available",
        "entry_hint",
        "pattern_label",
        "today_main_force_net_yi",
        "cumulative_5d_net_yi",
        "today_available",
        "five_day_available",
        "history_point_count",
    }
    assert opportunity["track"] == "momentum"
    assert opportunity["today_main_force_net_yi"] == 6.0
    assert opportunity["cumulative_5d_net_yi"] == 12.0
    assert opportunity["today_available"] is True
    assert opportunity["five_day_available"] is True
    assert opportunity["history_point_count"] == 8

    rotation = trimmed["sector_rotation"]
    assert len(rotation["market_top"]) == 3
    assert "sector_flow_by_label" not in rotation


def test_trim_never_exposes_top_level_internal_sector_flow_map() -> None:
    facts = _base_facts(
        sector_flow_by_label={"半导体": {"available": True}},
    )

    trimmed = trim_analysis_facts_for_llm(facts, analysis_mode="deep", phase=3)

    assert "sector_flow_by_label" not in trimmed


def test_missing_sector_opportunity_is_a_noop() -> None:
    facts = {"holdings": [{"fund_code": "000001"}], "sector_rotation": {"available": False, "market_top": []}}
    trimmed = trim_analysis_facts_for_llm(facts, analysis_mode="deep", phase=3)
    assert "sector_opportunity" not in trimmed["holdings"][0]
    assert trimmed["sector_rotation"] == {"available": False, "market_top": []}


def _facts_with_sector_fund_flow(**flow_overrides) -> dict:
    flow = {
        "available": True,
        "sector_label": "半导体",
        "trade_date": "2026-07-04",
        "flow_date": "2026-07-04",
        "date_aligned": True,
        "today_main_force_net_yi": -8.0,
        "main_force_direction": "outflow",
        "cumulative_5d_net_yi": 12.0,
        "cumulative_20d_net_yi": 30.0,
        "flow_tiers": {
            "super_large_net_yi": -20.0,
            "large_net_yi": -5.0,
            "medium_net_yi": 10.0,
            "small_net_yi": 7.0,
        },
        "flow_structure_hint": "超大单+大单（机构）净流出而中单+小单（大户/散户）净流入，散户接盘特征明显。",
        "pattern_label": "weak_outflow",
        "pattern_hint": "板块弱势且资金持续流出，短线加仓胜率通常不高。",
    }
    flow.update(flow_overrides)
    return {"holdings": [{"fund_code": "000001", "sector_fund_flow": flow}]}


def test_fast_mode_sector_fund_flow_keeps_today_tiers_but_not_daily_series() -> None:
    """2026-07-04：喂给 LLM 的资金结构只保留「今日」四档明细（flow_tiers）+ 系统解读
    （flow_structure_hint），5d/20d 只给汇总净流入，不含逐日明细序列。"""
    trimmed = trim_analysis_facts_for_llm(
        _facts_with_sector_fund_flow(), analysis_mode="fast", phase=2
    )
    flow = trimmed["holdings"][0]["sector_fund_flow"]
    assert flow["flow_tiers"] == {
        "super_large_net_yi": -20.0,
        "large_net_yi": -5.0,
        "medium_net_yi": 10.0,
        "small_net_yi": 7.0,
    }
    assert flow["flow_structure_hint"].startswith("超大单+大单（机构）净流出")
    assert flow["cumulative_5d_net_yi"] == 12.0
    assert flow["cumulative_20d_net_yi"] == 30.0
    assert "recent_5d_main_force_yi" not in flow


def test_deep_mode_sector_fund_flow_also_keeps_tiers_and_hint() -> None:
    trimmed = trim_analysis_facts_for_llm(
        _facts_with_sector_fund_flow(), analysis_mode="deep", phase=3
    )
    flow = trimmed["holdings"][0]["sector_fund_flow"]
    assert flow["flow_tiers"]["super_large_net_yi"] == -20.0
    assert flow["flow_structure_hint"].startswith("超大单+大单（机构）净流出")
