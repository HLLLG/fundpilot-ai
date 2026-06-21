from __future__ import annotations

import json
from datetime import datetime

import httpx

from app.config import get_settings
from app.models import DiscoveryRecommendation, FundDiscoveryReport, InvestorProfile, NewsItem, TopicBrief
from app.services.analysis_runtime import resolve_analysis_runtime
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
    format_deepseek_http_error,
)
from app.services.deepseek_client import _parse_model_json
from app.services.discovery_guard import apply_discovery_guards
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_payload import append_output_requirements_to_system, build_user_payload
from app.services.discovery_prompt import DEFAULT_DISCOVERY_ROLE_PROMPT, resolve_discovery_role_prompt

_DISCLAIMER = "仅供参考，不构成投资建议；基金有风险，决策需结合自身承受能力。"


class DiscoveryClient:
    def __init__(self) -> None:
        self.settings = get_settings()

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
        )
        system_prompt = append_output_requirements_to_system(
            self._system_prompt(runtime.news_tool_max_rounds > 0, system_role_prompt)
        )
        parsed = self._call_model(system_prompt, user_payload, runtime.model)
        recommendations = _parse_recommendations(parsed.get("recommendations"))
        guarded, guard_caveats = apply_discovery_guards(
            recommendations,
            candidate_pool=candidate_pool,
            held_codes=held_codes,
            profile=profile,
            budget_yuan=budget_yuan,
            sector_heat=sector_heat,
            market_news=market_news,
            topic_briefs=topic_briefs,
            scan_mode=scan_mode,
        )
        caveats = _as_str_list(parsed.get("caveats"))
        caveats.extend(guard_caveats)
        if _DISCLAIMER not in caveats:
            caveats.insert(0, _DISCLAIMER)

        return FundDiscoveryReport(
            title=str(parsed.get("title") or "今日基金机会扫描"),
            summary=str(parsed.get("summary") or ""),
            market_view=str(parsed.get("market_view") or ""),
            focus_sectors=focus_sectors,
            target_sectors=target_sectors,
            candidate_pool=candidate_pool,
            recommendations=guarded,
            discovery_facts=discovery_facts,
            caveats=caveats,
            provider="deepseek",
            analysis_mode=analysis_mode,  # type: ignore[arg-type]
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
                target_exit_days=_as_int(item.get("target_exit_days")),
                fee_break_even_percent=_as_float(item.get("fee_break_even_percent")),
                dip_drop_percent=_as_float(item.get("dip_drop_percent")),
                rebound_signals=_as_dict_list(item.get("rebound_signals")),
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


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dict_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
