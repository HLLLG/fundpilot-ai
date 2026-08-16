"""Daily report job path as a human-owned LangGraph. LLM is only the generate node."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models import AnalysisRequest, Report
from app.services.langgraph_runner import iter_graph_events
from app.services.langgraph_trace import apply_current_graph_run

DAILY_REPORT_GRAPH = "daily_report"
ProgressCallback = Callable[[str, str], None]

_scratch: ContextVar[dict[str, Any] | None] = ContextVar(
    "daily_report_scratch",
    default=None,
)


class DailyReportState(TypedDict, total=False):
    holdings_count: int
    report_id: str
    generated: bool


def _progress(stage: str) -> None:
    from app.services import analyze_pipeline as pipeline

    scratch = _scratch.get() or {}
    callback: ProgressCallback | None = scratch.get("on_progress")
    if callback is not None:
        callback(stage, pipeline.JOB_STAGES.get(stage, stage))


def preflight(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    scratch = _scratch.get() or {}
    request: AnalysisRequest = scratch["request"]
    decision_clock = pipeline.capture_decision_clock()
    resolved = pipeline.resolve_portfolio_preflight(
        request.holdings,
        allow_stale=request.allow_stale_portfolio_snapshot,
        now=decision_clock.decision_at,
    )
    request = request.model_copy(
        update={
            "holdings": resolved.holdings,
            "portfolio_snapshot_context": resolved.context,
        }
    )
    if not request.holdings:
        raise ValueError("至少需要一条基金持仓")
    scratch["request"] = request
    scratch["decision_clock"] = decision_clock
    return {"holdings_count": len(request.holdings)}


def resolve_holdings(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    scratch = _scratch.get() or {}
    request: AnalysisRequest = scratch["request"]
    resolved_holdings = pipeline.FundProfileService().resolve_holdings(request.holdings)
    scratch["request"] = request.model_copy(update={"holdings": resolved_holdings})
    return {"holdings_count": len(resolved_holdings)}


def evaluate_risk(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    scratch = _scratch.get() or {}
    request: AnalysisRequest = scratch["request"]
    scratch["risk"] = pipeline.evaluate_portfolio_risk(request.holdings, request.profile)
    return {"holdings_count": len(request.holdings)}


def fetch_fund_data(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    _progress("fund_data")
    scratch = _scratch.get() or {}
    request: AnalysisRequest = scratch["request"]
    snapshots, nav_trends = pipeline.FundDataService().get_snapshots_with_nav_trends(
        request.holdings
    )
    scratch["snapshots"] = snapshots
    scratch["nav_trends"] = nav_trends
    return {"holdings_count": len(request.holdings)}


def generate_report(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    scratch = _scratch.get() or {}
    request: AnalysisRequest = scratch["request"]
    report = pipeline.DeepSeekClient().generate_report(
        request,
        scratch["risk"],
        scratch["snapshots"],
        nav_trends_by_code=scratch["nav_trends"],
        on_progress=scratch.get("on_progress"),
        decision_at=scratch["decision_clock"].decision_at,
    )
    scratch["report"] = report
    return {"generated": True}


def save_report_node(_state: DailyReportState) -> dict[str, Any]:
    from app.services import analyze_pipeline as pipeline

    _progress("saving")
    scratch = _scratch.get() or {}
    report: Report = apply_current_graph_run(scratch["report"])
    saved = pipeline.save_report(report)
    scratch["report"] = saved
    return {"report_id": saved.id, "generated": True}


def _build_graph():
    graph = StateGraph(DailyReportState)
    graph.add_node("preflight", preflight)
    graph.add_node("resolve_holdings", resolve_holdings)
    graph.add_node("evaluate_risk", evaluate_risk)
    graph.add_node("fetch_fund_data", fetch_fund_data)
    graph.add_node("generate_report", generate_report)
    graph.add_node("save_report", save_report_node)
    graph.add_edge(START, "preflight")
    graph.add_edge("preflight", "resolve_holdings")
    graph.add_edge("resolve_holdings", "evaluate_risk")
    graph.add_edge("evaluate_risk", "fetch_fund_data")
    graph.add_edge("fetch_fund_data", "generate_report")
    graph.add_edge("generate_report", "save_report")
    graph.add_edge("save_report", END)
    return graph.compile()


_GRAPH = _build_graph()


def run_daily_report_graph(
    request: AnalysisRequest,
    on_progress: ProgressCallback | None = None,
) -> Report:
    scratch: dict[str, Any] = {"request": request, "on_progress": on_progress}
    token = _scratch.set(scratch)
    try:
        for event in iter_graph_events(
            _GRAPH,
            {"holdings_count": len(request.holdings)},
            graph_name=DAILY_REPORT_GRAPH,
        ):
            if event.get("type") == "graph" and event.get("run_id"):
                scratch["run_id"] = event["run_id"]
        report = scratch.get("report")
        if not isinstance(report, Report):
            raise RuntimeError("daily_report graph produced no report")
        return report
    finally:
        _scratch.reset(token)


__all__ = ["DAILY_REPORT_GRAPH", "run_daily_report_graph"]
