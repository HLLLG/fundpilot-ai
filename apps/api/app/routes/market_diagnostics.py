"""Read-only market and shadow diagnostic endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.fund_return_distribution import build_fund_return_distribution
from app.services.market_breadth_signal import build_market_breadth_signal
from app.services.sector_signal_backtest import build_sector_signal_backtest
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
    """Aggregate the latest published official open-fund NAV returns."""

    return build_fund_return_distribution()


@router.get("/shadow-escalation-digest")
def shadow_escalation_digest(days: int = 7) -> dict:
    """Return current-user bidirectional guard shadow outcomes."""

    lookback = max(1, min(days, 30))
    return build_shadow_escalation_digest(lookback_days=lookback)


__all__ = ["router"]
