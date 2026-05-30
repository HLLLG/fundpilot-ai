from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


Action = Literal["watch", "pause_add", "staggered_add", "risk_review"]
RiskLevel = Literal["low", "medium", "high"]


class Holding(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6)
    fund_name: str
    holding_amount: float = Field(..., ge=0)
    return_percent: float = 0
    daily_profit: float | None = None
    sector_name: str | None = None
    sector_return_percent: float | None = None
    user_note: str | None = None


class InvestorProfile(BaseModel):
    style: str = "稳健"
    horizon: str = "半年到一年"
    max_drawdown_percent: float = 8
    concentration_limit_percent: float = 35
    prefer_dca: bool = True
    avoid_chasing: bool = True


class RiskAlert(BaseModel):
    code: str
    severity: RiskLevel
    message: str
    evidence: str


class RiskAssessment(BaseModel):
    level: RiskLevel
    suggested_action: Action
    weighted_return_percent: float
    alerts: list[RiskAlert]


class AnalysisRequest(BaseModel):
    holdings: list[Holding]
    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    ocr_text: str | None = None


class MarketItem(BaseModel):
    topic: str
    query: str
    source: str
    note: str


class FundProfile(BaseModel):
    fund_code: str = Field(..., min_length=6, max_length=6)
    fund_name: str
    aliases: list[str] = Field(default_factory=list)
    holding_amount: float | None = None
    holding_shares: float | None = None
    position_percent: float | None = None
    holding_profit: float | None = None
    holding_return_percent: float | None = None
    holding_cost: float | None = None
    daily_profit: float | None = None
    yesterday_profit: float | None = None
    holding_days: int | None = None
    sector_name: str | None = None
    sector_return_percent: float | None = None
    source: str = "yangjibao-detail"


class FundSnapshot(BaseModel):
    fund_code: str
    fund_name: str
    latest_nav: float | None = None
    nav_date: str | None = None
    source: str
    note: str | None = None


class Report(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str
    risk: RiskAssessment
    holdings: list[Holding]
    snapshots: list[FundSnapshot] = Field(default_factory=list)
    market_context: list[MarketItem] = Field(default_factory=list)
    summary: str
    recommendations: list[str]
    caveats: list[str]
    provider: str = "offline"
