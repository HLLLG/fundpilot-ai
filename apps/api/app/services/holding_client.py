from __future__ import annotations

from app.models import Holding


def serialize_holding_for_client(holding: Holding) -> dict:
    """API 持仓 JSON：持久化字段 + 展示层口径（与 holding_estimates / holding_metrics 一致）。"""
    from app.services.holding_estimates import build_holding_display_metrics
    from app.services.holding_metrics import (
        compute_estimated_daily_return_percent,
        holding_daily_return_is_estimated,
    )
    from app.services.holding_amount_sync import resolve_display_settled_amount
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    payload = holding.model_dump()
    display = build_holding_display_metrics(holding)
    settled = resolve_display_settled_amount(holding)
    profile = get_profile_for_holding(holding)
    # 养基宝口径：持有金额=上一交易日结算额；当日收益单独一列。总资产才用 settled+daily。
    payload["settled_holding_amount"] = settled
    payload["display_holding_amount"] = settled
    payload["holding_amount"] = settled
    payload["estimated_holding_return_percent"] = display["estimated_holding_return_percent"]
    payload["estimated_holding_profit"] = display["estimated_holding_profit"]
    payload["holding_return_is_estimated"] = display["holding_return_is_estimated"]
    payload["estimated_daily_return_percent"] = compute_estimated_daily_return_percent(holding)
    payload["daily_return_is_estimated"] = holding_daily_return_is_estimated(holding)
    payload["profit_accrual_deferred"] = is_profit_accrual_deferred(profile)
    return payload


def serialize_holdings_for_client(holdings: list[Holding]) -> list[dict]:
    return [serialize_holding_for_client(holding) for holding in holdings]
