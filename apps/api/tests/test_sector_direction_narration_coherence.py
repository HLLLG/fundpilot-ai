"""方向已破位时，入场侧的叙述不得替它背书。

回归背景（用户在生产日报上提出的「这是不是自相矛盾」）：半导体材料同一张卡片上同时出现

* 退出侧：趋势强度 40.19，连续 3 个交易日低于退出线 52，判「大幅减仓评估 −50%」、
  `allows_add=False`；
* 入场侧：「趋势成形信号分 72/100 · **信号偏强**」、「本次比例 **计划仓位的 60%**」、
  等待条件「20日相对强度与趋势持续性**继续**改善」。

两条阈值本身是嵌套的（回到 52 停止减仓、爬到 60 恢复加仓资格），不构成逻辑矛盾。矛盾全部
出在入场侧的三处措辞/取值上，而它们各有独立的成因：

1. `继续改善` 是给"正在升温但还不够"的方向写的模板，从未适配"正在走坏"；
2. `first_tranche_scale` 的趋势刻度只在 `ready_to_start` 分支里夹，`forming` 压根不走那步，
   于是过热档（1 个过热标记 → 0.6）被原样发布；
3. 成形信号分与门禁不是同一套权重（趋势在信号分里只占 0.35，另有短期动量 0.20 + 资金加速
   0.20），所以趋势塌到 40 的方向仍能读出 70+ 分并被标成「信号偏强」。

三条都只影响叙述与展示，不影响 −50% 这个结论本身。
"""
from __future__ import annotations

import pytest

from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    EXIT_TREND_THRESHOLD,
    V3_EARLY_PROBE_FIRST_TRANCHE_CAP,
    V3_EARLY_PROBE_MIN_TREND,
    V3_GATE_THRESHOLDS,
    V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE,
    _entry_triggers_v3,
    _probability_band,
)

#: 生产实测值（2026-08-12 午间，012200 / 半导体材料）。
SEMI_TREND = 40.19
SEMI_SIGNAL_SCORE = 71.82


def _triggers(trend_strength: float, **overrides) -> list[str]:
    kwargs = {
        "entry_state": ENTRY_FORMING,
        "status": "insufficient",
        "evidence_quality": "complete",
        "trend_strength": trend_strength,
        "participation": 96.13,
        "position_risk": 71.75,
        "overheat_flags": [],
    }
    kwargs.update(overrides)
    return _entry_triggers_v3(**kwargs)


def test_broken_down_direction_is_not_told_it_keeps_improving() -> None:
    """跌破退出线时不再用预设"已经在改善"的措辞。"""
    lines = _triggers(SEMI_TREND)
    joined = "\n".join(lines)

    assert "继续改善" not in joined
    assert "止跌回升" in joined
    # 当前值与入场线要一起给出，否则用户无法判断"还差多少"。
    assert "40.2" in joined
    assert f"{V3_GATE_THRESHOLDS['trend']:g}" in joined


def test_direction_inside_the_hysteresis_band_keeps_the_original_wording() -> None:
    """在退出线之上、入场线之下（滞回带内）确实是"还需继续改善"，不该被改掉。"""
    inside_band = (EXIT_TREND_THRESHOLD + V3_GATE_THRESHOLDS["trend"]) / 2
    assert EXIT_TREND_THRESHOLD < inside_band < V3_GATE_THRESHOLDS["trend"]

    joined = "\n".join(_triggers(inside_band))

    assert "20日相对强度与趋势持续性继续改善" in joined
    assert "止跌回升" not in joined


def test_signal_band_stops_endorsing_a_broken_down_direction() -> None:
    """信号分照旧输出，但档位不再是「信号偏强」这类强弱标签。"""
    assert _probability_band(SEMI_SIGNAL_SCORE, trend_strength=SEMI_TREND) == "trend_breakdown"
    # 同一个分数在趋势健康时仍是原档位——封顶只对破位生效，不是把分数打低。
    assert _probability_band(SEMI_SIGNAL_SCORE, trend_strength=65.0) == "building"
    # 不传趋势时保持旧行为（荐基等调用方的向后兼容）。
    assert _probability_band(SEMI_SIGNAL_SCORE) == "building"


@pytest.mark.parametrize("trend", [0.0, 20.0, EXIT_TREND_THRESHOLD - 0.01])
def test_signal_band_breakdown_ignores_how_high_the_score_is(trend: float) -> None:
    """哪怕信号分满分，只要趋势破位就不给强弱标签——这正是事故里的组合。"""
    assert _probability_band(99.0, trend_strength=trend) == "trend_breakdown"


# --------------------------------------------------------------------------
# first_tranche_scale：只有确有入场通道时才给比例
# --------------------------------------------------------------------------


def _maturity_row_for_semi(**mainline_overrides) -> dict:
    """用生产实测输入重建半导体材料那一行（2026-08-12 午间，012200）。

    刻意直接驱动 `_entry_maturity_v3` 而不是走 `describe_sector_opportunity`：后者需要一整份
    主线快照才能让 `evidence_quality` 达到 `complete`，喂不全时会落到"证据不足"的兜底分支
    （`trend_strength = clamp(35 + 5日涨跌×1.5, 0, 45)`），趋势分变成 45.0 的占位值，
    本用例要复现的 40.19 就被冲掉了——第一版就是这样写错的。
    """
    from app.services.sector_opportunity_scoring import _entry_maturity_v3

    component_scores = {
        # 趋势轴两个分量都给 40.19，加权后仍是 40.19。
        "relative_strength": SEMI_TREND,
        "trend_persistence": SEMI_TREND,
        "fund_flow": 96.13,
        "breadth": 96.13,
        "market_structure": 71.75,
    }
    mainline = {
        "sector_label": "半导体材料",
        # 生产上这张卡片的主线徽章是「尚未形成主线」；只要不在
        # {forming, confirmed, crowded} 里就会触发「主线状态升至…」这条等待条件。
        # 不能用 "insufficient"——那会把 evidence_quality 打成 insufficient 并触发兜底分支。
        "status": "neutral",
        "feature_coverage": 1.0,
        "component_scores": component_scores,
        "features": {
            # 过热标记来自主线自己的 5 日收益，与热度榜的 change_5d 是两条序列；
            # 生产上正是它 ≥12% 才命中「近5日涨幅超过12%，短期加速」这**一个**标记。
            "return_5d_percent": 12.5,
            "distance_from_20d_high_percent": -6.0,
            "distance_from_ma20_percent": 3.0,
            "cumulative_20d_net_yi": 30.0,
        },
    }
    mainline.update(mainline_overrides)

    row = _entry_maturity_v3(
        label="半导体材料",
        track="momentum",
        legacy_score=74.3,
        change_1d=2.71,
        change_5d=8.57,
        today_flow=5.11,
        flow_5d=19.19,
        pattern="momentum_inflow",
        date_aligned=True,
        mainline=mainline,
    )
    return row


def test_forming_direction_without_any_channel_publishes_no_tranche_scale() -> None:
    """事故本体：forming + 趋势远低于入场线 + 1 个过热标记，此前发布 0.6。

    发布 `None` 而不是 0.0 是刻意的——本仓消费方一律把 0 读成"没有可用值"：
    `recommendation_guard._first_tranche_scaled_percent` 对 `scale <= 0` 返回**未缩放**的
    比例，`discovery_allocation_service` 对 `not 0 < scale <= 1` 换成 0.4。发 0 会在这两处
    被静默当成缺数据。
    """
    from app.services.sector_opportunity_scoring import V3_FIRST_TRANCHE_SCALE

    # 先锁住"过热档本身仍是 0.6"，证明 None 不是因为过热档变了。
    assert V3_FIRST_TRANCHE_SCALE[1] == 0.6

    row = _maturity_row_for_semi()

    assert row["entry_state"] == ENTRY_FORMING
    assert row["trend_strength_score"] == pytest.approx(SEMI_TREND, abs=0.5)
    assert row["overheat_flags"], "本用例要求命中过热标记，否则证不到 0.6 那条路"
    assert row["first_tranche_scale"] is None
    assert row["probability_early_probe_eligible"] is False


def test_zero_is_never_published_as_a_tranche_scale() -> None:
    """0 与 None 在本仓语义不同；这一层只允许出 None 或正数。"""
    row = _maturity_row_for_semi()
    scale = row["first_tranche_scale"]

    assert scale is None or scale > 0


def test_the_broken_down_row_is_internally_coherent_end_to_end() -> None:
    """这一行的三处叙述必须同时不再替方向背书（事故的完整复现）。"""
    row = _maturity_row_for_semi()

    assert row["evidence_quality"] == "complete", "兜底分支会把趋势分改成占位值，复现即失效"
    assert row["formation_probability_band"] == "trend_breakdown"
    assert row["first_tranche_scale"] is None
    joined = "\n".join(row["entry_triggers"])
    assert "继续改善" not in joined
    # 信号分本身照旧输出，不做隐藏。
    assert row["trend_formation_probability"] > 60


def test_ready_to_start_still_gets_a_positive_tranche_scale() -> None:
    """别把修复做成"谁都不给比例"——成熟方向必须照旧拿到正数。"""
    from app.services.sector_opportunity_scoring import _trend_tranche_scale

    # 趋势 85 → 满档 1.0；过热档不命中时首仓应为正数。
    assert _trend_tranche_scale(85.0) > 0


def test_early_probe_channel_keeps_its_cap_and_is_not_zeroed() -> None:
    """提前试仓通道的门槛（52）低于趋势刻度表起点（60），不能在那条分支再夹趋势刻度。

    否则 `trend_scale` 恒为 0，整条通道会被归零——这是本次重构最容易踩的回归。
    """
    from app.services.sector_opportunity_scoring import _trend_tranche_scale

    assert V3_EARLY_PROBE_MIN_TREND < V3_GATE_THRESHOLDS["trend"]
    assert _trend_tranche_scale(V3_EARLY_PROBE_MIN_TREND) == 0
    assert V3_EARLY_PROBE_FIRST_TRANCHE_CAP > 0
    assert V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE > 0


def test_the_three_entry_channels_are_mutually_exclusive_by_entry_state() -> None:
    """if/elif 改写的前提：三条通道对 entry_state 的要求互斥。

    这个前提一旦被破坏（例如某条通道放宽到接受多个 entry_state），if/elif 就不再等价于
    原来的三个独立 if，必须回来重新推导。
    """
    assert ENTRY_READY_TO_START != ENTRY_READY_ON_PULLBACK != ENTRY_FORMING
