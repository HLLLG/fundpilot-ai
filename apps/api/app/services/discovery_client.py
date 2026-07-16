from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime

import httpx

from app.config import get_settings
from app.request_context import get_request_user_id
from app.models import DiscoveryRecommendation, FundDiscoveryReport, InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_runtime import AnalysisRuntime, resolve_analysis_runtime
from app.services.deepseek_http import (
    ProviderOutputError,
    classify_deepseek_failure,
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
)
from app.services.deepseek_client import (
    FETCH_MARKET_NEWS_TOOL,
    REPORT_RESPONSE_FORMAT,
    _build_chat_payload,
    _execute_fetch_market_news,
    _is_valid_discovery_report_payload,
    _parse_model_json,
    tool_round_stage_label,
)
from app.services.discovery_guard import apply_discovery_guards
from app.services.discovery_allocation_service import (
    apply_deterministic_discovery_allocation,
    prepare_recommendations_for_deterministic_allocation,
)
from app.services.discovery_judge import judge_parsed_discovery_report
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_payload import append_output_requirements_to_system, build_user_payload
from app.services.discovery_prompt import DEFAULT_DISCOVERY_ROLE_PROMPT, resolve_discovery_role_prompt
from app.services.discovery_prompt import build_discovery_prompt_contract
from app.services.news_service import NewsService, _dedupe_news
from app.services.news_freshness import resolve_decision_local_datetime
from app.services.retired_market_evidence import sanitize_retired_market_evidence
from app.services.prompt_provenance import (
    build_prompt_contract as freeze_prompt_contract,
    content_hash,
    with_judge_result,
)
from app.services.provider_call_trace import (
    ProviderCallTraceCollector,
    provider_request_id_from_headers,
)
from app.services.decision_contract import POLICY_VERSION
from app.services.decision_repository import canonical_json
from app.services.report_pipeline import build_pipeline_metadata

ProgressCallback = Callable[[str, str], None]
logger = logging.getLogger(__name__)

_DISCLAIMER = "仅供参考，不构成投资建议；基金有风险，决策需结合自身承受能力。"


class DiscoveryClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.news_service = NewsService()
        self._prompt_shadow_capture = None
        self._last_report_raw_content: str | None = None
        self._last_report_parsed_payload: dict | None = None

    def run_discovery_news_tool_rounds(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        prefetched_news: list[NewsItem],
        runtime: AnalysisRuntime,
        on_stage: ProgressCallback | None = None,
        decision_at: datetime | None = None,
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
        decision_at: datetime | None = None,
        announcement_meta: dict | None = None,
    ) -> FundDiscoveryReport:
        if announcement_meta is not None:
            discovery_facts["fund_announcements"] = deepcopy(announcement_meta)
        runtime = resolve_analysis_runtime(self.settings, analysis_mode)  # type: ignore[arg-type]
        base_pipeline = build_pipeline_metadata(
            runtime=runtime,
            market_news=market_news,
            topic_briefs=topic_briefs,
            judge_meta={},
        )
        base_pipeline.update(
            {
                "provider": "offline" if not self.settings.deepseek_api_key else "deepseek",
                "provider_status": "offline" if not self.settings.deepseek_api_key else "pending",
                "attempted_model": None if not self.settings.deepseek_api_key else runtime.model,
            }
        )
        discovery_facts["pipeline"] = base_pipeline
        if not self.settings.deepseek_api_key:
            return build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=candidate_pool,
                discovery_facts=discovery_facts,
                profile=profile,
                focus_sectors=focus_sectors,
                analysis_mode=analysis_mode,
                decision_at=decision_at,
            )

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
            self._system_prompt(
                runtime.news_tool_max_rounds > 0,
                system_role_prompt,
                session=discovery_facts.get("session"),
            )
        )
        messages = build_discovery_chat_messages(system_prompt, user_payload)
        self._prompt_shadow_capture = None
        self._last_report_raw_content = None
        self._last_report_parsed_payload = None
        try:
            from app.services.prompt_shadow_service import (
                PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT,
                prepare_prompt_shadow_champion,
            )

            challenger_system_prompt = append_output_requirements_to_system(
                self._system_prompt(
                    False,
                    PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT,
                    session=discovery_facts.get("session"),
                )
            )
            self._prompt_shadow_capture = prepare_prompt_shadow_champion(
                user_id=get_request_user_id(),
                transport="sync",
                champion_system_prompt=system_prompt,
                challenger_system_prompt=challenger_system_prompt,
                user_payload=user_payload,
                model=runtime.model,
                max_tokens=self.settings.deepseek_max_tokens_report,
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
                decision_at=decision_at or datetime.now().astimezone(),
                default_prompt_only=(
                    system_role_prompt is None
                    or system_role_prompt == DEFAULT_DISCOVERY_ROLE_PROMPT
                ),
                news_tool_rounds=runtime.news_tool_max_rounds,
            )
        except Exception:  # noqa: BLE001 - champion must remain fail-open
            logger.exception("prompt-shadow champion preregistration skipped")
            self._prompt_shadow_capture = None
        try:
            if self._prompt_shadow_capture is None:
                parsed = self._call_model(system_prompt, user_payload, runtime.model)
            else:
                parsed = self._call_model(
                    system_prompt,
                    user_payload,
                    runtime.model,
                    trace_collector=self._prompt_shadow_capture.trace_collector,
                )
            self._last_report_parsed_payload = deepcopy(parsed)
        except (httpx.HTTPError, ProviderOutputError) as exc:
            failure = classify_deepseek_failure(exc)
            actual_messages = getattr(self, "_last_report_messages", None) or messages
            attempted_prompt_contract = build_discovery_prompt_provenance(
                role_prompt=system_role_prompt,
                messages=actual_messages,
                user_payload=user_payload,
                runtime=runtime,
                judge_meta={},
            )
            return build_offline_discovery_report(
                target_sectors=target_sectors,
                candidate_pool=candidate_pool,
                discovery_facts=discovery_facts,
                profile=profile,
                focus_sectors=focus_sectors,
                analysis_mode=analysis_mode,
                provider_failure=failure,
                attempted_model=runtime.model,
                prompt_contract=attempted_prompt_contract,
                decision_at=decision_at,
            )
        # M4：deep 模式风控复核角色（fast 模式内部直接短路返回，零新增 LLM 调用）。
        parsed, judge_meta = judge_parsed_discovery_report(
            parsed,
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
            analysis_mode=analysis_mode,
        )
        actual_messages = getattr(self, "_last_report_messages", None) or messages
        prompt_contract = build_discovery_prompt_provenance(
            role_prompt=system_role_prompt,
            messages=actual_messages,
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
        discovery_facts["pipeline"] = pipeline
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
            provider_model=runtime.model,
            decision_at=decision_at,
        )

    def _system_prompt(
        self,
        news_tool_enabled: bool,
        role_prompt: str | None = None,
        *,
        session: dict | None = None,
    ) -> str:
        now = resolve_decision_local_datetime(session)
        base_role = resolve_discovery_role_prompt(role_prompt)
        base = (
            f"{base_role}\n\n当前时间：{now}。"
            "你只能提供个人研究与风险提示，不能承诺收益。"
        )
        if news_tool_enabled:
            base += "深度模式可结合 user 消息中的 news 主题摘要；引用新闻须为已有标题。"
        return base

    def _call_model(
        self,
        system_prompt: str,
        user_payload: dict,
        model: str,
        *,
        trace_collector: ProviderCallTraceCollector | None = None,
        exact_provider_payload: Mapping[str, object] | None = None,
    ) -> dict:
        messages = build_discovery_chat_messages(system_prompt, user_payload)
        if exact_provider_payload is not None:
            exact = deepcopy(dict(exact_provider_payload))
            if exact.get("stream", False) is not False:
                raise ValueError("sync provider payload cannot enable streaming")
            if exact.get("messages") != messages or exact.get("model") != model:
                raise ValueError("exact provider payload conflicts with call arguments")
        else:
            exact = None
        self._last_report_messages = deepcopy(messages)
        body = (
            exact
            if exact is not None
            else _build_chat_payload(
                messages=messages,
                model=model,
                max_tokens=self.settings.deepseek_max_tokens_report,
                tools=None,
                response_format=REPORT_RESPONSE_FORMAT,
            )
        )
        if trace_collector is not None:
            trace_collector.start_request(body)
        try:
            with httpx.Client(timeout=deepseek_timeout(self.settings)) as client:
                response = client.post(
                    deepseek_chat_url(self.settings),
                    headers=deepseek_request_headers(self.settings),
                    json=body,
                )
                if trace_collector is not None:
                    trace_collector.mark_response_started(
                        http_status=getattr(response, "status_code", None),
                        provider_request_id=provider_request_id_from_headers(
                            getattr(response, "headers", None)
                        ),
                    )
                    raw_body = getattr(response, "content", b"")
                    if isinstance(raw_body, str):
                        raw_body = raw_body.encode("utf-8")
                    trace_collector.observe_sync_envelope(raw_body)
                response.raise_for_status()
                try:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise TypeError("provider envelope must be an object")
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise TypeError("provider choices must be a non-empty list")
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise TypeError("provider choice must be an object")
                    message = choice.get("message")
                    if not isinstance(message, dict):
                        raise TypeError("provider message must be an object")
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    if trace_collector is not None:
                        trace_collector.finish_error(
                            outcome="provider_error",
                            error_category="invalid_envelope",
                        )
                    raise ProviderOutputError("invalid_json") from exc

            if trace_collector is not None:
                trace_collector.observe_metadata(
                    actual_model=payload.get("model"),
                    finish_reason=choice.get("finish_reason"),
                    usage=payload.get("usage"),
                    provider_request_id=payload.get("id"),
                )
            content = message.get("content", "")
            if not isinstance(content, str):
                if trace_collector is not None:
                    trace_collector.finish_error(
                        outcome="provider_error",
                        error_category="invalid_envelope",
                    )
                raise ProviderOutputError("invalid_json")
            self._last_report_raw_content = content
            if trace_collector is not None:
                trace_collector.observe_content(content)
            if not content.strip():
                if trace_collector is not None:
                    trace_collector.finish_error(
                        outcome="provider_error",
                        error_category="empty_content",
                    )
                raise ProviderOutputError("empty_content")
            parsed = _parse_model_json(content)
            if parsed.get("_truncated") or not _is_valid_discovery_report_payload(parsed):
                if trace_collector is not None:
                    trace_collector.finish_error(
                        outcome="provider_error",
                        error_category="invalid_json",
                    )
                raise ProviderOutputError("invalid_json")
            if trace_collector is not None:
                trace_collector.finish_success()
            return parsed
        except Exception as exc:
            if trace_collector is not None and not trace_collector.finalized:
                outcome, category = _provider_trace_failure(exc)
                trace_collector.finish_error(
                    outcome=outcome,
                    error_category=category,
                )
            raise

    def _chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        response_format: dict | None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        body = _build_chat_payload(
            messages=messages,
            model=model or self.settings.deepseek_model,
            max_tokens=max_tokens or self.settings.deepseek_max_tokens,
            tools=tools,
            response_format=response_format,
        )
        with httpx.Client(timeout=deepseek_timeout(self.settings)) as client:
            response = client.post(
                deepseek_chat_url(self.settings),
                headers=deepseek_request_headers(self.settings),
                json=body,
            )
            response.raise_for_status()
            try:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("provider envelope must be an object")
                choices = payload.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise TypeError("provider choices must be a non-empty list")
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise TypeError("provider choice must be an object")
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise TypeError("provider message must be an object")
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                raise ProviderOutputError("invalid_json") from exc
        return message


def build_discovery_chat_messages(
    system_prompt: str,
    user_payload: dict,
) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": canonical_json(user_payload)},
    ]


def _provider_trace_failure(exc: BaseException) -> tuple[str, str]:
    """Classify a trace failure without retaining provider-controlled text."""

    if isinstance(exc, ProviderOutputError):
        return "provider_error", exc.category
    if isinstance(exc, httpx.ConnectTimeout):
        return "timeout", "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "timeout", "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "timeout", "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "timeout", "pool_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_error", "http_status"
    if isinstance(exc, httpx.ConnectError):
        return "transport_error", "connection_error"
    if isinstance(exc, httpx.HTTPError):
        return "transport_error", "transport_error"
    return "provider_error", "unknown_provider_error"


def build_discovery_prompt_provenance(
    *,
    role_prompt: str | None,
    messages: list[dict],
    user_payload: dict,
    runtime: AnalysisRuntime,
    judge_meta: dict | None = None,
) -> dict:
    """Freeze the exact discovery prompt and provider parameters."""

    component = build_discovery_prompt_contract(role_prompt)
    settings = get_settings()
    provider_payload = _build_chat_payload(
        messages=messages,
        model=runtime.model,
        max_tokens=settings.deepseek_max_tokens_report,
        tools=None,
        response_format=REPORT_RESPONSE_FORMAT,
    )
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
    provider_model: str = "deepseek",
    decision_at: datetime | None = None,
) -> FundDiscoveryReport:
    parsed = sanitize_retired_market_evidence(parsed)
    recommendations = _parse_recommendations(parsed.get("recommendations"))
    recommendations = prepare_recommendations_for_deterministic_allocation(
        recommendations,
        candidate_pool=candidate_pool,
    )
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
    guarded, allocation_plan, risk_context, allocation_caveats = (
        apply_deterministic_discovery_allocation(
            guarded,
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
            profile=profile,
            budget_yuan=budget_yuan,
            decision_at=decision_at,
        )
    )
    discovery_facts["risk_context"] = risk_context
    discovery_facts["allocation_plan"] = allocation_plan
    caveats = _as_str_list(parsed.get("caveats"))
    caveats.extend(guard_caveats)
    caveats.extend(allocation_caveats)
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

    report = FundDiscoveryReport(
        **({"created_at": decision_at} if decision_at is not None else {}),
        title=str(parsed.get("title") or "今日基金机会扫描"),
        summary=summary,
        market_view=market_view,
        focus_sectors=focus_sectors,
        target_sectors=target_sectors,
        candidate_pool=candidate_pool,
        recommendations=guarded,
        allocation_plan=allocation_plan,
        discovery_facts=discovery_facts,
        caveats=caveats,
        eliminated_candidates=eliminated,
        provider=provider_model,
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
    )
    return report


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
