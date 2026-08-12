"""基金侧量化证据的语义边界：可靠性只能放行，不能冒充强度、不能冒充方向。

回归背景（2026-08-12，用户要求先验证证据本身是否合理，再动加仓门禁）：

`evidence.composite.level` 此前**等于**因子 IC 可靠性等级，而后者由
`portfolio_snapshot` 按 `segment=peer_group` 计算，是**同类组级常量**——同一同类组内每只
基金逐字相同（实测 002610 与 015788 同为指数型，momentum 依据文本完全一致；012200 与
017787 同为混合型，同样一致）。于是这个字段对同类基金恒等、没有横截面区分能力，却被
`recommendation_guard` 当成逐只基金的一票否决依据。线上六只持仓全部 `reliability=低`、
全部带「量化证据背书弱」，加仓在任何一天对任何持仓都不可达。

本文件锁四条边界：
1. `level` 由该标的自身的信号强度决定，可靠性只负责放行；
2. 可靠性不放行时不输出方向（此前会用一个自己都判为不可信的符号去断言方向）；
3. 「反向/均值回归」必然伴随不可用的可靠性——这是删掉符号翻转逻辑的前提，
   哪天有生产者破坏它，这里先响；
4. `effect_size` / `coverage` 自带口径标识，不会被当成收益效应量与统计样本量。
"""
from __future__ import annotations

import pytest

from app.services.factor_confidence import factor_confidence
from app.services.signal_synthesis import (
    _USABLE_RELIABILITY_LEVELS,
    _factor_component,
    build_holding_evidence,
)

FUND = "011036"


def _factor_scores(*, level: str, basis: str, percentile: float) -> dict:
    """构造 `factor_scores`，只放一个因子，便于精确控制被选中的那条。"""
    return {
        "available": True,
        "ic_status": {"state": "available", "available": True},
        "holdings": [
            {
                "fund_code": FUND,
                "applicable": True,
                "peer_group": "index",
                "feature_completeness": 0.75,
                "factor_reliability": {"momentum": {"level": level, "basis": basis}},
                "factor_percentiles": {"momentum": percentile},
            }
        ],
    }


# --------------------------------------------------------------------------
# 1. level 由自身信号强度决定，可靠性只放行
# --------------------------------------------------------------------------


def test_reliability_no_longer_becomes_the_support_level() -> None:
    """可靠性「中」+ 百分位极端 → 支持强度由百分位定档，而不是照抄「中」。"""
    component = _factor_component(
        FUND,
        _factor_scores(level="中", basis="指数基金未来20日同类 IC 正向且样本外稳定", percentile=95),
    )

    assert component is not None
    assert component["reliability"]["level"] == "中"
    assert component["reliability"]["usable"] is True
    # |95-50|*2 = 90 → effect 档位「高」；level 跟着它，不再等于可靠性的「中」。
    assert component["effect_size"]["level"] == "高"
    assert component["level"] == "高"


def test_reliability_scope_is_disclosed_as_peer_group() -> None:
    """作用域必须写进 payload：这是防止再被误读成"这只基金的量化质量"的唯一机制。"""
    component = _factor_component(
        FUND,
        _factor_scores(level="中", basis="同类 IC 正向且样本外稳定", percentile=80),
    )

    assert component is not None
    assert component["reliability"]["scope"] == "peer_group"


@pytest.mark.parametrize("level", ["低", "不足"])
def test_unusable_reliability_produces_insufficient_support_not_weak_support(
    level: str,
) -> None:
    """不可用要报「不足」（这一路没结论），不是「低」（有结论但弱）。

    两者在 guard 与文案里含义不同：前者不该降档、不该被说成"基金更弱"。
    """
    component = _factor_component(
        FUND,
        _factor_scores(level=level, basis="样本外/区间稳定性不足", percentile=95),
    )

    if level == "不足":
        # 「不足」在候选筛选阶段就被排除，整条因子证据不产出。
        assert component is None
        return
    assert component is not None
    assert component["reliability"]["usable"] is False
    assert component["level"] == "不足"
    # 强度本身仍如实记录，只是不参与背书——不隐藏证据。
    assert component["effect_size"]["level"] == "高"


# --------------------------------------------------------------------------
# 2. 可靠性不放行时不输出方向
# --------------------------------------------------------------------------


def test_unusable_reliability_reports_unknown_direction() -> None:
    component = _factor_component(
        FUND,
        _factor_scores(level="低", basis="指数基金未来20日 IC +0.043，样本外/区间稳定性不足", percentile=90),
    )

    assert component is not None
    assert component["direction"] == "unknown"


def test_reversal_basis_is_no_longer_flipped_into_a_positive_direction() -> None:
    """事故本体：011036 回撤控制百分位 16（倒数），此前被翻转成 positive。"""
    component = _factor_component(
        FUND,
        _factor_scores(
            level="低",
            basis="指数基金未来20日呈反向/均值回归（IC +0.055，样本外 -0.075）",
            percentile=16,
        ),
    )

    assert component is not None
    assert component["direction"] == "unknown"
    assert component["direction"] != "positive"


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [(95, "positive"), (50, "neutral"), (5, "negative")],
)
def test_usable_reliability_takes_direction_from_the_percentile_only(
    percentile: float,
    expected: str,
) -> None:
    component = _factor_component(
        FUND,
        _factor_scores(level="中", basis="同类 IC 正向且样本外稳定", percentile=percentile),
    )

    assert component is not None
    assert component["direction"] == expected


# --------------------------------------------------------------------------
# 3. 「反向/均值回归」必然伴随不可用可靠性（删掉符号翻转的前提）
# --------------------------------------------------------------------------


def test_reversal_basis_always_comes_with_unusable_reliability() -> None:
    """所有产出「反向/均值回归」文案的生产者都必须同时把 level 定为不可用。

    删掉 `_factor_component` 里那段符号翻转的正当性完全依赖这条不变量：既然反向证据
    一律不可用，翻转就只作用在"不该被使用的证据"上。这里对生产者本身取样验证，而不是
    靠注释声明——哪天有人给反向因子发一个「中」，这个用例会先失败。
    """
    negative_ic = factor_confidence(
        {"momentum": {"mean_ic": -0.05, "significant": True, "periods": 120}},
        "momentum",
    )
    assert "反向" in negative_ic["basis"]
    assert negative_ic["level"] not in _USABLE_RELIABILITY_LEVELS


def test_research_path_reversal_is_also_unusable() -> None:
    """research_model 路径（生产实际走的那条）同样如此。"""
    from app.services.factor_confidence import _research_factor_confidence

    research_model = {
        "primary_horizon": 20,
        "cohort_mode": "current_survivors",
        "segments": {
            "index": {
                "label": "指数基金",
                "horizons": {
                    "20": {
                        "qualified": {"momentum": True},
                        "factors": [
                            {
                                "factor": "momentum",
                                "mean_ic": 0.055,
                                "oos_mean_ic": -0.075,
                            }
                        ],
                    }
                },
            }
        },
    }

    result = _research_factor_confidence(research_model, "index", "momentum")

    assert "反向" in result["basis"] or "均值回归" in result["basis"]
    assert result["level"] not in _USABLE_RELIABILITY_LEVELS


# --------------------------------------------------------------------------
# 4. effect_size / coverage 自带口径标识
# --------------------------------------------------------------------------


def test_effect_size_and_coverage_declare_what_they_actually_measure() -> None:
    component = _factor_component(
        FUND,
        _factor_scores(level="中", basis="同类 IC 正向且样本外稳定", percentile=84),
    )

    assert component is not None
    effect = component["effect_size"]
    assert effect["metric"] == "factor_percentile_extremity"
    assert effect["percentile"] == 84
    assert "不是收益效应量" in effect["basis"]

    coverage = component["coverage"]
    assert coverage["metric"] == "fund_feature_completeness"
    assert "非 IC 统计样本覆盖" in coverage["basis"]


# --------------------------------------------------------------------------
# 端到端：线上那六只的形态
# --------------------------------------------------------------------------


def test_production_shape_yields_insufficient_composite_and_no_direction() -> None:
    """复现线上形态：因子分量在、可靠性不可用 → composite 为「不足」且方向不冒充。

    修复前该形态给出 `composite.level="低"` 且 `direction="positive"`，
    guard 据此一票否决加仓。
    """
    evidence = build_holding_evidence(
        fund_code=FUND,
        signal_entry=None,
        factor_scores=_factor_scores(
            level="低",
            basis="指数基金未来20日呈反向/均值回归（IC +0.055，样本外 -0.075）",
            percentile=16,
        ),
        risk_metrics={
            "available": True,
            "sample_days": 22,
            "max_drawdown_percent": -18.0,
            "hhi": 0.3,
            "confidence": {"level": "低", "basis": "仅 22 交易日样本，指标较毛糙，置信低"},
        },
    )

    assert evidence is not None
    composite = evidence["composite"]
    assert composite["level"] == "不足"
    assert composite["direction"] in {"neutral", "unknown"}
    assert composite["positive_component_count"] == 0
    # 风险守卫照旧只作守卫，不冒充收益支持。
    assert composite["risk_guard_count"] == 1
