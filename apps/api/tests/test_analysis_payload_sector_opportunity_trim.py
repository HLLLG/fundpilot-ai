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
                    "pattern_label": "price_flow_aligned_up",
                    "sector_group": "tmt",
                    "opportunity_available": True,
                },
            }
        ],
        "sector_rotation": {
            "available": True,
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
    }
    assert opportunity["track"] == "momentum"

    rotation = trimmed["sector_rotation"]
    assert len(rotation["market_top"]) == 3


def test_missing_sector_opportunity_is_a_noop() -> None:
    facts = {"holdings": [{"fund_code": "000001"}], "sector_rotation": {"available": False, "market_top": []}}
    trimmed = trim_analysis_facts_for_llm(facts, analysis_mode="deep", phase=3)
    assert "sector_opportunity" not in trimmed["holdings"][0]
    assert trimmed["sector_rotation"] == {"available": False, "market_top": []}
