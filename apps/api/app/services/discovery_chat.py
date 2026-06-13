from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime

import httpx

from app.config import get_settings
from app.database import get_discovery_report, list_discovery_chat_messages, save_discovery_chat_message
from app.models import AnalysisMode, DiscoveryChatMessage
from app.services.deepseek_http import deepseek_chat_url, deepseek_request_headers, deepseek_timeout, format_deepseek_http_error
from app.services.discovery_export import discovery_report_to_markdown
from app.services.report_chat_runtime import resolve_report_chat_runtime

OFFLINE_REPLY = (
    "当前未配置有效的 DeepSeek API Key，无法在线追问。"
    "请先查看上方推荐报告中的基金建议与风险提示。"
)


def _discovery_chat_system_prompt(report_markdown: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "你是个人基金投研助手，正在就一份「基金机会推荐报告」回答追问。"
        "你只能提供个人研究和风险提示，不能承诺收益。"
        f"当前时间为 {now}。"
        "回答须基于下方报告中的候选池、推荐与板块数据，不要编造未出现的基金代码。"
        "若用户要求调整方向或预算，在报告框架内给出条件化建议。"
        "使用简洁中文 Markdown；单条回复尽量 800 字以内。\n\n"
        "## 已生成推荐报告\n\n"
        + report_markdown
    )


def stream_discovery_chat(
    discovery_report_id: str,
    user_message: str,
    chat_mode: AnalysisMode = "fast",
) -> Iterator[str]:
    report = get_discovery_report(discovery_report_id)
    if report is None:
        raise ValueError("报告不存在")

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
        {"role": "system", "content": _discovery_chat_system_prompt(report_markdown)},
    ]
    for item in history:
        role = str(item.get("role", ""))
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    assistant_parts: list[str] = []
    try:
        with httpx.Client(timeout=deepseek_timeout(settings)) as client:
            with client.stream(
                "POST",
                deepseek_chat_url(settings),
                headers=deepseek_request_headers(settings),
                json={
                    "model": runtime.model,
                    "messages": messages,
                    "temperature": 0.4,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    payload = json.loads(data)
                    delta = (
                        payload.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        assistant_parts.append(delta)
                        yield json.dumps({"type": "token", "content": delta}, ensure_ascii=False)
    except httpx.HTTPError as exc:
        error_text = format_deepseek_http_error(exc)
        assistant_parts.append(error_text)
        yield json.dumps({"type": "token", "content": error_text}, ensure_ascii=False)

    assistant_content = "".join(assistant_parts).strip() or "（无回复内容）"
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
