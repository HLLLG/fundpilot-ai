"""Follow-up chat as a LangGraph: model may pick tools; tools stay code-owned."""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar
import threading
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.chat_agent_tools import ChatAgentContext, execute_chat_tool, tool_status_label
from app.services.langgraph_runner import emit_custom, iter_graph_events
from app.services.streaming_heartbeat import raise_if_stream_cancelled

CHAT_FOLLOWUP_GRAPH = "chat_followup"

_chat_ctx: ContextVar[ChatAgentContext | None] = ContextVar(
    "chat_followup_ctx",
    default=None,
)
_stop_event: ContextVar[threading.Event | None] = ContextVar(
    "chat_followup_stop",
    default=None,
)
_deadline: ContextVar[float | None] = ContextVar(
    "chat_followup_deadline",
    default=None,
)


class ChatFollowupState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    model: str
    max_rounds: int
    max_tokens: int
    round_index: int
    route: str
    last_message: dict[str, Any]
    tool_names: list[str]
    assistant_text: str


def _yield_text_chunks(text: str) -> Iterator[str]:
    step = 24
    for index in range(0, len(text), step):
        yield text[index : index + step]


def prepare(state: ChatFollowupState) -> dict[str, Any]:
    tools = state.get("tools") or []
    max_rounds = int(state.get("max_rounds") or 0)
    route = "stream" if (not tools or max_rounds <= 0) else "llm"
    return {"round_index": 0, "route": route}


def route_after_prepare(state: ChatFollowupState) -> str:
    return "stream_answer" if state.get("route") == "stream" else "llm_call"


def llm_call(state: ChatFollowupState) -> dict[str, Any]:
    raise_if_stream_cancelled(_stop_event.get())
    round_index = int(state.get("round_index") or 0)
    max_rounds = int(state.get("max_rounds") or 0)
    if round_index >= max_rounds:
        return {"route": "stream"}

    from app.services.chat_agent_loop import DeepSeekClient

    client = DeepSeekClient()
    client._provider_deadline = _deadline.get()
    message = client._chat_completion(
        messages=list(state.get("messages") or []),
        tools=list(state.get("tools") or []),
        response_format=None,
        max_tokens=int(state.get("max_tokens") or 4096),
        model=str(state.get("model") or ""),
    )
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        content = (message.get("content") or "").strip()
        if content:
            return {
                "last_message": message,
                "assistant_text": content,
                "route": "answer",
            }
        return {"last_message": message, "route": "stream"}

    messages = list(state.get("messages") or [])
    messages.append(message)
    return {
        "last_message": message,
        "messages": messages,
        "route": "tools",
    }


def route_after_llm(state: ChatFollowupState) -> str:
    route = str(state.get("route") or "answer")
    if route == "tools":
        return "tools"
    if route == "stream":
        return "stream_answer"
    return "answer"


def tools_node(state: ChatFollowupState) -> dict[str, Any]:
    raise_if_stream_cancelled(_stop_event.get())
    context = _chat_ctx.get()
    if context is None:
        return {"route": "llm", "round_index": int(state.get("round_index") or 0) + 1}

    messages = list(state.get("messages") or [])
    last_message = state.get("last_message") or {}
    names: list[str] = []
    for tool_call in last_message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        name = str(function.get("name") or "").strip()
        names.append(name)
        emit_custom(
            {
                "kind": "status",
                "content": tool_status_label(name),
                "node": "tools",
                "owner": "code",
                "tool_name": name,
            }
        )
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
                emit_custom(
                    {
                        "kind": "job_started",
                        "node": "tools",
                        "owner": "code",
                        "job_kind": job.get("job_kind"),
                        "job_id": job.get("job_id"),
                    }
                )
            context.pending_jobs.clear()
    return {
        "messages": messages,
        "round_index": int(state.get("round_index") or 0) + 1,
        "route": "llm",
        "tool_names": names,
    }


def answer_node(state: ChatFollowupState) -> dict[str, Any]:
    text = str(state.get("assistant_text") or "")
    for chunk in _yield_text_chunks(text):
        emit_custom({"kind": "token", "content": chunk, "node": "answer", "owner": "worker"})
    return {"assistant_text": text}


def stream_answer(state: ChatFollowupState) -> dict[str, Any]:
    from app.services.deepseek_streaming import stream_chat_completion

    raise_if_stream_cancelled(_stop_event.get())
    for chunk in stream_chat_completion(
        messages=list(state.get("messages") or []),
        model=str(state.get("model") or ""),
        max_tokens=int(state.get("max_tokens") or 4096),
        response_format=None,
        stop_event=_stop_event.get(),
        deadline_monotonic=_deadline.get(),
    ):
        emit_custom(
            {"kind": "token", "content": chunk, "node": "stream_answer", "owner": "worker"}
        )
    return {}


def _build_graph():
    graph = StateGraph(ChatFollowupState)
    graph.add_node("prepare", prepare)
    graph.add_node("llm_call", llm_call)
    graph.add_node("tools", tools_node)
    graph.add_node("answer", answer_node)
    graph.add_node("stream_answer", stream_answer)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges(
        "prepare",
        route_after_prepare,
        {"stream_answer": "stream_answer", "llm_call": "llm_call"},
    )
    graph.add_conditional_edges(
        "llm_call",
        route_after_llm,
        {"tools": "tools", "answer": "answer", "stream_answer": "stream_answer"},
    )
    graph.add_edge("tools", "llm_call")
    graph.add_edge("answer", END)
    graph.add_edge("stream_answer", END)
    return graph.compile()


_GRAPH = _build_graph()


def iter_chat_followup_events(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    context: ChatAgentContext,
    model: str,
    max_rounds: int,
    max_tokens: int,
    stop_event: threading.Event | None = None,
    deadline_monotonic: float | None = None,
) -> Iterator[dict[str, Any]]:
    ctx_token = _chat_ctx.set(context)
    stop_token = _stop_event.set(stop_event)
    deadline_token = _deadline.set(deadline_monotonic)
    try:
        yield from iter_graph_events(
            _GRAPH,
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "max_rounds": max(0, min(int(max_rounds), 3)),
                "max_tokens": max_tokens,
                "round_index": 0,
            },
            graph_name=CHAT_FOLLOWUP_GRAPH,
        )
    finally:
        _chat_ctx.reset(ctx_token)
        _stop_event.reset(stop_token)
        _deadline.reset(deadline_token)


__all__ = ["CHAT_FOLLOWUP_GRAPH", "iter_chat_followup_events"]
