"""因子 IC 置信映射测试（模块4 竖切3）。

设计文档：docs/superpowers/specs/2026-06-24-factor-confidence-llm-design.md。
"""
from __future__ import annotations

import json

from app.services import factor_confidence as fc


def _ic(mean_ic, significant):
    return {"mean_ic": mean_ic, "significant": significant}


def test_significant_strong_positive_high():
    r = fc.factor_confidence({"momentum": _ic(0.041, True)}, "momentum")
    assert r["level"] == "高"
    assert "IC" in r["basis"]


def test_significant_weak_positive_medium():
    r = fc.factor_confidence({"momentum": _ic(0.018, True)}, "momentum")
    assert r["level"] == "中"


def test_significant_negative_low():
    r = fc.factor_confidence({"drawdown": _ic(-0.05, True)}, "drawdown")
    assert r["level"] == "低"
    assert "反向" in r["basis"]


def test_not_significant_low():
    r = fc.factor_confidence({"risk_adjusted": _ic(0.06, False)}, "risk_adjusted")
    assert r["level"] == "低"
    assert "不显著" in r["basis"]


def test_size_always_insufficient():
    r = fc.factor_confidence({"momentum": _ic(0.04, True)}, "size")
    assert r["level"] == "不足"
    assert "未回测" in r["basis"]


def test_missing_factor_insufficient():
    r = fc.factor_confidence({}, "momentum")
    assert r["level"] == "不足"


def test_factor_reliability_covers_all_four():
    rel = fc.factor_reliability({"momentum": _ic(0.04, True)})
    assert set(rel.keys()) == {"momentum", "risk_adjusted", "drawdown", "size"}
    assert rel["momentum"]["level"] == "高"
    assert rel["size"]["level"] == "不足"
    assert rel["drawdown"]["level"] == "不足"  # 无数据


def test_load_ic_summary_reads_file(tmp_path, monkeypatch):
    payload = {
        "factors": [
            {"factor": "momentum", "mean_ic": 0.04, "significant": True},
            {"factor": "drawdown", "mean_ic": -0.01, "significant": False},
        ]
    }
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(fc, "SUMMARY_PATH", p)
    fc._SUMMARY_CACHE.clear()
    out = fc.load_ic_summary()
    assert out["momentum"]["mean_ic"] == 0.04
    assert out["momentum"]["significant"] is True
    assert "drawdown" in out


def test_load_ic_summary_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "SUMMARY_PATH", tmp_path / "nope.json")
    fc._SUMMARY_CACHE.clear()
    assert fc.load_ic_summary() == {}
