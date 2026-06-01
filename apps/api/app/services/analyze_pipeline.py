from __future__ import annotations

from app.models import AnalysisRequest, Report
from app.services.deepseek_client import DeepSeekClient
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService
from app.services.risk import evaluate_portfolio_risk
from app.database import save_report


def run_analysis(request: AnalysisRequest) -> Report:
    if not request.holdings:
        raise ValueError("至少需要一条基金持仓")

    resolved_holdings = FundProfileService().resolve_holdings(request.holdings)
    enriched_request = request.model_copy(update={"holdings": resolved_holdings})
    risk = evaluate_portfolio_risk(enriched_request.holdings, enriched_request.profile)
    snapshots = FundDataService().get_snapshots(enriched_request.holdings)
    report = DeepSeekClient().generate_report(enriched_request, risk, snapshots)
    save_report(report)
    return report

