"""阶段 2：日报分析 SSE 流式生成器。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Iterator
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
    _offline_report,
    _parse_model_json,
    build_analysis_chat_messages,
)
from app.services.deepseek_streaming import stream_chat_completion
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.analysis_payload import prepare_analysis_bundle
from app.services.news_service import NewsService
from app.services.news_summarizer import (
    build_topic_briefs_offline,
    group_news_by_topic,
    summarize_all_topics,
)
from app.services.pipeline_concurrency import run_with_request_user
from app.services.risk import evaluate_portfolio_risk
from app.services.streaming_heartbeat import Heartbeat, iter_with_heartbeat
from app.services.streaming_json_parser import StreamingReportParser
from app.services.report_judge import judge_parsed_report
from app.services.stream_session_store import (
    create_stream_session,
    delete_stream_session,
    set_stream_session_stage,
)

NEWS_SUMMARY_TIMEOUT_SECONDS = 8.0
NEWS_SUMMARY_HEARTBEAT_SECONDS = 1.0
CONTEXT_HEARTBEAT_SECONDS = 1.0
# 与 discovery_streaming 一致：避免 LLM 首 token 延迟过久导致 SSE 连接被网关
# （如腾讯云开发 CloudBase，约 60s 空闲阈值）判定超时中断（ERR_ABORT_HANDLER）。
LLM_HEARTBEAT_SECONDS = 12.0


def stream_analysis(request: AnalysisRequest, *, user_id: int) -> Iterator[dict[str, Any]]:
    """把 run_analysis 拆成可流式产出 SSE 事件的版本（fast / deep）。"""
    if not request.holdings:
        yield {"type": "error", "message": "至少需要一条基金持仓"}
        return

    ctx_token = set_request_user_id(user_id)
    settings = get_settings()
    session = create_stream_session()
    started_at = time.monotonic()
    try:
        yield {"type": "session", "session_id": session.session_id}
        resolved = FundProfileService().resolve_holdings(request.holdings)
        enriched = request.model_copy(update={"holdings": resolved})
        risk = evaluate_portfolio_risk(enriched.holdings, enriched.profile)
        runtime = resolve_analysis_runtime(settings, enriched.analysis_mode)

        yield _emit_stage(session.session_id, "fund_data", started_at=started_at)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis-prep") as executor:
            fund_data_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: FundDataService().get_snapshots_with_nav_trends(enriched.holdings),
            )
            yield _emit_stage(session.session_id, "news_prefetch", started_at=started_at)
            news_future = executor.submit(
                run_with_request_user,
                user_id,
                lambda: NewsService().prefetch_for_holdings(
                    enriched.holdings,
                    max_topics=runtime.news_max_topics,
                ),
            )
            snapshots, nav_trends = fund_data_future.result()
            market_news = news_future.result()

        yield _emit_stage(session.session_id, "news_summarize", started_at=started_at)
        topic_briefs, summary_timed_out = yield from _build_topic_briefs_with_progress(
            session.session_id,
            market_news,
            settings,
            started_at=started_at,
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
        )

        if not settings.deepseek_configured:
            report = _offline_report(
                enriched,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
                analysis_bundle=bundle,
            )
            yield _emit_stage(session.session_id, "saving", started_at=started_at)
            save_report(report)
            yield _done(report)
            return

        yield _emit_stage(session.session_id, "generating", started_at=started_at)
        operator_notes = list(session.operator_notes)
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
        )

        parser = StreamingReportParser()
        all_chunks: list[str] = []
        parsed: dict | None = None

        try:
            for entry in iter_with_heartbeat(
                stream_chat_completion(
                    messages=messages,
                    model=runtime.model,
                    max_tokens=settings.deepseek_max_tokens_report,
                    response_format={"type": "json_object"},
                ),
                heartbeat_seconds=LLM_HEARTBEAT_SECONDS,
                heartbeat_factory=lambda: _emit_stage(
                    session.session_id, "generating", "AI 分析中…", started_at=started_at
                ),
            ):
                if isinstance(entry, Heartbeat):
                    yield entry.value
                    continue
                chunk = entry
                all_chunks.append(chunk)
                yield {"type": "token", "content": chunk}
                for partial in parser.feed(chunk):
                    yield partial
            parsed = _parse_model_json("".join(all_chunks))
        except (httpx.StreamError, httpx.ReadTimeout, httpx.HTTPError) as exc:
            if all_chunks:
                yield _emit_stage(
                    session.session_id,
                    "salvage",
                    "流式中断，已收集部分内容…",
                    started_at=started_at,
                )
                parsed = _parse_model_json("".join(all_chunks))
                parsed.setdefault("caveats", [])
                if isinstance(parsed["caveats"], list):
                    parsed["caveats"] = list(parsed["caveats"])
                    parsed["caveats"].append(
                        f"流式传输中断（{type(exc).__name__}），部分字段可能不完整。"
                    )
            else:
                yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
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
        )
        yield _emit_stage(session.session_id, "saving", started_at=started_at)
        save_report(report)
        yield _done(report)
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


def _build_topic_briefs(market_news: list[Any], settings: Any) -> list[Any]:
    return summarize_all_topics(market_news, settings, offline_only=True)


def _build_topic_briefs_offline(market_news: list[Any]) -> list[Any]:
    grouped = group_news_by_topic(market_news)
    return [
        build_topic_briefs_offline(topic, group_items)
        for topic, group_items in sorted(grouped.items())
    ]


def _build_topic_briefs_with_progress(
    session_id: str,
    market_news: list[Any],
    settings: Any,
    *,
    started_at: float,
) -> Iterator[dict[str, Any]]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-news-summary")
    future = executor.submit(_build_topic_briefs, market_news, settings)
    deadline = time.monotonic() + NEWS_SUMMARY_TIMEOUT_SECONDS
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return _build_topic_briefs_offline(market_news), True
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
        executor.shutdown(wait=False, cancel_futures=True)


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
) -> Iterator[dict[str, Any]]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-context")
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
        ),
    )
    try:
        while True:
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
        executor.shutdown(wait=False, cancel_futures=True)


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
