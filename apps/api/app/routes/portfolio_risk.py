"""Current-user portfolio stress and realized-fee evidence endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.database import list_fund_transactions
from app.services.portfolio_fee_evidence import build_portfolio_fee_evidence
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.portfolio_stress_test import build_portfolio_stress_test


router = APIRouter(prefix="/api/portfolio", tags=["portfolio-risk"])


@router.get("/stress-test")
def portfolio_stress_test(response: Response, lookback_days: int = 252) -> dict:
    response.headers["Cache-Control"] = "no-store"
    holdings, *_ = load_persisted_holdings()
    bounded = max(60, min(lookback_days, 400))
    return build_portfolio_stress_test(holdings, lookback_days=bounded)


@router.get("/fee-evidence")
def portfolio_fee_evidence(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return build_portfolio_fee_evidence(list_fund_transactions())


__all__ = ["router"]
