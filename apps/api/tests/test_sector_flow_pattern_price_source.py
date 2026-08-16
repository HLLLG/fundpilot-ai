"""量价 pattern 的价格腿必须与资金流同一个成分篮子。

回归背景：指数主题（如医疗）的展示涨幅走中证指数（source_code），主力资金走东财
BK 板块（flow_source_code），两个篮子同日涨跌可差数个百分点。旧实现把指数涨幅与
BK 资金拼在一起判 distribution/accumulation，可能产出纯粹由成分差造成的假背离，
再经打分（distribution −30）与日报 prompt 影响「该不该追」。现行为：

- pattern 的价格一律取资金流点位自带的板块涨跌（盘中：主题快照同一行 clist 的
  ``flow_change_1d_percent``；收盘后：daykline 行自身的 ``change_percent``）；
- 同源价格缺失时输出 ``price_source_mismatch``，宁可不判，也不拿指数涨幅凑。
"""

from __future__ import annotations

import pytest

from app.services import sector_fund_flow_context as ctx
from app.services.eastmoney_spot_client import _parse_current_board_flow_kline

_TRADE_DATE = "2026-08-14"


def _theme_snapshot(item: dict) -> dict:
    return {"trade_date": _TRADE_DATE, "items": [item]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(
        ctx, "fetch_eastmoney_current_board_flow", lambda *_args, **_kwargs: None
    )


def _patch_resolver(monkeypatch, code: str, label: str) -> None:
    monkeypatch.setattr(
        ctx, "resolve_board_flow_code_for_sector", lambda _label: (code, label)
    )


def _patch_history(monkeypatch, series: list[dict]) -> None:
    monkeypatch.setattr(
        ctx, "get_cached_board_flow_series", lambda _code, **_kwargs: list(series)
    )


def test_pattern_price_comes_from_flow_row_not_index(monkeypatch) -> None:
    """盘中：pattern 用主题快照里与资金同一行的板块涨跌，而不是指数涨幅。"""
    _patch_resolver(monkeypatch, "BK9901", "医疗")
    _patch_history(monkeypatch, [])
    snapshot = _theme_snapshot(
        {
            "sector_label": "医疗",
            "flow_source_code": "BK9901",
            "main_force_net_yi": -3.2,
            "flow_tiers": {
                "super_large_net_yi": -2.0,
                "large_net_yi": -1.2,
                "medium_net_yi": 1.4,
                "small_net_yi": 1.8,
            },
            # 指数口径当日可以是上涨的（旧实现会据此误判 distribution）；
            # 同源口径下板块自身下跌 + 主力流出 → weak_outflow。
            "flow_change_1d_percent": -0.8,
        }
    )

    context = ctx.build_sector_fund_flow_context(
        "医疗", trade_date=_TRADE_DATE, theme_snapshot=snapshot
    )

    assert context is not None
    assert context["date_aligned"] is True
    assert context["pattern_label"] == "weak_outflow"
    assert context["flow_price_change_percent"] == -0.8
    assert context["pattern_price_source"] == "board_flow"


def test_missing_same_source_price_never_borrows_index_change(monkeypatch) -> None:
    """同源价格缺失（如旧版快照无 flow_change 字段）时宁可不判，资金数字本身仍可用。"""
    _patch_resolver(monkeypatch, "BK9902", "军工")
    _patch_history(monkeypatch, [])
    snapshot = _theme_snapshot(
        {
            "sector_label": "军工",
            "flow_source_code": "BK9902",
            "main_force_net_yi": -3.2,
            "flow_tiers": None,
        }
    )

    context = ctx.build_sector_fund_flow_context(
        "军工", trade_date=_TRADE_DATE, theme_snapshot=snapshot
    )

    assert context is not None
    assert context["pattern_label"] == "price_source_mismatch"
    assert context["flow_price_change_percent"] is None
    assert context["pattern_price_source"] is None
    assert "勿做量价背离判断" in context["pattern_hint"]
    assert context["today_main_force_net_yi"] == -3.2


def test_post_close_daykline_change_drives_pattern(monkeypatch) -> None:
    """收盘后：daykline 当日行自带板块涨跌，pattern 直接在同源数据内判定。"""
    _patch_resolver(monkeypatch, "BK9903", "半导体")
    series = [
        {"date": f"2026-08-{day:02d}", "main_force_net_yi": 1.0, "change_percent": 0.4}
        for day in (10, 11, 12, 13)
    ] + [{"date": _TRADE_DATE, "main_force_net_yi": 5.5, "change_percent": 1.6}]
    _patch_history(monkeypatch, series)

    context = ctx.build_sector_fund_flow_context(
        "半导体", trade_date=_TRADE_DATE, theme_snapshot=None
    )

    assert context is not None
    assert context["pattern_label"] == "price_flow_aligned_up"
    assert context["flow_price_change_percent"] == 1.6
    assert context["pattern_price_source"] == "board_flow"
    assert context["five_day_source"] == "history"


def test_live_point_preserves_history_same_day_change(monkeypatch) -> None:
    """live 快照缺同源涨跌而当日 daykline 行已有时，价格腿保留历史行的值。"""
    _patch_resolver(monkeypatch, "BK9904", "创新药")
    _patch_history(
        monkeypatch,
        [{"date": _TRADE_DATE, "main_force_net_yi": -1.0, "change_percent": 1.2}],
    )
    snapshot = _theme_snapshot(
        {
            "sector_label": "创新药",
            "flow_source_code": "BK9904",
            # live 主力净流入是更新的权威值，但旧版快照没有 flow_change 字段。
            "main_force_net_yi": -3.6,
            "flow_tiers": None,
        }
    )

    context = ctx.build_sector_fund_flow_context(
        "创新药", trade_date=_TRADE_DATE, theme_snapshot=snapshot
    )

    assert context is not None
    assert context["today_main_force_net_yi"] == -3.6
    assert context["flow_price_change_percent"] == 1.2
    assert context["pattern_label"] == "distribution"


def test_current_board_flow_kline_carries_board_change_percent() -> None:
    """盘中单板 fflow 兜底行也带板块自身涨跌（f63，parts[12]），缺失时为 None。"""
    raw = (
        f"{_TRADE_DATE},-320000000.0,180000000.0,140000000.0,-120000000.0,"
        "-200000000.0,-3.1,1.7,1.4,-1.2,-1.9,1234.56,-0.83,0.0,0.0"
    )
    parsed = _parse_current_board_flow_kline(raw, trade_date=_TRADE_DATE)
    assert parsed is not None
    assert parsed["main_force_net_yi"] == -3.2
    assert parsed["change_percent"] == -0.83

    legacy = _parse_current_board_flow_kline(
        f"{_TRADE_DATE},-320000000.0,180000000.0,140000000.0,-120000000.0,-200000000.0",
        trade_date=_TRADE_DATE,
    )
    assert legacy is not None
    assert legacy["change_percent"] is None
