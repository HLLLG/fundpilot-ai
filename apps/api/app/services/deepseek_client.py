from __future__ import annotations

import json
import re
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
from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS, holding_analysis_payload
from app.services.news_service import NewsService, _dedupe_news
from app.services.news_summarizer import summarize_all_topics
from app.services.analysis_facts import build_analysis_facts
from app.services.news_citation import apply_news_citation_guards
from app.services.portfolio_snapshot import build_portfolio_trend_context
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.report_judge import judge_parsed_report
from app.services.report_pipeline import build_pipeline_metadata
from app.services.trading_session import build_trading_session
from app.services.recommendations import (
    build_offline_fund_recommendation,
    enrich_fund_recommendations,
    group_strings_to_fund_recommendations,
    parse_fund_recommendations_raw,
    portfolio_recommendation_lines,
)

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
    ) -> Report:
        nav_trends = nav_trends_by_code or {}
        runtime = resolve_analysis_runtime(self.settings, request.analysis_mode)
        market_news = self.news_service.prefetch_for_holdings(
            request.holdings,
            max_topics=runtime.news_max_topics,
        )
        topic_briefs = _build_topic_briefs(market_news, self.settings)
        initial_news_count = len(market_news)

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
            parsed, market_news = self._generate_with_tools(
                request,
                risk,
                snapshots,
                market_news,
                topic_briefs,
                runtime,
                nav_trends,
            )
            if len(market_news) > initial_news_count:
                topic_briefs = _build_topic_briefs(market_news, self.settings)
            parsed, judge_meta = judge_parsed_report(parsed, request, risk, snapshots, runtime)
            fallback = _offline_report(
                request,
                risk,
                snapshots,
                market_news=market_news,
                topic_briefs=topic_briefs,
                nav_trends_by_code=nav_trends,
            )
            portfolio_recs, fund_recs = _finalize_recommendations(
                parsed, fallback, request, risk, market_news, topic_briefs
            )
            facts = _compose_analysis_facts(
                request=request,
                risk=risk,
                snapshots=snapshots,
                topic_briefs=topic_briefs,
                nav_trends=nav_trends,
                runtime=runtime,
                market_news=market_news,
                judge_meta=judge_meta,
            )
            caveats = _user_facing_caveats(
                _non_empty_list(parsed.get("caveats"), fallback.caveats)
            )
            caveats = _append_news_pipeline_caveats(caveats, topic_briefs, market_news)
            caveats = _append_pipeline_caveats(caveats, facts)
            return Report(
                title=parsed.get("title", "每日基金操作日报"),
                risk=risk,
                holdings=request.holdings,
                snapshots=snapshots,
                market_context=[],
                market_news=market_news,
                topic_briefs=topic_briefs,
                fund_recommendations=fund_recs,
                summary=parsed.get("summary") or fallback.summary,
                recommendations=portfolio_recs,
                caveats=caveats,
                provider=runtime.model,
                analysis_facts=facts,
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

    def _generate_with_tools(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        prefetched_news: list[NewsItem],
        topic_briefs: list[TopicBrief],
        runtime: AnalysisRuntime,
        nav_trends_by_code: dict[str, dict] | None = None,
    ) -> tuple[dict, list[NewsItem]]:
        nav_trends = nav_trends_by_code or {}
        collected: list[NewsItem] = list(prefetched_news)
        news_enabled = runtime.news_enabled
        messages: list[dict] = [
            {"role": "system", "content": _system_prompt(news_enabled)},
            {
                "role": "user",
                "content": json.dumps(
                    _user_payload(
                        request,
                        risk,
                        snapshots,
                        prefetched_news,
                        topic_briefs,
                        nav_trends,
                    ),
                    ensure_ascii=False,
                ),
            },
        ]
        tools = [FETCH_MARKET_NEWS_TOOL] if news_enabled and runtime.news_tool_max_rounds > 0 else None
        max_rounds = min(runtime.news_tool_max_rounds, 1) if tools else 0

        final_content = ""
        for round_index in range(max_rounds + 1):
            allow_tools = tools is not None and round_index < max_rounds
            message = self._chat_completion(
                messages=messages,
                tools=tools if allow_tools else None,
                response_format=None if allow_tools else {"type": "json_object"},
                max_tokens=(
                    self.settings.deepseek_max_tokens
                    if allow_tools
                    else self.settings.deepseek_max_tokens_report
                ),
                model=runtime.model,
            )
            tool_calls = message.get("tool_calls")

            if tool_calls and allow_tools:
                messages.append(message)
                for tool_call in tool_calls:
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
                continue

            final_content = message.get("content") or ""
            break
        else:
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

        return parsed, _dedupe_news(collected)

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


def _system_prompt(news_enabled: bool) -> str:
    now = datetime.now()
    base = (
        "你是个人基金投研助手，只能提供个人研究和风险提示，不能承诺收益。"
        f"当前分析时点约为 {now.strftime('%Y-%m-%d %H:%M')}，用户通常在交易日 14:30 左右上传养基宝截图，"
        "需要在 15:00 A 股收盘前给出当日是否加仓/减仓/观察的决策。"
        "必须结合持仓、当日收益、板块涨跌、集中度、净值快照、analysis_facts 中的 nav_trend（近 N 日净值摘要）与新闻（优先当日）做分析。"
        "养基宝截图中：关联板块涨跌为当日实时值；持有收益率为昨日结算值。"
        "若 holdings 中 daily_return_percent 为空，请用 estimated_daily_return_percent"
        "（≈ sector_return_percent + holding_return_percent）近似当日基金涨跌，"
        "并在 points 中注明为估算；勿与 holding_return_percent 重复当作当日涨跌。"
    )
    if news_enabled:
        base += (
            "用户消息中 topic_briefs 为按主题预摘要（优先阅读），prefetched_news 为原始出处列表；"
            "利好/利空标题须能在 prefetched_news 或 topic_briefs.points.source_titles 中找到对应。"
            "优先采用当日新闻，前几日仅作背景并标注日期，避免用旧闻主导结论。"
            "如需补充可调用 fetch_market_news，但不要重复拉取已有主题。"
        )
    else:
        base += "若无新闻数据，须说明信息缺口并给出条件化方案。"
    base += "最终回复必须是完整 JSON，不要 Markdown，控制篇幅避免截断。"
    return base


def _build_topic_briefs(
    market_news: list[NewsItem],
    settings: object | None = None,
) -> list[TopicBrief]:
    resolved = settings or get_settings()
    if not market_news or not getattr(resolved, "news_summarize", True):
        return []
    return summarize_all_topics(market_news, resolved)  # type: ignore[arg-type]


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


def _user_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    prefetched_news: list[NewsItem],
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
) -> dict:
    briefs = topic_briefs or []
    nav_trends = nav_trends_by_code or {}
    session = build_trading_session()
    facts = build_analysis_facts(
        request.holdings,
        risk,
        snapshots,
        request.profile,
        briefs,
        nav_trends,
        session=session,
        portfolio_trend=build_portfolio_trend_context(),
    )
    return {
        "today": datetime.now().date().isoformat(),
        "analysis_session": session["session_kind"],
        "session": session,
        "profile": request.profile.model_dump(),
        "holding_return_semantics": HOLDING_RETURN_SEMANTICS,
        "analysis_facts": facts,
        "holdings": [holding_analysis_payload(holding) for holding in request.holdings],
        "risk": risk.model_dump(),
        "fund_snapshots": [snapshot.model_dump() for snapshot in snapshots],
        "ocr_text": request.ocr_text,
        "prefetched_news": [item.model_dump() for item in prefetched_news],
        "topic_briefs": [item.model_dump(mode="json") for item in briefs],
        "requirements": [
            "analysis_facts 为系统计算的只读事实，不得改写其中任何数字",
            "输出 title、summary、fund_recommendations、caveats 四个字段",
            "fund_recommendations 每只基金恰好 1 条",
            "每条字段：fund_code、fund_name、action、amount_yuan（可选）、amount_note（可选）、news_bullish（利好标题数组）、news_bearish（利空/风险标题数组）、points（1-3 条，每条≤60字）",
            "优先依据 topic_briefs 理解板块与宏观背景；news_bullish/news_bearish 须来自 prefetched_news 标题或 topic_briefs.points.source_titles；无则写「暂无明确利好/利空」",
            "须遵循 session.decision_window 与 session.session_kind 调整措辞，非 trading_day_pre_close 时不要写「收盘前必须今日下单」",
            "收盘前决策：action 仅限 观察/暂停追涨/分批加仓/减仓评估/风控复核 五选一",
            "若 risk.suggested_action 为 risk_review 或 level 为 high，禁止给出加仓类 action",
            "涉及加仓/减仓须给 amount_yuan 或 amount_note（结合 holding_amount 与 concentration_limit_percent）",
            "recommendations 可省略或仅 1 条组合级说明，禁止长新闻摘要堆砌",
            "旧新闻仅作参考；判断当日涨跌优先 daily_return_percent，否则用 estimated_daily_return_percent",
            "引用当日涨跌时区分：板块 sector_return_percent、昨日结算 holding_return_percent、估算/实际当日收益",
            "基金代码 000000 须提示补全代码",
            "偏稳健，避免追涨，不做实盘交易指令",
            "analysis_facts.holdings[].nav_trend 为近 N 交易日净值摘要（含 trend_label、距高点距离、recent_nav_series），用于判断反弹/回落与区间位置；不得编造未给出的净值序列",
            "若 nav_trend 为空（如基金代码 000000），须在 points 中说明无法使用净值走势",
        ],
    }


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


def _build_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    model: str,
    max_tokens: int,
) -> dict:
    """Legacy single-shot payload (tests)."""
    return _build_chat_payload(
        messages=[
            {"role": "system", "content": _system_prompt(get_settings().news_enabled)},
            {
                "role": "user",
                "content": json.dumps(
                    _user_payload(request, risk, snapshots, []),
                    ensure_ascii=False,
                ),
            },
        ],
        model=model,
        max_tokens=max_tokens,
        tools=None,
        response_format={"type": "json_object"},
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
        fund_recs, portfolio, request, risk, market_news, topic_briefs
    )
    fund_recs = apply_news_citation_guards(fund_recs, market_news, topic_briefs)
    return portfolio, fund_recs


def _compose_analysis_facts(
    *,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    topic_briefs: list[TopicBrief] | None,
    nav_trends: dict[str, dict],
    runtime: AnalysisRuntime,
    market_news: list[NewsItem] | None,
    judge_meta: dict,
) -> dict:
    return build_analysis_facts(
        request.holdings,
        risk,
        snapshots,
        request.profile,
        topic_briefs,
        nav_trends,
        session=build_trading_session(),
        pipeline=build_pipeline_metadata(
            runtime=runtime,
            market_news=market_news,
            topic_briefs=topic_briefs,
            judge_meta=judge_meta,
        ),
        portfolio_trend=build_portfolio_trend_context(),
    )


def _append_pipeline_caveats(caveats: list[str], facts: dict) -> list[str]:
    result = list(caveats)
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
    if pipeline.get("has_today_market_signal") is False and get_settings().news_require_today_for_add:
        result.append("当日无已标注「今日」的要闻，系统已限制激进加仓类建议。")
    window = session.get("decision_window")
    if window and window not in result:
        result.append(window)
    trend = facts.get("portfolio_trend") or {}
    summary_line = trend.get("summary_line")
    if trend.get("has_history") and summary_line:
        result.append(summary_line)
    return result


def _format_decision_recommendation(
    *,
    fund_name: str,
    action: str,
    weight: float,
    daily: str,
    daily_return: str,
    holding_profit: str,
    holding_return: str,
    sector: str,
    sector_change: str,
    fund_code: str,
) -> str:
    code_gap = "；需补全基金代码后核对净值/公告" if fund_code == "000000" else ""
    return (
        f"{fund_name}｜决策：{action}｜依据：仓位{weight:.1f}%，"
        f"当日{daily}/{daily_return}，持有{holding_profit}/{holding_return}，"
        f"板块{sector}{sector_change}｜触发：集中度、当日异动与持有收益背离复核"
        f"｜风险：追涨、单一主题拥挤和数据缺口{code_gap}"
    )


def _offline_report(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
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
    total_amount = sum(holding.holding_amount for holding in request.holdings) or 1
    fund_recommendations = [
        build_offline_fund_recommendation(
            holding,
            holding.holding_amount / total_amount * 100,
            total_amount,
            request.profile,
            market_news=news,
        )
        for holding in request.holdings
    ]

    if not fund_recommendations and not recommendations:
        recommendations.append("当前信息不足以支持新增买入，建议等待净值、公告和市场信息更新。")

    runtime = resolve_analysis_runtime(get_settings(), request.analysis_mode)
    recommendations, fund_recommendations = apply_recommendation_guards(
        fund_recommendations,
        recommendations,
        request,
        risk,
        news,
        briefs,
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

    facts = _compose_analysis_facts(
        request=request,
        risk=risk,
        snapshots=snapshots,
        topic_briefs=briefs,
        nav_trends=nav_trends_by_code or {},
        runtime=runtime,
        market_news=news,
        judge_meta={},
    )
    caveats = _append_pipeline_caveats(caveats, facts)
    return Report(
        title="每日基金操作日报",
        risk=risk,
        holdings=request.holdings,
        snapshots=snapshots,
        market_context=[],
        market_news=news,
        topic_briefs=briefs,
        summary=(
            f"本地规则评估：组合加权收益率 {risk.weighted_return_percent:.2f}%，"
            f"风险等级为 {risk.level}。"
            + (f" 已抓取 {len(news)} 条近期新闻" if news else "")
            + (
                f"（{len(briefs)} 个主题摘要）。"
                if briefs
                else ("供参考。" if news else "")
            )
        ),
        fund_recommendations=fund_recommendations,
        recommendations=recommendations,
        caveats=caveats,
        provider="offline",
        analysis_facts=facts,
    )
