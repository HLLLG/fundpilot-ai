"""Redacted, durable LangGraph node traces.

The graph runtime is for observability: every node transition is recorded with
an owner (code / worker / agent). Prompt text, holdings dumps, tool results,
and credentials are never stored. Persist failures must not break the request.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from app.config import get_settings
from app.database import _connect
from app.request_context import try_get_request_user_id

logger = logging.getLogger(__name__)

LANGGRAPH_TRACE_SCHEMA_VERSION = "langgraph_trace.v1"

GraphOwner = Literal["code", "worker", "agent"]
GraphStatus = Literal["running", "completed", "failed"]

MAX_PAYLOAD_CHARS = 1200
MAX_ERROR_CHARS = 240
MAX_EVENTS_PER_RUN = 200
MAX_LIST_LIMIT = 50

_KEEP_PAYLOAD_KEYS = frozenset(
    {
        "kind",
        "node",
        "owner",
        "label",
        "stage",
        "route",
        "round_index",
        "tool_name",
        "tool_names",
        "job_kind",
        "job_id",
        "holdings_count",
        "candidate_count",
        "message_count",
        "answer_chars",
        "token_count",
        "report_id",
        "generated",
        "model",
        "error",
    }
)

GRAPH_NODE_OWNERS: dict[tuple[str, str], GraphOwner] = {
    ("chat_followup", "prepare"): "code",
    ("chat_followup", "llm_call"): "agent",
    ("chat_followup", "tools"): "code",
    ("chat_followup", "answer"): "worker",
    ("chat_followup", "stream_answer"): "worker",
    ("daily_report", "preflight"): "code",
    ("daily_report", "resolve_holdings"): "code",
    ("daily_report", "evaluate_risk"): "code",
    ("daily_report", "fetch_fund_data"): "code",
    ("daily_report", "generate_report"): "worker",
    ("daily_report", "save_report"): "code",
    ("discovery_scan", "execute"): "code",
    ("discovery_scan", "connected"): "code",
    ("discovery_scan", "sector_heat"): "code",
    ("discovery_scan", "candidate_pool"): "code",
    ("discovery_scan", "news"): "worker",
    ("discovery_scan", "generating"): "worker",
    ("discovery_scan", "guarding"): "code",
    ("discovery_scan", "saving"): "code",
    ("daily_report_stream", "fund_data"): "code",
    ("daily_report_stream", "news_prefetch"): "worker",
    ("daily_report_stream", "news_summarize"): "worker",
    ("daily_report_stream", "context"): "code",
    ("daily_report_stream", "generating"): "worker",
    ("daily_report_stream", "judging"): "worker",
    ("daily_report_stream", "saving"): "code",
    ("daily_report_stream", "salvage"): "worker",
    ("discovery_scan_stream", "connected"): "code",
    ("discovery_scan_stream", "sector_heat"): "code",
    ("discovery_scan_stream", "candidate_pool"): "code",
    ("discovery_scan_stream", "news"): "worker",
    ("discovery_scan_stream", "generating"): "worker",
    ("discovery_scan_stream", "guarding"): "code",
    ("discovery_scan_stream", "saving"): "code",
    ("discovery_scan_stream", "salvage"): "worker",
}

_stream_recorder: ContextVar["GraphRunRecorder | None"] = ContextVar(
    "langgraph_stream_recorder",
    default=None,
)
_pending_graph_run: ContextVar[tuple[str, str] | None] = ContextVar(
    "langgraph_pending_run",
    default=None,
)


def node_owner(graph_name: str, node: str | None) -> GraphOwner:
    if not node:
        return "code"
    return GRAPH_NODE_OWNERS.get((graph_name, node), "code")


def redact_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _KEEP_PAYLOAD_KEYS:
            continue
        if key in {"tool_names"} and isinstance(value, list):
            redacted[key] = [str(item)[:64] for item in value[:8]]
            continue
        if key in {"tool_name", "job_kind", "job_id", "report_id", "model", "label", "stage", "route", "kind", "node", "owner"}:
            redacted[key] = str(value)[:80] if value is not None else None
            continue
        if key in {"round_index", "holdings_count", "candidate_count", "message_count", "answer_chars", "token_count"}:
            try:
                redacted[key] = int(value)
            except (TypeError, ValueError):
                continue
            continue
        if key == "generated":
            redacted[key] = bool(value)
            continue
        if key == "error":
            redacted[key] = str(value)[:MAX_ERROR_CHARS]
    encoded = json.dumps(redacted, ensure_ascii=False)
    if len(encoded) > MAX_PAYLOAD_CHARS:
        return {"truncated": True, "keys": sorted(redacted)}
    return redacted


def compact_state_update(update: Any) -> dict[str, Any]:
    if not isinstance(update, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "route",
        "round_index",
        "holdings_count",
        "candidate_count",
        "report_id",
        "generated",
        "model",
        "tool_names",
    ):
        if key in update:
            compact[key] = update[key]
    messages = update.get("messages")
    if isinstance(messages, list):
        compact["message_count"] = len(messages)
    last = update.get("last_message")
    if isinstance(last, dict):
        names: list[str] = []
        for call in last.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = str(function.get("name") or "").strip()
            if name:
                names.append(name)
        if names:
            compact["tool_names"] = names[:8]
        content = last.get("content")
        if isinstance(content, str) and content:
            compact["answer_chars"] = len(content)
    return redact_payload(compact)


def attach_graph_run_metadata(
    facts: dict[str, Any] | None,
    run_id: str | None,
    graph_name: str,
) -> dict[str, Any]:
    next_facts = dict(facts or {})
    if not run_id:
        return next_facts
    pipeline = dict(next_facts.get("pipeline") or {})
    pipeline["graph_run_id"] = run_id
    pipeline["graph_name"] = graph_name
    pipeline["graph_trace_schema"] = LANGGRAPH_TRACE_SCHEMA_VERSION
    next_facts["pipeline"] = pipeline
    return next_facts


def bind_current_graph_run(run_id: str, graph_name: str) -> Token:
    return _pending_graph_run.set((run_id, graph_name))


def reset_current_graph_run(token: Token) -> None:
    _pending_graph_run.reset(token)


def current_graph_run() -> tuple[str, str] | None:
    return _pending_graph_run.get()


def apply_current_graph_run(report: Any) -> Any:
    pending = _pending_graph_run.get()
    if pending is None:
        return report
    return attach_graph_run_to_report(report, pending[0], pending[1])


def attach_graph_run_to_report(report: Any, run_id: str | None, graph_name: str) -> Any:
    if report is None or not run_id:
        return report
    if hasattr(report, "analysis_facts"):
        return report.model_copy(
            update={
                "analysis_facts": attach_graph_run_metadata(
                    getattr(report, "analysis_facts", None),
                    run_id,
                    graph_name,
                )
            }
        )
    if hasattr(report, "discovery_facts"):
        return report.model_copy(
            update={
                "discovery_facts": attach_graph_run_metadata(
                    getattr(report, "discovery_facts", None),
                    run_id,
                    graph_name,
                )
            }
        )
    return report


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def start_run(graph_name: str, *, thread_id: str | None = None) -> str:
    run_id = uuid4().hex
    thread = thread_id or run_id
    user_id = try_get_request_user_id()
    if user_id is None:
        return run_id
    try:
        _prune_expired_runs()
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO langgraph_runs (
                    id, userId, graph_name, status, thread_id, summary, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'running', ?, NULL, NULL, ?, ?)
                """,
                (run_id, user_id, graph_name, thread, _now_iso(), _now_iso()),
            )
    except Exception:
        logger.exception("langgraph_runs insert failed")
    return run_id


def append_event(
    run_id: str,
    event_type: str,
    *,
    node: str | None = None,
    owner: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if try_get_request_user_id() is None:
        return
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM langgraph_run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row["n"] if row is not None else 0)
            if seq >= MAX_EVENTS_PER_RUN:
                return
            connection.execute(
                """
                INSERT INTO langgraph_run_events (
                    id, run_id, seq, event_type, node, owner, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    run_id,
                    seq,
                    event_type[:32],
                    (node or "")[:64] or None,
                    (owner or "")[:16] or None,
                    json.dumps(redact_payload(payload or {}), ensure_ascii=False),
                    _now_iso(),
                ),
            )
    except Exception:
        logger.exception("langgraph_run_events insert failed")


def finish_run(
    run_id: str,
    status: GraphStatus,
    *,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if try_get_request_user_id() is None:
        return
    try:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE langgraph_runs
                SET status = ?, summary = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(redact_payload(summary or {}), ensure_ascii=False),
                    (error or "")[:MAX_ERROR_CHARS] or None,
                    _now_iso(),
                    run_id,
                ),
            )
    except Exception:
        logger.exception("langgraph_runs finish failed")


def list_runs(
    *,
    graph_name: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    user_id = try_get_request_user_id()
    if user_id is None:
        return []
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    try:
        with _connect() as connection:
            if graph_name:
                rows = connection.execute(
                    """
                    SELECT id, graph_name, status, thread_id, summary, error,
                           created_at, updated_at
                    FROM langgraph_runs
                    WHERE userId = ? AND graph_name = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, graph_name, bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, graph_name, status, thread_id, summary, error,
                           created_at, updated_at
                    FROM langgraph_runs
                    WHERE userId = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, bounded),
                ).fetchall()
        return [_run_row(row) for row in rows]
    except Exception:
        logger.exception("langgraph_runs list failed")
        return []


def get_run(run_id: str) -> dict[str, Any] | None:
    user_id = try_get_request_user_id()
    if user_id is None:
        return None
    try:
        with _connect() as connection:
            row = connection.execute(
                """
                SELECT id, graph_name, status, thread_id, summary, error,
                       created_at, updated_at
                FROM langgraph_runs
                WHERE id = ? AND userId = ?
                """,
                (run_id, user_id),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """
                SELECT seq, event_type, node, owner, payload, created_at
                FROM langgraph_run_events
                WHERE run_id = ?
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()
        payload = _run_row(row)
        payload["events"] = [_event_row(item) for item in events]
        return payload
    except Exception:
        logger.exception("langgraph_runs get failed")
        return None


def _run_row(row: Any) -> dict[str, Any]:
    summary = {}
    raw = row["summary"] if row["summary"] is not None else None
    if raw:
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                summary = redact_payload(parsed)
        except json.JSONDecodeError:
            summary = {}
    return {
        "schema_version": LANGGRAPH_TRACE_SCHEMA_VERSION,
        "id": str(row["id"]),
        "graph_name": str(row["graph_name"]),
        "status": str(row["status"]),
        "thread_id": str(row["thread_id"]),
        "summary": summary,
        "error": row["error"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _event_row(row: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    raw = row["payload"]
    if raw:
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                payload = redact_payload(parsed)
        except json.JSONDecodeError:
            payload = {}
    return {
        "seq": int(row["seq"]),
        "event_type": str(row["event_type"]),
        "node": row["node"],
        "owner": row["owner"],
        "payload": payload,
        "created_at": str(row["created_at"]),
    }


def _prune_expired_runs() -> None:
    days = max(1, int(getattr(get_settings(), "langgraph_run_retention_days", 14)))
    cutoff = (_now() - timedelta(days=days)).isoformat()
    try:
        with _connect() as connection:
            stale = connection.execute(
                "SELECT id FROM langgraph_runs WHERE created_at < ?",
                (cutoff,),
            ).fetchall()
            ids = [str(row["id"]) for row in stale]
            if not ids:
                return
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"DELETE FROM langgraph_run_events WHERE run_id IN ({placeholders})",
                ids,
            )
            connection.execute(
                f"DELETE FROM langgraph_runs WHERE id IN ({placeholders})",
                ids,
            )
    except Exception:
        logger.exception("langgraph_runs prune failed")


class GraphRunRecorder:
    """Record named stages for SSE paths that are not yet a LangGraph."""

    def __init__(self, graph_name: str) -> None:
        self.graph_name = graph_name
        self.run_id = start_run(graph_name)
        self._last_node: str | None = None
        self._token: Token | None = None
        self._pending_token: Token | None = None
        self._finished = False
        self.completed = False

    def mark_completed(self) -> None:
        self.completed = True

    def stage(self, node: str, **payload: Any) -> None:
        if not node or node == self._last_node:
            return
        self._last_node = node
        owner = node_owner(self.graph_name, node)
        append_event(
            self.run_id,
            "stage",
            node=node,
            owner=owner,
            payload={"kind": "stage", "node": node, "owner": owner, **payload},
        )

    def finish(self, status: GraphStatus, error: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        finish_run(
            self.run_id,
            status,
            summary={"node": self._last_node} if self._last_node else {},
            error=error,
        )


def begin_stream_run(graph_name: str) -> GraphRunRecorder:
    recorder = GraphRunRecorder(graph_name)
    recorder._token = _stream_recorder.set(recorder)
    recorder._pending_token = bind_current_graph_run(recorder.run_id, graph_name)
    return recorder


def try_get_stream_recorder() -> GraphRunRecorder | None:
    return _stream_recorder.get()


def finish_stream_run(
    recorder: GraphRunRecorder,
    status: GraphStatus,
    error: str | None = None,
) -> None:
    recorder.finish("completed" if recorder.completed else status, error)
    if recorder._pending_token is not None:
        reset_current_graph_run(recorder._pending_token)
        recorder._pending_token = None
    if recorder._token is not None:
        _stream_recorder.reset(recorder._token)
        recorder._token = None


__all__ = [
    "GRAPH_NODE_OWNERS",
    "LANGGRAPH_TRACE_SCHEMA_VERSION",
    "GraphRunRecorder",
    "append_event",
    "apply_current_graph_run",
    "attach_graph_run_metadata",
    "attach_graph_run_to_report",
    "begin_stream_run",
    "bind_current_graph_run",
    "current_graph_run",
    "compact_state_update",
    "finish_run",
    "finish_stream_run",
    "get_run",
    "list_runs",
    "node_owner",
    "redact_payload",
    "reset_current_graph_run",
    "start_run",
    "try_get_stream_recorder",
]
