"""阶段 4.2：荐基 SSE 流式生成器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Iterator
from copy import deepcopy
import logging
import time
from typing import Any

import httpx

from app.config import get_settings
from app.database import save_discovery_report
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.analysis_runtime import (
    limit_news_topics_for_runtime,
    resolve_analysis_runtime,
)
from app.services.deepseek_client import (
    _is_usable_interrupted_response,
    _is_valid_discovery_report_payload,
    _parse_model_json,
)
from app.services.deepseek_streaming import stream_chat_completion
from app.services.deepseek_http import ProviderOutputError, classify_deepseek_failure
from app.services.provider_call_trace import (
    ProviderCallTraceCollector,
    attach_provider_call_trace,
)
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
from app.services.discovery_client import (
    DiscoveryClient,
    build_discovery_chat_messages,
    build_discovery_prompt_provenance,
    build_discovery_report_from_parsed,
)
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_judge import judge_parsed_discovery_report
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_pipeline import DISCOVERY_JOB_STAGES
from app.services.discovery_sector_opportunity import (
    build_sector_divergence_map_for_opportunities,
    build_sector_flow_map_for_opportunities,
    select_sector_opportunities,
)
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.discovery_sector_position import (
    build_sector_position_map_for_opportunities,
)
from app.services.mainline_regime import (
    build_mainline_regime_snapshot,
    mainline_regime_by_label,
)
from app.services.discovery_target_sectors import select_target_sectors
from app.services.news_service import (
    NewsService,
    announcement_fetch_facts,
    merge_market_news_with_announcements,
)
from app.services.news_summarizer import summarize_all_topics
from app.services.pipeline_concurrency import run_with_request_user
from app.services.risk import resolve_weight_denominator
from app.services.streaming_heartbeat import Heartbeat, iter_with_heartbeat
from app.services.discovery_payload import append_output_requirements_to_system, build_user_payload
from app.services.decision_data_evidence import (
    attach_discovery_data_evidence,
    resolve_portfolio_preflight,
)
from app.services.report_pipeline import build_pipeline_metadata
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
PREP_HEARTBEAT_SECONDS = 1.0
# LLM 首个 token 到达前若长时间无输出，网关（如腾讯云开发 CloudBase）会在 SSE
# 连接空闲约 60s 后主动断开（ERR_ABORT_HANDLER）。深度模式下模型思考耗时可能
# 逼近甚至超过该阈值，因此需要更短的心跳间隔持续产出字节，防止连接被判定空闲。
LLM_HEARTBEAT_SECONDS = 12.0


def stream_discovery(request: DiscoveryRequest, *, user_id: int) -> Iterator[dict[str, Any]]:
    ctx_token = set_request_user_id(user_id)
    settings = get_settings()
    decision_clock = capture_decision_clock()
    decision_at = decision_clock.decision_at
    runtime = resolve_analysis_runtime(settings, request.analysis_mode)
    started_at = time.monotonic()
    try:
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
        yield _stage("connected", started_at=started_at)
        yield _stage("sector_heat", started_at=started_at)
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
        news_service = NewsService()
        per_sector = 3
        pool_cap = 28
        held_codes = {h.fund_code.strip().zfill(6) for h in holdings if h.fund_code}

        selection_strategy = "balanced"
        recall_audit: dict = {}
        candidate_selection_audit_v1: dict = {}
        candidate_selection_stages: dict = {}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="discovery-prep") as executor:
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
            sector_flow_by_label = yield from _await_future_with_progress(
                flow_future,
                "sector_heat",
                "正在补充板块资金流…",
                started_at=started_at,
            )
            sector_divergence_by_label = yield from _await_future_with_progress(
                divergence_future,
                "sector_heat",
                "正在校验板块信号历史表现…",
                started_at=started_at,
            )
            sector_position_by_label = yield from _await_future_with_progress(
                position_future,
                "sector_heat",
                "正在计算板块多周期相对强度…",
                started_at=started_at,
            )
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
            prescreen_per_sector = per_sector + 1
            prescreen_pool_cap = pool_cap + max(4, min(len(target_sectors), 8))
            topics = list(dict.fromkeys(target_sectors + list(request.focus_sectors)))
            if not topics:
                topics = ["上证指数"]
            topics = limit_news_topics_for_runtime(topics, runtime)
            news_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: call_with_optional_time(
                    news_service.prefetch_topics,
                    topics,
                    keyword="now",
                    decision_at=decision_at,
                ),
            )
            yield _stage("news", started_at=started_at)
            yield _stage("candidate_pool", started_at=started_at)
            pool_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: finalize_candidate_pool(
                    call_with_optional_time(
                        enrich_candidates,
                        call_with_optional_time(
                            build_candidate_pool,
                            target_sectors,
                            keyword="decision_at",
                            decision_at=decision_at,
                            exclude_codes=held_codes,
                            fund_type_preference="any",
                            selection_strategy=selection_strategy,
                            per_sector=prescreen_per_sector,
                            pool_cap=prescreen_pool_cap,
                            sector_opportunities=sector_opportunities,
                            recall_audit_sink=recall_audit,
                        ),
                        keyword="decision_at",
                        decision_at=decision_at,
                    ),
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
                ),
            )
            pool = yield from _await_future_with_progress(
                pool_future,
                "candidate_pool",
                "正在优选候选基金…",
                started_at=started_at,
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
            market_news = yield from _await_future_with_progress(
                news_future,
                "news",
                "正在拉取市场要闻…",
                started_at=started_at,
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
        announcement_meta = announcement_fetch_facts(
            announcement_result
        )

        fund_codes = [
            str(item.get("fund_code", "")).strip().zfill(6)
            for item in pool[:12]
            if item.get("fund_code")
        ]
        fund_names = [str(item.get("fund_name") or code) for item, code in zip(pool[:12], fund_codes)]
        yield {
            "type": "skeleton",
            "fund_codes": fund_codes,
            "fund_names": fund_names,
        }

        topic_briefs = call_with_optional_time(
            summarize_all_topics,
            market_news,
            keyword="now",
            decision_at=decision_at,
            offline_only=True,
        )
        yield _stage("generating", "正在整理荐基上下文…", started_at=started_at)

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
                1
                for spec in benchmark_specs.values()
                if spec.get("tier") != "unavailable"
            ),
            "unavailable_count": sum(
                1
                for spec in benchmark_specs.values()
                if spec.get("tier") == "unavailable"
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
        base_pipeline = build_pipeline_metadata(
            runtime=runtime,
            market_news=market_news,
            topic_briefs=topic_briefs,
            judge_meta={},
        )
        base_pipeline.update(
            {
                "provider": "offline" if not settings.deepseek_configured else "deepseek",
                "provider_status": "offline" if not settings.deepseek_configured else "pending",
                "attempted_model": None if not settings.deepseek_configured else runtime.model,
            }
        )
        discovery_facts["pipeline"] = base_pipeline

        if not settings.deepseek_configured:
            report = build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=pool,
                discovery_facts=discovery_facts,
                profile=request.profile,
                focus_sectors=list(request.focus_sectors),
                analysis_mode=request.analysis_mode,
                decision_at=decision_at,
            )
            yield _stage("saving", started_at=started_at)
            report = save_discovery_report(report)
            yield _done(report)
            return

        yield _stage("generating", started_at=started_at)
        client = DiscoveryClient()
        user_payload = build_user_payload(
            discovery_facts=discovery_facts,
            profile=request.profile,
            focus_sectors=list(request.focus_sectors),
            scan_mode=request.scan_mode,
            market_news=market_news,
            topic_briefs=topic_briefs,
            analysis_mode=request.analysis_mode,
            fund_type_preference="any",
        )
        system_prompt = append_output_requirements_to_system(
            client._system_prompt(
                runtime.news_tool_max_rounds > 0,
                request.system_role_prompt,
                session=discovery_facts.get("session"),
            )
        )
        messages = build_discovery_chat_messages(system_prompt, user_payload)
        shadow_capture = None
        try:
            from app.services.discovery_prompt import DEFAULT_DISCOVERY_ROLE_PROMPT
            from app.services.prompt_shadow_service import (
                PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT,
                prepare_prompt_shadow_champion,
            )

            challenger_system_prompt = append_output_requirements_to_system(
                client._system_prompt(
                    False,
                    PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT,
                    session=discovery_facts.get("session"),
                )
            )
            shadow_capture = prepare_prompt_shadow_champion(
                user_id=user_id,
                transport="stream",
                champion_system_prompt=system_prompt,
                challenger_system_prompt=challenger_system_prompt,
                user_payload=user_payload,
                model=runtime.model,
                max_tokens=settings.deepseek_max_tokens_report,
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
                decision_at=decision_at,
                default_prompt_only=(
                    request.system_role_prompt is None
                    or request.system_role_prompt == DEFAULT_DISCOVERY_ROLE_PROMPT
                ),
                news_tool_rounds=runtime.news_tool_max_rounds,
            )
        except Exception:  # noqa: BLE001 - champion stream must remain fail-open
            logger.exception("prompt-shadow streaming preregistration skipped")
            shadow_capture = None
        provider_trace_collector = (
            shadow_capture.trace_collector
            if shadow_capture is not None
            else ProviderCallTraceCollector(transport="stream")
        )
        attempted_prompt_contract = build_discovery_prompt_provenance(
            role_prompt=request.system_role_prompt,
            messages=messages,
            user_payload=user_payload,
            runtime=runtime,
            judge_meta={},
        )
        all_chunks: list[str] = []
        stream_interrupted = False
        stream_arguments: dict[str, Any] = {
            "messages": messages,
            "model": runtime.model,
            "max_tokens": settings.deepseek_max_tokens_report,
            "response_format": {"type": "json_object"},
            "trace_collector": provider_trace_collector,
        }

        try:
            for entry in iter_with_heartbeat(
                stream_chat_completion(**stream_arguments),
                heartbeat_seconds=LLM_HEARTBEAT_SECONDS,
                heartbeat_factory=lambda: _stage(
                    "generating", "AI 分析中…", started_at=started_at
                ),
            ):
                if isinstance(entry, Heartbeat):
                    yield entry.value
                    continue
                chunk = entry
                all_chunks.append(chunk)
                # Raw model fragments are never SSE output.  The discovery
                # report becomes visible only after judge + deterministic guards.
            parsed = _parse_model_json("".join(all_chunks))
        except (httpx.StreamError, httpx.ReadTimeout, httpx.HTTPError) as exc:
            if all_chunks:
                interrupted_content = "".join(all_chunks)
                candidate = _parse_model_json(interrupted_content)
                if _is_usable_interrupted_response(
                    interrupted_content,
                    candidate,
                    report_kind="discovery",
                ):
                    stream_interrupted = True
                    yield _stage("salvage", "流式中断，已收集部分内容…", started_at=started_at)
                    parsed = candidate
                else:
                    failure = classify_deepseek_failure(exc)
                    provider_trace = provider_trace_collector.trace
                    if provider_trace is not None:
                        attach_provider_call_trace(discovery_facts, provider_trace)
                    report = build_offline_discovery_report(
                        target_sectors=target_sectors,
                        candidate_pool=pool,
                        discovery_facts=discovery_facts,
                        profile=request.profile,
                        focus_sectors=list(request.focus_sectors),
                        analysis_mode=request.analysis_mode,
                        provider_failure=failure,
                        attempted_model=runtime.model,
                        prompt_contract=attempted_prompt_contract,
                        decision_at=decision_at,
                    )
                    yield _stage("saving", started_at=started_at)
                    report = save_discovery_report(report)
                    _finalize_stream_shadow(
                        shadow_capture,
                        report,
                        parse_status="provider_error",
                        raw_content=interrupted_content,
                        parsed_payload=None,
                    )
                    yield _done(report)
                    return
            else:
                failure = classify_deepseek_failure(exc)
                provider_trace = provider_trace_collector.trace
                if provider_trace is not None:
                    attach_provider_call_trace(discovery_facts, provider_trace)
                report = build_offline_discovery_report(
                    target_sectors=target_sectors,
                    candidate_pool=pool,
                    discovery_facts=discovery_facts,
                    profile=request.profile,
                    focus_sectors=list(request.focus_sectors),
                    analysis_mode=request.analysis_mode,
                    provider_failure=failure,
                    attempted_model=runtime.model,
                    prompt_contract=attempted_prompt_contract,
                    decision_at=decision_at,
                )
                yield _stage("saving", started_at=started_at)
                report = save_discovery_report(report)
                _finalize_stream_shadow(
                    shadow_capture,
                    report,
                    parse_status="provider_error",
                    raw_content=None,
                    parsed_payload=None,
                )
                yield _done(report)
                return

        if not all_chunks or (
            not stream_interrupted
            and (
                parsed.get("_truncated")
                or not _is_valid_discovery_report_payload(parsed)
            )
        ):
            category = "empty_content" if not all_chunks else "invalid_json"
            failure = classify_deepseek_failure(ProviderOutputError(category))
            provider_trace = provider_trace_collector.trace
            if provider_trace is not None:
                attach_provider_call_trace(discovery_facts, provider_trace)
            report = build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=pool,
                discovery_facts=discovery_facts,
                profile=request.profile,
                focus_sectors=list(request.focus_sectors),
                analysis_mode=request.analysis_mode,
                provider_failure=failure,
                attempted_model=runtime.model,
                prompt_contract=attempted_prompt_contract,
                decision_at=decision_at,
            )
            yield _stage("saving", started_at=started_at)
            report = save_discovery_report(report)
            _finalize_stream_shadow(
                shadow_capture,
                report,
                parse_status="empty" if not all_chunks else "invalid",
                raw_content="".join(all_chunks) if all_chunks else None,
                parsed_payload=None,
            )
            yield _done(report)
            return

        raw_parsed = deepcopy(parsed)
        if stream_interrupted:
            trace = provider_trace_collector.trace
            if trace is not None and trace["outcome"] == "interrupted":
                provider_trace_collector.mark_interrupted_salvaged()
        yield _stage("guarding", started_at=started_at)
        # M4：deep 模式风控复核角色（fast 模式内部直接短路返回，零新增 LLM 调用）。
        parsed, judge_meta = judge_parsed_discovery_report(
            parsed,
            candidate_pool=pool,
            discovery_facts=discovery_facts,
            analysis_mode=request.analysis_mode,
        )
        prompt_contract = build_discovery_prompt_provenance(
            role_prompt=request.system_role_prompt,
            messages=messages,
            user_payload=user_payload,
            runtime=runtime,
            judge_meta=judge_meta,
        )
        pipeline = build_pipeline_metadata(
            runtime=runtime,
            market_news=market_news,
            topic_briefs=topic_briefs,
            judge_meta=judge_meta,
        )
        pipeline.update(
            {
                "provider": "deepseek",
                "provider_status": "success",
                "attempted_model": runtime.model,
                "prompt_contract": prompt_contract,
            }
        )
        provider_trace = provider_trace_collector.trace
        if provider_trace is not None:
            pipeline["provider_call_trace"] = provider_trace
        discovery_facts["pipeline"] = pipeline
        report = build_discovery_report_from_parsed(
            parsed,
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
            provider_model=runtime.model,
            decision_at=decision_at,
        )
        yield _stage("saving", started_at=started_at)
        report = save_discovery_report(report)
        _finalize_stream_shadow(
            shadow_capture,
            report,
            parse_status=("interrupted_salvaged" if stream_interrupted else "valid"),
            raw_content="".join(all_chunks),
            parsed_payload=raw_parsed,
        )
        yield _done(report)
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        reset_request_user_id(ctx_token)


def _finalize_stream_shadow(
    capture: Any,
    report: FundDiscoveryReport,
    *,
    parse_status: str,
    raw_content: str | None,
    parsed_payload: dict[str, Any] | None,
) -> None:
    if capture is None:
        return
    try:
        from app.services.prompt_shadow_service import finalize_prompt_shadow_champion

        finalize_prompt_shadow_champion(
            capture=capture,
            report=report,
            parse_status=parse_status,
            raw_content=raw_content,
            parsed_payload=parsed_payload,
        )
    except Exception:  # noqa: BLE001 - committed champion remains authoritative
        logger.exception("prompt-shadow streaming evidence finalization deferred")


def _stage(
    stage: str,
    label: str | None = None,
    *,
    started_at: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "stage",
        "stage": stage,
        "label": label or DISCOVERY_JOB_STAGES.get(stage, stage),
    }
    if started_at is not None:
        payload["elapsed_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    return payload


def _await_future_with_progress(
    future,
    stage: str,
    label: str,
    *,
    started_at: float,
) -> Iterator[dict[str, Any]]:
    while True:
        try:
            return future.result(timeout=PREP_HEARTBEAT_SECONDS)
        except FutureTimeoutError:
            yield _stage(stage, label, started_at=started_at)


def _done(report: FundDiscoveryReport) -> dict[str, Any]:
    return {
        "type": "done",
        "report_id": report.id,
        "report": report.model_dump(mode="json"),
    }


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
