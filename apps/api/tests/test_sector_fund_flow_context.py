"""板块资金流注入日报 facts：日期对齐与正负号语义。"""

from app.models import Holding
from app.services.board_fund_flow_history import parse_board_flow_kline
from app.services.sector_fund_flow_context import (
    _pick_flow_point,
    build_sector_fund_flow_context,
    build_sector_fund_flow_map,
)


def test_parse_board_flow_kline_positive_is_inflow():
    raw = "2026-06-24,21253000000.0,-3857000000.0,-17354000000.0,-1673000000.0,22926000000.0"
    parsed = parse_board_flow_kline(raw)
    assert parsed is not None
    assert parsed["date"] == "2026-06-24"
    assert parsed["main_force_net_yi"] == 212.53
    assert parsed["flow_tiers"]["super_large_net_yi"] == 229.26


def test_parse_board_flow_kline_negative_is_outflow():
    raw = "2026-06-23,-21673000000.0,6263000000.0,15387000000.0,-2047000000.0,-19626000000.0"
    parsed = parse_board_flow_kline(raw)
    assert parsed is not None
    assert parsed["main_force_net_yi"] == -216.73


def test_pick_flow_point_uses_trade_date_not_series_tail():
    series = [
        {"date": "2026-06-22", "main_force_net_yi": -73.75},
        {"date": "2026-06-23", "main_force_net_yi": -216.73},
        {"date": "2026-06-24", "main_force_net_yi": 212.53},
    ]
    point = _pick_flow_point(series, "2026-06-24")
    assert point["main_force_net_yi"] == 212.53


def test_build_sector_fund_flow_context_aligns_june24_inflow(monkeypatch):
    series = [
        {"date": "2026-06-23", "main_force_net_yi": -216.73, "flow_tiers": {}},
        {
            "date": "2026-06-24",
            "main_force_net_yi": 212.53,
            "flow_tiers": {"super_large_net_yi": 229.26, "small_net_yi": -38.57},
        },
    ]

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK1036", "半导体"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: list(series),
    )

    ctx = build_sector_fund_flow_context(
        "半导体",
        sector_return_percent=5.27,
        trade_date="2026-06-24",
    )
    assert ctx["today_main_force_net_yi"] == 212.53
    assert ctx["main_force_direction"] == "inflow"
    assert ctx["flow_date"] == "2026-06-24"
    assert ctx["date_aligned"] is True
    assert ctx["pattern_label"] == "price_flow_aligned_up"


def test_mismatch_date_skips_distribution_pattern(monkeypatch):
    series = [
        {"date": "2026-06-23", "main_force_net_yi": -216.73, "flow_tiers": {}},
    ]

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK1036", "半导体"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: list(series),
    )

    ctx = build_sector_fund_flow_context(
        "半导体",
        sector_return_percent=5.27,
        trade_date="2026-06-24",
    )
    assert ctx["flow_date"] == "2026-06-23"
    assert ctx["date_aligned"] is False
    assert ctx["pattern_label"] == "flow_date_mismatch"
    assert "勿做量价背离" in ctx["pattern_hint"]


def test_build_sector_fund_flow_map_reuses_trade_date(monkeypatch):
    calls: list[str | None] = []

    def _fake_build(label, *, sector_return_percent=None, trade_date=None):
        calls.append(trade_date)
        return {"available": True, "sector_label": label}

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.build_sector_fund_flow_context",
        _fake_build,
    )

    holdings = [
        Holding(fund_code="519674", fund_name="A", sector_name="半导体", holding_amount=1),
        Holding(fund_code="519672", fund_name="B", sector_name="半导体", holding_amount=2),
    ]
    build_sector_fund_flow_map(holdings, trade_date="2026-06-24")
    assert calls == ["2026-06-24"]
