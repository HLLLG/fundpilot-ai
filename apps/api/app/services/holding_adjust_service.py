from __future__ import annotations

from datetime import datetime, timezone

from app.database import (
    get_fund_profile_by_code,
    get_most_recent_portfolio_snapshot,
    get_portfolio_summary,
    save_fund_profile,
    save_portfolio_summary,
)
from app.models import AdjustHoldingRequest, Holding, PortfolioSummary
from app.services.holding_estimates import enrich_holdings_estimates, sum_daily_profit
from app.services.holding_filters import without_placeholder_holdings, without_test_holdings
from app.services.portfolio_holdings_service import build_portfolio_holdings_response
from app.services.portfolio_snapshot import save_daily_snapshot


def adjust_holding_in_portfolio(fund_code: str, payload: AdjustHoldingRequest) -> dict:
    """手动修改单只基金的结算持有金额/收益，对齐养基宝「修改持仓」。"""
    code = (fund_code or "").strip()
    if not code or code == "000000":
        raise ValueError("fund_code 无效")

    if (
        payload.settled_holding_amount is None
        and payload.holding_profit is None
        and payload.holding_return_percent is None
    ):
        raise ValueError("请至少填写一项要修改的字段")

    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot:
        raise LookupError("当前没有持仓快照")

    holdings = [Holding.model_validate(item) for item in snapshot.get("holdings", [])]
    index = next((i for i, h in enumerate(holdings) if h.fund_code == code), None)
    if index is None:
        raise LookupError("未找到该基金持仓")

    holding = holdings[index]
    profile = get_fund_profile_by_code(code)

    amount = payload.settled_holding_amount
    if amount is None:
        amount = (
            holding.settled_holding_amount
            or (profile.settled_holding_amount if profile else None)
            or holding.holding_amount
        )

    profit = payload.holding_profit
    if profit is None:
        profit = holding.holding_profit
        if profit is None and profile is not None:
            profit = profile.holding_profit

    return_percent = payload.holding_return_percent
    if return_percent is None:
        return_percent = holding.holding_return_percent
        if return_percent is None and profile is not None:
            return_percent = profile.holding_return_percent

    patch: dict = {
        "holding_amount": amount,
        "settled_holding_amount": amount,
        "amount_includes_today": False,
    }
    if profit is not None:
        patch["holding_profit"] = profit
    if return_percent is not None:
        patch["holding_return_percent"] = return_percent
        patch["return_percent"] = return_percent

    if profile is not None:
        profile_patch: dict = {
            "holding_amount": amount,
            "settled_holding_amount": amount,
        }
        if profit is not None:
            profile_patch["holding_profit"] = profit
        if return_percent is not None:
            profile_patch["holding_return_percent"] = return_percent
        shares = profile.holding_shares
        if shares and shares > 0 and amount and profit is not None:
            profile_patch["holding_cost"] = round((amount - profit) / shares, 4)
        save_fund_profile(profile.model_copy(update=profile_patch))

    holdings[index] = holding.model_copy(update=patch)
    holdings = enrich_holdings_estimates(holdings)

    holdings = without_placeholder_holdings(without_test_holdings(holdings))
    total_assets = round(
        sum(
            (h.settled_holding_amount or h.holding_amount) + (h.daily_profit or 0)
            for h in holdings
        ),
        2,
    )
    daily_profit = sum_daily_profit(holdings) if holdings else 0.0
    daily_return_percent = None
    if holdings and total_assets > daily_profit > 0:
        previous = total_assets - daily_profit
        if previous > 0:
            daily_return_percent = round(daily_profit / previous * 100, 2)

    summary = get_portfolio_summary()
    if summary is None:
        summary = PortfolioSummary(
            total_assets=total_assets,
            daily_profit=daily_profit,
            daily_return_percent=daily_return_percent,
            holding_count=len(holdings),
        )
    else:
        summary = summary.model_copy(
            update={
                "total_assets": total_assets,
                "daily_profit": daily_profit,
                "daily_return_percent": daily_return_percent,
                "holding_count": len(holdings),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    save_portfolio_summary(summary)
    save_daily_snapshot(holdings, summary)

    return build_portfolio_holdings_response(
        holdings,
        source="snapshot",
        snapshot_date=snapshot.get("snapshot_date"),
        refreshed_at=summary.updated_at,
    )
