from app.services.report_chat_export import report_chat_to_markdown


def test_report_chat_to_markdown_formats_messages():
    report = {"id": "abc", "title": "测试日报", "created_at": "2026-05-31T08:00:00Z"}
    messages = [
        {"role": "user", "content": "为什么暂停加仓？", "created_at": "2026-05-31T08:01:00Z"},
        {"role": "assistant", "content": "因浮亏接近阈值。", "created_at": "2026-05-31T08:01:05Z"},
    ]
    markdown = report_chat_to_markdown(report, messages)
    assert "测试日报" in markdown
    assert "为什么暂停加仓" in markdown
    assert "因浮亏接近阈值" in markdown
