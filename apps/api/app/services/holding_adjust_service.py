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
from app.services.fund_nav_service import get_latest_unit_nav
from app.services.holding_estimates import enrich_holdings_estimates, sum_daily_profit
from app.services.holding_filters import without_placeholder_holdings, without_test_holdings
from app.services.portfolio_ledger_service import has_user_confirmed_position_shares
from app.services.portfolio_holdings_service import build_portfolio_holdings_response
from app.services.portfolio_snapshot import save_daily_snapshot
from app.services.trading_session import get_effective_trade_date


class ConfirmedSharesAmountConflict(ValueError):
    """Raised when an amount edit would overwrite confirmed position truth."""


def _first_not_none(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _resolve_financials(
    *,
    amount: float,
    requested_profit: float | None,
    requested_return_percent: float | None,
    current_profit: float | None,
    current_return_percent: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Resolve profit, return and total cost into one internally consistent tuple."""

    if amount <= 0:
        raise ValueError("持有金额必须大于 0；如已清仓，请使用删除该基金")

    profit = requested_profit
    return_percent = requested_return_percent
    if profit is not None:
        cost_basis = amount - profit
        if cost_basis <= 0:
            raise ValueError("持有收益必须小于持有金额，无法得到有效持仓成本")
        derived_return = round(profit / cost_basis * 100, 4)
        if return_percent is not None and abs(return_percent - derived_return) > 0.05:
            raise ValueError("持有收益与持有收益率不一致，请核对后重试")
        return round(profit, 2), derived_return, round(cost_basis, 2)

    if return_percent is not None:
        if return_percent <= -100:
            raise ValueError("持有收益率必须大于 -100%")
        cost_basis = amount / (1 + return_percent / 100)
        profit = amount - cost_basis
        return round(profit, 2), round(return_percent, 4), round(cost_basis, 2)

    profit = current_profit
    if profit is not None:
        cost_basis = amount - profit
        if cost_basis <= 0:
            raise ValueError("当前持有收益与新金额不一致，请同时修正持有收益")
        return round(profit, 2), round(profit / cost_basis * 100, 4), round(cost_basis, 2)

    return_percent = current_return_percent
    if return_percent is not None:
        if return_percent <= -100:
            raise ValueError("当前持有收益率无效，请同时修正持有收益")
        cost_basis = amount / (1 + return_percent / 100)
        profit = amount - cost_basis
        return round(profit, 2), round(return_percent, 4), round(cost_basis, 2)

    return None, None, None


def adjust_holding_in_portfolio(fund_code: str, payload: AdjustHoldingRequest) -> dict:
    from app.services.portfolio_mutation_guard import portfolio_mutation_guard

    with portfolio_mutation_guard():
        return _adjust_holding_in_portfolio_unlocked(fund_code, payload)


def _adjust_holding_in_portfolio_unlocked(
    fund_code: str,
    payload: AdjustHoldingRequest,
) -> dict:
    """手动修改结算金额/收益，并重建可持续刷新的估算份额基线。"""
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

    current_amount = _first_not_none(
        holding.settled_holding_amount,
        profile.settled_holding_amount if profile else None,
        holding.holding_amount,
    )
    amount = payload.settled_holding_amount
    if amount is None:
        amount = current_amount
    if amount is None or amount <= 0:
        raise ValueError("持有金额必须大于 0；如已清仓，请使用删除该基金")

    amount_changed = current_amount is None or abs(amount - current_amount) > 0.01
    if amount_changed and has_user_confirmed_position_shares(code):
        raise ConfirmedSharesAmountConflict(
            "该基金已有人工确认的实际份额，持有金额由份额和净值计算。"
            "请使用“同步加仓/同步减仓”或重新确认份额完成对账。"
        )

    current_profit = _first_not_none(
        holding.holding_profit,
        profile.holding_profit if profile else None,
    )
    current_return = _first_not_none(
        holding.holding_return_percent,
        profile.holding_return_percent if profile else None,
        holding.return_percent,
    )
    profit, return_percent, cost_basis = _resolve_financials(
        amount=amount,
        requested_profit=payload.holding_profit,
        requested_return_percent=payload.holding_return_percent,
        current_profit=current_profit,
        current_return_percent=current_return,
    )

    patch: dict = {
        "holding_amount": amount,
        "settled_holding_amount": amount,
        "amount_includes_today": False,
    }
    patch["holding_profit"] = profit
    patch["holding_return_percent"] = return_percent
    if return_percent is not None:
        patch["return_percent"] = return_percent

    if profile is not None:
        profile_patch: dict = {
            "holding_amount": amount,
            "settled_holding_amount": amount,
            "holding_profit": profit,
            "holding_return_percent": return_percent,
        }
        shares = profile.holding_shares
        if amount_changed:
            trade_date = get_effective_trade_date()
            latest_nav = get_latest_unit_nav(code)
            shares = (
                round(amount / latest_nav, 6)
                if latest_nav is not None and latest_nav > 0
                else None
            )
            profile_patch.update(
                {
                    "holding_shares": shares,
                    "shares_baseline_date": trade_date,
                    # The manually entered amount is the settled truth for this
                    # trade date. A same-day refresh must not roll it a second time.
                    "profit_settled_trade_date": trade_date,
                }
            )
        if shares and shares > 0 and cost_basis is not None:
            profile_patch["holding_cost"] = round(cost_basis / shares, 8)
        elif amount_changed:
            profile_patch["holding_cost"] = None
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
    # 2026-07-04 修复：同 portfolio_persistence.py——去掉 `daily_profit > 0` 的错误门槛，
    # 平盘/亏损日也应正确算出收益率，而不是写成 None。
    if holdings:
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
