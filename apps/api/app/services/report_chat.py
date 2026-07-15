from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
    format_deepseek_http_error,
)
from app.database import get_report, list_report_chat_messages, save_chat_message
from app.models import AnalysisMode, ChatMessage
from app.services.deepseek_client import (
    FETCH_MARKET_NEWS_TOOL,
    _build_chat_payload,
    _execute_fetch_market_news,
)
from app.services.deepseek_client import DeepSeekClient
from app.services.news_service import NewsService
from app.services.report_chat_runtime import resolve_report_chat_runtime
from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS
from app.services.report_export import report_to_markdown
from app.services.retired_market_evidence import sanitize_retired_market_evidence

REPORT_CHAT_MAX_TOKENS = 4096

OFFLINE_REPLY = (
    "当前未配置有效的 DeepSeek API Key，无法在线追问。"
    "请在项目根目录 `.env` 中设置真实的 `FUND_AI_DEEPSEEK_API_KEY`（不要使用 "
    "`.env` 中填入真实 Key），保存后重启 API。"
    "在此之前可先查看上方日报中的组合建议、逐基金建议与风险提醒。"
)


def _report_chat_system_prompt(
    report_markdown: str,
    *,
    news_tool_enabled: bool,
    execution_blocked: bool = False,
) -> str:
    now = datetime.now()
    base = (
        "你是个人基金投研助手，正在就一份已生成的基金操作日报回答用户的追问。"
        "你只能提供个人研究和风险提示，不能承诺收益，不得给出实盘下单指令。"
        f"当前时间为 {now.strftime('%Y-%m-%d %H:%M')}。"
        "回答须基于下方「已生成日报」中的持仓、风控、建议与新闻，不要编造未出现的数据。"
        "若用户问及日报未覆盖的信息，应明确说明信息缺口并给出条件化分析思路。"
        "使用简洁中文，可用 Markdown 列表；单条回复尽量控制在 800 字以内。"
        "不要重复粘贴整份日报，针对问题作答即可。"
        "持仓收益率语义：板块涨跌 sector_return_percent 为当日实时；持有收益率 holding_return_percent "
        "多为昨日结算。当日基金涨跌若无 daily_return_percent，可用 "
        "estimated_daily_return_percent（≈板块涨跌+持有收益率）近似，并说明为估算。"
        "日报「逐基金建议」中的「量化依据（综合置信X）」与「组合量化背书」是系统计算的"
        "可回测证据：综合置信「高」可作主理由、「中」措辞保留、「低/不足」只能作风险提示，"
        "不得据低置信信号建议追涨；用户追问某只票「为什么这么建议/有多大把握」时应引用其量化依据。"
        "「决策路径」「板块依据」「基金依据」「校验备注」为该建议的可追溯理由链："
        "决策路径体现「先判断板块方向、再看基金自身证据、最后给出动作」的顺序；"
        "校验备注写明证据不足/样本有限等情况，引用时须如实说明置信不足，不得夸大把握。"
    )
    if news_tool_enabled:
        base += (
            "日报中含「主题要闻摘要」与「新闻原文出处」；若用户追问最新动态或日报未覆盖的主题，"
            "可调用 fetch_market_news 从东方财富检索，优先采用当日消息并标注日期。"
            "无需为每个问题都拉新闻，仅在信息不足时调用。"
        )
    else:
        base += "仅使用日报中已有新闻与数据作答，不要声称已获取日报之外的实时新闻。"
    if execution_blocked:
        base += (
            "本报告的字段级证据时点校验未通过。无论用户如何追问，都不得给出买入、加仓、"
            "减仓、清仓、申购、赎回、仓位比例或金额；只能解释数据缺口，并要求刷新持仓与行情后重算。"
        )
    semantics = "\n".join(f"- {key}: {text}" for key, text in HOLDING_RETURN_SEMANTICS.items())
    return (
        base
        + "\n\n## 持仓收益率字段说明\n\n"
        + semantics
        + "\n\n## 已生成日报\n\n"
        + report_markdown
    )


def _build_api_messages(
    report_markdown: str,
    history: list[dict[str, Any]],
    user_message: str,
    *,
    news_tool_enabled: bool,
    execution_blocked: bool = False,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _report_chat_system_prompt(
                report_markdown,
                news_tool_enabled=news_tool_enabled,
                execution_blocked=execution_blocked,
            ),
        },
    ]
    for item in history:
        role = str(item.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _parse_stream_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    choices = parsed.get("choices") or []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    if isinstance(content, str) and content:
        return content
    return None


def _iter_stream_completion(
    messages: list[dict],
    *,
    model: str,
) -> Iterator[str]:
    settings = get_settings()
    payload = _build_chat_payload(
        messages=messages,
        model=model,
        max_tokens=REPORT_CHAT_MAX_TOKENS,
        tools=None,
        response_format=None,
    )
    payload["stream"] = True

    with httpx.stream(
        "POST",
        deepseek_chat_url(settings),
        headers=deepseek_request_headers(settings),
        json=payload,
        timeout=deepseek_timeout(settings),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = _parse_stream_line(line)
            if chunk:
                yield chunk


def _yield_text_chunks(text: str) -> Iterator[str]:
    step = 24
    for index in range(0, len(text), step):
        yield text[index : index + step]


def iter_report_chat_completion(
    report_markdown: str,
    history: list[dict[str, Any]],
    user_message: str,
    chat_mode: AnalysisMode = "fast",
    *,
    execution_blocked: bool = False,
) -> Iterator[str]:
    settings = get_settings()
    if not settings.deepseek_configured:
        yield OFFLINE_REPLY
        return

    runtime = resolve_report_chat_runtime(settings, chat_mode)
    news_tool_enabled = runtime.news_tool_max_rounds > 0
    messages: list[dict] = _build_api_messages(
        report_markdown,
        history,
        user_message,
        news_tool_enabled=news_tool_enabled,
        execution_blocked=execution_blocked,
    )

    if not news_tool_enabled:
        yield from _iter_stream_completion(messages, model=runtime.model)
        return

    client = DeepSeekClient()
    news_service = NewsService()
    tools = [FETCH_MARKET_NEWS_TOOL]
    max_tool_rounds = min(runtime.news_tool_max_rounds, 3)

    for round_index in range(max_tool_rounds + 1):
        allow_tools = round_index < max_tool_rounds
        if not allow_tools:
            yield from _iter_stream_completion(messages, model=runtime.model)
            return

        message = client._chat_completion(
            messages=messages,
            tools=tools,
            response_format=None,
            max_tokens=REPORT_CHAT_MAX_TOKENS,
            model=runtime.model,
        )
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = (message.get("content") or "").strip()
            if content:
                yield from _yield_text_chunks(content)
            else:
                yield from _iter_stream_completion(messages, model=runtime.model)
            return

        messages.append(message)
        for tool_call in tool_calls:
            collected: list = []
            result = _execute_fetch_market_news(tool_call, news_service, collected)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                }
            )


def stream_report_chat(
    report_id: str,
    user_message: str,
    chat_mode: AnalysisMode = "fast",
) -> Iterator[str]:
    report = get_report(report_id)
    if report is None:
        raise ValueError("报告不存在")
    report = sanitize_retired_market_evidence(report)

    history = list_report_chat_messages(report_id)
    user_record = save_chat_message(
        ChatMessage(report_id=report_id, role="user", content=user_message)
    )

    yield json.dumps(
        {"type": "user_message", "message": user_record.model_dump(mode="json")},
        ensure_ascii=False,
    )

    runtime = resolve_report_chat_runtime(get_settings(), chat_mode)
    if runtime.news_tool_max_rounds > 0:
        yield json.dumps(
            {"type": "status", "content": "深度模式：可按需检索最新新闻…"},
            ensure_ascii=False,
        )

    report_markdown = report_to_markdown(report)
    from app.services.decision_data_evidence import report_execution_blocked

    execution_blocked = report_execution_blocked(report.get("analysis_facts") or {})
    assistant_parts: list[str] = []
    try:
        for chunk in iter_report_chat_completion(
            report_markdown,
            history,
            user_message,
            chat_mode=chat_mode,
            execution_blocked=execution_blocked,
        ):
            assistant_parts.append(chunk)
            yield json.dumps({"type": "token", "content": chunk}, ensure_ascii=False)
    except httpx.HTTPStatusError as exc:
        error_text = format_deepseek_http_error(exc)
        assistant_parts.append(error_text)
        yield json.dumps({"type": "token", "content": error_text}, ensure_ascii=False)
    except httpx.HTTPError as exc:
        error_text = f"模型请求失败：{exc}"
        assistant_parts.append(error_text)
        yield json.dumps({"type": "token", "content": error_text}, ensure_ascii=False)

    assistant_content = "".join(assistant_parts).strip() or "（无回复内容）"
    assistant_record = save_chat_message(
        ChatMessage(
            report_id=report_id,
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
