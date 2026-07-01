from __future__ import annotations

from app.models import Holding
from app.services.report_sector_opportunity import build_holding_sector_opportunity_context


def _holding(sector_name: str | None) -> Holding:
    return Holding(
        fund_code="000001",
        fund_name="测试基金",
        holding_amount=1000.0,
        sector_name=sector_name,
    )


def _heat_row(label: str, *, change_1d: float, change_5d: float, heat_score: float) -> dict:
    return {
        "sector_label": label,
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "heat_score": heat_score,
    }


def test_no_holding_sector_returns_unavailable() -> None:
    result = build_holding_sector_opportunity_context([_holding(None)])
    assert result["available"] is False
    assert result["held"] == {}
    assert result["market_top"] == []


def test_sector_heat_error_is_best_effort() -> None:
    def boom() -> list[dict]:
        raise RuntimeError("network down")

    result = build_holding_sector_opportunity_context(
        [_holding("半导体")], fetch_sector_heat=boom
    )
    assert result["available"] is False
    assert result["reason"] == "sector_heat_error"


def test_held_sector_is_present_even_when_not_a_top_opportunity(monkeypatch) -> None:
    heat = [
        _heat_row("半导体", change_1d=-3.0, change_5d=-8.0, heat_score=10.0),
        _heat_row("白酒", change_1d=2.0, change_5d=5.0, heat_score=90.0),
    ]

    def fake_flow_map(_sector_heat, _labels, **_kwargs):
        return {
            "半导体": {
                "available": True,
                "date_aligned": True,
                "today_main_force_net_yi": -6.0,
                "cumulative_5d_net_yi": -14.0,
                "pattern_label": "distribution",
            },
            "白酒": {
                "available": True,
                "date_aligned": True,
                "today_main_force_net_yi": 8.0,
                "cumulative_5d_net_yi": 20.0,
                "pattern_label": "price_flow_aligned_up",
            },
        }

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )

    result = build_holding_sector_opportunity_context(
        [_holding("半导体")], fetch_sector_heat=lambda: heat
    )

    assert result["available"] is True
    # 半导体资金持续流出、价格走弱：不构成机会，但依然要给出方向判断
    held = result["held"]["半导体"]
    assert held["opportunity_available"] is False
    assert held["pattern_label"] == "distribution"

    # 白酒机会分更高，且未被持有，应出现在轮动参考里
    market_top_labels = [item["sector_label"] for item in result["market_top"]]
    assert "白酒" in market_top_labels
    assert "半导体" not in market_top_labels


def test_market_top_excludes_all_held_labels(monkeypatch) -> None:
    heat = [
        _heat_row("半导体", change_1d=1.0, change_5d=4.0, heat_score=80.0),
        _heat_row("白酒", change_1d=2.0, change_5d=5.0, heat_score=90.0),
    ]

    def fake_flow_map(_sector_heat, _labels, **_kwargs):
        return {
            label: {
                "available": True,
                "date_aligned": True,
                "today_main_force_net_yi": 5.0,
                "cumulative_5d_net_yi": 10.0,
                "pattern_label": "price_flow_aligned_up",
            }
            for label in ("半导体", "白酒")
        }

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )

    result = build_holding_sector_opportunity_context(
        [_holding("半导体"), _holding("白酒")], fetch_sector_heat=lambda: heat
    )

    assert result["available"] is True
    assert result["held"]["半导体"]["opportunity_available"] is True
    assert result["held"]["白酒"]["opportunity_available"] is True
    assert result["market_top"] == []
