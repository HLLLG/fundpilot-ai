"""板块信号可信度打分器（模块4-4A）。

现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / 决策事实、仓位与 DataEvidence」。

纯函数：把模块3-3B 的信号回测桶（命中率/自然基线/edge/显著性）映射成一个
可回测的置信结论 {level, score, basis}，供 facts/prompt 与前端按置信分级表述。
不改回测算法本身，只消费其输出。
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_TRIGGERS = 30
EDGE_MEDIUM = 5.0
EDGE_HIGH = 10.0
SCORE_SAMPLE_FULL = 50


@dataclass
class ConfidenceScore:
    level: str  # 高 / 中 / 低 / 不足
    score: int  # 0–100，给前端做条/色，非分级依据
    basis: str  # 一句话依据


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_signal(bucket: dict | None) -> ConfidenceScore:
    if not bucket:
        return ConfidenceScore("不足", 0, "无触发样本")
    n = int(bucket.get("trigger_count") or 0)
    if n <= 0:
        return ConfidenceScore("不足", 0, "无触发样本")

    h = _num(bucket.get("hit_rate_percent"))
    b = _num(bucket.get("baseline_rate_percent"))
    e = _num(bucket.get("edge_percent"))
    if e is None and h is not None and b is not None:
        e = round(h - b, 2)
    if e is None:
        return ConfidenceScore("不足", 0, f"命中率数据缺失（{n} 次）")

    sig = bucket.get("significant")
    if sig is None:
        sig = e >= EDGE_MEDIUM and n >= MIN_TRIGGERS

    sample_factor = min(1.0, n / SCORE_SAMPLE_FULL)
    score = round(50 + _clamp(e * 2, -50, 50) * sample_factor)
    score = int(_clamp(score, 0, 100))

    if n < MIN_TRIGGERS:
        return ConfidenceScore("不足", score, f"样本仅 {n} 次（<{MIN_TRIGGERS}），不作数")
    if not sig or e < EDGE_MEDIUM:
        return ConfidenceScore("低", score, f"未稳定跑赢自然基线（edge {e:+.1f}%），置信低")
    if e < EDGE_HIGH:
        return ConfidenceScore("中", score, f"跑赢自然基线 {e:+.1f}%（{n} 次），置信中")
    return ConfidenceScore("高", score, f"显著跑赢自然基线 {e:+.1f}%（{n} 次），置信高")
