from __future__ import annotations

from app.services.report_export import report_to_markdown


def test_daily_markdown_prefers_system_percentage_over_legacy_amount_fields() -> None:
    markdown = report_to_markdown(
        {
            "title": "测试日报",
            "risk": {},
            "fund_recommendations": [
                {
                    "fund_code": "000001",
                    "fund_name": "测试基金",
                    "action": "分批加仓",
                    "suggested_position_change_percent": 15,
                    "estimated_position_change_amount_yuan": 1019.3,
                    "amount_yuan": 9999,
                    "amount_note": "旧版固定金额",
                }
            ],
        }
    )

    assert "**建议调整**：相对当前持仓加仓 15%" in markdown
    assert "**估算调整金额**：约 1,019 元（按报告生成时持仓估值折算）" in markdown
    assert "**金额**" not in markdown
    assert "9999" not in markdown
    assert "旧版固定金额" not in markdown
