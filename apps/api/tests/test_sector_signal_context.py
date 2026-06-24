"""板块信号 facts 注入置信分测试（模块4-4B）。"""
from __future__ import annotations

from app.services.sector_signal_context import _compact_rules


def test_compact_rules_attaches_confidence():
    raw = {
        "rule_x": {
            "label": "测试规则",
            "trigger_count": 60,
            "hit_count": 43,
            "hit_rate_percent": 72.0,
            "baseline_rate_percent": 55.0,
            "edge_percent": 17.0,
            "significant": True,
            "beats_baseline": True,
        }
    }
    out = _compact_rules(raw)
    conf = out["rule_x"]["confidence"]
    assert conf["level"] == "高"
    assert 0 <= conf["score"] <= 100
    assert isinstance(conf["basis"], str)


def test_compact_rules_low_confidence_marked():
    raw = {
        "rule_y": {
            "label": "弱规则",
            "trigger_count": 40,
            "hit_count": 23,
            "hit_rate_percent": 57.0,
            "baseline_rate_percent": 55.0,
            "edge_percent": 2.0,
            "significant": False,
            "beats_baseline": False,
        }
    }
    out = _compact_rules(raw)
    assert out["rule_y"]["confidence"]["level"] == "低"
