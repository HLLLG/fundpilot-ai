"""加仓侧接入"该仓自身是否已转正"这道成本基准门禁。

## 回归背景

接入前，日报的加仓档位完全由**板块侧**决定：`direction_score` 定档位、`first_tranche_scale`
定投入比例、`position_risk`（界面「结构修复度」）定板块价格位置。这三个量没有一个知道
**用户自己**是在什么价位买进来的。后果是一只已经浮亏 8% 的持仓，只要方向还在线上，就会拿到
与浮盈 8% 那只**完全相同**的档位——系统在亏损里越买越多，而且不认为这是一件需要区分的事。

`estimated_holding_return_percent` 此前在 `recommendation_guard` 里只被读过一次，而且只用在
**减仓**侧（`resolve_escalation_floor` 的 `has_unrealized_gain`，决定 −1/4 还是 −1/3）。

## 实测依据（`scripts/run_position_sizing_backtest.py`）

在同一批 PIT 入场信号上做逐 episode 配对比较（两边共用同一批 episode 与同一套退出规则），
"浮亏时封到最低档"相对现状：9 组 (最长持有期 × 止损幅度) 参数下均值差**全部为正**、7 组
|t| >= 2；20 日 / −10% 那组 +0.157%（t=2.90，87.2% 的 episode 不劣于现状），同时平均投出
47.5%（现状 51.4%）、费用 0.36%（现状 0.40%）——**收益更好且更省**。

中位差是 0.000%：它不提高胜率，只砍掉"在亏损里越买越多"那条尾巴。样本限制见
`_unrealized_loss_add_percent` 的 docstring（71 个 episode、单一下行区间、标的是板块指数）。

## 这里锁七条契约

1. 持有收益 <= 0 时档位封到阶梯最低档，且依据文案里能看到这个原因；
2. 持有收益 > 0 时完全不受影响（这道门禁只在亏损侧起作用）；
3. 口径不可得（`None`）时**不封顶**——"不知道"与"确实在亏"是两件事；
4. 封顶与既有的降一级机制可以叠加，但不会把结果压到最低档之下；
5. 封顶作用在**档位**上，分段试仓系数照旧在它之后乘（所以最终比例可以低于最低档）；
6. 本来就在最低档的行不会被再动，也不会平白多出一句依据；
7. 生产路径确实把 `analysis_facts` 的 `estimated_holding_return_percent` 透传进来，而且
   **加仓封档与减仓升档读的是同一个键**——否则同一份日报会出现"按浮亏封了加仓档、又按浮盈
   升了减仓档"这种自相矛盾的组合。契约 7 是本文件里唯一能挡住"悄悄 fail-open"的一条：
   前六条即使调用方压根不传参也全部通过。
"""
from __future__ import annotations

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.recommendation_guard import (
    _ADD_TIER_PERCENTS,
    _resolve_deterministic_position_change,
    _v3_gate_direction_score,
    apply_recommendation_guards,
)
from app.services.sector_opportunity_scoring import ENTRY_POLICY_VERSION_V3

#: 合成分 76.5 对应满档 20%——刻意选满档而不是中间档：封顶后是 5%，与"降一级"得到的
#: 15% 明显不同，断言因此不会把两种机制混为一谈。
_TOP_TIER_SCORE = 76.5
_TOP_TIER_PERCENT = max(_ADD_TIER_PERCENTS)
_FLOOR_PERCENT = min(_ADD_TIER_PERCENTS)

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_signal(monkeypatch):
    """本文件只测成本基准门禁，隔离真实盘中数据（网络/交易日相关）带来的偶发分支。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


def _v3_row(direction_score: float = _TOP_TIER_SCORE, **overrides) -> dict:
    row = {
        "sector_label": "半导体",
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "direction_score": direction_score,
        "entry_state": "ready_to_start",
        "opportunity_available": True,
        "confidence": "高",
        "pattern_label": "price_flow_aligned_up",
        "today_main_force_net_yi": 6.0,
        "cumulative_5d_net_yi": 12.0,
        # 1.0 = 方向层授权满额投入，把「分段试仓系数」这条独立维度隔离出去（契约 5 单独锁）。
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
            # 放宽浮亏线与集中度，避免这两道**另外的**门禁参与进来：本文件要观察的是
            # 档位封顶，而不是"浮亏超限改减仓"。
            max_drawdown_percent=50,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def _strong_fund_evidence() -> dict:
    """基金侧给满，把「基金证据降一级」这条独立维度隔离出去。"""
    return {
        "composite": {"level": "高", "score": 3.0},
        "components": [
            {"source": "factor", "level": "高", "basis": "主因子动量(百分位80)"}
        ],
        "summary": "主因子动量(百分位80)",
    }


def _usable_medium_fund_evidence() -> dict:
    """「证据可用但偏弱」才降一档；`reliability.usable` 是 `_fund_evidence_is_usable` 的判据。"""
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


def _percent(
    holding_return_percent: float | None,
    *,
    sector_opportunity: dict | None = None,
    **kwargs,
) -> tuple[float | None, str]:
    request = _request()
    percent, basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity=_v3_row() if sector_opportunity is None else sector_opportunity,
        evidence=kwargs.pop("evidence", _strong_fund_evidence()),
        holding_return_percent=holding_return_percent,
        **kwargs,
    )
    return percent, basis


# --- 契约 1：浮亏封到最低档 --------------------------------------------------


@pytest.mark.parametrize(
    "holding_return_percent",
    [
        # 0.0 必须算"未转正"：判据与减仓侧的 `has_unrealized_gain`（`> 0`）保持一致，
        # 否则同一个 0 在加仓侧算浮盈、在减仓侧算浮亏。
        0.0,
        -0.01,
        -3.2,
        -8.0,
        -25.0,
    ],
)
def test_unrealized_loss_caps_the_tier_to_the_lowest_rung(
    holding_return_percent: float,
) -> None:
    percent, basis = _percent(holding_return_percent)

    assert percent == _FLOOR_PERCENT
    assert "尚未转正" in basis
    assert f"最低档 {_FLOOR_PERCENT:g}%" in basis


def test_the_cap_is_visible_in_the_basis_next_to_the_tier_it_replaced() -> None:
    """封顶必须与它替换掉的档位同时出现在依据里，用户才看得出发生了什么。"""
    _, basis = _percent(-4.0)

    # 板块档位依据仍在（说明我们没有把板块结论藏掉），封顶原因紧随其后。
    assert "方向合成分" in basis
    assert basis.index("方向合成分") < basis.index("尚未转正")
    assert "-4.00%" in basis


# --- 契约 2：浮盈不受影响 ----------------------------------------------------


@pytest.mark.parametrize("holding_return_percent", [0.01, 1.5, 8.0, 120.0])
def test_unrealized_gain_leaves_the_tier_untouched(
    holding_return_percent: float,
) -> None:
    percent, basis = _percent(holding_return_percent)

    assert percent == _TOP_TIER_PERCENT
    assert "尚未转正" not in basis


# --- 契约 3：口径不可得不等于在亏 --------------------------------------------


def test_unknown_holding_return_does_not_cap() -> None:
    """`None` 表示"这一轮拿不到持有收益口径"，与"确实在亏"是两件事。

    这条同时保护了无成熟度层的旧口径行：它们走的是同一个默认值，行为必须与接入本门禁
    之前完全一致（见 `test_absent_maturity_layer_changes_nothing`）。生产路径不依赖这个
    默认值——契约 7 单独验证它真的传了参。
    """
    percent, basis = _percent(None)

    assert percent == _TOP_TIER_PERCENT
    assert "尚未转正" not in basis


# --- 契约 4/5/6：与既有降档机制的组合关系 ------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"evidence": _usable_medium_fund_evidence()},
        {
            "vehicle_quality": {
                "applicable": True,
                "status": "watch_only",
                "penalties": ["规模偏小"],
            }
        },
    ],
)
def test_cap_composes_with_one_step_downgrades_without_going_below_the_floor(
    kwargs: dict,
) -> None:
    """叠加"降一级"机制时结果仍恰好是最低档，不会被压穿。

    这里不断言两者的先后顺序：把 20% 先封到 5% 再降一级（最低档不再下降），与先降到 15%
    再封到 5%，最终数字相同，所以顺序在输出上不可观测——不能声称一条测不出来的契约。
    真正要锁的是**不穿底**：`_tier_percent_one_step_down` 在最低档必须原样返回。
    """
    percent, _basis = _percent(-4.0, **kwargs)

    assert percent == _FLOOR_PERCENT


def test_tranche_scale_still_applies_after_the_cap() -> None:
    """封顶作用在**档位**上；分段试仓系数是乘法，仍在它之后生效。

    所以"最低档"不是最终比例的下界——5% × 0.4 = 2%，与滞回态小额试探同一个量级。
    """
    percent, basis = _percent(
        -4.0,
        sector_opportunity=_v3_row(
            first_tranche_scale=0.4,
            overheat_flags=["单日涨幅过热"],
        ),
    )

    assert percent == pytest.approx(_FLOOR_PERCENT * 0.4)
    assert "尚未转正" in basis
    assert "方向分段试仓系数 40%" in basis


def test_row_already_on_the_lowest_tier_is_left_alone() -> None:
    """本来就在最低档时不该再动，也不该平白多一句依据。"""
    at_gate = _v3_row(_v3_gate_direction_score())

    capped, capped_basis = _percent(-4.0, sector_opportunity=at_gate)
    untouched, untouched_basis = _percent(None, sector_opportunity=at_gate)

    assert capped == untouched == _FLOOR_PERCENT
    assert capped_basis == untouched_basis
    assert "尚未转正" not in capped_basis


# --- 契约 7：生产路径真的传了参，且与减仓侧同源 ------------------------------


def _facts(holding_return_percent: float | None, **row_overrides) -> dict:
    row: dict = {
        "fund_code": "519674",
        "sector_opportunity": _v3_row(),
        "evidence": _strong_fund_evidence(),
    }
    if holding_return_percent is not None:
        row["estimated_holding_return_percent"] = holding_return_percent
    row.update(row_overrides)
    return {"holdings": [row]}


def _guarded(facts: dict, action: str = "分批加仓") -> FundRecommendation:
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action=action,
            )
        ],
        [],
        _request(),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


@pytest.mark.parametrize(
    ("holding_return_percent", "expected_percent"),
    [
        (8.0, _TOP_TIER_PERCENT),
        (0.0, _FLOOR_PERCENT),
        (-4.0, _FLOOR_PERCENT),
    ],
)
def test_production_path_passes_the_facts_holding_return(
    holding_return_percent: float,
    expected_percent: float,
) -> None:
    """端到端：只改 facts 里的持有收益，最终比例就要跟着变。

    这是唯一能挡住"悄悄 fail-open"的一条——上面所有单元用例即使
    `apply_recommendation_guards` 压根不传 `holding_return_percent` 也会全部通过。
    """
    rec = _guarded(_facts(holding_return_percent))

    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent == expected_percent
    assert rec.estimated_position_change_amount_yuan == pytest.approx(
        10_000 * expected_percent / 100
    )


def test_add_cap_and_reduce_escalation_read_the_same_facts_key() -> None:
    """同一个键同时驱动两侧：加仓封档用它，减仓的 1/4 vs 1/3 也用它。

    这条锁的是**一致性**而不是某一侧的数值。此前只有减仓侧读这个键，加仓侧另起一套
    （或压根不读）会让同一份日报出现"按浮亏封了加仓档、又按浮盈升了减仓档"的组合。
    """
    weak_sector = {
        "track": "momentum",
        "confidence": "高",
        "opportunity_available": False,
        "pattern_label": "distribution",
        "penalties": ["资金背离或持续流出"],
    }
    weak_evidence = {"composite": {"level": "不足", "score": 0.5}}

    in_loss = _guarded(
        _facts(-4.0, sector_opportunity=weak_sector, evidence=weak_evidence),
        action="观察",
    )
    in_profit = _guarded(
        _facts(8.0, sector_opportunity=weak_sector, evidence=weak_evidence),
        action="观察",
    )

    # 减仓侧：浮盈才升到 1/3，浮亏保持 1/4。
    assert in_loss.action == "减仓评估"
    assert in_loss.suggested_position_change_percent == -25.0
    assert in_profit.action == "减仓评估"
    assert in_profit.suggested_position_change_percent == pytest.approx(-(100 / 3))

    # 加仓侧：同一个键、同一个 `> 0` 判据，方向相反（浮亏收紧、浮盈放开）。
    assert _guarded(_facts(-4.0)).suggested_position_change_percent == _FLOOR_PERCENT
    assert _guarded(_facts(8.0)).suggested_position_change_percent == _TOP_TIER_PERCENT
