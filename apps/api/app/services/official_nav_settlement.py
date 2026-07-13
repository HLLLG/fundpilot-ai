from __future__ import annotations

from datetime import datetime, timezone

from app.database import (
    get_most_recent_portfolio_snapshot,
    list_fund_profiles,
    save_portfolio_summary,
)
from app.models import Holding, PortfolioSummary
from app.services.fund_nav_service import get_official_nav_return, prime_official_nav_cache
from app.services.fund_profile import match_profiles_to_holdings
from app.services.holding_estimates import compute_daily_profit_from_rate, sum_daily_profit
from app.services.holding_filters import without_inactive_holdings, without_placeholder_holdings, without_test_holdings
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.portfolio_snapshot import save_daily_snapshot
from app.services.profit_accrual_defer import is_profit_accrual_deferred
from app.services.trading_session import build_trading_session

SOURCE = "official_nav_settlement"


def _empty_response(
    *,
    reason: str,
    session: dict,
    settlement_date: str,
    source: str = SOURCE,
    snapshot_date: str | None = None,
) -> dict:
    return {
        "ok": True,
        "skipped": True,
        "reason": reason,
        "session": session,
        "settlement_date": settlement_date,
        "updated_count": 0,
        "holdings": [],
        "portfolio_summary": None,
        "source": source,
        "snapshot_date": snapshot_date,
        "refreshed_at": None,
    }


def settle_official_nav_for_holdings(
    holdings: list[Holding],
    *,
    settlement_date: str | None = None,
) -> tuple[list[Holding], int]:
    if settlement_date is None:
        settlement_date = str(build_trading_session().get("effective_trade_date") or "")
    if not holdings:
        return [], 0
    if not any(
        (holding.fund_code or "").strip() not in {"", "000000"}
        for holding in holdings
    ):
        return list(holdings), 0

    profiles = list_fund_profiles()
    profiles_by_code = {profile.fund_code: profile for profile in profiles}
    matched_profiles = match_profiles_to_holdings(
        holdings,
        profiles,
        profiles_by_code=profiles_by_code,
    )
    updated: list[Holding] = []
    updated_count = 0
    for holding, profile in zip(holdings, matched_profiles, strict=True):
        code = (holding.fund_code or "").strip()
        if not code or code == "000000":
            updated.append(holding)
            continue

        if is_profit_accrual_deferred(profile):
            updated.append(holding)
            continue

        nav_return = get_official_nav_return(code, settlement_date)
        if nav_return is None:
            updated.append(holding)
            continue

        amount = holding.settled_holding_amount or holding.holding_amount
        settled = holding.model_copy(
            update={
                "daily_return_percent": nav_return,
                "daily_profit": compute_daily_profit_from_rate(
                    amount,
                    nav_return,
                    amount_includes_today=holding.amount_includes_today or False,
                ),
                "daily_return_percent_source": "official_nav",
            }
        )
        updated.append(settled)
        updated_count += 1

    return updated, updated_count


def _load_settlement_holdings() -> tuple[list[Holding], str, str | None, datetime | None]:
    snapshot = get_most_recent_portfolio_snapshot()
    if snapshot and snapshot.get("holdings"):
        holdings = [
            Holding.model_validate(item)
            for item in snapshot.get("holdings", [])
        ]
        cleaned = without_inactive_holdings(
            without_placeholder_holdings(without_test_holdings(holdings))
        )
        return (
            cleaned,
            "snapshot",
            snapshot.get("snapshot_date"),
            (
                datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
                if snapshot.get("captured_at")
                else None
            ),
        )
    return load_persisted_holdings(fetch_benchmark=False)


def _persist_settlement_holdings(
    holdings: list[Holding],
    *,
    fetched_at: datetime,
) -> tuple[list[Holding], dict | None]:
    if not holdings:
        return holdings, None

    total_assets = round(
        sum(
            (holding.settled_holding_amount or holding.holding_amount)
            + (holding.daily_profit or 0)
            for holding in holdings
        ),
        2,
    )
    daily_profit = sum_daily_profit(holdings)
    daily_return_percent = None
    previous_assets = total_assets - daily_profit
    if previous_assets > 0:
        daily_return_percent = round(daily_profit / previous_assets * 100, 2)

    patch = {
        "total_assets": total_assets,
        "daily_profit": daily_profit,
        "daily_return_percent": daily_return_percent,
        "holding_count": len(holdings),
        "updated_at": fetched_at,
    }
    summary = PortfolioSummary(**patch)
    summary = save_portfolio_summary(summary)
    save_daily_snapshot(holdings, summary)
    return holdings, summary.model_dump(mode="json")


def _serialize_settlement_holdings_for_client(holdings: list[Holding]) -> list[dict]:
    payloads: list[dict] = []
    for holding in holdings:
        payload = holding.model_dump()
        sector_return = (
            holding.sector_return_percent
            if holding.sector_return_percent_source in {"realtime", "closing_estimate"}
            else None
        )
        settled = holding.settled_holding_amount or holding.holding_amount
        payload["settled_holding_amount"] = settled
        payload["display_holding_amount"] = settled
        payload["holding_amount"] = settled
        payload["sector_return_percent"] = sector_return
        payload["sector_return_percent_source"] = (
            holding.sector_return_percent_source if sector_return is not None else None
        )
        payload["estimated_daily_return_percent"] = (
            holding.daily_return_percent
            if holding.daily_return_percent is not None
            else sector_return
        )
        payload["daily_return_is_estimated"] = holding.daily_return_percent_source != "official_nav"
        payload["estimated_holding_return_percent"] = (
            holding.holding_return_percent
            if holding.holding_return_percent is not None
            else holding.return_percent
        )
        payload["estimated_holding_profit"] = holding.holding_profit
        payload["holding_return_is_estimated"] = False
        payload["profit_accrual_deferred"] = (
            holding.daily_return_percent_source == "pending_accrual"
        )
        payloads.append(payload)
    return payloads


def settle_official_nav_for_portfolio() -> dict:
    session = build_trading_session()
    settlement_date = str(session.get("effective_trade_date") or "")
    if session.get("session_kind") in {"trading_day_intraday", "trading_day_pre_close"}:
        return _empty_response(
            reason="intraday_session",
            session=session,
            settlement_date=settlement_date,
        )

    holdings, _source, snapshot_date, _refreshed_at = _load_settlement_holdings()
    if not holdings:
        return _empty_response(
            reason="no_holdings",
            session=session,
            settlement_date=settlement_date,
            snapshot_date=snapshot_date,
        )

    prime_official_nav_cache(
        [holding.fund_code for holding in holdings if holding.fund_code],
        settlement_date,
    )
    settled, updated_count = settle_official_nav_for_holdings(
        holdings,
        settlement_date=settlement_date,
    )
    from app.services.holding_amount_sync import sync_holding_amounts_from_shares

    settled = sync_holding_amounts_from_shares(
        settled,
        persist_profiles=True,
        allow_nav_fetch=False,
        estimate_quotes={},
    )
    if updated_count == 0 and not any(
        holding.daily_return_percent_source == "official_nav" for holding in settled
    ):
        return _empty_response(
            reason="no_nav_available",
            session=session,
            settlement_date=settlement_date,
            snapshot_date=snapshot_date,
        )
    fetched_at = datetime.now(timezone.utc)
    persisted, portfolio_summary = _persist_settlement_holdings(
        settled,
        fetched_at=fetched_at,
    )
    return {
        "ok": True,
        "skipped": False,
        "reason": None,
        "session": session,
        "settlement_date": settlement_date,
        "updated_count": updated_count,
        "holdings": _serialize_settlement_holdings_for_client(persisted),
        "portfolio_summary": portfolio_summary,
        "source": SOURCE,
        "snapshot_date": snapshot_date,
        "refreshed_at": fetched_at.isoformat(),
    }
