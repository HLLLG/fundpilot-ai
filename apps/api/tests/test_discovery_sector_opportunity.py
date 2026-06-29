from __future__ import annotations

from app.services.discovery_sector_opportunity import (
    build_sector_flow_map_for_opportunities,
    select_sector_opportunities,
)


def test_selects_balanced_momentum_and_setup_tracks():
    heat = [
        {"sector_label": "半导体", "change_1d_percent": 1.2, "change_5d_percent": 4.5, "heat_score": 88},
        {"sector_label": "创新药", "change_1d_percent": -0.4, "change_5d_percent": -1.2, "heat_score": 52},
        {"sector_label": "白酒", "change_1d_percent": 4.8, "change_5d_percent": 9.0, "heat_score": 95},
    ]
    flow = {
        "半导体": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 12.0,
            "cumulative_5d_net_yi": 28.0,
            "pattern_label": "price_flow_aligned_up",
        },
        "创新药": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 8.0,
            "cumulative_5d_net_yi": 3.0,
            "pattern_label": "accumulation",
        },
        "白酒": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": -5.0,
            "cumulative_5d_net_yi": -15.0,
            "pattern_label": "distribution",
        },
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=4,
        momentum_slots=2,
        setup_slots=2,
    )

    tracks = {item["sector_label"]: item["track"] for item in result}
    assert tracks["半导体"] == "momentum"
    assert tracks["创新药"] == "setup"
    assert "白酒" not in tracks


def test_pullback_acceptance_is_entry_hint_not_a_track():
    heat = [
        {"sector_label": "机器人", "change_1d_percent": -0.8, "change_5d_percent": 3.8, "heat_score": 70},
    ]
    flow = {
        "机器人": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 2.0,
            "cumulative_5d_net_yi": 11.0,
            "pattern_label": "price_flow_aligned_up",
        }
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=2,
        momentum_slots=2,
        setup_slots=0,
    )

    assert result[0]["track"] == "momentum"
    assert result[0]["entry_hint"] == "回调承接观察"


def test_sector_class_diversification_limits_one_chain_dominance():
    heat = [
        {"sector_label": "半导体", "change_1d_percent": 1.0, "change_5d_percent": 4.0, "heat_score": 90},
        {"sector_label": "半导体材料", "change_1d_percent": 1.1, "change_5d_percent": 4.2, "heat_score": 91},
        {"sector_label": "CPO", "change_1d_percent": 0.9, "change_5d_percent": 3.5, "heat_score": 85},
        {"sector_label": "创新药", "change_1d_percent": 0.7, "change_5d_percent": 2.4, "heat_score": 78},
    ]
    flow = {
        row["sector_label"]: {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 5.0,
            "cumulative_5d_net_yi": 10.0,
            "pattern_label": "price_flow_aligned_up",
        }
        for row in heat
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=4,
        momentum_slots=4,
        setup_slots=0,
        max_per_group=2,
    )

    labels = [item["sector_label"] for item in result]
    assert len([label for label in labels if label in {"半导体", "半导体材料", "CPO"}]) == 2
    assert "创新药" in labels


def test_high_extended_position_adds_chasing_penalty():
    heat = [
        {"sector_label": "半导体", "change_1d_percent": 1.0, "change_5d_percent": 4.0, "heat_score": 90},
    ]
    flow = {
        "半导体": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 6.0,
            "cumulative_5d_net_yi": 12.0,
            "pattern_label": "price_flow_aligned_up",
        }
    }
    position = {
        "半导体": {
            "available": True,
            "position_label": "high_extended",
            "drawdown_from_20d_high_percent": 0.8,
            "distance_from_20d_high_percent": -0.8,
            "distance_from_20d_low_percent": 18.0,
            "volume_ratio_5d_vs_20d": 1.05,
            "up_days_5d": 4,
            "down_days_5d": 1,
        }
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        sector_position_by_label=position,
        focus_sectors=[],
    )

    assert result[0]["entry_hint"] == "高位谨慎"
    assert "20日高位延伸" in result[0]["penalties"]
    assert result[0]["position_context"]["position_label"] == "high_extended"


def test_setup_position_context_adds_base_building_evidence():
    heat = [
        {"sector_label": "创新药", "change_1d_percent": -0.2, "change_5d_percent": -1.0, "heat_score": 50},
    ]
    flow = {
        "创新药": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 4.0,
            "cumulative_5d_net_yi": 2.0,
            "pattern_label": "accumulation",
        }
    }
    position = {
        "创新药": {
            "available": True,
            "position_label": "base_building",
            "drawdown_from_20d_high_percent": 4.0,
            "distance_from_20d_low_percent": 3.0,
            "volume_ratio_5d_vs_20d": 0.92,
            "up_days_5d": 2,
            "down_days_5d": 2,
        }
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        sector_position_by_label=position,
        focus_sectors=[],
    )

    assert result[0]["track"] == "setup"
    assert result[0]["entry_hint"] == "蓄势观察"
    assert "20日区间蓄势" in result[0]["evidence"]
    assert result[0]["position_context"]["position_label"] == "base_building"


def test_flow_map_for_opportunities_respects_total_budget(monkeypatch):
    import time

    def slow_flow(label, **_kwargs):
        time.sleep(0.08)
        return {"sector_label": label, "available": True}

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.build_sector_fund_flow_context",
        slow_flow,
    )

    start = time.monotonic()
    result = build_sector_flow_map_for_opportunities(
        [],
        ["半导体", "白酒", "创新药"],
        total_timeout_seconds=0.02,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.08
    assert result == {}
