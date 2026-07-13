from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import httpx

from app.config import get_settings
from app.models import DiscoveryRecommendation, FundDiscoveryReport, InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_runtime import AnalysisRuntime, resolve_analysis_runtime
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
    format_deepseek_http_error,
)
from app.services.deepseek_client import (
    FETCH_MARKET_NEWS_TOOL,
    _execute_fetch_market_news,
    _parse_model_json,
    tool_round_stage_label,
)
from app.services.discovery_guard import apply_discovery_guards
from app.services.discovery_judge import judge_parsed_discovery_report
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_payload import append_output_requirements_to_system, build_user_payload
from app.services.discovery_prompt import DEFAULT_DISCOVERY_ROLE_PROMPT, resolve_discovery_role_prompt
from app.services.news_service import NewsService, _dedupe_news
from app.services.retired_market_evidence import sanitize_retired_market_evidence

ProgressCallback = Callable[[str, str], None]

_DISCLAIMER = "仅供参考，不构成投资建议；基金有风险，决策需结合自身承受能力。"


class DiscoveryClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.news_service = NewsService()

    def run_discovery_news_tool_rounds(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        prefetched_news: list[NewsItem],
        runtime: AnalysisRuntime,
        on_stage: ProgressCallback | None = None,
    ) -> tuple[list[dict], list[NewsItem]]:
        """深度荐基：同步新闻 tool 轮，返回可供最终 JSON 流式补全的 messages。"""
        collected: list[NewsItem] = list(prefetched_news)
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        tools = (
            [FETCH_MARKET_NEWS_TOOL]
            if runtime.news_enabled and runtime.news_tool_max_rounds > 0
            else None
        )
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

    def generate_report(
        self,
        *,
        target_sectors: list[str],
        focus_sectors: list[str],
        scan_mode: str = "full_market",
        candidate_pool: list[dict],
        discovery_facts: dict,
        profile: InvestorProfile,
        held_codes: set[str],
        budget_yuan: float,
        sector_heat: list[dict],
        market_news: list[NewsItem] | None = None,
        topic_briefs: list[TopicBrief] | None = None,
        analysis_mode: str = "deep",
        system_role_prompt: str | None = None,
    ) -> FundDiscoveryReport:
        if not self.settings.deepseek_api_key:
            return build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=candidate_pool,
                discovery_facts=discovery_facts,
                profile=profile,
                focus_sectors=focus_sectors,
                analysis_mode=analysis_mode,
            )

        runtime = resolve_analysis_runtime(self.settings, analysis_mode)  # type: ignore[arg-type]
        user_payload = build_user_payload(
            discovery_facts=discovery_facts,
            profile=profile,
            focus_sectors=focus_sectors,
            scan_mode=scan_mode,
            market_news=market_news,
            topic_briefs=topic_briefs,
            analysis_mode=analysis_mode,  # type: ignore[arg-type]
        )
        system_prompt = append_output_requirements_to_system(
            self._system_prompt(runtime.news_tool_max_rounds > 0, system_role_prompt)
        )
        parsed = self._call_model(system_prompt, user_payload, runtime.model)
        # M4：deep 模式风控复核角色（fast 模式内部直接短路返回，零新增 LLM 调用）。
        parsed, _judge_meta = judge_parsed_discovery_report(
            parsed,
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
            analysis_mode=analysis_mode,
        )
        return build_discovery_report_from_parsed(
            parsed,
            target_sectors=target_sectors,
            focus_sectors=focus_sectors,
            scan_mode=scan_mode,
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
            profile=profile,
            held_codes=held_codes,
            budget_yuan=budget_yuan,
            sector_heat=sector_heat,
            market_news=market_news,
            topic_briefs=topic_briefs,
            analysis_mode=analysis_mode,
        )

    def _system_prompt(self, news_tool_enabled: bool, role_prompt: str | None = None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        base_role = resolve_discovery_role_prompt(role_prompt)
        base = (
            f"{base_role}\n\n当前时间：{now}。"
            "你只能提供个人研究与风险提示，不能承诺收益。"
        )
        if news_tool_enabled:
            base += "深度模式可结合 user 消息中的 news 主题摘要；引用新闻须为已有标题。"
        return base

    def _call_model(self, system_prompt: str, user_payload: dict, model: str) -> dict:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        try:
            with httpx.Client(timeout=deepseek_timeout(self.settings)) as client:
                response = client.post(
                    deepseek_chat_url(self.settings),
                    headers=deepseek_request_headers(self.settings),
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(format_deepseek_http_error(exc)) from exc

        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            raise RuntimeError("DeepSeek 返回空内容")
        return _parse_model_json(content)

    def _chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        body: dict = {
            "model": model or self.settings.deepseek_model,
            "messages": messages,
            "temperature": 0.3,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        try:
            with httpx.Client(timeout=deepseek_timeout(self.settings)) as client:
                response = client.post(
                    deepseek_chat_url(self.settings),
                    headers=deepseek_request_headers(self.settings),
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(format_deepseek_http_error(exc)) from exc
        return payload.get("choices", [{}])[0].get("message", {})


def build_discovery_chat_messages(
    system_prompt: str,
    user_payload: dict,
) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def build_discovery_report_from_parsed(
    parsed: dict,
    *,
    target_sectors: list[str],
    focus_sectors: list[str],
    scan_mode: str,
    candidate_pool: list[dict],
    discovery_facts: dict,
    profile: InvestorProfile,
    held_codes: set[str],
    budget_yuan: float,
    sector_heat: list[dict],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    analysis_mode: str = "fast",
) -> FundDiscoveryReport:
    parsed = sanitize_retired_market_evidence(parsed)
    recommendations = _parse_recommendations(parsed.get("recommendations"))
    guarded, guard_caveats, eliminated = apply_discovery_guards(
        recommendations,
        candidate_pool=candidate_pool,
        held_codes=held_codes,
        profile=profile,
        budget_yuan=budget_yuan,
        sector_heat=sector_heat,
        discovery_facts=discovery_facts,
        market_news=market_news,
        topic_briefs=topic_briefs,
        scan_mode=scan_mode,
    )
    caveats = _as_str_list(parsed.get("caveats"))
    caveats.extend(guard_caveats)
    from app.services.decision_data_evidence import (
        portfolio_snapshot_caveats,
        report_execution_blocked,
    )

    caveats.extend(item for item in portfolio_snapshot_caveats(discovery_facts) if item not in caveats)
    if _DISCLAIMER not in caveats:
        caveats.insert(0, _DISCLAIMER)
    summary = str(parsed.get("summary") or "")
    market_view = str(parsed.get("market_view") or "")
    if report_execution_blocked(discovery_facts):
        summary = "字段级证据时点校验未通过，本次仅保留观察候选；请刷新持仓与候选数据后重新扫描。"
        market_view = "当前证据只足以描述市场背景，不支持买入方向或金额判断。"

    return FundDiscoveryReport(
        title=str(parsed.get("title") or "今日基金机会扫描"),
        summary=summary,
        market_view=market_view,
        focus_sectors=focus_sectors,
        target_sectors=target_sectors,
        candidate_pool=candidate_pool,
        recommendations=guarded,
        discovery_facts=discovery_facts,
        caveats=caveats,
        eliminated_candidates=eliminated,
        provider="deepseek",
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
    )


def _parse_recommendations(raw: object) -> list[DiscoveryRecommendation]:
    if not isinstance(raw, list):
        return []
    results: list[DiscoveryRecommendation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("fund_code", "")).strip().zfill(6)
        if not code.isdigit():
            continue
        results.append(
            DiscoveryRecommendation(
                fund_code=code,
                fund_name=str(item.get("fund_name", "")),
                sector_name=str(item.get("sector_name", "")),
                action=str(item.get("action") or "建议关注"),
                suggested_amount_yuan=_as_float(item.get("suggested_amount_yuan")),
                amount_note=_optional_str(item.get("amount_note")),
                hold_horizon=str(item.get("hold_horizon") or ""),
                confidence=str(item.get("confidence") or "中"),
                points=_as_str_list(item.get("points")),
                risks=_as_str_list(item.get("risks")),
                news_bullish=_as_str_list(item.get("news_bullish")),
                decision_path=str(item.get("decision_path") or ""),
                sector_evidence=_as_str_list(item.get("sector_evidence")),
                fund_evidence=_as_str_list(item.get("fund_evidence")),
                validation_notes=_as_str_list(item.get("validation_notes")),
            )
        )
    return results


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
