from __future__ import annotations

from collections.abc import Callable

from app.database import save_discovery_report
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.services.discovery_candidate_pool import build_candidate_pool, enrich_candidates
from app.services.discovery_client import DiscoveryClient
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.discovery_target_sectors import select_target_sectors
from app.services.news_service import NewsService
from app.services.news_summarizer import summarize_all_topics
from app.services.risk import resolve_weight_denominator

ProgressCallback = Callable[[str, str], None]

DISCOVERY_JOB_STAGES: dict[str, str] = {
    "queued": "排队中…",
    "sector_heat": "计算板块热度…",
    "candidate_pool": "构建候选基金池…",
    "news": "拉取市场要闻…",
    "generating": "AI 分析中…",
    "guarding": "校验推荐结果…",
    "saving": "保存报告…",
    "completed": "完成",
}


def run_discovery(
    request: DiscoveryRequest,
    on_progress: ProgressCallback | None = None,
) -> FundDiscoveryReport:
    def progress(stage: str) -> None:
        if on_progress is not None:
            on_progress(stage, DISCOVERY_JOB_STAGES.get(stage, stage))

    holdings = list(request.holdings)
    progress("sector_heat")
    sector_heat = build_sector_heat_ranking()
    target_sectors = select_target_sectors(
        holdings,
        request.focus_sectors,
        sector_heat,
        request.profile,
    )
    held_codes = {h.fund_code.strip().zfill(6) for h in holdings if h.fund_code}

    progress("candidate_pool")
    pool = build_candidate_pool(target_sectors, exclude_codes=held_codes)
    pool = enrich_candidates(pool)

    progress("news")
    news_service = NewsService()
    topics = list(dict.fromkeys(target_sectors + request.focus_sectors))
    if not topics:
        topics = ["上证指数"]
    market_news = news_service.prefetch_topics(topics)
    topic_briefs = summarize_all_topics(topics, market_news)

    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    denominator = resolve_weight_denominator(holdings, request.profile)
    budget = request.budget_yuan
    if budget is None:
        expected = request.profile.expected_investment_amount or denominator
        budget = max(float(expected) - total_amount, 0.0)

    discovery_facts = build_discovery_facts(
        holdings=holdings,
        profile=request.profile,
        target_sectors=target_sectors,
        sector_heat=sector_heat,
        candidate_pool=pool,
        market_news=market_news,
        topic_briefs=topic_briefs,
        budget_yuan=budget,
    )

    progress("generating")
    report = DiscoveryClient().generate_report(
        target_sectors=target_sectors,
        focus_sectors=list(request.focus_sectors),
        candidate_pool=pool,
        discovery_facts=discovery_facts,
        profile=request.profile,
        held_codes=held_codes,
        budget_yuan=budget,
        sector_heat=sector_heat,
        market_news=market_news,
        topic_briefs=topic_briefs,
        analysis_mode=request.analysis_mode,
    )
    progress("guarding")
    progress("saving")
    save_discovery_report(report)
    return report
