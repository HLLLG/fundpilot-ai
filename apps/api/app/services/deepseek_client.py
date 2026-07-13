from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime

import httpx

from app.config import get_settings
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
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
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    append_output_requirements_to_system,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
)
from app.services.news_service import NewsService, _dedupe_news
from app.services.news_summarizer import summarize_all_topics
from app.services.news_citation import apply_news_citation_guards
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.report_judge import judge_parsed_report
from app.services.report_pipeline import build_pipeline_metadata
from app.services.recommendations import (
    build_offline_fund_recommendation,
    enrich_fund_recommendations,
    group_strings_to_fund_recommendations,
    parse_fund_recommendations_raw,
    portfolio_recommendation_lines,
)

ProgressCallback = Callable[[str, str], None]

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
            "用户通常在交易日 14:30 左右分析、15:00 收盘前决策，请优先拉取当日新闻。"
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

    def generate_report(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        nav_trends_by_code: dict[str, dict] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Report:
        def progress(stage: str) -> None:
            if on_progress is not None:
                on_progress(stage, JOB_STAGES.get(stage, stage))

        nav_trends = nav_trends_by_code or {}
        runtime = resolve_analysis_runtime(self.settings, request.analysis_mode)
        progress("news_prefetch")
        market_news = self.news_service.prefetch_for_holdings(
            request.holdings,
            max_topics=runtime.news_max_topics,
        )
        progress("news_summarize")
        topic_briefs = _build_topic_briefs(market_news, self.settings)
        if not self.settings.deepseek_configured:
            return _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
            )

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
            )
            progress("judging")
            parsed, judge_meta = judge_parsed_report(
                parsed, request, risk, snapshots, runtime,
                facts=analysis_bundle.facts,
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
            )
        except httpx.TimeoutException as exc:
            fallback = _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
            )
            fallback.summary = (
                f"{fallback.summary}\n\nDeepSeek 调用超时：{exc}。"
                f"当前 read timeout 为 {self.settings.deepseek_timeout_seconds:.0f} 秒。"
                "可以调大 FUND_AI_DEEPSEEK_TIMEOUT_SECONDS，或将模型切换为 deepseek-v4-flash 提升速度。"
            )
            fallback.provider = "offline-fallback"
            return fallback
        except httpx.HTTPStatusError as exc:
            fallback = _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
            )
            fallback.summary = (
                f"{fallback.summary}\n\nDeepSeek HTTP 错误：{exc.response.status_code} "
                f"{exc.response.text[:300]}"
            )
            fallback.provider = "offline-fallback"
            return fallback
        except Exception as exc:
            fallback = _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
            )
            fallback.summary = (
                f"{fallback.summary}\n\nDeepSeek 调用失败，已使用本地规则生成报告：{exc}"
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
        )
        collected: list[NewsItem] = list(prefetched_news)
        news_enabled = runtime.news_enabled
        messages: list[dict] = [
            {
                "role": "system",
                "content": _system_prompt(
                    news_enabled,
                    request.profile.decision_style,
                    request.system_role_prompt,
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
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    }
                )

        return messages, _dedupe_news(collected)

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
        )
        parsed = self._generate_report_json(messages, runtime)
        return parsed, _dedupe_news(prefetched_news)

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
        )

        return self._generate_report_json(messages, runtime), _dedupe_news(collected)

    def _generate_report_json(
        self,
        messages: list[dict],
        runtime: AnalysisRuntime,
    ) -> dict:
        message = self._chat_completion(
            messages=messages,
            tools=None,
            response_format={"type": "json_object"},
            max_tokens=self.settings.deepseek_max_tokens_report,
            model=runtime.model,
        )
        final_content = message.get("content") or ""

        parsed = _parse_model_json(final_content)
        if _response_incomplete(parsed):
            retry_message = self._chat_completion(
                messages=messages
                + [
                    {
                        "role": "user",
                        "content": (
                            "上次 JSON 输出不完整。请仅输出完整 JSON，"
                            "包含 title、summary、fund_recommendations、caveats；"
                            "每只基金一条，points 每条不超过 60 字，总输出尽量精炼。"
                        ),
                    }
                ],
                tools=None,
                response_format={"type": "json_object"},
                max_tokens=self.settings.deepseek_max_tokens_report,
                model=runtime.model,
            )
            parsed = _parse_model_json(retry_message.get("content") or "")

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
        payload = _build_chat_payload(
            messages=messages,
            model=model or self.settings.deepseek_model,
            max_tokens=max_tokens or self.settings.deepseek_max_tokens,
            tools=tools,
            response_format=response_format,
        )
        response = httpx.post(
            deepseek_chat_url(self.settings),
            headers=deepseek_request_headers(self.settings),
            json=payload,
            timeout=deepseek_timeout(self.settings),
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]


def _system_prompt(
    news_enabled: bool,
    decision_style: str = "conservative",
    system_role_prompt: str | None = None,
) -> str:
    from app.services.analysis_prompt import resolve_role_prompt

    now = datetime.now()
    tactical = decision_style == "tactical"
    aggressive = decision_style == "aggressive"
    base = resolve_role_prompt(system_role_prompt)
    base += f"当前分析时点约为 {now.strftime('%Y-%m-%d %H:%M')}。"
    if news_enabled:
        base += (
            "用户消息中 topic_briefs 为按主题预摘要（优先阅读），news_titles 为可引用新闻标题列表；"
            "利好/利空标题须能在 news_titles 或 topic_briefs.points.source_titles 中找到对应。"
            "优先采用当日新闻，前几日仅作背景并标注日期，避免用旧闻主导结论。"
            "如需补充可调用 fetch_market_news，但不要重复拉取已有主题。"
        )
    else:
        base += "若无新闻数据，须说明信息缺口并给出条件化方案。"
    if aggressive:
        base += (
            "当前为激进波段模式：持有周期约 3～7 天，跌深企稳可分批买入，"
            "持有收益达扣费止盈线（手续费 + 净赚目标，见 profile 或 analysis_facts.portfolio.take_profit_threshold_percent）"
            "优先减仓落袋；须结合 nav_trend.recent_5d_change_percent、sector_intraday 分时企稳与 sector_return_percent；"
            "不追涨当日大涨板块，不得承诺收益。"
        )
    elif tactical:
        base += (
            "当前为战术短线模式：在遵守集中度与风险复核前提下，优先最大化当日收盘前与下一交易日的战术收益空间；"
            "须结合 sector_intraday（分时形态）、sector_momentum（涨后回吐等）、stock_connect_flow 与 news.freshness_label；"
            "北向实时净买额不参与决策，stock_connect_flow 中南向数据仅作港股资金面的独立参考；"
            "对「涨一天跌一天」场景须明确次日冲高回落时的止盈/观望条件，但仍不得承诺收益。"
        )
        settings = get_settings()
        if settings.tactical_prompt_tuning_enabled:
            from app.services.prompt_tuning import resolve_prompt_tuning_hints

            tuning = resolve_prompt_tuning_hints(
                lookback_reports=settings.tactical_prompt_tuning_lookback_reports,
            )
            for hint in tuning.get("hints") or []:
                base += hint
    else:
        base += "当前为稳健模式：偏保守，避免追涨，加仓需有当日要闻或明确盘面支撑。"
    base += "最终回复必须是完整 JSON，不要 Markdown，控制篇幅避免截断。"
    return append_output_requirements_to_system(base)


def _build_topic_briefs(
    market_news: list[NewsItem],
    settings: object | None = None,
) -> list[TopicBrief]:
    resolved = settings or get_settings()
    if not market_news or not getattr(resolved, "news_summarize", True):
        return []
    return summarize_all_topics(market_news, resolved, offline_only=True)  # type: ignore[arg-type]


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
) -> list[dict]:
    """fast 模式流式路径：无 tool calling，直接 JSON 输出。"""
    return [
        {
            "role": "system",
            "content": _system_prompt(
                runtime.news_enabled,
                request.profile.decision_style,
                request.system_role_prompt,
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
                ),
                ensure_ascii=False,
            ),
        },
    ]


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
) -> Report:
    fallback = _offline_report(
        request,
        risk,
        snapshots,
        market_news=market_news,
        topic_briefs=topic_briefs,
        nav_trends_by_code=nav_trends,
        analysis_bundle=analysis_bundle,
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
    facts = finalize_analysis_facts(
        analysis_bundle.facts,
        market_news=market_news,
        topic_briefs=topic_briefs,
        pipeline=build_pipeline_metadata(
            runtime=runtime,
            market_news=market_news,
            topic_briefs=topic_briefs,
            judge_meta=judge_meta,
        ),
    )
    caveats = _user_facing_caveats(
        _non_empty_list(parsed.get("caveats"), fallback.caveats)
    )
    caveats = _append_news_pipeline_caveats(caveats, topic_briefs, market_news)
    caveats = _append_pipeline_caveats(caveats, facts)
    from app.services.decision_data_evidence import report_execution_blocked

    summary = parsed.get("summary") or fallback.summary
    if report_execution_blocked(facts):
        summary = "字段级证据时点校验未通过，本次报告仅保留观察与风险复核；请刷新持仓和行情后重新生成。"
    return Report(
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
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
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

    items = news_service.search(topic, limit=limit)
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

    fund_recs = parse_fund_recommendations_raw(parsed.get("fund_recommendations"))
    if not fund_recs:
        fund_recs = group_strings_to_fund_recommendations(all_lines, request.holdings)
    if not fund_recs:
        fund_recs = list(fallback.fund_recommendations)

    if _response_incomplete(parsed):
        fund_recs = list(fallback.fund_recommendations)

    fund_recs = enrich_fund_recommendations(
        fund_recs, request, market_news, topic_briefs
    )
    portfolio, fund_recs = apply_recommendation_guards(
        fund_recs,
        portfolio,
        request,
        risk,
        market_news,
        topic_briefs,
        nav_trends_by_code=nav_trends_by_code,
        facts=facts,
    )
    fund_recs = apply_news_citation_guards(fund_recs, market_news, topic_briefs)
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
        note = "字段级证据时点校验未通过，系统已隐藏仓位动作、金额及相关可执行措辞。"
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
    decision_style = (facts.get("portfolio") or {}).get("decision_style", "conservative")
    if (
        decision_style not in {"tactical", "aggressive"}
        and pipeline.get("has_today_market_signal") is False
        and get_settings().news_require_today_for_add
    ):
        result.append("当日无已标注「今日」的要闻，系统已限制激进加仓类建议。")
    elif decision_style == "tactical":
        result.append("战术短线模式已启用：守卫未因缺少当日要闻而压制加仓类建议，请自行承担短线波动风险。")
    elif decision_style == "aggressive":
        threshold = (facts.get("portfolio") or {}).get("take_profit_threshold_percent", 2.5)
        result.append(
            f"激进波段模式已启用：跌深买入、达 {threshold}% 扣费止盈线优先减仓，请自行承担短线波动风险。"
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
) -> Report:
    recommendations = []
    if risk.suggested_action == "risk_review":
        recommendations.append("组合已触发风险复核线，今日不建议新增加仓，先检查亏损来源和持仓集中度。")
    else:
        recommendations.append("未触发硬性止损线，建议保持观察，只有在仓位低于计划上限时考虑小额定投。")

    for alert in risk.alerts:
        recommendations.append(alert.message)

    news = market_news or []
    briefs = topic_briefs if topic_briefs is not None else _build_topic_briefs(news)
    from app.services.risk import holding_weight_percent, resolve_weight_denominator

    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    nav_trends = nav_trends_by_code or {}
    fund_recommendations = [
        build_offline_fund_recommendation(
            holding,
            holding_weight_percent(holding, request.holdings, request.profile),
            weight_denominator,
            request.profile,
            market_news=news,
            nav_trend=nav_trends.get(holding.fund_code),
        )
        for holding in request.holdings
    ]

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
    )
    recommendations, fund_recommendations = apply_recommendation_guards(
        fund_recommendations,
        recommendations,
        request,
        risk,
        news,
        briefs,
        nav_trends_by_code=nav_trends,
        facts=bundle.facts,
    )
    fund_recommendations = apply_news_citation_guards(
        fund_recommendations, news, briefs
    )

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
    )
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
        summary = "字段级证据时点校验未通过，本次仅保留观察与风险复核；请刷新数据后重新生成。"
    return Report(
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
        provider="offline",
        analysis_facts=facts,
    )
