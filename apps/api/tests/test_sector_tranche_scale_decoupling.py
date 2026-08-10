"""首仓比例必须由趋势强度决定，不能由未校准的趋势成形信号分决定。

背景：`trend_formation_probability` 是 `15 + 加权信号分 × 0.82` 的仿射变换，从未做过校准
（各项都中性的方向就会读出 56）。它原来被 `min()` 进 `first_tranche_scale`，等于让一个
未验证的量表直接决定投多少钱。趋势强度才是这套模型里唯一实测显著有效的轴，且自家网格
回测记录了"趋势阈值越高、去均值超额越高"，所以分段建仓改挂趋势强度。
"""
from __future__ import annotations

import pytest

from app.services.sector_opportunity_scoring import (
    V3_TREND_TRANCHE_SCALES,
    _probability_tranche_scale,
    _trend_tranche_scale,
)


@pytest.mark.parametrize(
    ("trend", "expected"),
    [
        (95.0, 1.0),
        (80.0, 1.0),
        (79.9, 0.65),
        (70.0, 0.65),
        (69.9, 0.4),
        (60.0, 0.4),  # 刚过入场线：够格买 ≠ 够格买满
        (59.9, 0.0),
    ],
)
def test_trend_tranche_scale_is_monotone_and_gated_at_the_entry_line(
    trend: float,
    expected: float,
) -> None:
    assert _trend_tranche_scale(trend) == pytest.approx(expected)


def test_trend_tranche_table_is_sorted_high_to_low() -> None:
    """表必须按阈值降序，否则线性查找会返回错误档位。"""
    thresholds = [threshold for threshold, _ in V3_TREND_TRANCHE_SCALES]
    assert thresholds == sorted(thresholds, reverse=True)
    scales = [scale for _, scale in V3_TREND_TRANCHE_SCALES]
    assert scales == sorted(scales, reverse=True)


def test_neutral_direction_reads_56_on_the_uncalibrated_probability_scale() -> None:
    """记录问题本身：中性方向（各项 50 分）在旧刻度上就能落进可投档。

    signal_score = 50 → probability = 15 + 50 × 0.82 = 56，而旧档位表最低档是 55。
    也就是说旧刻度的"不给钱"区间只剩下略低于中性的一小段——这正是不能用它定仓位的原因。
    """
    neutral_probability = 15.0 + 50.0 * 0.82
    assert neutral_probability == pytest.approx(56.0)
    assert _probability_tranche_scale(neutral_probability) > 0


def test_probability_scale_still_exists_for_observability_only() -> None:
    """保留该函数用于观测与向后兼容；它不应再被首仓比例引用。"""
    import inspect

    from app.services import sector_opportunity_scoring as module

    source = inspect.getsource(module._entry_maturity_v3)
    assert "trend_scale" in source
    # 唯一允许出现 probability_scale 的地方是"仅观测"的赋值，不得再进 min()
    assert "min(\n            first_tranche_scale,\n            probability_scale" not in source
