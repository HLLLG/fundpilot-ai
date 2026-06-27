"""阶段 4.2：荐基 SSE 流式生成器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
import time
from typing import Any

import httpx

from app.config import get_settings
from app.database import save_discovery_report
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.analysis_runtime import resolve_analysis_runtime
from app.services.deepseek_client import _parse_model_json
from app.services.deepseek_streaming import stream_chat_completion
from app.services.discovery_candidate_pool import build_candidate_pool, enrich_candidates
from app.services.discovery_client import (
    DiscoveryClient,
    build_discovery_chat_messages,
    build_discovery_report_from_parsed,
)
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_pipeline import DISCOVERY_JOB_STAGES
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.discovery_target_sectors import select_target_sectors
from app.services.news_service import NewsService
from app.services.news_summarizer import summarize_all_topics
from app.services.pipeline_concurrency import run_with_request_user
from app.services.risk import resolve_weight_denominator
from app.services.streaming_json_parser import StreamingReportParser
from app.services.discovery_payload import append_output_requirements_to_system, build_user_payload
from app.services.news_summarizer import merge_topic_briefs


def stream_discovery(request: DiscoveryRequest, *, user_id: int) -> Iterator[dict[str, Any]]:
    ctx_token = set_request_user_id(user_id)
    settings = get_settings()
    started_at = time.monotonic()
    try:
        holdings = list(request.holdings)
        yield _stage("connected", started_at=started_at)
        yield _stage("sector_heat", started_at=started_at)
        sector_heat = build_sector_heat_ranking(include_5d=(request.scan_mode == "dip_swing"))
        target_sectors = select_target_sectors(
            holdings,
            request.focus_sectors,
            sector_heat,
            request.profile,
            scan_mode=request.scan_mode,
        )
        per_sector = 3 if request.scan_mode == "full_market" else 5
        pool_cap = 25
        held_codes = {h.fund_code.strip().zfill(6) for h in holdings if h.fund_code}

        selection_strategy = request.selection_strategy
        if request.scan_mode == "dip_swing":
            selection_strategy = "dip_rebound"

        topics = list(dict.fromkeys(target_sectors + list(request.focus_sectors)))
        if not topics:
            topics = ["上证指数"]
        news_service = NewsService()
        yield _stage("news", started_at=started_at)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="discovery-prep") as executor:
            news_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: news_service.prefetch_topics(topics),
            )
            if request.scan_mode == "dip_swing":
                yield _stage("dip_prescreen", started_at=started_at)
                from app.services.dip_drop_scanner import build_dip_pool_for_sectors

                pool = build_dip_pool_for_sectors(
                    target_sectors,
                    lookback_days=request.dip_lookback_days,
                    min_drop_percent=request.dip_min_drop_percent,
                    exclude_codes=held_codes,
                )
                pool = enrich_candidates(pool)
            else:
                yield _stage("candidate_pool", started_at=started_at)
                pool = build_candidate_pool(
                    target_sectors,
                    exclude_codes=held_codes,
                    fund_type_preference=request.fund_type_preference,
                    selection_strategy=selection_strategy,
                    per_sector=per_sector,
                    pool_cap=pool_cap,
                )
                pool = enrich_candidates(pool)
            market_news = news_future.result()

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
        )

        if not settings.deepseek_configured:
            report = build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=pool,
                discovery_facts=discovery_facts,
                profile=request.profile,
                focus_sectors=list(request.focus_sectors),
                analysis_mode=request.analysis_mode,
            )
            yield _stage("saving", started_at=started_at)
            save_discovery_report(report)
            yield _done(report)
            return

        yield _stage("generating", started_at=started_at)
        runtime = resolve_analysis_runtime(settings, request.analysis_mode)
        client = DiscoveryClient()
        user_payload = build_user_payload(
            discovery_facts=discovery_facts,
            profile=request.profile,
            focus_sectors=list(request.focus_sectors),
            scan_mode=request.scan_mode,
            market_news=market_news,
            topic_briefs=topic_briefs,
            analysis_mode=request.analysis_mode,
        )
        system_prompt = append_output_requirements_to_system(
            client._system_prompt(runtime.news_tool_max_rounds > 0, request.system_role_prompt)
        )
        initial_news_count = len(market_news)
        pending_stages: list[tuple[str, str]] = []

        if runtime.news_tool_max_rounds > 0:
            messages, market_news = client.run_discovery_news_tool_rounds(
                system_prompt=system_prompt,
                user_payload=user_payload,
                prefetched_news=market_news,
                runtime=runtime,
                on_stage=lambda stage, label: pending_stages.append((stage, label)),
            )
            for stage, label in pending_stages:
                yield _stage(stage, label, started_at=started_at)
            if len(market_news) > initial_news_count:
                topic_briefs = merge_topic_briefs(topic_briefs, market_news, settings)
        else:
            messages = build_discovery_chat_messages(system_prompt, user_payload)
        parser = StreamingReportParser(
            array_field="recommendations",
            item_partial_field="recommendation",
        )
        all_chunks: list[str] = []

        try:
            for chunk in stream_chat_completion(
                messages=messages,
                model=runtime.model,
                max_tokens=settings.deepseek_max_tokens_report,
                response_format={"type": "json_object"},
            ):
                all_chunks.append(chunk)
                yield {"type": "token", "content": chunk}
                for partial in parser.feed(chunk):
                    yield partial
            parsed = _parse_model_json("".join(all_chunks))
        except (httpx.StreamError, httpx.ReadTimeout, httpx.HTTPError) as exc:
            if all_chunks:
                yield _stage("salvage", "流式中断，已收集部分内容…", started_at=started_at)
                parsed = _parse_model_json("".join(all_chunks))
            else:
                yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                return

        yield _stage("guarding", started_at=started_at)
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
        )
        yield _stage("saving", started_at=started_at)
        save_discovery_report(report)
        yield _done(report)
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        reset_request_user_id(ctx_token)


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


def _done(report: FundDiscoveryReport) -> dict[str, Any]:
    return {
        "type": "done",
        "report_id": report.id,
        "report": report.model_dump(mode="json"),
    }
