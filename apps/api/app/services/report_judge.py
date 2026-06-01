from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.services.analysis_facts import build_analysis_facts
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
)
from app.models import AnalysisRequest, FundSnapshot, RiskAssessment
from app.services.recommendation_guard import normalize_action_text


def judge_parsed_report(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    runtime: AnalysisRuntime,
) -> dict:
    judged = _rule_judge(parsed, request, risk, snapshots)
    if runtime.analysis_mode != "deep" or not get_settings().deepseek_configured:
        return judged
    return _llm_judge(judged, request, risk, snapshots, runtime)


def _rule_judge(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
) -> dict:
    facts = build_analysis_facts(request.holdings, risk, snapshots, request.profile)
    weight_by_code = {
        item["fund_code"]: item["weight_percent"] for item in facts["holdings"]
    }
    allowed = set(facts["allowed_actions"])

    raw_recs = parsed.get("fund_recommendations")
    if not isinstance(raw_recs, list):
        return parsed

    fixed_recs: list[dict] = []
    for entry in raw_recs:
        if not isinstance(entry, dict):
            continue
        copy = dict(entry)
        action = normalize_action_text(str(copy.get("action", "观察")))
        if action not in allowed:
            action = "观察"
        code = str(copy.get("fund_code", "")).strip()
        if risk.suggested_action == "risk_review" and _action_bucket(action) >= 3:
            action = "暂停追涨"
        if code in weight_by_code and weight_by_code[code] > request.profile.concentration_limit_percent:
            if _action_bucket(action) >= 3:
                action = "减仓评估"
        copy["action"] = action
        fixed_recs.append(copy)

    copy_parsed = dict(parsed)
    copy_parsed["fund_recommendations"] = fixed_recs

    summary = str(copy_parsed.get("summary", ""))
    if risk.suggested_action == "risk_review" and "加仓" in summary and "不宜" not in summary:
        copy_parsed["summary"] = (
            f"{summary}\n\n（系统复核：组合处于风险复核状态，今日不宜新增加仓。）"
        ).strip()

    return copy_parsed


def _llm_judge(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    runtime: AnalysisRuntime,
) -> dict:
    settings = get_settings()
    facts = build_analysis_facts(request.holdings, risk, snapshots, request.profile)
    payload = {
        "facts": facts,
        "draft_report": parsed,
        "task": (
            "你是审校员。对照 facts 检查 draft_report，修正与数字/风控矛盾之处。"
            "仅输出完整 JSON，结构同 draft_report（title、summary、fund_recommendations、caveats）。"
            "不得放宽风控：risk_review 时禁止加仓类 action。"
        ),
    }
    try:
        response = httpx.post(
            deepseek_chat_url(settings),
            headers=deepseek_request_headers(settings),
            json={
                "model": settings.deepseek_model_fast,
                "messages": [
                    {"role": "system", "content": "你是严谨的基金日报审校员。"},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0.1,
                "max_tokens": min(settings.deepseek_max_tokens_report, 8000),
                "response_format": {"type": "json_object"},
            },
            timeout=deepseek_timeout(settings),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        from app.services.deepseek_client import _parse_model_json

        reviewed = _parse_model_json(content)
        if reviewed.get("fund_recommendations"):
            return reviewed
    except Exception:
        pass
    return parsed


def _action_bucket(action: str) -> int:
    if any(token in action for token in ("减仓", "复核", "风控")):
        return 0
    if any(token in action for token in ("暂停",)):
        return 2
    if any(token in action for token in ("加仓", "定投", "分批")):
        return 3
    return 1
