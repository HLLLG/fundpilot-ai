from __future__ import annotations

from collections.abc import Callable
import logging

from app.models import AnalysisRequest, Report
from app.services.deepseek_client import DeepSeekClient, JOB_STAGES
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.risk import evaluate_portfolio_risk
from app.services.decision_data_evidence import resolve_portfolio_preflight
from app.database import save_report
from app.services.decision_clock import capture_decision_clock
from app.services.provider_lane import LANE_ANALYSIS, provider_lane

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str], None]


def run_analysis(
    request: AnalysisRequest,
    on_progress: ProgressCallback | None = None,
) -> Report:
    from app.config import get_settings

    with provider_lane(LANE_ANALYSIS):
        if getattr(get_settings(), "langgraph_enabled", True):
            try:
                return _run_analysis_via_graph(request, on_progress)
            except ImportError:
                logger.warning(
                    "langgraph is enabled but not installed; "
                    "falling back to the linear daily-report pipeline"
                )
        return run_analysis_linear(request, on_progress)


def _run_analysis_via_graph(
    request: AnalysisRequest,
    on_progress: ProgressCallback | None,
) -> Report:
    from app.services.graphs.daily_report import run_daily_report_graph

    return run_daily_report_graph(request, on_progress)


def run_analysis_linear(
    request: AnalysisRequest,
    on_progress: ProgressCallback | None = None,
) -> Report:
    decision_clock = capture_decision_clock()
    preflight = resolve_portfolio_preflight(
        request.holdings,
        allow_stale=request.allow_stale_portfolio_snapshot,
        now=decision_clock.decision_at,
    )
    request = request.model_copy(
        update={
            "holdings": preflight.holdings,
            "portfolio_snapshot_context": preflight.context,
        }
    )
    if not request.holdings:
        raise ValueError("至少需要一条基金持仓")

    def progress(stage: str) -> None:
        if on_progress is not None:
            on_progress(stage, JOB_STAGES.get(stage, stage))

    resolved_holdings = FundProfileService().resolve_holdings(request.holdings)
    enriched_request = request.model_copy(update={"holdings": resolved_holdings})
    risk = evaluate_portfolio_risk(enriched_request.holdings, enriched_request.profile)
    progress("fund_data")
    snapshots, nav_trends = FundDataService().get_snapshots_with_nav_trends(
        enriched_request.holdings
    )
    report = DeepSeekClient().generate_report(
        enriched_request,
        risk,
        snapshots,
        nav_trends_by_code=nav_trends,
        on_progress=on_progress,
        decision_at=decision_clock.decision_at,
    )
    progress("saving")
    return save_report(report)
