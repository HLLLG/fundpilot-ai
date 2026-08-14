"""日报加仓档位改挂荐基已标定的方向合成分。

回归背景：`_ADD_POSITION_PERCENT_TIERS` 的 85/70/50 是手写阈值，且作用在 `research_score`
上。没有主线快照时 `research_score == legacy_score`，那是一个不封顶的动量加权和
（`max(change_1d,0)*5 + max(change_5d,0)*4 + 资金 + 热度*0.15`），只在读取时才 clamp 到
100——一个当日 +4%、五日 +10% 的板块轻松越过 85 拿满档 20%。而荐基 V3 恰恰因为价格结构
实测 Rank IC 为 -0.011 / -0.053 把整块**删掉**了。换句话说日报在用一个荐基已证伪的先验
决定仓位大小。

这里锁五条契约：
1. 有 V3 方向成熟度层时，档位由 `direction_score`（按实测 IC 定权的三块合成）决定；
2. 档位阈值从标定入场线派生——荐基重新标定门槛时日报自动跟随，不能再各写一套；
3. 两把尺子量纲不同，不得互相套用（旧分 85 ≠ 合成分 85 的含金量）；
4. 旧阶梯降级为兜底，且文案要标明；
5. 「只降不升」纪律不变：基金证据 / 载体质量 / 分段试仓系数仍从新档位往下调。
"""
from __future__ import annotations

import pytest

from app.models import AnalysisRequest, Holding, InvestorProfile
from app.services.recommendation_guard import (
    _ADD_TIER_PERCENTS,
    _V3_ADD_TIER_TOP_SCORE,
    _resolve_deterministic_position_change,
    _resolve_sector_add_tier,
    _v3_add_tier_thresholds,
    _v3_direction_score,
    _v3_gate_direction_score,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION_V3,
    V3_BLOCK_WEIGHTS,
    V3_GATE_THRESHOLDS,
)


def _v3_row(direction_score: float, **overrides) -> dict:
    row = {
        "sector_label": "半导体",
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "direction_score": direction_score,
        "entry_state": "ready_to_start",
        "opportunity_available": True,
        "confidence": "高",
        # 1.0 = 方向层授权满额投入、不做缩放，把「分段试仓系数」这条独立维度隔离出去
        # （契约 5 由 `test_first_tranche_scale_still_shrinks_the_v3_tier` 单独锁）。
        #
        # 这个键不能省：`describe_sector_opportunity` 对每个 V3 行都会写它，`None` 的语义
        # 是"本轮没有任何入场通道授权投入"，`_first_tranche_scaled_percent` 因此对 V3 行
        # fail-closed（缺席即不授权加仓）。省掉它会让本文件测的是一条生产中不存在的行。
        "first_tranche_scale": 1.0,
    }
    row.update(overrides)
    return row


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
        ),
    )


def _strong_fund_evidence() -> dict:
    """基金侧给满，把「基金证据降一级」这条独立维度隔离出去。"""
    return {"composite": {"level": "高", "score": 3.0}}


def _usable_medium_fund_evidence() -> dict:
    """「证据可用但偏弱」才降一档；`reliability.usable` 是 `_fund_evidence_is_usable` 的判据。

    只写 `composite.level=中` 已不足以触发降档——那种数据在新口径下构造不出来（中档只能
    由一条可靠性放行的分量产生），而"证据不可用"按本仓原则不算基金更弱。
    """
    return {
        "composite": {"level": "中", "score": 2.0},
        "components": [
            {
                "source": "factor",
                "role": "return_signal",
                "level": "中",
                "direction": "positive",
                "reliability": {"level": "中", "scope": "peer_group", "usable": True},
            }
        ],
    }


def _percent(sector_opportunity: dict | None, **kwargs) -> tuple[float | None, str]:
    request = _request()
    percent, basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity=sector_opportunity,
        evidence=kwargs.pop("evidence", _strong_fund_evidence()),
        **kwargs,
    )
    return percent, basis


# --- 契约 2：阈值必须从标定门槛派生 -----------------------------------------


def test_gate_score_is_the_calibrated_gate_run_through_the_same_weights() -> None:
    """可加仓的下边界 = 三块恰好卡在标定入场线时的合成分，不是另写一个数字。"""
    expected = (
        V3_GATE_THRESHOLDS["trend"] * V3_BLOCK_WEIGHTS["trend_strength"]
        + V3_GATE_THRESHOLDS["participation"] * V3_BLOCK_WEIGHTS["participation"]
        + V3_GATE_THRESHOLDS["position"] * V3_BLOCK_WEIGHTS["position_risk"]
    )
    assert _v3_gate_direction_score() == pytest.approx(expected)


def test_tier_thresholds_are_an_equal_split_of_gate_to_top() -> None:
    gate = _v3_gate_direction_score()
    span = _V3_ADD_TIER_TOP_SCORE - gate
    rungs = len(_ADD_TIER_PERCENTS)
    thresholds = _v3_add_tier_thresholds()

    assert len(thresholds) == rungs
    assert thresholds[-1] == float("-inf")
    for index, threshold in enumerate(thresholds[:-1]):
        assert threshold == pytest.approx(gate + span * (rungs - 1 - index) / rungs)
    # 单调递减，否则 zip 匹配会把高档给到低分。
    assert list(thresholds) == sorted(thresholds, reverse=True)


def test_top_anchor_is_reachable_and_bottom_anchor_is_the_gate() -> None:
    """上锚点必须取得到（否则满档形同废除），下锚点必须正好是入场线。"""
    gate = _v3_gate_direction_score()

    assert _resolve_sector_add_tier(_v3_row(_V3_ADD_TIER_TOP_SCORE))[0] == max(
        _ADD_TIER_PERCENTS
    )
    assert _resolve_sector_add_tier(_v3_row(gate))[0] == min(_ADD_TIER_PERCENTS)


def test_tier_follows_a_recalibrated_gate(monkeypatch) -> None:
    """荐基重新标定门槛后，日报档位必须自动跟随——这是"不能再各写一套"的实质检验。"""
    gate_before = _v3_gate_direction_score()
    before = _v3_add_tier_thresholds()

    monkeypatch.setitem(V3_GATE_THRESHOLDS, "trend", V3_GATE_THRESHOLDS["trend"] + 10.0)

    gate_after = _v3_gate_direction_score()
    after = _v3_add_tier_thresholds()

    # 门槛抬高 → 下边界抬高 → 每一档的下界都跟着抬高。
    assert gate_after > gate_before
    assert all(
        later > earlier
        for later, earlier in zip(after[:-1], before[:-1])
    )


# --- 契约 1/3：用哪把尺子 ----------------------------------------------------


@pytest.mark.parametrize(
    ("direction_score", "expected_percent"),
    [
        (51.0, 5.0),
        (59.4, 5.0),
        (59.5, 10.0),
        (68.0, 15.0),
        (76.5, 20.0),
        (99.0, 20.0),
    ],
)
def test_v3_direction_score_drives_the_tier(
    direction_score: float, expected_percent: float
) -> None:
    percent, basis = _percent(_v3_row(direction_score))

    assert percent == expected_percent
    assert "方向合成分" in basis
    assert "实测 IC 定权" in basis
    assert "旧口径兜底" not in basis


def test_the_two_scales_are_not_interchangeable() -> None:
    """旧分 85 拿满档，合成分 85 才是真的强——同一个数字在两把尺子上含义不同。

    这条用例锁的是回归方向：一个动量拉满、但三块合成只有中位的板块，不能再拿满档。
    """
    legacy_percent, legacy_basis = _percent({"score": 85.0})
    assert legacy_percent == 20.0
    assert "旧口径兜底" in legacy_basis

    # 同一个 85 出现在 V3 行的旧机会分字段上，但合成分只有 60：必须按合成分给档。
    v3_percent, v3_basis = _percent(_v3_row(60.0, score=85.0, research_score=85.0))
    assert v3_percent == 10.0
    assert "方向合成分 60" in v3_basis


def test_chasing_momentum_no_longer_buys_the_top_tier() -> None:
    """当日 +4%、五日 +10% 的板块在旧口径下轻松拿满档；接上 V3 后由合成分说话。"""
    from app.services.sector_opportunity_scoring import describe_sector_opportunity

    hot_heat = {
        "sector_label": "半导体",
        "change_1d_percent": 4.0,
        "change_5d_percent": 10.0,
        "heat_score": 90.0,
    }
    hot_flow = {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": True,
        "today_main_force_net_yi": 8.0,
        "cumulative_5d_net_yi": 20.0,
    }
    legacy_row = describe_sector_opportunity(hot_heat, hot_flow)
    assert legacy_row is not None
    # 旧分确实被动量推到很高（这就是问题所在）。
    assert (legacy_row.get("research_score") or 0) >= 70.0

    # 接上 V3 层后，同一个热板块若三块合成只到中位，档位就不是满档。
    percent, basis = _percent(
        _v3_row(64.0, score=legacy_row["score"], research_score=legacy_row["research_score"])
    )
    assert percent == 10.0
    assert "方向合成分 64" in basis


# --- 契约 4：旧阶梯只作兜底 --------------------------------------------------


def test_legacy_ladder_is_used_only_without_the_v3_layer() -> None:
    assert _v3_direction_score({"direction_score": 80.0}) is None, (
        "没有 V3 版本号的行不得被当成标定合成分——两套量纲混用比用错阈值更糟"
    )
    assert _v3_direction_score(_v3_row(80.0)) == 80.0
    assert _v3_direction_score(None) is None
    assert _v3_direction_score({"score_policy_version": ENTRY_POLICY_VERSION_V3}) is None


def test_missing_sector_evidence_still_falls_back_to_the_probe_rung() -> None:
    percent, basis = _percent(None)
    assert percent == min(_ADD_TIER_PERCENTS)
    assert "板块机会分暂缺" in basis


def test_v3_row_without_a_finite_direction_score_falls_back_to_legacy() -> None:
    percent, basis = _percent(_v3_row(float("nan"), score=85.0))
    assert percent == 20.0
    assert "旧口径兜底" in basis


# --- 契约 5：只降不升的纪律不变 ----------------------------------------------


def test_fund_evidence_still_steps_the_v3_tier_down() -> None:
    full, _ = _percent(_v3_row(76.5))
    stepped, basis = _percent(
        _v3_row(76.5), evidence=_usable_medium_fund_evidence()
    )

    assert full == 20.0
    assert stepped == 15.0
    assert "档位下调至 15%" in basis


def test_first_tranche_scale_still_shrinks_the_v3_tier() -> None:
    scaled, basis = _percent(
        _v3_row(76.5, first_tranche_scale=0.4, overheat_flags=["单日涨幅过热"])
    )

    assert scaled == 8.0
    assert "方向分段试仓系数 40%" in basis


def test_vehicle_quality_still_steps_the_v3_tier_down() -> None:
    stepped, basis = _percent(
        _v3_row(76.5),
        vehicle_quality={
            "applicable": True,
            "status": "watch_only",
            "penalties": ["规模偏小"],
        },
    )

    assert stepped == 15.0
    assert "被动载体质量未达标" in basis


def test_v3_tier_is_never_raised_by_strong_fund_evidence() -> None:
    """量化证据只能增加置信度，不得提额——最低档不会因证据强而上调。"""
    percent, _ = _percent(_v3_row(_v3_gate_direction_score()))
    assert percent == min(_ADD_TIER_PERCENTS)


# --- 同一把尺子：持有一个板块不该换来分数加成 --------------------------------


def test_held_sectors_no_longer_get_the_pinned_focus_bonus(monkeypatch) -> None:
    """日报给持仓板块打的分必须与荐基给同一板块打的分一致。

    `focus` 的唯一作用是 +6 的 `focus_bonus`，语义是"用户点名要看的方向"。日报曾对每个
    持仓板块都传 `focus={label}`，于是同一个板块在日报里天然比在荐基里高 6 分——而这 6 分
    会经 `research_score` 直接落到旧口径兜底档位上。持有不是看多的证据。
    """
    from app.services import report_sector_opportunity as sector_ctx
    from app.services.sector_opportunity_scoring import describe_sector_opportunity

    heat = {
        "sector_label": "半导体",
        "change_1d_percent": 1.4,
        "change_5d_percent": 5.2,
        "heat_score": 62.0,
    }

    monkeypatch.setattr(
        sector_ctx, "_load_direction_state_history", lambda _previous: (None, "no_history")
    )
    context = sector_ctx.build_holding_sector_opportunity_context(
        [
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            )
        ],
        trade_date="2026-06-11",
        fetch_sector_heat=lambda: [heat],
        fetch_sector_position=lambda _labels, _date: {},
        mainline_by_label={},
        mainline_meta={"available": False, "reason": "test"},
    )

    held = context["held"]["半导体"]
    # 荐基对一个**未被点名**的板块会这样打分（flow 在离线测试下取不到，两边一致）。
    neutral = describe_sector_opportunity(heat, None)
    assert neutral is not None
    assert held["score"] == neutral["score"]
    assert held["research_score"] == neutral["research_score"]

    pinned = describe_sector_opportunity(heat, None, focus={"半导体"})
    assert pinned is not None
    assert pinned["score"] > held["score"], "焦点加分本身应仍然存在，只是不该给持仓白拿"
