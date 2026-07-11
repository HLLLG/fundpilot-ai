"""板块资金流日期不对齐时不得被当作「今日」证据（2026-07-04 回归修复）。

根因：`_compute_opportunity_row` 检测到 `date_aligned=False` 时只追加了一条
"资金流日期未对齐" 的警示文案，却让 `today_main_force_net_yi` / `cumulative_5d_net_yi`
原样参与打分并写进 evidence/返回字段——导致日报/荐基卡片一边显示"资金日期需核验"，
一边又言之凿凿地给出具体的"今日主力净流入 XX 亿"（实际是几天前的旧数据）。
"""

from __future__ import annotations

from app.services.sector_opportunity_scoring import (
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
)


def _heat_row(label: str = "商业航天", *, change_1d: float = 2.24, change_5d: float = 3.26) -> dict:
    return {
        "sector_label": label,
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "heat_score": 60.0,
    }


def _misaligned_flow(*, today: float = -126.92, five_day: float = -351.42) -> dict:
    return {
        "available": True,
        "date_aligned": False,
        "flow_date": "2026-06-30",
        "trade_date": "2026-07-03",
        "today_main_force_net_yi": today,
        "cumulative_5d_net_yi": five_day,
        "pattern_label": "flow_date_mismatch",
    }


def test_describe_opportunity_hides_stale_flow_numbers_when_dates_misaligned():
    result = describe_sector_opportunity(_heat_row(), _misaligned_flow(), focus=set())
    assert result is not None
    assert result["today_main_force_net_yi"] is None
    assert result["cumulative_5d_net_yi"] is None
    assert "今日主力净流入" not in result["evidence"]
    assert "5日主力净流入" not in result["evidence"]
    assert "资金流日期未对齐" in result["penalties"]
    assert result["pattern_label"] == "flow_date_mismatch"


def test_misaligned_flow_does_not_boost_score_vs_no_flow_at_all():
    """未对齐的资金流不应比"完全没有资金流"打出更高的分——否则说明旧数字仍在偷偷计分。"""
    misaligned = describe_sector_opportunity(_heat_row(), _misaligned_flow(), focus=set())
    no_flow = describe_sector_opportunity(_heat_row(), None, focus=set())
    assert misaligned["score"] == no_flow["score"]


def test_aligned_flow_still_boosts_score_normally():
    """确认修复没有误伤"日期对齐"的正常路径——对齐时资金流仍应参与加分。"""
    aligned_flow = {
        "available": True,
        "date_aligned": True,
        "today_main_force_net_yi": 30.0,
        "cumulative_5d_net_yi": 40.0,
        "pattern_label": "price_flow_aligned_up",
    }
    aligned = describe_sector_opportunity(_heat_row(), aligned_flow, focus=set())
    misaligned = describe_sector_opportunity(_heat_row(), _misaligned_flow(), focus=set())
    assert aligned["today_main_force_net_yi"] == 30.0
    assert aligned["today_available"] is True
    assert aligned["five_day_available"] is True
    assert "今日主力净流入" in aligned["evidence"]
    assert aligned["score"] > misaligned["score"]


def test_today_and_five_day_availability_are_gated_and_propagated_independently():
    flow = {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": False,
        "five_day_source": "eastmoney_rank",
        "history_point_count": 2,
        "today_main_force_net_yi": 30.0,
        "cumulative_5d_net_yi": 40.0,
        "pattern_label": "price_flow_aligned_up",
    }

    result = describe_sector_opportunity(_heat_row(), flow, focus=set())

    assert result["today_available"] is True
    assert result["five_day_available"] is False
    assert result["five_day_source"] == "eastmoney_rank"
    assert result["history_point_count"] == 2
    assert result["today_main_force_net_yi"] == 30.0
    assert result["cumulative_5d_net_yi"] is None
    assert "今日主力净流入" in result["evidence"]
    assert "5日主力净流入" not in result["evidence"]


def test_opportunity_flow_map_forwards_explicit_trade_date(monkeypatch):
    calls: list[tuple[str, float | None, str | None]] = []

    def _fake_build(label, *, sector_return_percent=None, trade_date=None):
        calls.append((label, sector_return_percent, trade_date))
        return {"available": True, "sector_label": label}

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.build_sector_fund_flow_context",
        _fake_build,
    )

    result = build_sector_flow_map_for_opportunities(
        [_heat_row()],
        ["商业航天"],
        trade_date="2026-07-03",
    )

    assert result == {"商业航天": {"available": True, "sector_label": "商业航天"}}
    assert calls == [("商业航天", 2.24, "2026-07-03")]


def test_confidence_stays_low_when_date_misaligned_even_with_strong_divergence_evidence():
    result = describe_sector_opportunity(
        _heat_row(),
        _misaligned_flow(),
        focus=set(),
        divergence_backtest={
            "by_rule": {
                "flow_price_distribution": {"significant": True, "edge_percent": 20.0},
            }
        },
    )
    assert result["confidence"] == "低"
