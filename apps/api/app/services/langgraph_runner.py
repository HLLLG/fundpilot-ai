"""Run a compiled LangGraph and persist redacted node transitions."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from langgraph.config import get_stream_writer

from app.services.langgraph_trace import (
    append_event,
    bind_current_graph_run,
    compact_state_update,
    finish_run,
    node_owner,
    redact_payload,
    reset_current_graph_run,
    start_run,
)


def emit_custom(payload: dict[str, Any]) -> None:
    try:
        get_stream_writer()(payload)
    except Exception:
        return


def iter_graph_events(
    graph: Any,
    inputs: dict[str, Any],
    *,
    graph_name: str,
    config: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    run_id = start_run(graph_name)
    pending_token = bind_current_graph_run(run_id, graph_name)
    last_node: str | None = None
    try:
        append_event(
            run_id,
            "run_start",
            payload={"kind": "run_start", "node": graph_name},
        )
        yield {
            "type": "graph",
            "run_id": run_id,
            "graph_name": graph_name,
            "status": "started",
            "owner": "code",
        }
        for chunk in graph.stream(
            inputs,
            config or {},
            stream_mode=["updates", "custom"],
        ):
            mode, data = _unpack_stream_chunk(chunk)
            if mode == "custom" and isinstance(data, dict):
                kind = str(data.get("kind") or "custom")
                node = str(data.get("node") or last_node or "")
                owner = str(data.get("owner") or node_owner(graph_name, node or None))
                if kind != "token":
                    append_event(
                        run_id,
                        kind,
                        node=node or None,
                        owner=owner,
                        payload=redact_payload({**data, "node": node, "owner": owner}),
                    )
                mapped = _map_custom_event(data, run_id=run_id, owner=owner)
                if mapped is not None:
                    yield mapped
                continue
            if mode == "updates" and isinstance(data, dict):
                for node_name, update in data.items():
                    last_node = str(node_name)
                    owner = node_owner(graph_name, last_node)
                    append_event(
                        run_id,
                        "node_end",
                        node=last_node,
                        owner=owner,
                        payload=compact_state_update(update),
                    )
                    yield {
                        "type": "graph",
                        "run_id": run_id,
                        "graph_name": graph_name,
                        "node": last_node,
                        "status": "completed",
                        "owner": owner,
                    }
        finish_run(run_id, "completed", summary={"node": last_node} if last_node else {})
        yield {
            "type": "graph",
            "run_id": run_id,
            "graph_name": graph_name,
            "status": "completed",
            "owner": "code",
        }
    except Exception as exc:
        finish_run(run_id, "failed", error=type(exc).__name__)
        append_event(
            run_id,
            "error",
            node=last_node,
            owner="code",
            payload={"error": type(exc).__name__},
        )
        raise
    finally:
        reset_current_graph_run(pending_token)


def _unpack_stream_chunk(chunk: Any) -> tuple[str, Any]:
    if isinstance(chunk, tuple) and len(chunk) == 2:
        return str(chunk[0]), chunk[1]
    if isinstance(chunk, dict) and "type" in chunk:
        return str(chunk.get("type") or ""), chunk.get("data")
    return "updates", chunk


def _map_custom_event(
    data: dict[str, Any],
    *,
    run_id: str,
    owner: str,
) -> dict[str, Any] | None:
    kind = str(data.get("kind") or "")
    if kind == "token" and isinstance(data.get("content"), str):
        return {"type": "token", "content": data["content"]}
    if kind == "status" and isinstance(data.get("content"), str):
        return {"type": "status", "content": data["content"]}
    if kind == "job_started":
        return {
            "type": "job_started",
            "job_kind": data.get("job_kind"),
            "job_id": data.get("job_id"),
        }
    node = data.get("node")
    return {
        "type": "graph",
        "run_id": run_id,
        "node": node,
        "status": kind or "custom",
        "owner": owner,
        "label": data.get("label"),
    }


__all__ = ["emit_custom", "iter_graph_events"]
