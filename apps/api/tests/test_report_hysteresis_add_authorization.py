"""滞回保留的方向档位不得自动换来加仓资格（2026-08-13 线上缺陷回归）。

## 缺陷现场

2026-08-13 的日报把「万家宏观择时多策略混合C」(017787，煤炭) 从模型草案「观察」抬成
「分批加仓 +10%」，而同一张卡片上：

* 「本轮比例」写着 **本轮不投入**（`first_tranche_scale = None`）；
* 「量化证据质量」写着 正向支持 **不足** / 可靠性 **低** / 方向 **中性**；
* `points` 自己写着「参与度仅 28.9、今日主力 -0.46亿」，`risks` 写着「资金参与度偏弱」。

两处口径漂移叠加造成的：

1. **滞回抬了 `entry_state`，加仓门禁只读它。** `apply_direction_state_hysteresis` 在
   "昨天已 ready 且趋势未跌破退出线"时把 `entry_state` 抬回 `ready_to_start`，它的保留条件
   **只校验趋势**（`EXIT_TREND_THRESHOLD = 入场线 - 8`）。煤炭的趋势 67.28 本就在入场线 60
   之上、压根不在滞回带内，真正没过的是参与度 28.93 < 35；而 `_entry_state_add_block_reason`
   此前只读 `entry_state`，参与度与价格位置在滞回之后再没有任何地方复查。
2. **`first_tranche_scale=None` 被当成"没有缩放系数"。** `describe_sector_opportunity` 在
   "没有任何入场通道授权投入"时刻意发 `None` 而不是 `0.0`，理由写在
   `sector_opportunity_scoring.py`：「两处都另有 entry_state 门禁在前，本身不会走到」。
   滞回让这个前提失效，于是 `_first_tranche_scaled_percent` 原样返回**满档**比例。

当天 5 只持仓有 4 只处于同样的滞回态、3 只带着 `first_tranche_scale=None`；另外那几只只是
恰好被"板块资金流偏弱"或"涨后回吐"这些**无关**的门禁挡住——只有一只出错是巧合，不是门禁
在起作用。本文件因此用五只持仓的真实数值逐条锁住修复后的行为。

## 滞回态给多少（用户决策，2026-08-13）

第一版把这种情况完全封死。用户随后选择"先给一个很小的试探仓位"，因此改为**最低档小额
试探**，比例由两个**既有**常量复合，不引入新数字：

* 档位 = `_ADD_TIER_PERCENTS[-1]`（5%，本仓命名为「小机会试探档」）；
* 系数 = 0.4（`V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE` == `V3_EARLY_PROBE_FIRST_TRANCHE_CAP`
  == 趋势刻度地板，本仓所有"已授权试仓通道"共用这个数）。

→ **2%**。档位必须**另外封顶**而不能只乘系数：加仓档位由 `direction_score` 决定，它给趋势
的权重是 0.70，而滞回态这批行的共同特征恰恰是"趋势强、参与度弱"，`direction_score` 因此
天然偏高（黄金 78.92 → 强机会档 20%，而它的参与度是 **0.0**）。只乘系数的话，参与度最差
的那只反而拿到最大的试探仓位。

## 锁住的契约

1. 滞回态下**非趋势**门禁未过 → 只给最低档小额试探 2%，依据里说清哪一项没过；排序必须是
   滞回态试探 2% < 已标定试仓通道 4% < 当日三块全过 8%；
2. 非趋势门禁未过**且**趋势也回落到入场线之下（两根轴一起坏）→ 连试探也不给；
3. 滞回态下唯一未过项是趋势、且趋势落在滞回带内 → 正常加仓资格（这是滞回被造出来的场景，
   与 `test_report_direction_hysteresis` 契约 3 一致）；
4. 提前试仓通道（资金转强 / 成形信号分）在滞回态下照样有效，且**已授权比例的行不得被试探
   档降级**——更弱的判据不能覆盖更强的判据；
5. 当日真正三块全过的方向不受影响；
6. V3 行的 `first_tranche_scale` 缺席/非正 → 不授权加仓（fail-closed，试探通道是它唯一的
   例外，且自带系数）；旧口径行不受影响；
7. 滞回行自身不得自相矛盾（提示语与试仓系数一致、试仓通道不因档位被抬走而失活）。
"""
from __future__ import annotations

import pytest

from app.models import AnalysisRequest, Holding, InvestorProfile
from app.services.recommendation_guard import (
    _ADD_TIER_PERCENTS,
    _HYSTERESIS_PROBE_TRANCHE_SCALE,
    _entry_state_add_block_reason,
    _first_tranche_scaled_percent,
    _hysteresis_probe_eligible,
    _resolve_deterministic_position_change,
    _weak_evidence_reasons,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION_V3,
    EXIT_TREND_THRESHOLD,
    V3_GATE_THRESHOLDS,
)

# --- 线上五只持仓的真实方向行（2026-08-13，报告 e68dce2c…） -------------------
#
# 只保留与本文件契约相关的键。`qualifies_for_ready=False` + `raw_entry_state != entry_state`
# 就是滞回保留的签名，由 `apply_direction_state_hysteresis` 写入。


_BASE_ROW: dict = {
    "score_policy_version": ENTRY_POLICY_VERSION_V3,
    "entry_state": "ready_to_start",
    "raw_entry_state": "ready_on_pullback",
    "qualifies_for_ready": False,
    "opportunity_available": True,
    "confidence": "中",
    "pattern_label": "neutral",
    "flow_improving_probe_eligible": False,
    "probability_early_probe_eligible": False,
    "first_tranche_scale": None,
}


def _row(fields: dict, overrides: dict) -> dict:
    return {**_BASE_ROW, **fields, **overrides}


def _coal_row(**overrides) -> dict:
    """017787 煤炭：趋势在入场线之上，参与度 28.93 < 35，滞回保留，本轮不投入。"""
    return _row(
        {
            "sector_label": "煤炭",
            "direction_score": 64.12,
            "trend_strength_score": 67.28,
            "participation_score": 28.93,
            "position_risk_score": 84.6,
        },
        overrides,
    )


def _gold_row(**overrides) -> dict:
    """002610 黄金：趋势 93.94 很强，参与度 0.0——最极端的一只。"""
    return _row(
        {
            "sector_label": "黄金",
            "direction_score": 78.92,
            "trend_strength_score": 93.94,
            "participation_score": 0.0,
            "position_risk_score": 87.73,
            "pattern_label": "weak_outflow",
        },
        overrides,
    )


def _rare_earth_row(**overrides) -> dict:
    """011036 稀土：参与度 12.78。"""
    return _row(
        {
            "sector_label": "稀土",
            "direction_score": 71.32,
            "trend_strength_score": 81.81,
            "participation_score": 12.78,
            "position_risk_score": 80.95,
            "pattern_label": "weak_outflow",
        },
        overrides,
    )


def _digital_economy_row(**overrides) -> dict:
    """015788 数字经济：参与度 29.24 同样未过，但今日资金转强，走标定的提前试仓通道。"""
    return _row(
        {
            "sector_label": "数字经济",
            "direction_score": 67.28,
            "trend_strength_score": 71.8,
            "participation_score": 29.24,
            "position_risk_score": 84.22,
            "pattern_label": "multi_day_outflow_then_inflow",
            "flow_improving_probe_eligible": True,
            "first_tranche_scale": 0.4,
        },
        overrides,
    )


def _healthcare_row(**overrides) -> dict:
    """011373 医疗：当日三块全过、`qualifies_for_ready=True`，不是滞回态。"""
    return _row(
        {
            "sector_label": "医疗",
            "raw_entry_state": "ready_to_start",
            "qualifies_for_ready": True,
            "direction_score": 91.05,
            "trend_strength_score": 93.04,
            "participation_score": 77.83,
            "position_risk_score": 95.0,
            "pattern_label": "price_flow_aligned_up",
            "first_tranche_scale": 0.4,
        },
        overrides,
    )


def _unusable_fund_evidence() -> dict:
    """线上那份基金侧证据：三路全部 `reliability.usable=false`、composite 为「不足」。"""
    return {
        "composite": {"level": "不足", "direction": "neutral"},
        "components": [
            {
                "source": "factor",
                "role": "return_signal",
                "level": "不足",
                "direction": "unknown",
                "reliability": {"level": "低", "scope": "peer_group", "usable": False},
            }
        ],
    }


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="017787",
                fund_name="万家宏观择时多策略混合C",
                sector_name="煤炭",
                holding_amount=1036.6,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
        ),
    )


def _percent(sector_opportunity: dict | None) -> float | None:
    request = _request()
    percent, _basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity=sector_opportunity,
        evidence=_unusable_fund_evidence(),
    )
    return percent


# --- 契约 1：滞回态下非趋势门禁未过 → 只给最低档小额试探 ---------------------
#
# 用户决策（2026-08-13）：这种情况不完全封死，给一次很小的试探。比例由两个**既有**常量
# 复合而成，不引入新数字：档位取既有阶梯最低档 `_ADD_TIER_PERCENTS[-1]`（本仓命名为
# 「小机会试探档」），系数取 0.4（本仓所有"已授权试仓通道"共用的那个数）。
#
# 档位必须**另外封顶**、不能只乘系数：加仓档位由 `direction_score` 决定，而它给趋势的权重
# 是 0.70，滞回态这批行恰恰"趋势强、参与度弱"，`direction_score` 因此天然偏高（黄金 78.92
# → 强机会档 20%，而它的参与度是 0.0）。只乘系数的话参与度最差的那只反而拿到最大试探仓位。


_EXPECTED_PROBE_PERCENT = 2.0  # = _ADD_TIER_PERCENTS[-1] (5%) × 0.4


def test_the_probe_size_is_composed_of_existing_constants_only() -> None:
    """2% 不是新引入的数字，而是两个既有常量的乘积——这条锁住"没有魔法数"这个前提。

    哪天有人改动其中任何一个，这里先响，而不是让线上仓位悄悄变大。
    """
    from app.services.sector_opportunity_scoring import (
        V3_EARLY_PROBE_FIRST_TRANCHE_CAP,
        V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE,
        V3_TREND_TRANCHE_SCALES,
    )

    # 0.4 是本仓所有"已授权试仓通道"共用的系数，也是趋势刻度的地板（刚过入场线那一档）。
    assert _HYSTERESIS_PROBE_TRANCHE_SCALE == V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE
    assert V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE == V3_EARLY_PROBE_FIRST_TRANCHE_CAP
    assert min(scale for _threshold, scale in V3_TREND_TRANCHE_SCALES) == 0.4
    # 档位取既有阶梯最低档（「小机会试探档」）。
    assert _ADD_TIER_PERCENTS[-1] == min(_ADD_TIER_PERCENTS)
    assert _EXPECTED_PROBE_PERCENT == pytest.approx(
        _ADD_TIER_PERCENTS[-1] * _HYSTERESIS_PROBE_TRANCHE_SCALE
    )


@pytest.mark.parametrize(
    ("row_factory", "expected_gate", "tier_without_the_cap"),
    [
        (_coal_row, "资金参与度 28.9<35", 10.0),
        (_gold_row, "资金参与度 0.0<35", 20.0),
        (_rare_earth_row, "资金参与度 12.8<35", 15.0),
    ],
    ids=["coal", "gold", "rare_earth"],
)
def test_hysteresis_hold_gets_only_the_smallest_probe(
    row_factory, expected_gate: str, tier_without_the_cap: float
) -> None:
    row = row_factory()
    # 前提：这确实是"滞回抬起来的 ready"，趋势并不在滞回带内（在入场线之上）。
    assert row["entry_state"] == "ready_to_start"
    assert row["qualifies_for_ready"] is False
    assert row["trend_strength_score"] >= V3_GATE_THRESHOLDS["trend"]
    assert _hysteresis_probe_eligible(row) is True

    percent, basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=_request().holdings[0],
        profile=_request().profile,
        weight_denominator=100_000,
        sector_opportunity=row,
        evidence=_unusable_fund_evidence(),
    )

    assert percent == pytest.approx(_EXPECTED_PROBE_PERCENT)
    assert percent < tier_without_the_cap, (
        "档位必须被封到最低档；否则 direction_score 会让参与度最差的那只拿到最大仓位"
    )
    # 依据要说清"哪一项没过"与"为什么只给这么小"，否则用户看不懂 2% 从哪来。
    assert expected_gate in basis
    assert "小额试探" in basis
    # 措辞不得把原因说成"位置不宜追高"——价格位置这三只都远超门槛。
    assert "价格位置" not in basis


def test_the_probe_is_strictly_smaller_than_every_calibrated_channel() -> None:
    """排序必须是：滞回态试探 < 已标定试仓通道 < 当日三块全过。

    这条是整个取值选择的正当性所在。若试探比标定通道还大，等于用更弱的判据换更大的仓位。
    """
    probe = _percent(_coal_row())
    calibrated_channel = _percent(_digital_economy_row())
    fully_qualified = _percent(_healthcare_row())

    assert probe is not None and calibrated_channel is not None
    assert fully_qualified is not None
    assert probe < calibrated_channel < fully_qualified
    assert (probe, calibrated_channel, fully_qualified) == pytest.approx((2.0, 4.0, 8.0))


def test_the_probe_never_reaches_the_shipped_ten_percent() -> None:
    """线上那条 +10% 必须不可能再出现（煤炭持仓 ¥1036.6，10% ≈ ¥104、2% ≈ ¥21）。"""
    assert _percent(_coal_row()) == pytest.approx(_EXPECTED_PROBE_PERCENT)
    assert _percent(_coal_row()) != pytest.approx(10.0)


def test_a_row_with_an_authorized_scale_does_not_fall_into_the_probe() -> None:
    """已有标定通道授权比例的行不得走试探档——否则更弱的判据会覆盖更强的判据。

    2026-08-13 的数字经济同时满足"滞回保留 + 参与度未过 + 趋势在入场线上"，但它的
    `flow_improving_probe_eligible=true` 已经授权了 0.4；若让试探档抢先封顶，它会从应得的
    4% 被降到 2%。
    """
    row = _digital_economy_row()
    assert row["first_tranche_scale"] == 0.4

    assert _hysteresis_probe_eligible(row) is False
    assert _percent(row) == pytest.approx(4.0)


def test_the_probe_does_not_survive_a_weak_sector_flow() -> None:
    """这道口子比看起来窄：板块侧的资金流检查仍在它之后生效。

    黄金与稀土 `pattern_label=weak_outflow`，`_weak_evidence_reasons` 照旧返回非空，
    确定性提议因此不会开加仓——线上当天最终只有资金形态中性的煤炭能拿到那 2%。
    """
    for row in (_gold_row(), _rare_earth_row()):
        assert _hysteresis_probe_eligible(row) is True, "比例层够格"
        reasons = _weak_evidence_reasons(row, _unusable_fund_evidence(), None)
        assert any("板块资金流偏弱" in item for item in reasons), "但动作层仍被拦住"

    assert _weak_evidence_reasons(_coal_row(), _unusable_fund_evidence(), None) == []


# --- 契约 2：趋势抖动仍由滞回带兜住 ------------------------------------------


def test_trend_only_dip_inside_the_exit_band_keeps_add_open() -> None:
    """唯一未过项是趋势、且落在 [退出线, 入场线) 内：这正是滞回被造出来的场景。

    与 `test_report_direction_hysteresis` 契约 3 是同一条，只是这里直接给分数、
    不走整条 context 构建，便于把"带内"这个条件单独钉住。
    """
    trend = (EXIT_TREND_THRESHOLD + V3_GATE_THRESHOLDS["trend"]) / 2
    row = _coal_row(
        raw_entry_state="forming",
        trend_strength_score=trend,
        participation_score=66.0,
        position_risk_score=58.0,
        first_tranche_scale=0.4,
    )
    assert EXIT_TREND_THRESHOLD <= trend < V3_GATE_THRESHOLDS["trend"]

    assert _entry_state_add_block_reason(row) is None
    assert _weak_evidence_reasons(row, _unusable_fund_evidence(), None) == []


def test_trend_dip_below_the_exit_band_is_not_covered() -> None:
    """跌破退出线就不是"抖动"了。滞回本身也不会保留这种行，这里是纵深防御。"""
    row = _coal_row(
        raw_entry_state="forming",
        trend_strength_score=EXIT_TREND_THRESHOLD - 1,
        participation_score=66.0,
        position_risk_score=58.0,
        first_tranche_scale=0.4,
    )

    assert _entry_state_add_block_reason(row) is not None


def test_two_axes_failing_together_gets_no_probe_at_all() -> None:
    """非趋势门禁未过**且**趋势也回落到入场线以下：两根轴一起坏了，连试探也不给。

    这是试探通道的下边界。滞回只保证趋势 ≥ 退出线 52，它撑不起"我们真正信的那根轴还在
    过线"这个前提，所以此时回到完全封死。
    """
    row = _coal_row(
        trend_strength_score=(EXIT_TREND_THRESHOLD + V3_GATE_THRESHOLDS["trend"]) / 2,
        participation_score=28.93,
    )
    assert row["trend_strength_score"] < V3_GATE_THRESHOLDS["trend"]

    assert _hysteresis_probe_eligible(row) is False
    reason = _entry_state_add_block_reason(row)
    assert reason is not None
    assert "趋势已回落到入场线之下" in reason
    assert _percent(row) is None


# --- 契约 3：提前试仓通道在滞回态下照样有效 ----------------------------------


def test_flow_improving_probe_still_opens_add_in_the_hysteresis_band() -> None:
    """参与度略低于门槛本来就有标定通道；修复不得把它一并关掉。

    数字经济 2026-08-13 参与度 29.24 同样未过门槛 35，但今日资金转强、命中
    `flow_improving_probe_eligible`，应按 40% 试仓系数拿到小额加仓，而不是被一并拦下。
    """
    row = _digital_economy_row()

    assert _entry_state_add_block_reason(row) is None
    assert _weak_evidence_reasons(row, _unusable_fund_evidence(), None) == []
    # 10% 的中等机会档乘 0.4 = 4%
    assert _percent(row) == pytest.approx(4.0)


def test_probability_early_probe_does_not_open_daily_add_in_the_hysteresis_band() -> None:
    row = _coal_row(probability_early_probe_eligible=True, first_tranche_scale=0.4)

    reason = _entry_state_add_block_reason(row)
    assert reason is not None


# --- 契约 4：当日真正达标的方向不受影响 --------------------------------------


def test_a_genuinely_qualifying_direction_is_untouched() -> None:
    """医疗当日三块全过，`qualifies_for_ready=True`——不是滞回态，行为必须与修复前一致。

    这条同时钉住修复的方向性：当天唯一真正通过入场门禁的方向拿到 8%，而未通过的煤炭
    拿到 None。修复前恰好相反（煤炭 10%、医疗因过热与回吐不加）。
    """
    row = _healthcare_row()

    assert _entry_state_add_block_reason(row) is None
    # 20% 的强机会档乘 0.4 = 8%
    assert _percent(row) == pytest.approx(8.0)


# --- 契约 5：试仓系数缺席即不授权（fail-closed） -----------------------------


def test_v3_row_without_a_tranche_scale_withholds_the_add() -> None:
    scaled, basis = _first_tranche_scaled_percent(10.0, _coal_row())

    assert scaled is None
    assert basis is not None and "未授权" in basis


@pytest.mark.parametrize("scale", [None, 0.0, -0.5])
def test_non_positive_tranche_scale_is_never_read_as_unscaled(scale) -> None:
    """`0` 与 `None` 都不得被读成"没有可用值"→ 满档下发。"""
    scaled, _basis = _first_tranche_scaled_percent(
        20.0, _coal_row(first_tranche_scale=scale)
    )

    assert scaled is None


def test_legacy_rows_without_the_maturity_layer_are_unaffected() -> None:
    """旧口径行没有成熟度层，缺 `first_tranche_scale` 是"没有这个概念"而不是"未授权"。

    与 `test_report_direction_maturity.test_absent_maturity_layer_changes_nothing` 同源：
    fail-closed 必须以 `score_policy_version` 为条件，否则会把接入成熟度层之前的行为改掉。
    """
    scaled, basis = _first_tranche_scaled_percent(20.0, {"score": 85})

    assert scaled == 20.0
    assert basis is None


# --- 契约 6：滞回行自身不得自相矛盾 ------------------------------------------
#
# 同一张卡片上「本轮不投入」和「本次投入保持小额」同时出现过（前者来自
# `first_tranche_scale=None`，后者是 `entry_hint` 的固定文案）；`flow_improving_probe_eligible`
# 为真却 `_active` 为假、而 `first_tranche_scale` 正是那条通道算出来的，也是同一类漂移。


def _hysteresis_input(**overrides) -> dict:
    """一行"昨天已 ready、今天掉回 ready_on_pullback、趋势仍在退出线之上"的原始打分行。"""
    row = {
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "sector_label": "煤炭",
        "entry_state": "ready_on_pullback",
        "trend_strength_score": 67.28,
        "participation_score": 28.93,
        "position_risk_score": 84.6,
        "flow_improving_probe_eligible": False,
        "probability_early_probe_eligible": False,
        "first_tranche_scale": None,
        "confidence": "中",
    }
    row.update(overrides)
    return row


def _held_by_hysteresis(**overrides) -> dict:
    from app.services.sector_direction_state import (
        DirectionStateRecord,
        apply_direction_state_hysteresis,
    )

    previous = {
        "煤炭": DirectionStateRecord(
            trade_date="2026-08-12",
            sector_label="煤炭",
            entry_state="ready_to_start",
            raw_entry_state="ready_to_start",
            qualifies_for_ready=True,
            consecutive_qualifying_days=2,
        )
    }
    rows = apply_direction_state_hysteresis(
        [_hysteresis_input(**overrides)],
        trade_date="2026-08-13",
        previous_trade_date="2026-08-12",
        previous_states=previous,
    )
    return rows[0]


def test_hysteresis_hint_matches_the_published_tranche_scale() -> None:
    """没有通道授权投入时，提示语不得再说"本次投入保持小额"。"""
    row = _held_by_hysteresis()

    assert row["entry_state"] == "ready_to_start"
    assert row["qualifies_for_ready"] is False
    assert row["first_tranche_scale"] is None
    assert "本轮不新增投入" in row["entry_hint"]
    assert "保持小额" not in row["entry_hint"]


def test_hysteresis_hint_still_says_small_when_a_tranche_is_authorized() -> None:
    row = _held_by_hysteresis(
        flow_improving_probe_eligible=True, first_tranche_scale=0.4
    )

    assert row["first_tranche_scale"] == 0.4
    assert "保持小额" in row["entry_hint"]


def test_probe_channel_stays_active_through_a_hysteresis_hold() -> None:
    """授权本轮投入的是原始档位上开的通道，`_active` 不得因为档位被抬走而失活。

    否则同一行会说"没有通道生效"，却继续沿用那条通道算出来的 `first_tranche_scale`。
    """
    row = _held_by_hysteresis(
        flow_improving_probe_eligible=True, first_tranche_scale=0.4
    )

    assert row["entry_state"] == "ready_to_start"
    assert row["flow_improving_probe_active"] is True
    assert row["execution_eligible"] is True


def test_hysteresis_still_does_not_invent_a_tranche_scale() -> None:
    """滞回只改时序行为，不改分数也不改比例——不得给未授权的方向补一个系数。"""
    row = _held_by_hysteresis()

    assert row["first_tranche_scale"] is None
    assert row["trend_strength_score"] == pytest.approx(67.28)
    assert row["participation_score"] == pytest.approx(28.93)
