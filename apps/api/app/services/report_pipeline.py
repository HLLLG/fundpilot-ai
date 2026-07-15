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
    from app.config import get_settings

    return {
        "analysis_mode": runtime.mode,
        "model": runtime.model,
        # ``news_tool_rounds`` is retained for older readers, but now has the
        # only truthful meaning it can have: rounds actually executed by the
        # main report generator.  Configured capacity is a separate field.
        "news_retrieval_policy": runtime.news_retrieval_policy,
        "news_tool_rounds": runtime.news_tool_rounds_executed,
        "news_tool_rounds_configured": runtime.news_tool_rounds_configured,
        "news_tool_rounds_executed": runtime.news_tool_rounds_executed,
        "news_count": len(news),
        "today_news_count": count_today_news(news),
        "topic_brief_count": len(briefs),
        "has_today_market_signal": has_today_market_signal(news, briefs),
        "topic_brief_providers": providers,
        "rule_judge": bool(judge.get("rule_judge", True)),
        "llm_judge_attempted": bool(judge.get("llm_judge_attempted", False)),
        "llm_judge_applied": bool(judge.get("llm_judge_applied", False)),
        # M6：记录生成本报告时的双向 guard 灰度模式，供 shadow_escalation_digest.py
        # 判断该报告是否属于"灰度观察期"样本（历史报告可能在 shadow/enforced 切换
        # 前后跨越，不能假设全部报告都是同一模式）。
        "decision_escalation_mode": get_settings().decision_escalation_mode,
    }
