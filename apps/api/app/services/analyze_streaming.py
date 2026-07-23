"""阶段 2：日报分析 SSE 流式生成器。"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from collections.abc import Iterator
import threading
import time
from typing import Any

import httpx

from app.config import get_settings
from app.database import save_report
from app.models import AnalysisRequest, Report
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.analysis_runtime import resolve_analysis_runtime
from app.services.deepseek_client import (
    JOB_STAGES,
    DeepSeekClient,
    _build_final_report,
    _daily_provider_response_incomplete,
    _is_usable_interrupted_response,
    _is_valid_daily_report_payload,
    _offline_report,
    _parse_model_json,
    build_analysis_prompt_provenance,
    build_analysis_chat_messages,
)
from app.services.deepseek_streaming import stream_chat_completion
from app.services.deepseek_http import ProviderOutputError, classify_deepseek_failure
from app.services.provider_call_trace import ProviderCallTraceCollector
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.analysis_payload import prepare_analysis_bundle
from app.services.news_service import (
    NewsService,
    announcement_fetch_facts,
    merge_market_news_with_announcements,
)
from app.services.news_summarizer import (
    build_topic_briefs_offline,
    group_news_by_topic,
    summarize_all_topics,
)
from app.services.pipeline_concurrency import run_with_request_user
from app.services.risk import evaluate_portfolio_risk
from app.services.streaming_heartbeat import (
    Heartbeat,
    StreamCancelled,
    iter_with_heartbeat,
    raise_if_stream_cancelled,
)
from app.services.report_judge import judge_parsed_report
from app.services.decision_data_evidence import resolve_portfolio_preflight
from app.services.stream_session_store import (
    create_stream_session,
    delete_stream_session,
    get_stream_session,
    set_stream_session_stage,
)
from app.services.decision_clock import capture_decision_clock
from app.services.decision_time_call import (
    call_with_optional_time,
    prefetch_fund_announcements_compat,
)
from app.services.shared_executors import (
    get_analysis_context_executor,
    get_shared_io_executor,
)

NEWS_SUMMARY_TIMEOUT_SECONDS = 8.0
NEWS_SUMMARY_HEARTBEAT_SECONDS = 1.0
CONTEXT_HEARTBEAT_SECONDS = 1.0
# 与 discovery_streaming 一致：避免 LLM 首 token 延迟过久导致 SSE 连接被网关
# （如腾讯云开发 CloudBase，约 60s 空闲阈值）判定超时中断（ERR_ABORT_HANDLER）。
LLM_HEARTBEAT_SECONDS = 12.0


def stream_analysis(
    request: AnalysisRequest,
    *,
    user_id: int,
    stop_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    """把 run_analysis 拆成可流式产出 SSE 事件的版本（fast / deep）。"""
    stop = stop_event or threading.Event()
    ctx_token = set_request_user_id(user_id)
    settings = get_settings()
    decision_clock = capture_decision_clock()
    decision_at = decision_clock.decision_at
    session = create_stream_session()
    started_at = time.monotonic()
    try:
        raise_if_stream_cancelled(stop)
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
        if not request.holdings:
            yield {"type": "error", "message": "至少需要一条基金持仓"}
            return
        raise_if_stream_cancelled(stop)
        yield {"type": "session", "session_id": session.session_id}
        resolved = FundProfileService().resolve_holdings(request.holdings)
        enriched = request.model_copy(update={"holdings": resolved})
        risk = evaluate_portfolio_risk(enriched.holdings, enriched.profile)
        runtime = resolve_analysis_runtime(settings, enriched.analysis_mode)

        yield _emit_stage(session.session_id, "fund_data", started_at=started_at)
        executor = get_shared_io_executor()
        prep_futures: list[Any] = []
        try:
            fund_data_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: _get_snapshots_with_cancel(
                    FundDataService(),
                    enriched.holdings,
                    stop,
                ),
            )
            prep_futures.append(fund_data_future)
            yield _emit_stage(session.session_id, "news_prefetch", started_at=started_at)
            news_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: call_with_optional_time(
                    NewsService().prefetch_for_holdings,
                    enriched.holdings,
                    keyword="now",
                    decision_at=decision_at,
                    max_topics=runtime.news_max_topics,
                ),
            )
            prep_futures.append(news_future)
            announcement_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: prefetch_fund_announcements_compat(
                    NewsService(),
                    [holding.fund_code for holding in enriched.holdings],
                    decision_at=decision_at,
                ),
            )
            prep_futures.append(announcement_future)
            snapshots, nav_trends = _await_future_or_cancel(fund_data_future, stop)
            market_news = _await_future_or_cancel(news_future, stop)
            announcement_result = _await_future_or_cancel(announcement_future, stop)
            market_news = merge_market_news_with_announcements(
                market_news,
                list(announcement_result.get("items") or []),
                now=decision_at,
            )
            announcement_meta = announcement_fetch_facts(
                announcement_result
            )
        finally:
            for future in prep_futures:
                future.cancel()

        raise_if_stream_cancelled(stop)
        yield _emit_stage(session.session_id, "news_summarize", started_at=started_at)
        topic_briefs, summary_timed_out = yield from _build_topic_briefs_with_progress(
            session.session_id,
            market_news,
            settings,
            started_at=started_at,
            decision_at=decision_at,
            stop_event=stop,
        )
        if summary_timed_out:
            yield _emit_stage(
                session.session_id,
                "news_summarize",
                "要闻摘要超时，已使用标题规则摘要继续…",
                started_at=started_at,
            )

        yield {
            "type": "skeleton",
            "fund_codes": [h.fund_code for h in enriched.holdings],
            "fund_names": [h.fund_name for h in enriched.holdings],
        }
        yield _emit_stage(
            session.session_id,
            "generating",
            "正在整理分析上下文…",
            started_at=started_at,
        )
        bundle = yield from _prepare_analysis_bundle_with_progress(
            session.session_id,
            user_id,
            enriched,
            risk,
            snapshots,
            market_news,
            topic_briefs,
            nav_trends,
            runtime,
            started_at=started_at,
            decision_at=decision_at,
            stop_event=stop,
        )
        bundle.facts["fund_announcements"] = dict(announcement_meta)

        if not settings.deepseek_configured:
            report = _offline_report(
                enriched,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
                analysis_bundle=bundle,
                decision_at=decision_at,
                announcement_meta=announcement_meta,
            )
            yield _emit_stage(session.session_id, "saving", started_at=started_at)
            report = save_report(report)
            yield _done(report)
            return

        yield _emit_stage(session.session_id, "generating", started_at=started_at)
        # The follow-up may have landed on another Uvicorn worker; reload the
        # database-backed session immediately before building the LLM prompt.
        latest_session = get_stream_session(session.session_id)
        operator_notes = (
            list(latest_session.operator_notes)
            if latest_session is not None
            else []
        )
        messages = build_analysis_chat_messages(
            enriched,
            risk,
            snapshots,
            market_news,
            topic_briefs,
            nav_trends,
            runtime,
            bundle,
            operator_notes=operator_notes or None,
            decision_at=decision_at,
        )
        attempted_prompt_contract = build_analysis_prompt_provenance(
            request=enriched,
            messages=messages,
            runtime=runtime,
            judge_meta={},
        )

        all_chunks: list[str] = []
        parsed: dict | None = None
        stream_interrupted = False
        provider_trace_collector = ProviderCallTraceCollector(transport="stream")

        try:
            for entry in iter_with_heartbeat(
                stream_chat_completion(
                    messages=messages,
                    model=runtime.model,
                    max_tokens=settings.deepseek_max_tokens_report,
                    response_format={"type": "json_object"},
                    trace_collector=provider_trace_collector,
                    stop_event=stop,
                ),
                heartbeat_seconds=LLM_HEARTBEAT_SECONDS,
                heartbeat_factory=lambda: _emit_stage(
                    session.session_id, "generating", "AI 分析中…", started_at=started_at
                ),
                stop_event=stop,
            ):
                if isinstance(entry, Heartbeat):
                    yield entry.value
                    continue
                chunk = entry
                all_chunks.append(chunk)
                # Do not emit raw model partials.  Title, summary, caveats, and
                # recommendation items can all carry unguarded trade advice;
                # the complete report is emitted only after judge + guards.
            parsed = _parse_model_json("".join(all_chunks))
        except (httpx.StreamError, httpx.ReadTimeout, httpx.HTTPError) as exc:
            if all_chunks:
                interrupted_content = "".join(all_chunks)
                candidate = _parse_model_json(interrupted_content)
                if _is_usable_interrupted_response(
                    interrupted_content,
                    candidate,
                    report_kind="daily",
                ):
                    stream_interrupted = True
                    yield _emit_stage(
                        session.session_id,
                        "salvage",
                        "流式中断，已收集部分内容…",
                        started_at=started_at,
                    )
                    parsed = candidate
                    parsed.setdefault("caveats", [])
                    if isinstance(parsed["caveats"], list):
                        parsed["caveats"] = list(parsed["caveats"])
                        parsed["caveats"].append(
                            f"流式传输中断（{type(exc).__name__}），部分字段可能不完整。"
                        )
                else:
                    failure = classify_deepseek_failure(exc)
                    report = _offline_report(
                        enriched,
                        risk,
                        snapshots,
                        market_news=market_news,
                        topic_briefs=topic_briefs,
                        nav_trends_by_code=nav_trends,
                        analysis_bundle=bundle,
                        provider_failure=failure,
                        attempted_model=runtime.model,
                        prompt_contract=attempted_prompt_contract,
                        provider_call_trace=provider_trace_collector.trace,
                        decision_at=decision_at,
                        announcement_meta=announcement_meta,
                    )
                    yield _emit_stage(session.session_id, "saving", started_at=started_at)
                    report = save_report(report)
                    yield _done(report)
                    return
            else:
                failure = classify_deepseek_failure(exc)
                report = _offline_report(
                    enriched,
                    risk,
                    snapshots,
                    market_news=market_news,
                    topic_briefs=topic_briefs,
                    nav_trends_by_code=nav_trends,
                    analysis_bundle=bundle,
                    provider_failure=failure,
                    attempted_model=runtime.model,
                    prompt_contract=attempted_prompt_contract,
                    provider_call_trace=provider_trace_collector.trace,
                    decision_at=decision_at,
                    announcement_meta=announcement_meta,
                )
                yield _emit_stage(session.session_id, "saving", started_at=started_at)
                report = save_report(report)
                yield _done(report)
                return

        if stream_interrupted:
            trace = provider_trace_collector.trace
            if trace is not None and trace["outcome"] == "interrupted":
                provider_trace_collector.mark_interrupted_salvaged()

        if parsed is None or not all_chunks or (
            not stream_interrupted
            and (
                parsed.get("_truncated")
                or not _is_valid_daily_report_payload(parsed)
                or _daily_provider_response_incomplete(parsed)
            )
        ):
            category = "empty_content" if not all_chunks else "invalid_json"
            failure = classify_deepseek_failure(ProviderOutputError(category))
            report = _offline_report(
                enriched,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
                analysis_bundle=bundle,
                provider_failure=failure,
                attempted_model=runtime.model,
                prompt_contract=attempted_prompt_contract,
                provider_call_trace=provider_trace_collector.trace,
                decision_at=decision_at,
                announcement_meta=announcement_meta,
            )
            yield _emit_stage(session.session_id, "saving", started_at=started_at)
            report = save_report(report)
            yield _done(report)
            return

        if parsed is None:
            yield {"type": "error", "message": "未收到 LLM 输出"}
            return

        yield _emit_stage(session.session_id, "judging", started_at=started_at)
        parsed, judge_meta = judge_parsed_report(
            parsed,
            enriched,
            risk,
            snapshots,
            runtime,
            facts=bundle.facts,
            stop_event=stop,
        )
        prompt_contract = build_analysis_prompt_provenance(
            request=enriched,
            messages=messages,
            runtime=runtime,
            judge_meta=judge_meta,
        )
        report = _build_final_report(
            parsed,
            request=enriched,
            risk=risk,
            snapshots=snapshots,
            market_news=market_news,
            topic_briefs=topic_briefs,
            nav_trends=nav_trends,
            analysis_bundle=bundle,
            judge_meta=judge_meta,
            runtime=runtime,
            prompt_contract=prompt_contract,
            provider_call_trace=provider_trace_collector.trace,
            decision_at=decision_at,
            announcement_meta=announcement_meta,
        )
        yield _emit_stage(session.session_id, "saving", started_at=started_at)
        report = save_report(report)
        yield _done(report)
    except StreamCancelled:
        return
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        delete_stream_session(session.session_id)
        reset_request_user_id(ctx_token)


def _emit_stage(
    session_id: str,
    stage: str,
    label: str | None = None,
    *,
    started_at: float | None = None,
) -> dict[str, Any]:
    set_stream_session_stage(session_id, stage)
    return _stage(stage, label, started_at=started_at)


def _build_topic_briefs(
    market_news: list[Any],
    settings: Any,
    decision_at: Any = None,
) -> list[Any]:
    return summarize_all_topics(
        market_news,
        settings,
        offline_only=True,
        now=decision_at,
    )


def _build_topic_briefs_offline(
    market_news: list[Any],
    decision_at: Any = None,
) -> list[Any]:
    grouped = group_news_by_topic(market_news)
    return [
        build_topic_briefs_offline(topic, group_items, now=decision_at)
        for topic, group_items in sorted(grouped.items())
    ]


def _build_topic_briefs_with_progress(
    session_id: str,
    market_news: list[Any],
    settings: Any,
    *,
    started_at: float,
    decision_at: Any = None,
    stop_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    executor = get_analysis_context_executor()
    future = executor.submit(
        call_with_optional_time,
        _build_topic_briefs,
        market_news,
        settings,
        keyword="decision_at",
        decision_at=decision_at,
    )
    deadline = time.monotonic() + NEWS_SUMMARY_TIMEOUT_SECONDS
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return _build_topic_briefs_offline(market_news, decision_at), True
            try:
                return future.result(
                    timeout=min(NEWS_SUMMARY_HEARTBEAT_SECONDS, remaining)
                ), False
            except FutureTimeoutError:
                yield _emit_stage(
                    session_id,
                    "news_summarize",
                    "正在生成主题要闻摘要…",
                    started_at=started_at,
                )
    finally:
        future.cancel()


def _prepare_analysis_bundle_with_progress(
    session_id: str,
    user_id: int,
    enriched: AnalysisRequest,
    risk: Any,
    snapshots: list[Any],
    market_news: list[Any],
    topic_briefs: list[Any],
    nav_trends: dict[str, Any],
    runtime: Any,
    *,
    started_at: float,
    decision_at: Any = None,
    stop_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    executor = get_analysis_context_executor()
    future = executor.submit(
        run_with_request_user,
        user_id,
        lambda: prepare_analysis_bundle(
            enriched,
            risk,
            snapshots,
            market_news,
            topic_briefs,
            nav_trends,
            analysis_mode=runtime.mode,
            budget_enhancements=True,
            decision_at=decision_at,
            stop_event=stop_event,
        ),
    )
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            try:
                return future.result(timeout=CONTEXT_HEARTBEAT_SECONDS)
            except FutureTimeoutError:
                yield _emit_stage(
                    session_id,
                    "generating",
                    "正在整理分析上下文…",
                    started_at=started_at,
                )
    finally:
        future.cancel()


def _await_future_or_cancel(future: Any, stop_event: threading.Event) -> Any:
    while True:
        raise_if_stream_cancelled(stop_event)
        try:
            return future.result(timeout=0.25)
        except FutureTimeoutError:
            continue


def _get_snapshots_with_cancel(
    service: FundDataService,
    holdings: list[Holding],
    stop_event: threading.Event,
):
    """Pass cancellation while preserving injected legacy test/provider shims."""

    try:
        return service.get_snapshots_with_nav_trends(
            holdings,
            stop_event=stop_event,
        )
    except TypeError as exc:
        message = str(exc)
        if (
            "unexpected keyword argument" not in message
            or "stop_event" not in message
        ):
            raise
        return service.get_snapshots_with_nav_trends(holdings)


def _stage(
    stage: str,
    label: str | None = None,
    *,
    started_at: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "stage",
        "stage": stage,
        "label": label or JOB_STAGES.get(stage, stage),
    }
    if started_at is not None:
        payload["elapsed_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    return payload


def _done(report: Report) -> dict[str, Any]:
    return {
        "type": "done",
        "report_id": report.id,
        "report": report.model_dump(mode="json"),
    }
