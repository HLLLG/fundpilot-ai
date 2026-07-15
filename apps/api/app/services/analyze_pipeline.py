from __future__ import annotations

from collections.abc import Callable

from app.models import AnalysisRequest, Report
from app.services.deepseek_client import DeepSeekClient, JOB_STAGES
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.risk import evaluate_portfolio_risk
from app.services.decision_data_evidence import resolve_portfolio_preflight
from app.database import save_report
from app.services.decision_clock import capture_decision_clock

ProgressCallback = Callable[[str, str], None]


def run_analysis(
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
