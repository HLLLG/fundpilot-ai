"""阶段 2：日报分析 SSE 流式生成器。"""

from __future__ import annotations

from collections.abc import Iterator
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
    _build_topic_briefs,
    _offline_report,
    _parse_model_json,
    build_analysis_chat_messages,
)
from app.services.deepseek_streaming import stream_chat_completion
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.analysis_payload import prepare_analysis_bundle
from app.services.news_service import NewsService
from app.services.news_summarizer import merge_topic_briefs
from app.services.risk import evaluate_portfolio_risk
from app.services.streaming_json_parser import StreamingReportParser
from app.services.report_judge import judge_parsed_report
from app.services.stream_session_store import (
    create_stream_session,
    delete_stream_session,
    set_stream_session_stage,
)


def stream_analysis(request: AnalysisRequest, *, user_id: int) -> Iterator[dict[str, Any]]:
    """把 run_analysis 拆成可流式产出 SSE 事件的版本（fast / deep）。"""
    if not request.holdings:
        yield {"type": "error", "message": "至少需要一条基金持仓"}
        return

    ctx_token = set_request_user_id(user_id)
    settings = get_settings()
    session = create_stream_session()
    try:
        yield {"type": "session", "session_id": session.session_id}
        yield _emit_stage(session.session_id, "fund_data")
        resolved = FundProfileService().resolve_holdings(request.holdings)
        enriched = request.model_copy(update={"holdings": resolved})
        risk = evaluate_portfolio_risk(enriched.holdings, enriched.profile)
        snapshots, nav_trends = FundDataService().get_snapshots_with_nav_trends(
            enriched.holdings
        )

        yield _emit_stage(session.session_id, "news_prefetch")
        runtime = resolve_analysis_runtime(settings, enriched.analysis_mode)
        news_service = NewsService()
        market_news = news_service.prefetch_for_holdings(
            enriched.holdings,
            max_topics=runtime.news_max_topics,
        )

        yield _emit_stage(session.session_id, "news_summarize")
        topic_briefs = _build_topic_briefs(market_news, settings)

        bundle = prepare_analysis_bundle(
            enriched,
            risk,
            snapshots,
            market_news,
            topic_briefs,
            nav_trends,
            analysis_mode=runtime.mode,
        )
        yield {
            "type": "skeleton",
            "fund_codes": [h.fund_code for h in enriched.holdings],
            "fund_names": [h.fund_name for h in enriched.holdings],
        }

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
            yield _emit_stage(session.session_id, "saving")
            save_report(report)
            yield _done(report)
            return

        yield _emit_stage(session.session_id, "generating")
        initial_news_count = len(market_news)
        pending_stages: list[tuple[str, str]] = []
        operator_notes = list(session.operator_notes)

        if runtime.news_tool_max_rounds > 0:
            client = DeepSeekClient()
            messages, market_news = client.run_news_tool_rounds(
                enriched,
                risk,
                snapshots,
                market_news,
                topic_briefs,
                runtime,
                nav_trends,
                analysis_bundle=bundle,
                on_stage=lambda stage, label: pending_stages.append((stage, label)),
                operator_notes=operator_notes or None,
            )
            for stage, label in pending_stages:
                yield _emit_stage(session.session_id, stage, label)
            if len(market_news) > initial_news_count:
                topic_briefs = merge_topic_briefs(topic_briefs, market_news, settings)
        else:
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
                yield _emit_stage(session.session_id, "salvage", "流式中断，已收集部分内容…")
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

        yield _emit_stage(session.session_id, "judging")
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
        yield _emit_stage(session.session_id, "saving")
        save_report(report)
        yield _done(report)
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        delete_stream_session(session.session_id)
        reset_request_user_id(ctx_token)


def _emit_stage(session_id: str, stage: str, label: str | None = None) -> dict[str, str]:
    set_stream_session_stage(session_id, stage)
    return _stage(stage, label)


def _stage(stage: str, label: str | None = None) -> dict[str, str]:
    return {
        "type": "stage",
        "stage": stage,
        "label": label or JOB_STAGES.get(stage, stage),
    }


def _done(report: Report) -> dict[str, Any]:
    return {
        "type": "done",
        "report_id": report.id,
        "report": report.model_dump(mode="json"),
    }
