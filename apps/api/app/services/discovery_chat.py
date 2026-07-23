from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
import threading

import httpx

from app.config import get_settings
from app.database import get_discovery_report, list_discovery_chat_messages, save_discovery_chat_message
from app.models import AnalysisMode, DiscoveryChatMessage
from app.services.deepseek_http import (
    deepseek_request_deadline,
    format_deepseek_http_error,
)
from app.services.discovery_chat_guard import (
    format_candidate_pool_whitelist,
    sanitize_discovery_chat_fund_codes,
)
from app.services.discovery_export import discovery_report_to_markdown
from app.services.report_chat_runtime import resolve_report_chat_runtime
from app.services.retired_market_evidence import sanitize_retired_market_evidence

DISCOVERY_CHAT_MAX_TOKENS = 4096

OFFLINE_REPLY = (
    "当前未配置有效的 DeepSeek API Key，无法在线追问。"
    "请先查看上方推荐报告中的基金建议与风险提示。"
)


def _discovery_chat_system_prompt(report_markdown: str, report: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    whitelist = format_candidate_pool_whitelist(report)
    from app.services.decision_data_evidence import report_execution_blocked

    execution_guard = (
        "本报告的字段级证据时点校验未通过。无论用户如何追问，都不得给出买入、加仓、申购、"
        "仓位比例或金额；只能解释数据缺口，并要求刷新持仓与候选数据后重新扫描。"
        if report_execution_blocked(report.get("discovery_facts") or {})
        else ""
    )
    return (
        "你是个人基金投研助手，正在就一份「基金机会推荐报告」回答追问。"
        "你只能提供个人研究和风险提示，不能承诺收益。"
        f"当前时间为 {now}。"
        "回答须严格基于下方「候选基金池」与「已生成推荐报告」；"
        "提及具体基金时，代码与名称必须与候选池表格完全一致，禁止编造表外基金代码"
        "（含臆造 ETF 场内代码）。若用户追问的板块在候选池中有对应行，只能引用那些基金。"
        "若用户要求调整方向或预算，在报告框架内给出条件化建议。"
        + execution_guard
        + "使用简洁中文 Markdown；单条回复尽量 800 字以内。\n\n"
        + whitelist
        + "\n\n## 已生成推荐报告\n\n"
        + report_markdown
    )


def stream_discovery_chat(
    discovery_report_id: str,
    user_message: str,
    chat_mode: AnalysisMode = "fast",
    *,
    stop_event: threading.Event | None = None,
) -> Iterator[str]:
    report = get_discovery_report(discovery_report_id)
    if report is None:
        raise ValueError("报告不存在")
    report = sanitize_retired_market_evidence(report)

    history = list_discovery_chat_messages(discovery_report_id)
    user_record = save_discovery_chat_message(
        DiscoveryChatMessage(
            discovery_report_id=discovery_report_id,
            role="user",
            content=user_message,
        )
    )
    yield json.dumps(
        {"type": "user_message", "message": user_record.model_dump(mode="json")},
        ensure_ascii=False,
    )

    settings = get_settings()
    runtime = resolve_report_chat_runtime(settings, chat_mode)
    if not settings.deepseek_api_key:
        assistant_record = save_discovery_chat_message(
            DiscoveryChatMessage(
                discovery_report_id=discovery_report_id,
                role="assistant",
                content=OFFLINE_REPLY,
            )
        )
        yield json.dumps({"type": "token", "content": OFFLINE_REPLY}, ensure_ascii=False)
        yield json.dumps(
            {
                "type": "done",
                "message": assistant_record.model_dump(mode="json"),
                "chat_mode": chat_mode,
                "model": "offline",
            },
            ensure_ascii=False,
        )
        return

    report_markdown = discovery_report_to_markdown(report)
    messages = [
        {"role": "system", "content": _discovery_chat_system_prompt(report_markdown, report)},
    ]
    for item in history:
        role = str(item.get("role", ""))
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    assistant_parts: list[str] = []
    try:
        from app.services.deepseek_streaming import stream_chat_completion

        deadline = deepseek_request_deadline(settings)
        provider_payload = {
            "model": runtime.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": DISCOVERY_CHAT_MAX_TOKENS,
            "stream": True,
        }
        for delta in stream_chat_completion(
            messages=messages,
            model=runtime.model,
            max_tokens=DISCOVERY_CHAT_MAX_TOKENS,
            exact_provider_payload=provider_payload,
            stop_event=stop_event,
            deadline_monotonic=deadline,
        ):
            assistant_parts.append(delta)
            yield json.dumps(
                {"type": "token", "content": delta},
                ensure_ascii=False,
            )
    except httpx.HTTPError as exc:
        error_text = format_deepseek_http_error(exc)
        assistant_parts.append(error_text)
        yield json.dumps({"type": "token", "content": error_text}, ensure_ascii=False)

    assistant_content = "".join(assistant_parts).strip() or "（无回复内容）"
    assistant_content, guard_notes = sanitize_discovery_chat_fund_codes(assistant_content, report)
    if guard_notes:
        unique_notes = list(dict.fromkeys(guard_notes))
        assistant_content = (
            assistant_content.rstrip()
            + "\n\n> "
            + "\n> ".join(unique_notes)
        )
    assistant_record = save_discovery_chat_message(
        DiscoveryChatMessage(
            discovery_report_id=discovery_report_id,
            role="assistant",
            content=assistant_content,
        )
    )
    yield json.dumps(
        {
            "type": "done",
            "message": assistant_record.model_dump(mode="json"),
            "chat_mode": chat_mode,
            "model": runtime.model,
        },
        ensure_ascii=False,
    )
