from __future__ import annotations

from app.services.report_export import report_to_markdown


def _report(**overrides) -> dict:
    base = {
        "title": "测试日报",
        "created_at": "2026-07-01T00:00:00Z",
        "provider": "deepseek",
        "risk": {"level": "medium", "suggested_action": "watch", "weighted_return_percent": 1.2, "alerts": []},
        "summary": "测试摘要",
        "recommendations": [],
        "fund_recommendations": [],
        "caveats": [],
        "analysis_facts": {},
    }
    base.update(overrides)
    return base


def test_markdown_includes_structured_decision_fields_when_present() -> None:
    report = _report(
        fund_recommendations=[
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "action": "分批加仓",
                "confidence": "高",
                "hold_horizon": "1-2周",
                "decision_path": "先看板块方向，再看基金证据，最后给出动作",
                "sector_evidence": ["顺势观察，置信度高"],
                "fund_evidence": ["三路量化证据综合置信：高"],
                "validation_notes": ["样本有限"],
                "risks": ["板块波动可能导致净值回撤"],
                "points": ["测试要点"],
            }
        ]
    )
    markdown = report_to_markdown(report)
    assert "**置信度**：高" in markdown
    assert "**持有/观察窗口**：1-2周" in markdown
    assert "**决策路径**：先看板块方向，再看基金证据，最后给出动作" in markdown
    assert "**板块依据**：顺势观察，置信度高" in markdown
    assert "**基金依据**：三路量化证据综合置信：高" in markdown
    assert "**校验备注**：样本有限" in markdown
    assert "**风险：**" in markdown
    assert "板块波动可能导致净值回撤" in markdown


def test_markdown_omits_structured_sections_when_fields_absent() -> None:
    report = _report(
        fund_recommendations=[
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "action": "观察",
                "points": ["测试要点"],
            }
        ]
    )
    markdown = report_to_markdown(report)
    assert "**决策路径**" not in markdown
    assert "**板块依据**" not in markdown
    assert "**基金依据**" not in markdown
    assert "**校验备注**" not in markdown
    assert "**风险：**" not in markdown
    assert "测试要点" in markdown
