"""板块信号回测 Bug B 修复测试（命中率基准从固定 50% 改为方向感知 base rate + 显著性）。

设计文档：docs/superpowers/specs/2026-06-24-portfolio-risk-metrics-design.md §9.2（Bug B）。
"""
from __future__ import annotations

from app.services.sector_signal_backtest import (
    EDGE_MIN_PERCENT,
    MIN_TRIGGERS_FOR_SIGNIFICANCE,
    _baseline_prob,
    _direction_fractions,
    _evaluate_rules,
    _finalize_bucket,
)


def _bar(change: float, high: float | None = None) -> dict:
    return {"date": "2026-01-01", "change_percent": change, "high_change_percent": high}


# ---------------------------------------------------------------------------
# 方向分布 + 基准概率
# ---------------------------------------------------------------------------


def test_direction_fractions_basic():
    up, down, flat = _direction_fractions([1.0, -1.0, 0.0])  # flat 阈值 0.3
    assert round(up, 3) == round(1 / 3, 3)
    assert round(down, 3) == round(1 / 3, 3)
    assert round(flat, 3) == round(1 / 3, 3)


def test_baseline_prob_is_direction_aware():
    fracs = (0.2, 0.5, 0.3)  # up=0.2, down=0.5, flat=0.3
    assert abs(_baseline_prob("up", fracs) - 0.2) < 1e-9
    assert abs(_baseline_prob("down", fracs) - 0.5) < 1e-9
    # 下跌或平：down+flat
    assert abs(_baseline_prob("down_or_flat", fracs) - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# 桶收尾：基准、超额、显著性
# ---------------------------------------------------------------------------


def test_finalize_bucket_significant_when_beats_baseline():
    bucket = {"trigger_count": 40, "hit_count": 30, "expected_random_hits": 20.0}
    _finalize_bucket(bucket)
    assert bucket["hit_rate_percent"] == 75.0
    assert bucket["baseline_rate_percent"] == 50.0
    assert bucket["edge_percent"] == 25.0
    assert bucket["significant"] is True
    assert bucket["beats_baseline"] is True
    assert bucket["beats_random"] is True  # 向后兼容别名


def test_finalize_bucket_not_significant_when_too_few_triggers():
    # 超额很大，但触发次数 < 门槛 → 不显著（可能是运气）
    bucket = {"trigger_count": 5, "hit_count": 5, "expected_random_hits": 1.0}
    _finalize_bucket(bucket)
    assert bucket["trigger_count"] < MIN_TRIGGERS_FOR_SIGNIFICANCE
    assert bucket["significant"] is False
    assert bucket["beats_baseline"] is False


def test_finalize_bucket_not_significant_when_edge_small():
    # 命中率高，但只是因为基准本来就高（板块自然上涨率高）→ 超额不足
    bucket = {"trigger_count": 50, "hit_count": 36, "expected_random_hits": 35.0}
    _finalize_bucket(bucket)
    assert bucket["hit_rate_percent"] == 72.0
    assert bucket["baseline_rate_percent"] == 70.0
    assert bucket["edge_percent"] == 2.0
    assert 2.0 < EDGE_MIN_PERCENT
    assert bucket["significant"] is False


def test_finalize_bucket_zero_triggers():
    bucket = {"trigger_count": 0, "hit_count": 0, "expected_random_hits": 0.0}
    _finalize_bucket(bucket)
    assert bucket["hit_rate_percent"] is None
    assert bucket["baseline_rate_percent"] is None
    assert bucket["significant"] is False


# ---------------------------------------------------------------------------
# _evaluate_rules 累计 expected_random_hits（方向感知）
# ---------------------------------------------------------------------------


def test_evaluate_rules_accumulates_expected_random_hits():
    # 全是大跌日 → sector_weak 每天触发，预测 down_or_flat；
    # 未来日多为下跌 → 基准(down+flat 占比)应较高
    series = [_bar(-3.0) for _ in range(10)]
    stats = _evaluate_rules(series, ("sector_weak",))
    bucket = stats["sector_weak"]
    assert bucket["trigger_count"] > 0
    # 未来全是下跌 → 基准概率≈1 → expected_random_hits≈trigger_count
    assert abs(bucket["expected_random_hits"] - bucket["trigger_count"]) < 1e-6
    # 既然基准≈100%，命中率也≈100% → 超额≈0 → 不显著（正确地识破"假信号"）
    assert bucket["significant"] is False
