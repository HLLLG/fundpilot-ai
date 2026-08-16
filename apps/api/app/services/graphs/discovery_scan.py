"""Discovery job path as a LangGraph wrapper around the existing pipeline.

The long `run_discovery_impl` body is not split in this slice. Each existing
`progress()` stage is emitted as a custom graph event so the same diagnostics
API can show sector_heat → candidate_pool → news → generating → save.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models import DiscoveryRequest, FundDiscoveryReport
from app.services.langgraph_runner import emit_custom, iter_graph_events
from app.services.langgraph_trace import node_owner

DISCOVERY_SCAN_GRAPH = "discovery_scan"
ProgressCallback = Callable[[str, str], None]

_scratch: ContextVar[dict[str, Any] | None] = ContextVar(
    "discovery_scan_scratch",
    default=None,
)


class DiscoveryScanState(TypedDict, total=False):
    report_id: str
    generated: bool


def execute(_state: DiscoveryScanState) -> dict[str, Any]:
    from app.services.discovery_pipeline import DISCOVERY_JOB_STAGES, run_discovery_impl

    scratch = _scratch.get() or {}
    request: DiscoveryRequest = scratch["request"]
    callback: ProgressCallback | None = scratch.get("on_progress")

    def on_progress(stage: str, label: str) -> None:
        owner = node_owner(DISCOVERY_SCAN_GRAPH, stage)
        emit_custom(
            {
                "kind": "stage",
                "node": stage,
                "label": label or DISCOVERY_JOB_STAGES.get(stage, stage),
                "owner": owner,
            }
        )
        if callback is not None:
            callback(stage, label)

    report = run_discovery_impl(request, on_progress=on_progress)
    scratch["report"] = report
    return {"report_id": report.id, "generated": True}


def _build_graph():
    graph = StateGraph(DiscoveryScanState)
    graph.add_node("execute", execute)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    return graph.compile()


_GRAPH = _build_graph()


def run_discovery_graph(
    request: DiscoveryRequest,
    on_progress: ProgressCallback | None = None,
) -> FundDiscoveryReport:
    scratch: dict[str, Any] = {"request": request, "on_progress": on_progress}
    token = _scratch.set(scratch)
    try:
        for event in iter_graph_events(
            _GRAPH,
            {},
            graph_name=DISCOVERY_SCAN_GRAPH,
        ):
            if event.get("type") == "graph" and event.get("run_id"):
                scratch["run_id"] = event["run_id"]
        report = scratch.get("report")
        if not isinstance(report, FundDiscoveryReport):
            raise RuntimeError("discovery_scan graph produced no report")
        return report
    finally:
        _scratch.reset(token)


__all__ = ["DISCOVERY_SCAN_GRAPH", "run_discovery_graph"]
