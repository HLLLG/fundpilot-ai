"""Shared tool-calling loop for report / discovery follow-up chat."""

from __future__ import annotations

from collections.abc import Iterator
import threading
from typing import Any

from app.services.chat_agent_tools import (
    ChatAgentContext,
    execute_chat_tool,
    tool_status_label,
)
from app.services.deepseek_client import DeepSeekClient
from app.services.streaming_heartbeat import raise_if_stream_cancelled

CHAT_AGENT_MAX_TOKENS = 4096


def _yield_text_chunks(text: str) -> Iterator[str]:
    step = 24
    for index in range(0, len(text), step):
        yield text[index : index + step]


def iter_chat_agent_events(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    context: ChatAgentContext,
    model: str,
    max_rounds: int,
    max_tokens: int = CHAT_AGENT_MAX_TOKENS,
    stop_event: threading.Event | None = None,
    deadline_monotonic: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield chat SSE-shaped events: status / job_started / token."""

    from app.config import get_settings
    from app.services.deepseek_streaming import stream_chat_completion

    if getattr(get_settings(), "langgraph_enabled", True):
        from app.services.graphs.chat_followup import iter_chat_followup_events

        yield from iter_chat_followup_events(
            messages=messages,
            tools=tools,
            context=context,
            model=model,
            max_rounds=max_rounds,
            max_tokens=max_tokens,
            stop_event=stop_event,
            deadline_monotonic=deadline_monotonic,
        )
        return

    def stream_tokens() -> Iterator[dict[str, Any]]:
        for chunk in stream_chat_completion(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            response_format=None,
            stop_event=stop_event,
            deadline_monotonic=deadline_monotonic,
        ):
            yield {"type": "token", "content": chunk}

    if not tools or max_rounds <= 0:
        yield from stream_tokens()
        return

    client = DeepSeekClient()
    client._provider_deadline = deadline_monotonic
    bounded_rounds = max(0, min(int(max_rounds), 3))

    for round_index in range(bounded_rounds + 1):
        raise_if_stream_cancelled(stop_event)
        if round_index >= bounded_rounds:
            yield from stream_tokens()
            return

        message = client._chat_completion(
            messages=messages,
            tools=tools,
            response_format=None,
            max_tokens=max_tokens,
            model=model,
        )
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = (message.get("content") or "").strip()
            if content:
                for chunk in _yield_text_chunks(content):
                    yield {"type": "token", "content": chunk}
            else:
                yield from stream_tokens()
            return

        messages.append(message)
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            name = str(function.get("name") or "").strip()
            yield {"type": "status", "content": tool_status_label(name)}
            result = execute_chat_tool(tool_call, context)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": result,
                }
            )
            if context.pending_jobs:
                for job in context.pending_jobs:
                    yield {
                        "type": "job_started",
                        "job_kind": job.get("job_kind"),
                        "job_id": job.get("job_id"),
                    }
                context.pending_jobs.clear()
