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
from app.services.chat_agent_loop import iter_chat_agent_events
from app.services.chat_agent_tools import ChatAgentContext, tool_specs_for
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


def _discovery_chat_system_prompt(
    report_markdown: str,
    report: dict,
    *,
    news_tool_enabled: bool = False,
    agent_tools_enabled: bool = False,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    whitelist = format_candidate_pool_whitelist(report)
    from app.services.decision_data_evidence import report_execution_blocked

    execution_guard = (
        "本报告的字段级证据时点校验未通过。无论用户如何追问，都不得给出买入、加仓、申购、"
        "仓位比例或金额；只能解释数据缺口，并要求刷新持仓与候选数据后重新扫描。"
        if report_execution_blocked(report.get("discovery_facts") or {})
        else ""
    )
    prompt = (
        "你是个人基金投研助手，正在就一份「基金机会推荐报告」回答追问。"
        "你只能提供个人研究和风险提示，不能承诺收益。"
        f"当前时间为 {now}。"
        "回答须严格基于下方「候选基金池」与「已生成推荐报告」；"
        "提及具体基金时，代码与名称必须与候选池表格完全一致，禁止编造表外基金代码"
        "（含臆造 ETF 场内代码）。若用户追问的板块在候选池中有对应行，只能引用那些基金。"
        "若用户要求调整方向或预算，在报告框架内给出条件化建议。"
        + execution_guard
        + "使用简洁中文 Markdown；单条回复尽量 800 字以内。"
    )
    if agent_tools_enabled:
        prompt += (
            "深度模式可按需调用工具：get_holdings 读当前账本；"
            "explain_candidate_decision 核对本报告已有候选；"
            "lookup_fund 只查目录身份，查到的表外基金不得说成可买；"
            "get_sector_context 只读已落库方向状态；"
        )
        if news_tool_enabled:
            prompt += "fetch_market_news 仅在信息不足时拉新闻；"
        prompt += (
            "run_daily_report / run_discovery_scan 仅在用户明确要求重新生成或扫描时调用，"
            "且必须 confirm=true。工具不能改质量门、金额硬上限或持仓真值；"
            "触发任务后如实告知已排队，不要假装已经看到新报告正文。"
        )
    return (
        prompt
        + "\n\n"
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
    news_tool_enabled = runtime.news_tool_max_rounds > 0
    agent_tools_enabled = runtime.agent_tool_max_rounds > 0
    messages = [
        {
            "role": "system",
            "content": _discovery_chat_system_prompt(
                report_markdown,
                report,
                news_tool_enabled=news_tool_enabled,
                agent_tools_enabled=agent_tools_enabled,
            ),
        },
    ]
    for item in history:
        role = str(item.get("role", ""))
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    assistant_parts: list[str] = []
    graph_run_id: str | None = None
    try:
        from app.services.deepseek_streaming import stream_chat_completion

        deadline = deepseek_request_deadline(settings)
        if agent_tools_enabled:
            yield json.dumps(
                {
                    "type": "status",
                    "content": "深度模式：可按需查持仓、方向账本、新闻或触发既有任务…",
                },
                ensure_ascii=False,
            )
            from app.services.decision_data_evidence import report_execution_blocked

            for event in iter_chat_agent_events(
                messages=messages,
                tools=tool_specs_for(surface="discovery", news_enabled=news_tool_enabled),
                context=ChatAgentContext(
                    surface="discovery",
                    report=report,
                    execution_blocked=report_execution_blocked(
                        report.get("discovery_facts") or {}
                    ),
                    news_enabled=news_tool_enabled,
                ),
                model=runtime.model,
                max_rounds=runtime.agent_tool_max_rounds,
                max_tokens=DISCOVERY_CHAT_MAX_TOKENS,
                stop_event=stop_event,
                deadline_monotonic=deadline,
            ):
                if event.get("type") == "graph" and isinstance(event.get("run_id"), str):
                    graph_run_id = event["run_id"]
                if event.get("type") == "token" and isinstance(event.get("content"), str):
                    assistant_parts.append(event["content"])
                yield json.dumps(event, ensure_ascii=False)
        else:
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
    done_event = {
        "type": "done",
        "message": assistant_record.model_dump(mode="json"),
        "chat_mode": chat_mode,
        "model": runtime.model,
    }
    if graph_run_id:
        done_event["graph_run_id"] = graph_run_id
    yield json.dumps(done_event, ensure_ascii=False)
