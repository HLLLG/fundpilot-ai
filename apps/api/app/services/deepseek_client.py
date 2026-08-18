from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime

import httpx

from app.config import get_settings
from app.services.deepseek_http import (
    ProviderFailure,
    ProviderOutputError,
    classify_deepseek_failure,
    deepseek_chat_url,
    deepseek_request_deadline,
    deepseek_request_headers,
    deepseek_timeout,
    get_deepseek_http_client,
    post_deepseek_chat,
)
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    FundSnapshot,
    MarketItem,
    NewsItem,
    Report,
    RiskAssessment,
    TopicBrief,
)
from app.services.analysis_runtime import AnalysisRuntime, resolve_analysis_runtime
from app.services.analysis_prompt import build_analysis_prompt_contract
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    append_output_requirements_to_system,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
)
from app.services.news_service import (
    NewsService,
    _dedupe_news,
    announcement_fetch_facts,
    merge_market_news_with_announcements,
)
from app.services.news_freshness import normalize_news_now, resolve_decision_local_datetime
from app.services.news_summarizer import summarize_all_topics
from app.services.news_citation import apply_news_citation_guards
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.report_judge import judge_parsed_report
from app.services.report_pipeline import build_pipeline_metadata
from app.services.provider_fallback import apply_provider_failure_to_facts
from app.services.provider_call_trace import normalize_provider_call_trace
from app.services.prompt_provenance import (
    build_prompt_contract as freeze_prompt_contract,
    content_hash,
    with_judge_result,
)
from app.services.decision_contract import POLICY_VERSION
from app.services.decision_time_call import (
    call_with_optional_time,
    prefetch_fund_announcements_compat,
)
from app.services.retired_market_evidence import sanitize_retired_market_evidence
from app.services.fund_lookthrough_claim_validator import (
    validate_fund_lookthrough_claims,
)
from app.services.recommendations import (
    build_offline_fund_recommendation,
    build_offline_fund_recommendations,
    canonicalize_fund_recommendations,
    enrich_fund_recommendations,
    group_strings_to_fund_recommendations,
    parse_fund_recommendations_raw,
    portfolio_recommendation_lines,
)

ProgressCallback = Callable[[str, str], None]

REPORT_TEMPERATURE = 0.2
REPORT_RESPONSE_FORMAT = {"type": "json_object"}

JOB_STAGES: dict[str, str] = {
    "fund_data": "正在拉取净值与诊断数据…",
    "news_prefetch": "正在检索市场新闻…",
    "news_summarize": "正在生成主题要闻摘要…",
    "generating": "正在生成 AI 日报…",
    "judging": "正在审校报告…",
    "saving": "正在保存报告…",
    "salvage": "流式中断，已收集部分内容…",
}


def tool_round_stage_label(round_index: int, total_rounds: int) -> tuple[str, str]:
    stage = f"tool_round_{round_index}"
    return stage, f"正在检索新闻 ({round_index}/{total_rounds})…"

FETCH_MARKET_NEWS_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_market_news",
        "description": (
            "从东方财富检索板块/主题新闻。优先返回当日消息，前几日可作背景。"
            "请结合当前交易会话与决策窗口判断时效，交易日优先拉取当日新闻。"
            "主题用板块名（如电网设备、半导体）或 6 位基金代码。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "检索主题，例如「电网设备」「半导体」或基金代码",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回新闻条数，默认 5，最大 10",
                },
            },
            "required": ["topic"],
        },
    },
}


class DeepSeekClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.news_service = NewsService()
        self._provider_deadline: float | None = None

    def generate_report(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        nav_trends_by_code: dict[str, dict] | None = None,
        on_progress: ProgressCallback | None = None,
        decision_at: datetime | None = None,
    ) -> Report:
        self._provider_deadline = None

        def progress(stage: str) -> None:
            if on_progress is not None:
                on_progress(stage, JOB_STAGES.get(stage, stage))

        decision_at = normalize_news_now(decision_at)
        nav_trends = nav_trends_by_code or {}
        runtime = resolve_analysis_runtime(self.settings, request.analysis_mode)
        self._last_report_messages = []

        def attempted_prompt_contract() -> dict | None:
            actual_messages = getattr(self, "_last_report_messages", None)
            if not actual_messages:
                return None
            return build_analysis_prompt_provenance(
                request=request,
                messages=actual_messages,
                runtime=runtime,
                judge_meta={},
            )
        progress("news_prefetch")
        market_news = call_with_optional_time(
            self.news_service.prefetch_for_holdings,
            request.holdings,
            keyword="now",
            decision_at=decision_at,
            max_topics=runtime.news_max_topics,
        )
        announcement_result = prefetch_fund_announcements_compat(
            self.news_service,
            [holding.fund_code for holding in request.holdings],
            decision_at=decision_at,
        )
        market_news = merge_market_news_with_announcements(
            market_news,
            list(announcement_result.get("items") or []),
            now=decision_at,
        )
        announcement_meta = announcement_fetch_facts(announcement_result)
        progress("news_summarize")
        topic_briefs = _build_topic_briefs(
            market_news,
            self.settings,
            now=decision_at,
        )
        if not self.settings.deepseek_configured:
            return _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
                decision_at=decision_at,
                announcement_meta=announcement_meta,
            )

        analysis_bundle: AnalysisFactsBundle | None = None
        try:
            progress("generating")
            analysis_bundle = prepare_analysis_bundle(
                request,
                risk,
                snapshots,
                market_news,
                topic_briefs,
                nav_trends,
                analysis_mode=runtime.mode,
                budget_enhancements=True,
                decision_at=decision_at,
            )
            analysis_bundle.facts["fund_announcements"] = deepcopy(
                announcement_meta
            )
            parsed, market_news = self._generate_direct_report(
                request,
                risk,
                snapshots,
                market_news,
                topic_briefs,
                runtime,
                nav_trends,
                analysis_bundle=analysis_bundle,
                decision_at=decision_at,
            )
            progress("judging")
            judge_fallback = build_offline_fund_recommendations(
                request,
                market_news,
                nav_trends_by_code=nav_trends,
            )
            parsed, judge_meta = judge_parsed_report(
                parsed, request, risk, snapshots, runtime,
                facts=analysis_bundle.facts,
                fallback_recommendations=judge_fallback,
            )
            prompt_contract = None
            actual_messages = getattr(self, "_last_report_messages", None)
            if actual_messages:
                prompt_contract = build_analysis_prompt_provenance(
                    request=request,
                    messages=actual_messages,
                    runtime=runtime,
                    judge_meta=judge_meta,
                )
            return _build_final_report(
                parsed,
                request=request,
                risk=risk,
                snapshots=snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends=nav_trends,
                analysis_bundle=analysis_bundle,
                judge_meta=judge_meta,
                runtime=runtime,
                prompt_contract=prompt_contract,
                decision_at=decision_at,
                announcement_meta=announcement_meta,
            )
        except (httpx.HTTPError, ProviderOutputError) as exc:
            failure = classify_deepseek_failure(exc)
            fallback = _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
                analysis_bundle=analysis_bundle,
                provider_failure=failure,
                attempted_model=runtime.model,
                prompt_contract=attempted_prompt_contract(),
                decision_at=decision_at,
                announcement_meta=announcement_meta,
            )
            fallback.provider = "offline-fallback"
            return fallback

    def run_news_tool_rounds(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        prefetched_news: list[NewsItem],
        topic_briefs: list[TopicBrief],
        runtime: AnalysisRuntime,
        nav_trends_by_code: dict[str, dict] | None = None,
        *,
        analysis_bundle: AnalysisFactsBundle | None = None,
        on_stage: ProgressCallback | None = None,
        operator_notes: list[str] | None = None,
        decision_at: datetime | None = None,
    ) -> tuple[list[dict], list[NewsItem]]:
        """运行新闻 tool 轮（同步），返回可供最终 JSON 补全的 messages。"""
        nav_trends = nav_trends_by_code or {}
        bundle = analysis_bundle or prepare_analysis_bundle(
            request,
            risk,
            snapshots,
            prefetched_news,
            topic_briefs,
            nav_trends,
            analysis_mode=runtime.mode,
            decision_at=decision_at,
        )
        collected: list[NewsItem] = list(prefetched_news)
        news_enabled = runtime.news_enabled
        messages: list[dict] = [
            {
                "role": "system",
                "content": _system_prompt(
                    news_enabled,
                    request.system_role_prompt,
                    session=bundle.session,
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    build_user_payload(
                        request,
                        risk,
                        snapshots,
                        collected,
                        topic_briefs,
                        nav_trends,
                        analysis_mode=runtime.mode,
                        analysis_bundle=bundle,
                        operator_notes=operator_notes,
                        decision_at=decision_at,
                    ),
                    ensure_ascii=False,
                ),
            },
        ]
        tools = [FETCH_MARKET_NEWS_TOOL] if news_enabled and runtime.news_tool_max_rounds > 0 else None
        max_rounds = runtime.news_tool_max_rounds if tools else 0

        for round_index in range(max_rounds):
            stage, label = tool_round_stage_label(round_index + 1, max_rounds)
            if on_stage is not None:
                on_stage(stage, label)
            message = self._chat_completion(
                messages=messages,
                tools=tools,
                response_format=None,
                max_tokens=self.settings.deepseek_max_tokens,
                model=runtime.model,
            )
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                messages.append(message)
                break
            messages.append(message)
            for tool_call in tool_calls:
                if on_stage is not None:
                    on_stage("fetch_market_news", "正在拉取市场新闻…")
                result = _execute_fetch_market_news(
                    tool_call,
                    self.news_service,
                    collected,
                    decision_at=decision_at,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    }
                )

        return messages, _dedupe_news(collected, now=decision_at)

    def _generate_direct_report(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        prefetched_news: list[NewsItem],
        topic_briefs: list[TopicBrief],
        runtime: AnalysisRuntime,
        nav_trends_by_code: dict[str, dict] | None = None,
        *,
        analysis_bundle: AnalysisFactsBundle,
        decision_at: datetime | None = None,
    ) -> tuple[dict, list[NewsItem]]:
        messages = build_analysis_chat_messages(
            request,
            risk,
            snapshots,
            prefetched_news,
            topic_briefs,
            nav_trends_by_code or {},
            runtime,
            analysis_bundle,
            decision_at=decision_at,
        )
        parsed = self._generate_report_json(messages, runtime)
        return parsed, list(prefetched_news)

    def _generate_with_tools(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        prefetched_news: list[NewsItem],
        topic_briefs: list[TopicBrief],
        runtime: AnalysisRuntime,
        nav_trends_by_code: dict[str, dict] | None = None,
        *,
        analysis_bundle: AnalysisFactsBundle | None = None,
        decision_at: datetime | None = None,
    ) -> tuple[dict, list[NewsItem]]:
        messages, collected = self.run_news_tool_rounds(
            request=request,
            risk=risk,
            snapshots=snapshots,
            prefetched_news=prefetched_news,
            topic_briefs=topic_briefs,
            runtime=runtime,
            nav_trends_by_code=nav_trends_by_code,
            analysis_bundle=analysis_bundle,
            decision_at=decision_at,
        )

        return self._generate_report_json(messages, runtime), _dedupe_news(
            collected,
            now=decision_at,
        )

    def _generate_report_json(
        self,
        messages: list[dict],
        runtime: AnalysisRuntime,
    ) -> dict:
        self._last_report_messages = deepcopy(messages)
        message = self._chat_completion(
            messages=messages,
            tools=None,
            response_format={"type": "json_object"},
            max_tokens=self.settings.deepseek_max_tokens_report,
            model=runtime.model,
        )
        final_content = message.get("content")
        if not isinstance(final_content, str):
            raise ProviderOutputError("invalid_json")

        parsed = normalize_daily_report_payload(_parse_model_json(final_content))
        if _daily_provider_response_incomplete(parsed):
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "上次 JSON 输出不完整。请仅输出完整 JSON，"
                        "包含 title、summary、fund_recommendations、caveats；"
                        "每只基金一条，points 每条不超过 60 字，总输出尽量精炼。"
                    ),
                }
            ]
            self._last_report_messages = deepcopy(retry_messages)
            retry_message = self._chat_completion(
                messages=retry_messages,
                tools=None,
                response_format={"type": "json_object"},
                max_tokens=self.settings.deepseek_max_tokens_report,
                model=runtime.model,
            )
            retry_content = retry_message.get("content")
            if not isinstance(retry_content, str):
                raise ProviderOutputError("invalid_json")
            if not retry_content.strip():
                raise ProviderOutputError("empty_content")
            parsed = normalize_daily_report_payload(_parse_model_json(retry_content))
            if _daily_provider_response_incomplete(parsed):
                raise ProviderOutputError("invalid_json")

        self._last_report_messages = deepcopy(
            getattr(self, "_last_chat_messages", messages)
        )
        return parsed

    def _chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        if self._provider_deadline is None:
            self._provider_deadline = deepseek_request_deadline(self.settings)
        self._last_chat_messages = deepcopy(messages)
        payload = _build_chat_payload(
            messages=messages,
            model=model or self.settings.deepseek_model,
            max_tokens=max_tokens or self.settings.deepseek_max_tokens,
            tools=tools,
            response_format=response_format,
        )
        response = post_deepseek_chat(
            get_deepseek_http_client(self.settings),
            deepseek_chat_url(self.settings),
            headers=deepseek_request_headers(self.settings),
            json=payload,
            timeout=deepseek_timeout(
                self.settings,
                deadline_monotonic=self._provider_deadline,
                first_byte_watchdog=True,
            ),
        )
        response.raise_for_status()
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ProviderOutputError("invalid_json") from exc
        if not isinstance(message, dict):
            raise ProviderOutputError("invalid_json")
        return message


def _system_prompt(
    news_enabled: bool,
    system_role_prompt: str | None = None,
    *,
    session: dict | None = None,
) -> str:
    from app.services.analysis_prompt import resolve_role_prompt

    decision_now = resolve_decision_local_datetime(session)
    base = resolve_role_prompt(system_role_prompt)
    base += f"当前分析时点约为 {decision_now}。"
    if news_enabled:
        base += (
            "用户消息中 topic_briefs 为按主题预摘要（优先阅读），news_titles 为可引用新闻标题列表；"
            "利好/利空标题须能在 news_titles 或 topic_briefs.points.source_titles 中找到对应。"
            "优先采用当日新闻，前几日仅作背景并标注日期，避免用旧闻主导结论。"
            "新闻证据仅限本请求已预取并列出的标题与摘要，不得声称另行检索或浏览。"
        )
    else:
        base += "若无新闻数据，须说明信息缺口并给出条件化方案。"
    # 单一数据驱动决策风格（2026-08 收敛）：动作凭结构化证据说话，方向与力度由
    # 趋势/资金/证据档位决定，不再按稳健/战术/激进预设倾向。
    base += (
        "决策凭已提供的结构化数据判断：加仓须有板块方向（sector_opportunity）、"
        "资金流与基金证据支撑，不追涨当日大涨板块；"
        "持有收益达扣费止盈线（analysis_facts.portfolio.take_profit_threshold_percent）"
        "且出现走弱/回吐信号时，优先考虑分批止盈；"
        "方向退出信号（direction_exit）出现时不得以长期逻辑掩盖减仓需求；不得承诺收益。"
    )
    base += "最终回复必须是完整 JSON，不要 Markdown，控制篇幅避免截断。"
    return append_output_requirements_to_system(base)


def _build_topic_briefs(
    market_news: list[NewsItem],
    settings: object | None = None,
    *,
    now: datetime | None = None,
) -> list[TopicBrief]:
    resolved = settings or get_settings()
    if not market_news or not getattr(resolved, "news_summarize", True):
        return []
    return summarize_all_topics(  # type: ignore[arg-type]
        market_news,
        resolved,
        offline_only=True,
        now=now,
    )


def build_analysis_chat_messages(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_news: list[NewsItem],
    topic_briefs: list[TopicBrief],
    nav_trends: dict[str, dict],
    runtime: AnalysisRuntime,
    analysis_bundle: AnalysisFactsBundle,
    operator_notes: list[str] | None = None,
    *,
    decision_at: datetime | None = None,
) -> list[dict]:
    """fast 模式流式路径：无 tool calling，直接 JSON 输出。"""
    return [
        {
            "role": "system",
            "content": _system_prompt(
                runtime.news_enabled,
                request.system_role_prompt,
                session=analysis_bundle.session,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                build_user_payload(
                    request,
                    risk,
                    snapshots,
                    market_news,
                    topic_briefs,
                    nav_trends,
                    analysis_mode=runtime.mode,
                    analysis_bundle=analysis_bundle,
                    operator_notes=operator_notes,
                    decision_at=decision_at,
                ),
                ensure_ascii=False,
            ),
        },
    ]


def build_analysis_prompt_provenance(
    *,
    request: AnalysisRequest,
    messages: list[dict],
    runtime: AnalysisRuntime,
    judge_meta: dict | None = None,
) -> dict:
    """Freeze provenance from the exact messages used by the provider request."""

    component = build_analysis_prompt_contract(request.system_role_prompt)
    user_payload: dict = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        try:
            candidate = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            user_payload = candidate
            break
    provider_payload = _build_chat_payload(
        messages=messages,
        model=runtime.model,
        max_tokens=get_settings().deepseek_max_tokens_report,
        tools=None,
        response_format=REPORT_RESPONSE_FORMAT,
    )
    settings = get_settings()
    contract = freeze_prompt_contract(
        template_version=component.template_version,
        template_snapshot=component.template_snapshot,
        user_appendix_snapshot=component.user_appendix,
        messages=messages,
        user_payload=user_payload,
        provider_payload=provider_payload,
        analysis_mode=runtime.mode,
        news_retrieval_policy=runtime.news_retrieval_policy,
        news_tool_rounds_configured=runtime.news_tool_rounds_configured,
        news_tool_rounds_executed=runtime.news_tool_rounds_executed,
        judge_mode=(
            "optional_second_pass"
            if runtime.mode == "deep" and settings.decision_escalation_mode == "enforced"
            else "rule_only"
        ),
        judge_meta=judge_meta,
        decision_escalation_mode=settings.decision_escalation_mode,
        policy_version=POLICY_VERSION,
    )
    contract.update(
        {
            "normalized_user_appendix_snapshot": component.normalized_user_appendix,
            "normalized_user_appendix_hash": content_hash(
                component.normalized_user_appendix
            ),
            "user_appendix_kind": component.user_appendix_kind,
            "user_appendix_legacy": component.user_appendix_legacy,
            "user_appendix_truncated": component.user_appendix_truncated,
        }
    )
    contract.pop("contract_hash", None)
    contract["contract_hash"] = content_hash(contract)
    return with_judge_result(contract, judge_meta)


def _build_final_report(
    parsed: dict,
    *,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_news: list[NewsItem],
    topic_briefs: list[TopicBrief],
    nav_trends: dict[str, dict],
    analysis_bundle: AnalysisFactsBundle,
    judge_meta: dict,
    runtime: AnalysisRuntime,
    prompt_contract: dict | None = None,
    provider_call_trace: dict | None = None,
    decision_at: datetime | None = None,
    announcement_meta: dict | None = None,
) -> Report:
    parsed = sanitize_retired_market_evidence(parsed)
    # 穿透证据一旦进入 prompt，模型就可能写出"两只基金几乎没有重合，组合很分散"这类
    # 披露口径不支持的断言。校验器在守卫链之前先清洗叙述字段（只动白名单里的文本，
    # 不碰动作与金额），并留下确定性审计供 decision_quality_artifacts 复盘。
    parsed, lookthrough_claim_audit = validate_fund_lookthrough_claims(
        parsed,
        analysis_bundle.facts.get("fund_lookthrough"),
    )
    fallback = _offline_report(
        request,
        risk,
        snapshots,
        market_news=market_news,
        topic_briefs=topic_briefs,
        nav_trends_by_code=nav_trends,
        analysis_bundle=analysis_bundle,
        decision_at=decision_at,
        announcement_meta=announcement_meta,
    )
    portfolio_recs, fund_recs = _finalize_recommendations(
        parsed,
        fallback,
        request,
        risk,
        market_news,
        topic_briefs,
        nav_trends_by_code=nav_trends,
        facts=analysis_bundle.facts,
    )
    pipeline = build_pipeline_metadata(
        runtime=runtime,
        market_news=market_news,
        topic_briefs=topic_briefs,
        judge_meta=judge_meta,
    )
    pipeline.update(
        {
            "provider": runtime.model,
            "provider_status": "success",
            "attempted_model": runtime.model,
        }
    )
    if prompt_contract is not None:
        pipeline["prompt_contract"] = deepcopy(prompt_contract)
    if provider_call_trace is not None:
        pipeline["provider_call_trace"] = normalize_provider_call_trace(
            provider_call_trace
        )
    facts = finalize_analysis_facts(
        analysis_bundle.facts,
        market_news=market_news,
        topic_briefs=topic_briefs,
        pipeline=pipeline,
        decision_at=decision_at,
    )
    if announcement_meta is not None:
        facts["fund_announcements"] = deepcopy(announcement_meta)
    facts["fund_lookthrough_claim_audit"] = lookthrough_claim_audit
    caveats = _user_facing_caveats(
        _non_empty_list(parsed.get("caveats"), fallback.caveats)
    )
    caveats = _append_news_pipeline_caveats(caveats, topic_briefs, market_news)
    caveats = _append_pipeline_caveats(caveats, facts)
    from app.services.decision_data_evidence import report_execution_blocked

    summary = parsed.get("summary") or fallback.summary
    if report_execution_blocked(facts):
        summary = "关键持仓或行情数据未达到时点可用条件，本次只做观察和风险提示；请更新数据后重新生成。"
    report = Report(
        **({"created_at": decision_at} if decision_at is not None else {}),
        title=parsed.get("title", "每日基金操作日报"),
        risk=risk,
        holdings=request.holdings,
        snapshots=snapshots,
        market_context=[],
        market_news=market_news,
        topic_briefs=topic_briefs,
        fund_recommendations=fund_recs,
        summary=summary,
        recommendations=portfolio_recs,
        caveats=caveats,
        provider=runtime.model,
        analysis_facts=facts,
    )
    return report


def _append_news_pipeline_caveats(
    caveats: list[str],
    topic_briefs: list[TopicBrief],
    market_news: list[NewsItem],
) -> list[str]:
    result = list(caveats)
    if market_news and not topic_briefs and get_settings().news_summarize:
        result.append("新闻主题摘要未生成（已回退为标题列表），结论请结合 prefetched_news 人工核对。")
    elif topic_briefs and any(brief.provider == "rule-fallback" for brief in topic_briefs):
        result.append("部分新闻主题为规则摘要（模型不可用或超时），请以原始新闻标题为准交叉验证。")
    return result


def _build_chat_payload(
    *,
    messages: list[dict],
    model: str,
    max_tokens: int,
    tools: list[dict] | None,
    response_format: dict | None,
    temperature: float = REPORT_TEMPERATURE,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format
    return payload


def _execute_fetch_market_news(
    tool_call: dict,
    news_service: NewsService,
    collected: list[NewsItem],
    *,
    decision_at: datetime | None = None,
) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name")
    if name != "fetch_market_news":
        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)

    raw_args = function.get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        args = {}

    topic = str(args.get("topic", "")).strip()
    if not topic:
        return json.dumps({"error": "topic is required"}, ensure_ascii=False)

    limit_raw = args.get("limit", news_service.settings.news_per_topic)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = news_service.settings.news_per_topic
    limit = max(1, min(limit, 10))

    items = news_service.search(topic, limit=limit, now=decision_at)
    collected.extend(items)
    return json.dumps(
        {
            "topic": topic,
            "count": len(items),
            "items": [item.model_dump() for item in items],
        },
        ensure_ascii=False,
    )


def _parse_model_json(content: str) -> dict:
    for candidate in _json_candidates(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    salvaged = _salvage_partial_json(content)
    if salvaged:
        return salvaged

    summary = content.strip()
    if _looks_like_json(summary):
        summary = "模型返回的 JSON 不完整，已使用本地规则补齐操作候选，请重新生成以获取完整模型建议。"

    return {
        "title": "每日基金操作日报",
        "summary": summary,
        "recommendations": [],
        "caveats": [],
        "_truncated": True,
    }


def _json_candidates(content: str) -> list[str]:
    stripped = content.strip()
    candidates = [stripped]
    candidates.extend(
        re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    )
    extracted = _extract_first_json_object(stripped)
    if extracted:
        candidates.append(extracted)
    return candidates


def _extract_first_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None

    in_string = False
    escaped = False
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]

    return None


def _salvage_partial_json(content: str) -> dict | None:
    if not _looks_like_json(content):
        return None

    title = _extract_json_string_field(content, "title") or "每日基金操作日报"
    summary = _extract_json_string_field(content, "summary")
    if not summary:
        return None

    return {
        "title": title,
        "summary": summary,
        "recommendations": [],
        "caveats": [],
        "_truncated": True,
    }


def _is_usable_interrupted_response(
    content: str,
    parsed: dict,
    *,
    report_kind: str,
) -> bool:
    """Accept an interrupted response only when it contains auditable meaning.

    A complete JSON object is safe to continue through judge + deterministic
    guards.  A truncated object must at least contain a fully closed, non-empty
    ``summary`` field; a lone ``{`` or another arbitrary fragment is not a
    salvage candidate and must take the provider-fallback path.
    """

    if not isinstance(parsed, dict) or not parsed:
        return False
    if not parsed.get("_truncated"):
        if report_kind == "daily":
            return not _daily_provider_response_incomplete(parsed)
        if report_kind == "discovery":
            return _is_valid_discovery_report_payload(parsed)
        return False
    return _salvage_partial_json(content) is not None


def _extract_json_string_field(content: str, field: str) -> str | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', content)
    if not match:
        return None

    value_start = match.end()
    escaped = False
    value_chars: list[str] = []
    for char in content[value_start:]:
        if escaped:
            value_chars.append("\\" + char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            raw = "".join(value_chars)
            try:
                return json.loads(f'"{raw}"')
            except json.JSONDecodeError:
                return raw
        else:
            value_chars.append(char)

    return None


def _looks_like_json(content: str) -> bool:
    stripped = content.strip()
    return (
        stripped.startswith("{")
        or stripped.startswith("```json")
        or '"title"' in stripped
    )


def _non_empty_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    return default


_INTERNAL_CAVEAT_MARKERS = (
    "JSON 被截断",
    "无法解析为完整 JSON",
    "已使用本地规则补齐",
)


def _user_facing_caveats(caveats: list[str]) -> list[str]:
    return [line for line in caveats if not any(marker in line for marker in _INTERNAL_CAVEAT_MARKERS)]


def _response_incomplete(parsed: dict) -> bool:
    if parsed.get("_truncated"):
        return True
    if not parse_fund_recommendations_raw(parsed.get("fund_recommendations")):
        return bool(parsed.get("summary"))
    return False


def _coerce_advisory_string_list(value: object) -> list[str] | None:
    """把提示类字段收敛成 list[str]；形状确实无法采信时返回 None（交给准入校验拒绝）。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                text = entry.strip()
            elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
                text = str(entry)
            else:
                # 嵌套对象说明模型套用的是另一套 schema，不做猜测式改写。
                return None
            if text:
                items.append(text)
        return items
    return None


def normalize_daily_report_payload(parsed: dict) -> dict:
    """就地收敛日报输出里可容忍的类型偏差，避免整份报告被判成“模型不可用”。

    `caveats` 与 `recommendations` 是提示文案和组合级说明，不是决策事实：服务端
    随后会用 `_user_facing_caveats` 过滤，再追加新闻与流水线提示，缺失时还有本地
    兜底文案，取值处 `_non_empty_list` 本来就容忍任意形状。唯独准入校验
    `_is_valid_daily_report_payload` 要求它们必须是 `list[str]`，于是模型把
    caveats 写成一个字符串（而不是单元素数组）时，一份 title / summary /
    fund_recommendations 全部完整、逐只都能解析出建议的报告会被整份丢弃，替换成
    “模型服务不可用”的离线兜底——实测 deepseek-v4-pro 在 HTTP 200、
    finish_reason=stop、内容完整的情况下就会这样。

    只收敛文案字段：`title` / `summary` / `fund_recommendations` 仍然严格校验，
    真正被截断的响应依旧会被挡在门外。
    """
    if not isinstance(parsed, dict):
        return parsed
    # caveats 缺失同样不该否决整份报告：服务端有本地兜底文案，并会继续追加提示。
    caveats = _coerce_advisory_string_list(parsed.get("caveats"))
    if caveats is not None:
        parsed["caveats"] = caveats
    # recommendations 在日报 schema 里本就可省略，只在给出时才收敛类型。
    if "recommendations" in parsed:
        legacy = _coerce_advisory_string_list(parsed.get("recommendations"))
        if legacy is not None:
            parsed["recommendations"] = legacy
    return parsed


def _daily_provider_response_incomplete(parsed: dict) -> bool:
    return not _is_valid_daily_report_payload(parsed) or _response_incomplete(parsed)


def _is_valid_daily_report_payload(parsed: object) -> bool:
    if not isinstance(parsed, dict):
        return False
    if not isinstance(parsed.get("title"), str) or not parsed["title"].strip():
        return False
    if not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
        return False
    fund_recommendations = parsed.get("fund_recommendations")
    if not isinstance(fund_recommendations, list) or not all(
        isinstance(item, dict) for item in fund_recommendations
    ):
        return False
    caveats = parsed.get("caveats")
    if not isinstance(caveats, list) or not all(isinstance(item, str) for item in caveats):
        return False
    legacy = parsed.get("recommendations")
    return legacy is None or (
        isinstance(legacy, list) and all(isinstance(item, str) for item in legacy)
    )


def _is_valid_discovery_report_payload(parsed: object) -> bool:
    if not isinstance(parsed, dict):
        return False
    if not isinstance(parsed.get("title"), str) or not parsed["title"].strip():
        return False
    if not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
        return False
    recommendations = parsed.get("recommendations")
    if not isinstance(recommendations, list) or not all(
        isinstance(item, dict) for item in recommendations
    ):
        return False
    caveats = parsed.get("caveats")
    return isinstance(caveats, list) and all(isinstance(item, str) for item in caveats)


def _apply_recommendation_guards_by_holding_order(
    fund_recs: list[FundRecommendation],
    portfolio: list[str],
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None,
    *,
    nav_trends_by_code: dict[str, dict] | None,
    facts: dict | None,
) -> tuple[list[str], list[FundRecommendation]]:
    """Run the legacy guard with stable per-holding identities.

    The guard historically keyed offline rules and facts by fund code. Multiple
    ``000000`` rows (or duplicate real codes from an imported ledger) therefore
    overwrote each other. At the already-canonicalized outlet we can safely assign
    temporary unique codes by holding index, run the guard once, then restore the
    server identities. Normal portfolios take the original zero-copy path.
    """
    codes = [holding.fund_code for holding in request.holdings]
    counts = Counter(codes)
    if not any(code == "000000" or counts[code] > 1 for code in codes):
        return apply_recommendation_guards(
            fund_recs,
            portfolio,
            request,
            risk,
            market_news,
            nav_trends_by_code=nav_trends_by_code,
            facts=facts,
        )

    aliases = _build_guard_identity_aliases(codes)
    alias_to_original = dict(zip(aliases, codes, strict=True))
    guard_request = request.model_copy(deep=True)
    guard_recs = [item.model_copy(deep=True) for item in fund_recs]
    for index, alias in enumerate(aliases):
        guard_request.holdings[index].fund_code = alias
        if index < len(guard_recs):
            guard_recs[index].fund_code = alias
            guard_recs[index].fund_name = guard_request.holdings[index].fund_name

    guard_facts = _alias_guard_facts(facts, codes, aliases)
    guard_nav_trends = dict(nav_trends_by_code or {})
    for original, alias in zip(codes, aliases, strict=True):
        if original in guard_nav_trends:
            guard_nav_trends[alias] = guard_nav_trends[original]

    guarded_portfolio, guarded_recs = apply_recommendation_guards(
        guard_recs,
        portfolio,
        guard_request,
        risk,
        market_news,
        nav_trends_by_code=guard_nav_trends,
        facts=guard_facts,
    )
    for index, item in enumerate(guarded_recs):
        if index < len(request.holdings):
            item.fund_code = request.holdings[index].fund_code
            item.fund_name = request.holdings[index].fund_name
    _restore_guard_facts(facts, guard_facts, alias_to_original)
    return guarded_portfolio, guarded_recs


def _build_guard_identity_aliases(codes: list[str]) -> list[str]:
    counts = Counter(codes)
    used = set(codes)
    candidate = 999_999
    result: list[str] = []
    for code in codes:
        if code != "000000" and counts[code] == 1:
            result.append(code)
            continue
        while f"{candidate:06d}" in used:
            candidate -= 1
        if candidate < 0:
            raise RuntimeError("unable to allocate temporary holding identity")
        alias = f"{candidate:06d}"
        candidate -= 1
        used.add(alias)
        result.append(alias)
    return result


def _alias_guard_facts(
    facts: dict | None,
    original_codes: list[str],
    aliases: list[str],
) -> dict | None:
    if not isinstance(facts, dict):
        return facts
    copied = deepcopy(facts)
    rows = copied.get("holdings")
    if isinstance(rows, list):
        for index, alias in enumerate(aliases):
            if index < len(rows) and isinstance(rows[index], dict):
                rows[index]["fund_code"] = alias

    registry = copied.get("data_evidence")
    if isinstance(registry, dict) and isinstance(registry.get("items"), list):
        source_items = [item for item in registry["items"] if isinstance(item, dict)]
        aliased_items: list[dict] = []
        for original, alias in zip(original_codes, aliases, strict=True):
            if original == alias:
                continue
            prefix = f"holdings.{str(original).strip().zfill(6)}."
            for item in source_items:
                fact_id = str(item.get("fact_id") or "")
                if fact_id.startswith(prefix):
                    cloned = deepcopy(item)
                    cloned["fact_id"] = f"holdings.{alias}.{fact_id[len(prefix):]}"
                    aliased_items.append(cloned)
        registry["items"] = [*source_items, *aliased_items]
    return copied


def _restore_guard_facts(
    facts: dict | None,
    guard_facts: dict | None,
    alias_to_original: dict[str, str],
) -> None:
    if not isinstance(facts, dict) or not isinstance(guard_facts, dict):
        return
    guard_meta = guard_facts.get("data_evidence_guard")
    if not isinstance(guard_meta, dict):
        return
    restored = deepcopy(guard_meta)
    restored["blocked_fund_codes"] = sorted(
        {
            alias_to_original.get(str(code), str(code))
            for code in guard_meta.get("blocked_fund_codes") or []
        }
    )
    reasons_by_fund: dict[str, list[str]] = {}
    for code, reasons in (guard_meta.get("reasons_by_fund") or {}).items():
        restored_code = alias_to_original.get(str(code), str(code))
        bucket = reasons_by_fund.setdefault(restored_code, [])
        for reason in reasons or []:
            if reason not in bucket:
                bucket.append(reason)
    restored["reasons_by_fund"] = reasons_by_fund
    facts["data_evidence_guard"] = restored


def _finalize_recommendations(
    parsed: dict,
    fallback: Report,
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
    facts: dict | None = None,
) -> tuple[list[str], list[FundRecommendation]]:
    raw_lines = parsed.get("recommendations")
    if isinstance(raw_lines, list) and raw_lines:
        all_lines = [str(item) for item in raw_lines]
    else:
        all_lines = list(fallback.recommendations)

    portfolio = portfolio_recommendation_lines(all_lines, request.holdings)
    if not portfolio:
        portfolio = portfolio_recommendation_lines(fallback.recommendations, request.holdings)

    fund_recs = parse_fund_recommendations_raw(
        parsed.get("fund_recommendations"), merge_items=False
    )
    if not fund_recs:
        fund_recs = group_strings_to_fund_recommendations(all_lines, request.holdings)
    if not fund_recs:
        fund_recs = list(fallback.fund_recommendations)

    if _response_incomplete(parsed):
        fund_recs = list(fallback.fund_recommendations)

    fund_recs = canonicalize_fund_recommendations(
        fund_recs,
        request.holdings,
        fallback_recommendations=fallback.fund_recommendations,
    )
    fund_recs = enrich_fund_recommendations(
        fund_recs,
        request,
        market_news,
        topic_briefs,
        merge_items=False,
    )
    portfolio, fund_recs = _apply_recommendation_guards_by_holding_order(
        fund_recs,
        portfolio,
        request,
        risk,
        market_news,
        nav_trends_by_code=nav_trends_by_code,
        facts=facts,
    )
    fund_recs = apply_news_citation_guards(fund_recs, market_news, topic_briefs)
    fund_recs = canonicalize_fund_recommendations(fund_recs, request.holdings)
    return portfolio, fund_recs


def _append_pipeline_caveats(caveats: list[str], facts: dict) -> list[str]:
    result = list(caveats)
    from app.services.decision_data_evidence import (
        portfolio_snapshot_caveats,
        report_execution_blocked,
    )

    for caveat in portfolio_snapshot_caveats(facts):
        if caveat not in result:
            result.append(caveat)
    if report_execution_blocked(facts):
        note = "关键信息完整性与更新时间校验未通过，系统已暂时隐藏仓位动作和调整比例。"
        if note not in result:
            result.append(note)
    pipeline = facts.get("pipeline") or {}
    session = facts.get("session") or {}
    mode = pipeline.get("analysis_mode")
    model = pipeline.get("model")
    if mode and model:
        judge_note = ""
        if pipeline.get("llm_judge_applied"):
            judge_note = "，深度审校已应用"
        elif pipeline.get("llm_judge_attempted"):
            judge_note = "，深度审校未生效"
        result.append(
            f"分析管线：{mode} 模式 / 模型 {model}{judge_note}；"
            f"当日要闻 {pipeline.get('today_news_count', 0)} 条。"
        )
    window = session.get("decision_window")
    if window and window not in result:
        result.append(window)
    trend = facts.get("portfolio_trend") or {}
    summary_line = trend.get("summary_line")
    if trend.get("has_history") and summary_line:
        result.append(summary_line)
    guard_policy = facts.get("guard_policy") or {}
    for line in guard_policy.get("backtest_summary_lines") or []:
        if line and line not in result:
            result.append(f"板块信号回测：{line}")
    policy_reason = guard_policy.get("reason")
    if policy_reason and policy_reason not in result:
        result.append(str(policy_reason))
    return result


def _offline_report(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
    *,
    analysis_bundle: AnalysisFactsBundle | None = None,
    provider_failure: ProviderFailure | None = None,
    attempted_model: str | None = None,
    prompt_contract: dict | None = None,
    provider_call_trace: dict | None = None,
    decision_at: datetime | None = None,
    announcement_meta: dict | None = None,
) -> Report:
    recommendations = []
    if risk.suggested_action == "risk_review":
        recommendations.append("组合已触发风险复核线，今日不建议新增加仓，先检查亏损来源和持仓集中度。")
    else:
        recommendations.append("未触发硬性止损线，建议保持观察，只有在仓位低于计划上限时考虑小额定投。")

    for alert in risk.alerts:
        recommendations.append(alert.message)

    news = market_news or []
    briefs = (
        topic_briefs
        if topic_briefs is not None
        else _build_topic_briefs(news, now=decision_at)
    )
    nav_trends = nav_trends_by_code or {}
    fund_recommendations = build_offline_fund_recommendations(
        request,
        news,
        nav_trends_by_code=nav_trends,
    )

    if not fund_recommendations and not recommendations:
        recommendations.append("当前信息不足以支持新增买入，建议等待净值、公告和市场信息更新。")

    runtime = resolve_analysis_runtime(get_settings(), request.analysis_mode)
    bundle = analysis_bundle or prepare_analysis_bundle(
        request,
        risk,
        snapshots,
        news,
        briefs,
        nav_trends,
        analysis_mode=runtime.mode,
        decision_at=decision_at,
    )
    if provider_failure is None:
        bundle.facts.setdefault("pipeline", {}).update(
            {
                "provider": "offline",
                "provider_status": "offline",
                "provider_attempted": False,
                "attempted_model": None,
            }
        )
    if provider_failure is not None:
        apply_provider_failure_to_facts(
            bundle.facts,
            failure=provider_failure,
            attempted_model=attempted_model or runtime.model,
            prompt_contract=prompt_contract,
        )
    if announcement_meta is not None:
        bundle.facts["fund_announcements"] = deepcopy(announcement_meta)
    recommendations, fund_recommendations = _apply_recommendation_guards_by_holding_order(
        fund_recommendations,
        recommendations,
        request,
        risk,
        news,
        nav_trends_by_code=nav_trends,
        facts=bundle.facts,
    )
    fund_recommendations = apply_news_citation_guards(
        fund_recommendations, news, briefs
    )
    fund_recommendations = canonicalize_fund_recommendations(
        fund_recommendations, request.holdings
    )
    if provider_failure is not None:
        _project_provider_failure_daily_recommendations(fund_recommendations)
        recommendations = [
            "模型服务暂不可用，本次仅保留观察与风险复核；请刷新数据后重新生成。"
        ]

    caveats = [
        "本报告仅用于个人投研辅助，不构成投资建议。",
        "OCR、第三方数据和模型分析都可能出错，实际操作前请人工核对。",
    ]
    if not news and get_settings().news_enabled:
        caveats.append("本次未能拉取到有效新闻条目，政策与资金面判断请结合公开信息人工复核。")
    caveats = _append_news_pipeline_caveats(caveats, briefs, news)

    facts = finalize_analysis_facts(
        bundle.facts,
        market_news=news,
        topic_briefs=briefs,
        pipeline=build_pipeline_metadata(
            runtime=runtime,
            market_news=news,
            topic_briefs=briefs,
            judge_meta={},
        ),
        decision_at=decision_at,
    )
    if provider_failure is not None:
        apply_provider_failure_to_facts(
            facts,
            failure=provider_failure,
            attempted_model=attempted_model or runtime.model,
            prompt_contract=prompt_contract,
        )
    if provider_call_trace is not None:
        facts.setdefault("pipeline", {})["provider_call_trace"] = (
            normalize_provider_call_trace(provider_call_trace)
        )
    if announcement_meta is not None:
        facts["fund_announcements"] = deepcopy(announcement_meta)
    caveats = _append_pipeline_caveats(caveats, facts)
    from app.services.decision_data_evidence import report_execution_blocked

    summary = (
        f"本地规则评估：组合加权收益率 {risk.weighted_return_percent:.2f}%，"
        f"风险等级为 {risk.level}。"
        + (f" 已抓取 {len(news)} 条近期新闻" if news else "")
        + (
            f"（{len(briefs)} 个主题摘要）。"
            if briefs
            else ("供参考。" if news else "")
        )
    )
    if report_execution_blocked(facts):
        summary = "关键持仓或行情数据未达到时点可用条件，本次只做观察和风险提示；请更新数据后重新生成。"
    if provider_failure is not None:
        summary = f"{summary}\n\n{provider_failure.message}"
        if provider_failure.message not in caveats:
            caveats.append(provider_failure.message)
    report = Report(
        **({"created_at": decision_at} if decision_at is not None else {}),
        title="每日基金操作日报",
        risk=risk,
        holdings=request.holdings,
        snapshots=snapshots,
        market_context=[],
        market_news=news,
        topic_briefs=briefs,
        summary=summary,
        fund_recommendations=fund_recommendations,
        recommendations=recommendations,
        caveats=caveats,
        provider="offline-fallback" if provider_failure is not None else "offline",
        analysis_facts=facts,
    )
    return report


def _project_provider_failure_daily_recommendations(
    recommendations: list[FundRecommendation],
) -> None:
    """Force provider fallbacks to a low-confidence, non-executable projection."""

    from app.services.decision_data_evidence import (
        contains_executable_decision_text,
        safe_blocked_points,
    )

    for recommendation in recommendations:
        original_action = recommendation.action
        recommendation.action = (
            "风控复核"
            if any(token in original_action for token in ("减仓", "清仓", "止损", "风控"))
            else "观察"
        )
        recommendation.amount_yuan = None
        recommendation.amount_note = None
        recommendation.confidence = "低"
        recommendation.suggested_position_change_percent = None
        recommendation.suggested_position_change_basis = ""
        recommendation.estimated_position_change_amount_yuan = None
        recommendation.points = safe_blocked_points(
            recommendation.points,
            fallback="模型服务不可用，本条仅保留低置信观察与风险复核。",
        )
        recommendation.decision_path = "Provider 调用失败，系统已阻断仓位动作并降为观察/风险复核。"
        recommendation.sector_evidence = [
            value
            for value in recommendation.sector_evidence
            if not contains_executable_decision_text(value)
        ]
        recommendation.fund_evidence = [
            value
            for value in recommendation.fund_evidence
            if not contains_executable_decision_text(value)
        ]
        recommendation.validation_notes = [
            value
            for value in recommendation.validation_notes
            if not contains_executable_decision_text(value)
        ] + ["模型服务失败，金额与仓位动作已被确定性阻断。"]
