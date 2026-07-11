from __future__ import annotations

from app.models import Holding
from app.services.report_sector_opportunity import build_holding_sector_opportunity_context


def _holding(
    sector_name: str | None,
    *,
    sector_return_percent: float | None = None,
) -> Holding:
    return Holding(
        fund_code="000001",
        fund_name="测试基金",
        holding_amount=1000.0,
        sector_name=sector_name,
        sector_return_percent=sector_return_percent,
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


def test_sector_heat_error_still_fetches_and_returns_held_flow(monkeypatch) -> None:
    flow = {
        "半导体": {
            "available": True,
            "date_aligned": True,
            "today_available": True,
            "five_day_available": False,
            "history_point_count": 1,
            "today_main_force_net_yi": 2.0,
            "cumulative_5d_net_yi": None,
            "pattern_label": "flow_turning_positive",
        }
    }
    calls: list[tuple[list[dict], list[str], str | None]] = []

    def fake_flow_map(sector_heat, labels, *, trade_date=None, **_kwargs):
        calls.append((sector_heat, labels, trade_date))
        return flow

    def boom() -> list[dict]:
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    result = build_holding_sector_opportunity_context(
        [_holding("半导体", sector_return_percent=-2.5)],
        trade_date="2026-07-10",
        fetch_sector_heat=boom,
    )

    assert calls == [
        (
            [{"sector_label": "半导体", "change_1d_percent": -2.5}],
            ["半导体"],
            "2026-07-10",
        )
    ]
    assert result["available"] is False
    assert result["reason"] == "sector_heat_error"
    assert result["sector_flow_by_label"] is flow
    assert result["held"]["半导体"]["today_main_force_net_yi"] == 2.0
    assert result["market_top"] == []


def test_missing_heat_and_flow_never_create_a_positive_held_opportunity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )

    result = build_holding_sector_opportunity_context(
        [_holding("test-sector")],
        fetch_sector_heat=lambda: [],
    )

    held = result["held"]["test-sector"]
    assert result["available"] is False
    assert held["opportunity_available"] is False
    assert held["confidence"] == "不足"
    assert held["entry_hint"] == "数据不足，保持观察"


def test_placeholder_heat_rows_do_not_create_held_or_market_opportunities(
    monkeypatch,
) -> None:
    flow_inputs: list[list[dict]] = []

    def fake_flow_map(sector_heat, *_args, **_kwargs):
        flow_inputs.append(sector_heat)
        return {}

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    placeholder_heat = [
        {
            "sector_label": "test-sector",
            "change_1d_percent": None,
            "change_5d_percent": None,
            "heat_score": None,
        },
        {
            "sector_label": "placeholder-candidate",
            "change_1d_percent": None,
            "change_5d_percent": None,
            "heat_score": None,
        },
    ]

    result = build_holding_sector_opportunity_context(
        [_holding("test-sector")],
        fetch_sector_heat=lambda: placeholder_heat,
    )

    assert flow_inputs == [
        [{"sector_label": "test-sector", "change_1d_percent": None}]
    ]
    assert result["available"] is False
    assert result["held"]["test-sector"]["opportunity_available"] is False
    assert result["market_top"] == []


def test_held_labels_are_requested_before_heat_candidates_and_trade_date_is_forwarded(
    monkeypatch,
) -> None:
    heat = [
        _heat_row("证券", change_1d=2.0, change_5d=5.0, heat_score=99.0),
        _heat_row("半导体", change_1d=1.0, change_5d=3.0, heat_score=80.0),
        _heat_row("白酒", change_1d=0.5, change_5d=2.0, heat_score=70.0),
    ]
    flow: dict[str, dict] = {}
    calls: list[tuple[list[str], str | None]] = []

    def fake_flow_map(_sector_heat, labels, *, trade_date=None, **_kwargs):
        calls.append((list(labels), trade_date))
        return flow

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )

    result = build_holding_sector_opportunity_context(
        [_holding("半导体"), _holding("白酒"), _holding("半导体")],
        trade_date="2026-07-10",
        fetch_sector_heat=lambda: heat,
    )

    assert calls == [(["半导体", "白酒", "证券"], "2026-07-10")]
    assert result["sector_flow_by_label"] is flow


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
