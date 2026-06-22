from __future__ import annotations

from app.models import Holding


def serialize_holding_for_client(holding: Holding) -> dict:
    """API 持仓 JSON：持久化字段 + 展示层口径（与 holding_estimates / holding_metrics 一致）。"""
    from app.services.holding_estimates import build_holding_display_metrics
    from app.services.holding_metrics import (
        compute_estimated_daily_return_percent,
        holding_daily_return_is_estimated,
    )

    payload = holding.model_dump()
    display = build_holding_display_metrics(holding)
    settled = holding.settled_holding_amount or holding.holding_amount
    payload["settled_holding_amount"] = settled
    payload["display_holding_amount"] = settled
    payload["estimated_holding_return_percent"] = display["estimated_holding_return_percent"]
    payload["estimated_holding_profit"] = display["estimated_holding_profit"]
    payload["holding_return_is_estimated"] = display["holding_return_is_estimated"]
    payload["estimated_daily_return_percent"] = compute_estimated_daily_return_percent(holding)
    payload["daily_return_is_estimated"] = holding_daily_return_is_estimated(holding)
    return payload


def serialize_holdings_for_client(holdings: list[Holding]) -> list[dict]:
    return [serialize_holding_for_client(holding) for holding in holdings]
