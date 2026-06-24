"""report_to_markdown 证据渲染测试（量化依据进追问上下文）。"""
from __future__ import annotations

from app.services.report_export import report_to_markdown


def _report_with_evidence():
    return {
        "title": "每日基金操作日报",
        "risk": {"level": "medium", "suggested_action": "watch", "weighted_return_percent": 0.5, "alerts": []},
        "summary": "测试摘要",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "基金A", "action": "分批加仓", "points": ["论据1"]},
            {"fund_code": "000002", "fund_name": "基金B", "action": "观察", "points": ["论据2"]},
        ],
        "analysis_facts": {
            "holdings": [
                {
                    "fund_code": "000001",
                    "evidence": {
                        "composite": {"level": "高", "score": 3},
                        "summary": "主因子 动量(百分位88)·IC置信高；组合风险样本置信高",
                    },
                },
            ],
            "evidence_overview": {
                "available": True,
                "backed_weight_percent": 70.0,
                "summary": "组合 70% 市值有中/高量化背书，1/2 只持仓有证据覆盖。",
            },
        },
    }


def test_markdown_includes_per_fund_evidence():
    md = report_to_markdown(_report_with_evidence())
    assert "**量化依据**（综合置信高）" in md
    assert "主因子 动量(百分位88)·IC置信高" in md


def test_markdown_includes_evidence_overview():
    md = report_to_markdown(_report_with_evidence())
    assert "组合量化背书" in md
    assert "中/高背书市值占比：70.0%" in md


def test_markdown_omits_evidence_when_absent():
    report = _report_with_evidence()
    report["analysis_facts"] = {}
    md = report_to_markdown(report)
    assert "量化依据" not in md
    assert "组合量化背书" not in md


def test_markdown_fund_without_evidence_has_no_evidence_line():
    md = report_to_markdown(_report_with_evidence())
    # 基金B 无 evidence → 其小节不应出现「量化依据」（仅基金A 有一处）
    assert md.count("量化依据") == 1
