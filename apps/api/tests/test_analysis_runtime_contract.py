from __future__ import annotations

from app.config import Settings
from app.services.analysis_runtime import (
    limit_news_topics_for_runtime,
    resolve_analysis_runtime,
)
from app.services.report_pipeline import build_pipeline_metadata


def test_deep_main_report_records_configured_tool_rounds_but_executes_none():
    settings = Settings(
        news_enabled=True,
        news_tool_max_rounds=3,
    )

    runtime = resolve_analysis_runtime(settings, "deep")
    pipeline = build_pipeline_metadata(
        runtime=runtime,
        market_news=[],
        topic_briefs=[],
    )

    assert runtime.news_tool_max_rounds == 0
    assert runtime.news_tool_rounds_configured == 3
    assert runtime.news_tool_rounds_executed == 0
    assert pipeline["news_retrieval_policy"] == "bounded_prefetch.v1"
    assert pipeline["news_tool_rounds"] == 0
    assert pipeline["news_tool_rounds_configured"] == 3
    assert pipeline["news_tool_rounds_executed"] == 0


def test_fast_main_report_has_no_configured_or_executed_tool_rounds():
    settings = Settings(
        news_enabled=True,
        news_tool_max_rounds=3,
    )

    runtime = resolve_analysis_runtime(settings, "fast")

    assert runtime.news_tool_rounds_configured == 0
    assert runtime.news_tool_rounds_executed == 0
    assert runtime.news_max_topics <= 3


def test_discovery_topic_budget_uses_the_selected_runtime():
    settings = Settings(news_max_topics=8)
    topics = ["半导体", "人工智能", "医药", "银行", "军工"]

    fast = limit_news_topics_for_runtime(
        topics,
        resolve_analysis_runtime(settings, "fast"),
    )
    deep = limit_news_topics_for_runtime(
        topics,
        resolve_analysis_runtime(settings, "deep"),
    )

    assert fast == topics[:3]
    assert deep == topics
