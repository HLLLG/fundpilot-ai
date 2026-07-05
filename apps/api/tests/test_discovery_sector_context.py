"""荐基 target_sector_context 的资金流裁剪：今日四档结构保留，逐日序列不保留（2026-07-04）。"""

from __future__ import annotations

from app.services.discovery_sector_context import _slim_sector_fund_flow


def _full_flow(**overrides) -> dict:
    flow = {
        "available": True,
        "sector_label": "半导体",
        "board_code": "BK1036",
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
    flow.update(overrides)
    return flow


def test_slim_sector_fund_flow_keeps_today_tiers_and_hint_drops_board_code() -> None:
    slim = _slim_sector_fund_flow(_full_flow())
    assert slim["flow_tiers"] == {
        "super_large_net_yi": -20.0,
        "large_net_yi": -5.0,
        "medium_net_yi": 10.0,
        "small_net_yi": 7.0,
    }
    assert slim["flow_structure_hint"].startswith("超大单+大单（机构）净流出")
    assert slim["cumulative_5d_net_yi"] == 12.0
    assert slim["cumulative_20d_net_yi"] == 30.0
    assert "board_code" not in slim
    assert "recent_5d_main_force_yi" not in slim


def test_slim_sector_fund_flow_unavailable_short_circuits() -> None:
    slim = _slim_sector_fund_flow({"available": False, "sector_label": "半导体", "message": "暂无板块历史资金流"})
    assert slim == {
        "available": False,
        "sector_label": "半导体",
        "message": "暂无板块历史资金流",
    }
