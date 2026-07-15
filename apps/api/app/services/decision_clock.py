from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.news_freshness import normalize_news_now
from app.services.trading_session import build_trading_session


@dataclass(frozen=True)
class DecisionClock:
    """One immutable Shanghai-time clock for a logical report request."""

    decision_at: datetime
    session: dict


def capture_decision_clock(now: datetime | None = None) -> DecisionClock:
    decision_at = normalize_news_now(now)
    return DecisionClock(
        decision_at=decision_at,
        session=build_trading_session(decision_at),
    )
