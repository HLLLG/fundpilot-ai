"""信号合成（证据卡）测试（模块4 竖切5）。

设计文档：docs/superpowers/specs/2026-06-24-signal-synthesis-design.md。
"""
from __future__ import annotations

from app.services.signal_synthesis import (
    build_evidence_overview,
    build_holding_evidence,
    synthesize_confidence,
)


# ---------------------------------------------------------------------------
# synthesize_confidence
# ---------------------------------------------------------------------------


def test_two_high_is_high():
    assert synthesize_confidence(["高", "高"])["level"] == "高"


def test_high_and_low_is_medium():
    assert synthesize_confidence(["高", "低"])["level"] == "中"


def test_two_low_is_low():
    assert synthesize_confidence(["低", "低"])["level"] == "低"


def test_empty_is_insufficient():
    assert synthesize_confidence([])["level"] == "不足"


def test_insufficient_levels_ignored():
    # 只有一路「高」，其余「不足」被忽略 → 综合高
    assert synthesize_confidence(["高", "不足", "不足"])["level"] == "高"


# ---------------------------------------------------------------------------
# build_holding_evidence
# ---------------------------------------------------------------------------


def _factor_scores():
    return {
        "available": True,
        "factor_reliability": {
            "momentum": {"level": "高", "basis": "回测显著正向（IC +0.041），置信高"},
            "risk_adjusted": {"level": "不足", "basis": "无回测数据"},
            "drawdown": {"level": "低", "basis": "回测不显著，仅描述性"},
        },
        "holdings": [
            {
                "fund_code": "000001",
                "composite_grade": "A",
                "factor_percentiles": {
                    "momentum": 88,
                    "risk_adjusted": 95,  # 百分位最高但 IC「不足」→ 应跳过, 选 momentum
                    "drawdown": 60,
                    "size": 40,
                },
            }
        ],
    }


def _signal_entry():
    return {
        "sector_label": "半导体",
        "by_rule": {
            "r1": {"label": "规则1", "confidence": {"level": "中", "score": 64, "basis": "跑赢基线"}},
            "r2": {"label": "规则2", "confidence": {"level": "高", "score": 78, "basis": "显著跑赢"}},
        },
    }


def _risk_metrics():
    return {"available": True, "confidence": {"level": "高", "basis": "150 交易日样本，置信高"}}


def test_all_three_components():
    ev = build_holding_evidence(
        fund_code="000001",
        signal_entry=_signal_entry(),
        factor_scores=_factor_scores(),
        risk_metrics=_risk_metrics(),
    )
    assert ev is not None
    sources = {c["source"]: c for c in ev["components"]}
    assert set(sources) == {"factor", "signal", "risk"}
    # 因子主因子应为 momentum（risk_adjusted 百分位更高但 IC 不足被跳过）
    assert "动量" in sources["factor"]["basis"] or "momentum" in sources["factor"]["basis"]
    assert sources["factor"]["level"] == "高"
    # 信号取 score 最高的 r2（高）
    assert sources["signal"]["level"] == "高"
    assert sources["risk"]["level"] == "高"
    assert ev["composite"]["level"] == "高"
    assert isinstance(ev["summary"], str) and ev["summary"]


def test_only_risk_component():
    ev = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=None,
        risk_metrics=_risk_metrics(),
    )
    assert ev is not None
    assert [c["source"] for c in ev["components"]] == ["risk"]
    assert ev["composite"]["level"] == "高"


def test_no_components_returns_none():
    ev = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=None,
        risk_metrics=None,
    )
    assert ev is None


def _row(code: str, amount: float, level: str | None):
    row = {"fund_code": code, "fund_name": code, "holding_amount": amount}
    if level is not None:
        row["evidence"] = {"composite": {"level": level, "score": 2}, "components": [], "summary": ""}
    return row


def test_overview_weighted_distribution():
    rows = [
        _row("a", 6000, "高"),
        _row("b", 3000, "低"),
        _row("c", 1000, "中"),
    ]
    ov = build_evidence_overview(rows)
    assert ov["available"] is True
    assert ov["total_holdings"] == 3
    assert ov["covered_holdings"] == 3
    assert ov["count_by_level"]["高"] == 1
    assert ov["weight_by_level"]["高"] == 60.0
    assert ov["weight_by_level"]["低"] == 30.0
    assert ov["weight_by_level"]["中"] == 10.0
    # backed = 高 + 中 = 70%
    assert ov["backed_weight_percent"] == 70.0


def test_overview_uncovered_counts_in_denominator():
    rows = [
        _row("a", 5000, "高"),  # covered
        _row("b", 5000, None),  # uncovered → 计入分母不计入分子
    ]
    ov = build_evidence_overview(rows)
    assert ov["covered_holdings"] == 1
    assert ov["weight_by_level"]["高"] == 50.0
    assert ov["backed_weight_percent"] == 50.0


def test_overview_no_evidence_unavailable():
    rows = [_row("a", 5000, None), _row("b", 5000, None)]
    assert build_evidence_overview(rows)["available"] is False


def test_overview_zero_amount_unavailable():
    assert build_evidence_overview([_row("a", 0, "高")])["available"] is False


def test_factor_skips_when_all_reliability_insufficient():
    fs = {
        "available": True,
        "factor_reliability": {
            "momentum": {"level": "不足", "basis": "无回测数据"},
            "risk_adjusted": {"level": "不足", "basis": "无回测数据"},
            "drawdown": {"level": "不足", "basis": "无回测数据"},
        },
        "holdings": [
            {"fund_code": "000001", "composite_grade": "B",
             "factor_percentiles": {"momentum": 80, "risk_adjusted": 70, "drawdown": 60, "size": 50}}
        ],
    }
    ev = build_holding_evidence(
        fund_code="000001", signal_entry=None, factor_scores=fs, risk_metrics=None
    )
    assert ev is None  # 因子全不足、无信号、无风险 → 无分量
