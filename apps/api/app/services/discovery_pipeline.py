from __future__ import annotations

from collections.abc import Callable

from app.database import save_discovery_report
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.services.discovery_candidate_pool import build_candidate_pool, enrich_candidates
from app.services.discovery_client import DiscoveryClient
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_sector_opportunity import (
    build_sector_flow_map_for_opportunities,
    select_sector_opportunities,
)
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.discovery_target_sectors import select_target_sectors
from app.services.news_service import NewsService
from app.services.news_summarizer import summarize_all_topics
from app.services.risk import resolve_weight_denominator

ProgressCallback = Callable[[str, str], None]

DISCOVERY_JOB_STAGES: dict[str, str] = {
    "queued": "排队中…",
    "connected": "连接已建立…",
    "sector_heat": "计算板块热度…",
    "dip_prescreen": "预筛大跌基金…",
    "candidate_pool": "构建候选基金池…",
    "news": "拉取市场要闻…",
    "generating": "AI 分析中…",
    "guarding": "校验推荐结果…",
    "salvage": "流式中断，已收集部分内容…",
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
    sector_heat = build_sector_heat_ranking(include_5d=(request.scan_mode == "dip_swing"))
    target_sectors = select_target_sectors(
        holdings,
        request.focus_sectors,
        sector_heat,
        request.profile,
        scan_mode=request.scan_mode,
    )
    flow_labels = _opportunity_flow_labels(
        sector_heat,
        target_sectors,
        list(request.focus_sectors),
    )
    sector_flow_by_label = build_sector_flow_map_for_opportunities(
        sector_heat,
        flow_labels,
    )
    sector_opportunities = select_sector_opportunities(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        focus_sectors=list(request.focus_sectors),
        max_total=8,
        momentum_slots=4,
        setup_slots=4,
    )
    if sector_opportunities:
        target_sectors = [str(item["sector_label"]) for item in sector_opportunities]
    per_sector = 3 if request.scan_mode == "full_market" else 5
    pool_cap = 28
    held_codes = {h.fund_code.strip().zfill(6) for h in holdings if h.fund_code}

    selection_strategy = request.selection_strategy
    if request.scan_mode == "dip_swing":
        selection_strategy = "dip_rebound"

    if request.scan_mode == "dip_swing":
        progress("dip_prescreen")
        from app.services.dip_drop_scanner import build_dip_pool_for_sectors

        pool = build_dip_pool_for_sectors(
            target_sectors,
            lookback_days=request.dip_lookback_days,
            min_drop_percent=request.dip_min_drop_percent,
            exclude_codes=held_codes,
        )
        pool = enrich_candidates(pool)
    else:
        progress("candidate_pool")
        pool = build_candidate_pool(
            target_sectors,
            exclude_codes=held_codes,
            fund_type_preference=request.fund_type_preference,
            selection_strategy=selection_strategy,
            per_sector=per_sector,
            pool_cap=pool_cap,
            sector_opportunities=sector_opportunities,
        )
        pool = enrich_candidates(pool)

    progress("news")
    news_service = NewsService()
    topics = list(dict.fromkeys(target_sectors + request.focus_sectors))
    if not topics:
        topics = ["上证指数"]
    market_news = news_service.prefetch_topics(topics)
    topic_briefs = summarize_all_topics(market_news, offline_only=True)

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
        selection_strategy=selection_strategy,
        scan_mode=request.scan_mode,
        dip_lookback_days=request.dip_lookback_days,
        dip_min_drop_percent=request.dip_min_drop_percent,
        focus_sectors=list(request.focus_sectors),
        fund_type_preference=request.fund_type_preference,
        sector_opportunities=sector_opportunities,
        budget_enhancements=True,
    )

    progress("generating")
    role_prompt = request.system_role_prompt
    report = DiscoveryClient().generate_report(
        target_sectors=target_sectors,
        focus_sectors=list(request.focus_sectors),
        scan_mode=request.scan_mode,
        candidate_pool=pool,
        discovery_facts=discovery_facts,
        profile=request.profile,
        held_codes=held_codes,
        budget_yuan=budget,
        sector_heat=sector_heat,
        market_news=market_news,
        topic_briefs=topic_briefs,
        analysis_mode=request.analysis_mode,
        system_role_prompt=role_prompt,
    )
    progress("guarding")
    progress("saving")
    save_discovery_report(report)
    return report


def _opportunity_flow_labels(
    sector_heat: list[dict],
    target_sectors: list[str],
    focus_sectors: list[str],
) -> list[str]:
    labels = list(dict.fromkeys([*target_sectors, *focus_sectors]))
    for row in sorted(
        sector_heat,
        key=lambda item: float(item.get("heat_score") or -999),
        reverse=True,
    )[:12]:
        label = str(row.get("sector_label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels[:16]
