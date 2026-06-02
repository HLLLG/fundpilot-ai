from __future__ import annotations

from app.models import NewsItem, TopicBrief
from app.services.analysis_runtime import AnalysisRuntime
from app.services.market_signal import count_today_news, has_today_market_signal


def build_pipeline_metadata(
    *,
    runtime: AnalysisRuntime,
    market_news: list[NewsItem] | None,
    topic_briefs: list[TopicBrief] | None,
    judge_meta: dict | None = None,
) -> dict:
    briefs = topic_briefs or []
    news = market_news or []
    providers = sorted({brief.provider for brief in briefs if brief.provider})
    judge = judge_meta or {}
    return {
        "analysis_mode": runtime.mode,
        "model": runtime.model,
        "news_tool_rounds": runtime.news_tool_max_rounds,
        "news_count": len(news),
        "today_news_count": count_today_news(news),
        "topic_brief_count": len(briefs),
        "has_today_market_signal": has_today_market_signal(news, briefs),
        "topic_brief_providers": providers,
        "rule_judge": bool(judge.get("rule_judge", True)),
        "llm_judge_attempted": bool(judge.get("llm_judge_attempted", False)),
        "llm_judge_applied": bool(judge.get("llm_judge_applied", False)),
    }
