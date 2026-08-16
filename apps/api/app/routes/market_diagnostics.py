"""Read-only market and shadow diagnostic endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.fund_return_distribution import build_fund_return_distribution
from app.services.llm_judge_digest import build_llm_judge_digest
from app.services.market_breadth_signal import build_market_breadth_signal
from app.services.sector_signal_backtest import build_sector_signal_backtest
from app.services.langgraph_trace import get_run, list_runs
from app.services.shadow_escalation_digest import build_shadow_escalation_digest


router = APIRouter(prefix="/api/diagnostics", tags=["market-diagnostics"])


@router.get("/sector-signal-backtest")
def sector_signal_backtest(
    days: int = 120,
    sectors: str | None = None,
) -> dict:
    labels = [part.strip() for part in (sectors or "").split(",") if part.strip()]
    return build_sector_signal_backtest(
        labels or None,
        lookback_days=days,
    )


@router.get("/market-breadth")
def market_breadth() -> dict:
    """Return the shared Shanghai/Shenzhen breadth temperature."""

    return build_market_breadth_signal()


@router.get("/fund-return-distribution")
def fund_return_distribution() -> dict:
    """Return the prewarmed current-session open-fund return distribution."""

    return build_fund_return_distribution()


@router.get("/shadow-escalation-digest")
def shadow_escalation_digest(days: int = 7) -> dict:
    """Return current-user bidirectional guard shadow outcomes."""

    lookback = max(1, min(days, 30))
    return build_shadow_escalation_digest(lookback_days=lookback)


@router.get("/llm-judge-digest")
def llm_judge_digest(days: int = 7) -> dict:
    """Return whether the optional second-pass LLM judge is actually working.

    The judge degrades silently on timeout (the deterministic guard still holds the
    line), so "working" and "paying for a timed-out call on every report" look
    identical from the outside without this.
    """

    lookback = max(1, min(days, 30))
    return build_llm_judge_digest(lookback_days=lookback)


@router.get("/graph-runs")
def graph_runs(limit: int = 20, graph_name: str | None = None) -> dict:
    """Current-user LangGraph traces. Payloads are redacted node metadata only."""

    bounded = max(1, min(limit, 50))
    name = (graph_name or "").strip() or None
    return {
        "schema_version": "langgraph_trace.v1",
        "runs": list_runs(graph_name=name, limit=bounded),
    }


@router.get("/graph-runs/{run_id}")
def graph_run_detail(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="graph run not found")
    return run


__all__ = ["router"]
