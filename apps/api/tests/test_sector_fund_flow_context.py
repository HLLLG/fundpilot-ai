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
    """历史资金流序列缺当日数据、且主题板块实时快照也没有该板块时的兜底行为：
    仍标 date_aligned=False，不伪造当日数字。"""
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
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: None,
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


def test_missing_today_row_is_spliced_from_live_theme_board_snapshot(monkeypatch):
    """根因修复（2026-07-04）：东财 daykline 历史接口盘中常滞后一天落定「今日」行，
    但主题板块榜的 main_force_net_yi 与当日涨跌幅同一次实时快照拉取、天然同日对齐——
    历史序列缺当日行时应拼接这个已对齐的实时值，而不是把前一日数据误标为「今日」。"""
    series = [
        {"date": "2026-06-30", "main_force_net_yi": -126.92, "flow_tiers": {}},
    ]

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0963", "商业航天"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: list(series),
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {
            "items": [
                {
                    "sector_label": "商业航天",
                    "flow_source_code": "BK0963",
                    "main_force_net_yi": 41.86,
                    "flow_tiers": {
                        "super_large_net_yi": 34.73,
                        "large_net_yi": 7.13,
                        "medium_net_yi": -21.73,
                        "small_net_yi": -19.04,
                    },
                }
            ]
        },
    )

    ctx = build_sector_fund_flow_context(
        "商业航天",
        sector_return_percent=2.24,
        trade_date="2026-07-03",
    )
    assert ctx["flow_date"] == "2026-07-03"
    assert ctx["date_aligned"] is True
    assert ctx["today_main_force_net_yi"] == 41.86
    assert ctx["main_force_direction"] == "inflow"
    assert ctx["pattern_label"] == "price_flow_aligned_up"
    # 5 日累计须包含拼接后的当日行，不能只统计历史序列里滞后的旧数据。
    assert ctx["cumulative_5d_net_yi"] == round(-126.92 + 41.86, 2)


def test_live_snapshot_without_matching_board_code_does_not_splice(monkeypatch):
    """实时快照存在但没有该板块（板块不在主题白名单/未缓存该 BK）时，不应误拼接。"""
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
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {"items": [{"sector_label": "白酒", "flow_source_code": "BK0896", "main_force_net_yi": 5.0}]},
    )

    ctx = build_sector_fund_flow_context(
        "半导体",
        sector_return_percent=5.27,
        trade_date="2026-06-24",
    )
    assert ctx["date_aligned"] is False
    assert ctx["today_main_force_net_yi"] == -216.73


def test_flow_tiers_and_structure_hint_are_returned_for_institutional_vs_retail_divergence(
    monkeypatch,
):
    """2026-07-04：机构(超大单+大单) vs 散户(中单+小单) 资金结构解读不再局限于
    「涨但主力流出」这一种 pattern，只要四档数据存在且方向背离即给出解读；
    同时确认 flow_tiers 原样透传（供后续按当日结构分析，而非逐日序列）。"""
    series = [
        {
            "date": "2026-07-03",
            "main_force_net_yi": -8.0,
            "flow_tiers": {
                "super_large_net_yi": -20.0,
                "large_net_yi": -5.0,
                "medium_net_yi": 10.0,
                "small_net_yi": 7.0,
            },
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
        sector_return_percent=-1.2,  # price_down + flow_out -> weak_outflow 分支，非 distribution
        trade_date="2026-07-03",
    )
    assert ctx["pattern_label"] == "weak_outflow"
    assert ctx["flow_tiers"]["super_large_net_yi"] == -20.0
    assert "recent_5d_main_force_yi" not in ctx
    assert ctx["flow_structure_hint"] == "超大单+大单（机构）净流出而中单+小单（大户/散户）净流入，散户接盘特征明显。"


def test_flow_structure_hint_is_none_when_tiers_missing(monkeypatch):
    series = [{"date": "2026-07-03", "main_force_net_yi": 5.0, "flow_tiers": {}}]
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
        sector_return_percent=1.0,
        trade_date="2026-07-03",
    )
    assert ctx["flow_structure_hint"] is None


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
