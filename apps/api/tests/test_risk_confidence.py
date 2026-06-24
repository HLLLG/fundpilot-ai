"""组合风险度量置信（样本充足度）测试（模块4 竖切4）。

设计文档：docs/superpowers/specs/2026-06-24-risk-confidence-llm-design.md。
"""
from __future__ import annotations

from app.services.risk_confidence import risk_metrics_confidence


def test_high_when_long_sample():
    r = risk_metrics_confidence({"available": True, "sample_days": 150})
    assert r["level"] == "高"
    assert "150" in r["basis"]


def test_medium_sample():
    r = risk_metrics_confidence({"available": True, "sample_days": 80})
    assert r["level"] == "中"


def test_low_sample():
    r = risk_metrics_confidence({"available": True, "sample_days": 30})
    assert r["level"] == "低"


def test_boundary_60_is_medium():
    assert risk_metrics_confidence({"available": True, "sample_days": 60})["level"] == "中"


def test_boundary_120_is_high():
    assert risk_metrics_confidence({"available": True, "sample_days": 120})["level"] == "高"


def test_unavailable_is_insufficient():
    r = risk_metrics_confidence({"available": False, "sample_days": 5})
    assert r["level"] == "不足"


def test_none_is_insufficient():
    assert risk_metrics_confidence(None)["level"] == "不足"
    assert risk_metrics_confidence({})["level"] == "不足"


def test_available_but_below_min_is_insufficient():
    r = risk_metrics_confidence({"available": True, "sample_days": 15})
    assert r["level"] == "不足"
