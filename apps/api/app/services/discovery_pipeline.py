from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging

from app.config import get_settings
from app.database import save_discovery_report
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.services.discovery_candidate_pool import (
    attach_candidate_benchmark_research,
    build_candidate_pool,
    enrich_candidates,
    finalize_candidate_pool,
)
from app.services.benchmark_mapping_service import load_decision_benchmark_specs
from app.services.fund_benchmark_research import (
    attach_fund_benchmark_metrics,
    build_fund_benchmark_research_batch,
    summarize_benchmark_research,
)
from app.services.fund_vehicle_quality import assess_candidate_vehicle_quality_batch
from app.services.discovery_client import DiscoveryClient
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_sector_opportunity import (
    build_sector_divergence_map_for_opportunities,
    build_sector_flow_map_for_opportunities,
    select_sector_opportunities,
)
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.discovery_sector_position import (
    build_sector_position_map_for_opportunities,
)
from app.services.discovery_sector_prefilter import select_opportunity_evidence_labels
from app.services.mainline_regime import (
    build_mainline_regime_snapshot,
    mainline_regime_by_label,
)
from app.services.discovery_target_sectors import select_target_sectors
from app.services.fund_discovery_data_cache import (
    fetch_discovery_fund_universe_cached,
)
from app.services.analysis_runtime import (
    limit_news_topics_for_runtime,
    resolve_analysis_runtime,
)
from app.services.news_service import (
    NewsService,
    announcement_fetch_facts,
    merge_market_news_with_announcements,
)
from app.services.news_summarizer import summarize_all_topics
from app.services.risk import resolve_weight_denominator
from app.services.decision_data_evidence import (
    attach_discovery_data_evidence,
    resolve_portfolio_preflight,
)
from app.services.decision_clock import capture_decision_clock
from app.services.decision_time_call import (
    call_with_optional_time,
    prefetch_fund_announcements_compat,
)

from app.services.discovery_strategy import discovery_minimum_holding_days
from app.services.candidate_selection_audit import (
    build_pipeline_candidate_selection_audit_v2,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str], None]

DISCOVERY_JOB_STAGES: dict[str, str] = {
    "queued": "排队中…",
    "connected": "连接已建立…",
    "sector_heat": "计算板块热度…",
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
    runtime = resolve_analysis_runtime(get_settings(), request.analysis_mode)

    def progress(stage: str) -> None:
        if on_progress is not None:
            on_progress(stage, DISCOVERY_JOB_STAGES.get(stage, stage))

    # Prepare and pin the catalogue before freezing the one logical decision
    # clock. A cold fetch completed after decision_at would otherwise be
    # correctly rejected by the PIT peer gate and collapse every cohort to 0.
    progress("connected")
    prepared_universe_rows = fetch_discovery_fund_universe_cached(limit=20_000)
    decision_clock = capture_decision_clock()
    decision_at = decision_clock.decision_at

    preflight = resolve_portfolio_preflight(
        request.holdings,
        allow_stale=request.allow_stale_portfolio_snapshot,
        now=decision_at,
    )
    request = request.model_copy(
        update={
            "holdings": preflight.holdings,
            "portfolio_snapshot_context": preflight.context,
        }
    )
    holdings = list(request.holdings)
    progress("sector_heat")
    sector_heat = call_with_optional_time(
        build_sector_heat_ranking,
        keyword="decision_at",
        decision_at=decision_at,
    )
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
    effective_trade_date = str(
        build_trading_session(decision_at).get("effective_trade_date") or ""
    ).strip() or None
    # M1.4：量价背离历史回测（confidence 升级判定的证据来源），仅对候选方向拉取，
    # best-effort，任一板块失败/超时不影响其他板块或整体扫描。
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="discovery-sector") as executor:
        flow_future = executor.submit(
            build_sector_flow_map_for_opportunities,
            sector_heat,
            flow_labels,
            trade_date=effective_trade_date,
        )
        divergence_future = executor.submit(
            build_sector_divergence_map_for_opportunities,
            flow_labels,
        )
        position_future = executor.submit(
            build_sector_position_map_for_opportunities,
            flow_labels,
            as_of_trade_date=effective_trade_date,
        )
        sector_flow_by_label = flow_future.result()
        sector_divergence_by_label = divergence_future.result()
        sector_position_by_label = position_future.result()
    mainline_snapshot = build_mainline_regime_snapshot(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        sector_position_by_label=sector_position_by_label,
        sector_labels=flow_labels,
        decision_at=decision_at,
    )
    mainline_by_label = mainline_regime_by_label(mainline_snapshot)
    sector_opportunities = select_sector_opportunities(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        sector_divergence_by_label=sector_divergence_by_label,
        mainline_by_label=mainline_by_label,
        focus_sectors=list(request.focus_sectors),
        max_total=8,
        momentum_slots=4,
        setup_slots=4,
    )
    if request.scan_mode == "full_market" and sector_opportunities:
        target_sectors = [str(item["sector_label"]) for item in sector_opportunities]
    per_sector = 3
    pool_cap = 28
    prescreen_per_sector = per_sector + 1
    prescreen_pool_cap = pool_cap + max(4, min(len(target_sectors), 8))
    held_codes = {h.fund_code.strip().zfill(6) for h in holdings if h.fund_code}

    selection_strategy = "balanced"
    progress("candidate_pool")
    recall_audit: dict = {}
    pool = call_with_optional_time(
        build_candidate_pool,
        target_sectors,
        keyword="decision_at",
        decision_at=decision_at,
        exclude_codes=held_codes,
        fund_type_preference="any",
        selection_strategy=selection_strategy,
        prepared_universe_rows=prepared_universe_rows,
        per_sector=prescreen_per_sector,
        pool_cap=prescreen_pool_cap,
        sector_opportunities=sector_opportunities,
        recall_audit_sink=recall_audit,
    )
    pool = call_with_optional_time(
        enrich_candidates,
        pool,
        keyword="decision_at",
        decision_at=decision_at,
    )
    candidate_selection_audit_v1: dict = {}
    candidate_selection_stages: dict = {}
    pool = finalize_candidate_pool(
        pool,
        target_sectors,
        per_sector=per_sector,
        pool_cap=pool_cap,
        minimum_holding_days=discovery_minimum_holding_days(
            request.discovery_strategy,
            request.profile,
        ),
        discovery_strategy=request.discovery_strategy,
        audit_sink=candidate_selection_audit_v1,
        stage_audit_sink=candidate_selection_stages,
    )
    candidate_selection_audit = build_pipeline_candidate_selection_audit_v2(
        decision_at=decision_at,
        recall_snapshot=recall_audit,
        gate_candidates=candidate_selection_stages.get("gate_candidates") or [],
        prescreen_candidates=candidate_selection_stages.get("prescreen_candidates") or [],
        final_candidates=candidate_selection_stages.get("final_candidates") or pool,
    )
    benchmark_specs = load_decision_benchmark_specs(
        [item.get("fund_code") for item in pool],
        decision_at=decision_at,
    )
    pool = attach_candidate_benchmark_research(
        pool,
        benchmark_specs,
        decision_at=decision_at,
    )
    benchmark_metrics = build_fund_benchmark_research_batch(
        pool,
        decision_at=decision_at,
    )
    pool = attach_fund_benchmark_metrics(pool, benchmark_metrics)
    pool = assess_candidate_vehicle_quality_batch(pool)

    progress("news")
    news_service = NewsService()
    topics = list(dict.fromkeys(target_sectors + request.focus_sectors))
    if not topics:
        topics = ["上证指数"]
    topics = limit_news_topics_for_runtime(topics, runtime)
    market_news = call_with_optional_time(
        news_service.prefetch_topics,
        topics,
        keyword="now",
        decision_at=decision_at,
    )
    announcement_result = prefetch_fund_announcements_compat(
        news_service,
        [str(item.get("fund_code") or "") for item in pool[:12]],
        decision_at=decision_at,
    )
    market_news = merge_market_news_with_announcements(
        market_news,
        list(announcement_result.get("items") or []),
        now=decision_at,
    )
    announcement_meta = announcement_fetch_facts(announcement_result)
    topic_briefs = call_with_optional_time(
        summarize_all_topics,
        market_news,
        keyword="now",
        decision_at=decision_at,
        offline_only=True,
    )

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
        discovery_strategy=request.discovery_strategy,
        focus_sectors=list(request.focus_sectors),
        fund_type_preference="any",
        sector_opportunities=sector_opportunities,
        sector_flow_by_label=sector_flow_by_label,
        mainline_snapshot=mainline_snapshot,
        budget_enhancements=True,
        decision_at=decision_at,
    )
    discovery_facts["fund_announcements"] = announcement_meta
    discovery_facts["candidate_selection_audit"] = candidate_selection_audit
    discovery_facts["candidate_selection_audit_v1"] = candidate_selection_audit_v1
    discovery_facts["benchmark_specs"] = benchmark_specs
    discovery_facts["benchmark_contract"] = {
        "schema_version": "fund_benchmark_mapping.v1",
        "lookup_policy": "cached_point_in_time_before_generation",
        "formal_excess_policy": "verified_fund_contract_only",
        "available_count": sum(
            1 for spec in benchmark_specs.values() if spec.get("tier") != "unavailable"
        ),
        "unavailable_count": sum(
            1 for spec in benchmark_specs.values() if spec.get("tier") == "unavailable"
        ),
    }
    discovery_facts["benchmark_research"] = benchmark_metrics
    discovery_facts["benchmark_research_contract"] = summarize_benchmark_research(
        benchmark_metrics
    )
    discovery_facts = attach_discovery_data_evidence(
        discovery_facts,
        holdings=holdings,
        candidate_pool=pool,
        portfolio_context=request.portfolio_snapshot_context,
    )

    progress("generating")
    role_prompt = request.system_role_prompt
    client = DiscoveryClient()
    report = client.generate_report(
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
        decision_at=decision_at,
        announcement_meta=announcement_meta,
    )
    progress("guarding")
    progress("saving")
    saved = save_discovery_report(report)
    # Tests and compatible third-party adapters may provide a minimal
    # DiscoveryClient replacement that predates the optional shadow channel.
    # The champion report must remain authoritative in that case.
    capture = getattr(client, "_prompt_shadow_capture", None)
    if capture is not None:
        try:
            from app.services.prompt_shadow_service import (
                finalize_prompt_shadow_champion,
            )

            trace = capture.trace_collector.require_trace()
            category = str(trace.get("error_category") or "")
            if client._last_report_parsed_payload is not None:
                parse_status = "valid"
            elif category == "empty_content":
                parse_status = "empty"
            elif trace.get("outcome") == "timeout":
                parse_status = "timeout"
            elif trace.get("outcome") == "http_error":
                parse_status = "http_error"
            elif trace.get("outcome") in {"transport_error", "interrupted"}:
                parse_status = "provider_error"
            else:
                parse_status = "invalid"
            finalize_prompt_shadow_champion(
                capture=capture,
                report=saved,
                parse_status=parse_status,
                raw_content=client._last_report_raw_content,
                parsed_payload=client._last_report_parsed_payload,
            )
        except Exception:  # noqa: BLE001 - saved champion is authoritative
            logger.exception("prompt-shadow champion evidence finalization deferred")
    return saved


def _opportunity_flow_labels(
    sector_heat: list[dict],
    target_sectors: list[str],
    focus_sectors: list[str],
) -> list[str]:
    return select_opportunity_evidence_labels(
        sector_heat,
        target_sectors,
        focus_sectors,
    )
