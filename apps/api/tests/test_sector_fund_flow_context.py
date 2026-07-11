"""板块资金流注入日报 facts：日期对齐与正负号语义。"""

from datetime import date, timedelta
from threading import Event
from time import monotonic

import pytest

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
            "trade_date": "2026-07-03",
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
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["history_point_count"] == 2
    assert ctx["cumulative_5d_net_yi"] is None


def test_live_only_snapshot_makes_today_available_without_fabricating_five_day(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0963", "商业航天"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {
            "trade_date": "2026-07-03",
            "items": [
                {
                    "flow_source_code": "BK0963",
                    "main_force_net_yi": 41.86,
                    "flow_tiers": {"super_large_net_yi": 34.73},
                }
            ]
        },
    )

    ctx = build_sector_fund_flow_context(
        "商业航天",
        sector_return_percent=2.24,
        trade_date="2026-07-03",
    )

    assert ctx["available"] is True
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["history_point_count"] == 1
    assert ctx["today_main_force_net_yi"] == 41.86
    assert ctx["cumulative_5d_net_yi"] is None


def test_live_snapshot_replaces_same_day_history_point(monkeypatch):
    series = [
        {"date": "2026-06-29", "main_force_net_yi": 1.0},
        {"date": "2026-06-30", "main_force_net_yi": 2.0},
        {"date": "2026-07-01", "main_force_net_yi": 3.0},
        {"date": "2026-07-02", "main_force_net_yi": 4.0},
        {"date": "2026-07-03", "main_force_net_yi": -100.0},
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
            "trade_date": "2026-07-03",
            "items": [
                {
                    "flow_source_code": "BK0963",
                    "main_force_net_yi": 5.0,
                    "flow_tiers": {"large_net_yi": 2.0},
                }
            ]
        },
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is True
    assert ctx["history_point_count"] == 5
    assert ctx["today_main_force_net_yi"] == 5.0
    assert ctx["cumulative_5d_net_yi"] == 15.0
    assert ctx["flow_tiers"] == {"large_net_yi": 2.0}


@pytest.mark.parametrize("snapshot_trade_date", [None, "2026-07-04"])
def test_live_snapshot_without_matching_trade_date_is_not_merged(
    monkeypatch,
    snapshot_trade_date,
):
    series = [{"date": "2026-07-02", "main_force_net_yi": 2.0}]
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
            "trade_date": snapshot_trade_date,
            "items": [
                {
                    "flow_source_code": "BK0963",
                    "main_force_net_yi": 50.0,
                }
            ],
        },
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["today_available"] is False
    assert ctx["history_point_count"] == 1
    assert ctx["flow_date"] == "2026-07-02"
    assert ctx["today_main_force_net_yi"] == 2.0


def test_mismatched_live_snapshot_does_not_replace_independent_same_day_history(monkeypatch):
    series = [{"date": "2026-07-03", "main_force_net_yi": 3.0}]
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
            "trade_date": "2026-07-04",
            "items": [
                {
                    "flow_source_code": "BK0963",
                    "main_force_net_yi": 50.0,
                }
            ],
        },
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["today_available"] is True
    assert ctx["history_point_count"] == 1
    assert ctx["today_main_force_net_yi"] == 3.0


def test_future_and_non_finite_points_are_discarded(monkeypatch):
    series = [
        {"date": "2026-07-02", "main_force_net_yi": 2.0},
        {"date": "2026-07-03", "main_force_net_yi": 3.0},
        {"date": "2026-07-04", "main_force_net_yi": 400.0},
        {"date": "2026-07-01", "main_force_net_yi": float("nan")},
        {"date": "2026-06-30", "main_force_net_yi": float("inf")},
        {"date": "2026-06-29", "main_force_net_yi": "not-a-number"},
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
        lambda: None,
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["history_point_count"] == 2
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["cumulative_5d_net_yi"] is None
    assert ctx["cumulative_20d_net_yi"] == 5.0


def test_dates_are_deduplicated_and_sorted_before_latest_five_are_summed(monkeypatch):
    series = [
        {"date": "2026-07-06", "main_force_net_yi": 6.0},
        {"date": "2026-06-29", "main_force_net_yi": 1.0},
        {"date": "2026-06-30", "main_force_net_yi": 2.0},
        {"date": "2026-07-01", "main_force_net_yi": 3.0},
        {"date": "2026-07-01", "main_force_net_yi": 3.0},
        {"date": "2026-07-02", "main_force_net_yi": 4.0},
        {"date": "2026-07-03", "main_force_net_yi": 5.0},
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
        lambda: None,
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-06")

    assert ctx["history_point_count"] == 6
    assert ctx["five_day_available"] is True
    assert ctx["cumulative_5d_net_yi"] == 20.0


def test_fewer_than_five_aligned_points_do_not_produce_five_day_value(monkeypatch):
    series = [
        {"date": "2026-06-30", "main_force_net_yi": 1.0},
        {"date": "2026-07-01", "main_force_net_yi": 2.0},
        {"date": "2026-07-02", "main_force_net_yi": 3.0},
        {"date": "2026-07-03", "main_force_net_yi": 4.0},
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
        lambda: None,
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["history_point_count"] == 4
    assert ctx["cumulative_5d_net_yi"] is None


def test_exactly_five_aligned_unique_points_produce_five_day_value(monkeypatch):
    series = [
        {"date": "2026-06-29", "main_force_net_yi": 1.0},
        {"date": "2026-06-30", "main_force_net_yi": 2.0},
        {"date": "2026-07-01", "main_force_net_yi": 3.0},
        {"date": "2026-07-02", "main_force_net_yi": 4.0},
        {"date": "2026-07-03", "main_force_net_yi": 5.0},
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
        lambda: None,
    )

    ctx = build_sector_fund_flow_context("商业航天", trade_date="2026-07-03")

    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is True
    assert ctx["history_point_count"] == 5
    assert ctx["cumulative_5d_net_yi"] == 15.0


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
        lambda: {
            "trade_date": "2026-06-24",
            "items": [
                {
                    "sector_label": "白酒",
                    "flow_source_code": "BK0896",
                    "main_force_net_yi": 5.0,
                }
            ],
        },
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


def test_matching_theme_live_flow_does_not_wait_for_cold_history(monkeypatch):
    release = Event()
    finished = Event()

    def _blocked_history(_board_code, _trade_date):
        try:
            release.wait(timeout=0.2)
            return []
        finally:
            finished.set()

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK9001", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        _blocked_history,
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._FLOW_HISTORY_BUDGET_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {
            "trade_date": "2026-07-10",
            "items": [
                {
                    "flow_source_code": "BK9001",
                    "main_force_net_yi": -12.5,
                    "flow_tiers": {"super_large_net_yi": -8.0},
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.fetch_eastmoney_current_board_flow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("targeted fallback must not run when the theme snapshot hits")
        ),
    )

    started = monotonic()
    try:
        ctx = build_sector_fund_flow_context("人工智能", trade_date="2026-07-10")
        elapsed = monotonic() - started
    finally:
        release.set()
        assert finished.wait(timeout=0.2)

    assert elapsed < 0.08
    assert ctx["available"] is True
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["today_main_force_net_yi"] == -12.5
    assert ctx["history_point_count"] == 1


def test_targeted_current_flow_fills_board_missing_from_theme_snapshot(monkeypatch):
    release = Event()
    finished = Event()
    targeted_calls: list[tuple[str, str]] = []

    def _blocked_history(_board_code, _trade_date):
        try:
            release.wait(timeout=0.2)
            return []
        finally:
            finished.set()

    def _targeted(secid, *, trade_date, **_kwargs):
        targeted_calls.append((secid, trade_date))
        return {
            "date": "2026-07-10",
            "main_force_net_yi": -134.84,
            "flow_tiers": {
                "super_large_net_yi": -100.81,
                "large_net_yi": -34.02,
                "medium_net_yi": 38.1,
                "small_net_yi": 97.24,
            },
        }

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0800", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        _blocked_history,
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._FLOW_HISTORY_BUDGET_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {
            "trade_date": "2026-07-10",
            "items": [{"flow_source_code": "BK9999", "main_force_net_yi": 1.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.fetch_eastmoney_current_board_flow",
        _targeted,
    )

    started = monotonic()
    try:
        ctx = build_sector_fund_flow_context("人工智能", trade_date="2026-07-10")
        elapsed = monotonic() - started
    finally:
        release.set()
        assert finished.wait(timeout=0.2)

    assert elapsed < 0.08
    assert targeted_calls == [("90.BK0800", "2026-07-10")]
    assert ctx["available"] is True
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is False
    assert ctx["history_point_count"] == 1
    assert ctx["today_main_force_net_yi"] == -134.84
    assert ctx["cumulative_5d_net_yi"] is None
    assert ctx["flow_tiers"]["small_net_yi"] == 97.24


def test_no_live_flow_and_slow_history_degrades_promptly(monkeypatch):
    release = Event()
    finished = Event()

    def _blocked_history(_board_code, _trade_date):
        try:
            release.wait(timeout=0.2)
            return []
        finally:
            finished.set()

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK9003", "无实时板块"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        _blocked_history,
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._FLOW_HISTORY_BUDGET_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {"trade_date": "2026-07-10", "items": []},
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.fetch_eastmoney_current_board_flow",
        lambda *_args, **_kwargs: None,
    )

    started = monotonic()
    try:
        ctx = build_sector_fund_flow_context("无实时板块", trade_date="2026-07-10")
        elapsed = monotonic() - started
    finally:
        release.set()
        assert finished.wait(timeout=0.2)

    assert elapsed < 0.08
    assert ctx["available"] is False
    assert ctx["today_available"] is False
    assert ctx["five_day_available"] is False
    assert ctx["history_point_count"] == 0
    assert "today_main_force_net_yi" not in ctx


def test_fast_history_still_merges_with_live_for_exact_5d_and_20d(monkeypatch):
    start = date(2026, 6, 21)
    history = [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "main_force_net_yi": float(offset + 1),
        }
        for offset in range(20)
    ]
    history[-1]["main_force_net_yi"] = -100.0

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK9004", "快速历史"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        lambda _board_code, _trade_date: list(history),
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {
            "trade_date": "2026-07-10",
            "items": [
                {
                    "flow_source_code": "BK9004",
                    "main_force_net_yi": 20.0,
                    "flow_tiers": {"large_net_yi": 2.0},
                }
            ],
        },
    )

    ctx = build_sector_fund_flow_context("快速历史", trade_date="2026-07-10")

    assert ctx["history_point_count"] == 20
    assert ctx["today_available"] is True
    assert ctx["five_day_available"] is True
    assert ctx["cumulative_5d_net_yi"] == 90.0
    assert ctx["cumulative_20d_net_yi"] == 210.0


def test_targeted_current_flow_date_is_rechecked_before_merge(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK9005", "日期安全"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        lambda _board_code, _trade_date: [],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {"trade_date": "2026-07-10", "items": []},
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.fetch_eastmoney_current_board_flow",
        lambda *_args, **_kwargs: {
            "date": "2026-07-09",
            "main_force_net_yi": 99.0,
            "flow_tiers": {},
        },
    )

    ctx = build_sector_fund_flow_context("日期安全", trade_date="2026-07-10")

    assert ctx["available"] is False
    assert ctx["today_available"] is False
    assert ctx["history_point_count"] == 0


def test_slow_targeted_current_flow_does_not_escape_its_request_budget(monkeypatch):
    release = Event()
    finished = Event()

    def _blocked_current_flow(*_args, **_kwargs):
        try:
            release.wait(timeout=0.2)
            return None
        finally:
            finished.set()

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK9006", "慢实时板块"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._load_flow_series",
        lambda _board_code, _trade_date: [],
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context._CURRENT_FLOW_BUDGET_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: {"trade_date": "2026-07-10", "items": []},
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.fetch_eastmoney_current_board_flow",
        _blocked_current_flow,
    )

    started = monotonic()
    try:
        ctx = build_sector_fund_flow_context("慢实时板块", trade_date="2026-07-10")
        elapsed = monotonic() - started
    finally:
        release.set()
        assert finished.wait(timeout=0.2)

    assert elapsed < 0.08
    assert ctx["available"] is False
    assert ctx["today_available"] is False
