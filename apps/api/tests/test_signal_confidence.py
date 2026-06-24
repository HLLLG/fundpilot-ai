"""板块信号可信度打分器测试（模块4-4A）。

设计文档：docs/superpowers/specs/2026-06-24-signal-confidence-design.md。
"""
from __future__ import annotations

from app.services.signal_confidence import ConfidenceScore, score_signal


def _bucket(n, h, b, significant=None):
    e = round(h - b, 2)
    return {
        "trigger_count": n,
        "hit_rate_percent": h,
        "baseline_rate_percent": b,
        "edge_percent": e,
        "significant": (e >= 5 and n >= 30) if significant is None else significant,
    }


def test_high_confidence():
    r = score_signal(_bucket(60, 72.0, 55.0))  # edge 17, n 60
    assert isinstance(r, ConfidenceScore)
    assert r.level == "高"
    assert 0 <= r.score <= 100 and r.score > 60
    assert "置信高" in r.basis


def test_medium_confidence():
    r = score_signal(_bucket(40, 62.0, 55.0))  # edge 7, n 40, significant
    assert r.level == "中"


def test_low_when_not_significant():
    r = score_signal(_bucket(40, 57.0, 55.0))  # edge 2 (<5) → 不显著
    assert r.level == "低"
    assert r.score < 60


def test_insufficient_sample():
    r = score_signal(_bucket(10, 80.0, 50.0))  # n<30
    assert r.level == "不足"


def test_none_bucket():
    assert score_signal(None).level == "不足"
    assert score_signal({"trigger_count": 0}).level == "不足"


def test_zero_edge_is_50():
    r = score_signal(_bucket(50, 55.0, 55.0))  # edge 0
    assert r.score == 50


def test_negative_edge_below_50():
    r = score_signal(_bucket(50, 45.0, 55.0))  # edge -10
    assert r.score < 50
    assert r.level == "低"


def test_edge_missing_falls_back_to_h_minus_b():
    b = {
        "trigger_count": 60,
        "hit_rate_percent": 70.0,
        "baseline_rate_percent": 55.0,
        "significant": True,
    }
    r = score_signal(b)  # edge 缺失，用 70-55=15 兜底
    assert r.level == "高"
