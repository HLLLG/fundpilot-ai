from __future__ import annotations

from datetime import date, datetime, timezone

from app.database import (
    get_most_recent_portfolio_snapshot,
    list_portfolio_daily_snapshots,
    save_portfolio_daily_snapshot,
)
from app.models import Holding, PortfolioDailySnapshot, PortfolioSummary


def snapshot_date_key(when: datetime | None = None) -> str:
    moment = when or datetime.now(timezone.utc)
    return moment.date().isoformat()


def save_daily_snapshot(
    holdings: list[Holding],
    summary: PortfolioSummary | None,
) -> PortfolioDailySnapshot:
    total_from_holdings = sum(holding.holding_amount for holding in holdings)
    payload_holdings = [holding.model_dump() for holding in holdings]
    snapshot = PortfolioDailySnapshot(
        snapshot_date=snapshot_date_key(summary.updated_at if summary else None),
        total_assets=summary.total_assets if summary and summary.total_assets else total_from_holdings,
        daily_profit=summary.daily_profit if summary else None,
        daily_return_percent=summary.daily_return_percent if summary else None,
        holdings=payload_holdings,
        captured_at=datetime.now(timezone.utc),
    )
    save_portfolio_daily_snapshot(snapshot)
    return snapshot


def get_previous_holdings_for_review() -> list[Holding]:
    previous = get_most_recent_portfolio_snapshot()
    if previous is None:
        return []
    return [Holding.model_validate(item) for item in previous.get("holdings", [])]


def build_dashboard_payload(
    *,
    summary: PortfolioSummary | None,
    profiles: list,
) -> dict:
    history_rows = list_portfolio_daily_snapshots(limit=30)
    history = [
        {
            "date": row["snapshot_date"],
            "total_assets": row.get("total_assets"),
            "daily_profit": row.get("daily_profit"),
            "daily_return_percent": row.get("daily_return_percent"),
        }
        for row in reversed(history_rows)
    ]

    latest = history_rows[0] if history_rows else None
    allocation_source = latest.get("holdings", []) if latest else []
    total_assets = (
        (summary.total_assets if summary and summary.total_assets else None)
        or (latest.get("total_assets") if latest else None)
        or sum(
            profile.holding_amount or 0
            for profile in profiles
            if getattr(profile, "holding_amount", None)
        )
        or 0
    )

    allocation = _build_allocation(allocation_source, profiles, total_assets)

    return {
        "summary": summary.model_dump(mode="json") if summary else {},
        "history": history,
        "allocation": allocation,
        "snapshot_count": len(history_rows),
        "latest_snapshot_date": latest["snapshot_date"] if latest else None,
    }


def _build_allocation(
    holdings_payload: list,
    profiles: list,
    total_assets: float,
) -> list[dict]:
    if holdings_payload:
        items = holdings_payload
    else:
        items = [
            {
                "fund_code": profile.fund_code,
                "fund_name": profile.fund_name,
                "holding_amount": profile.holding_amount or 0,
                "daily_profit": profile.daily_profit,
                "holding_return_percent": profile.holding_return_percent,
            }
            for profile in profiles
            if (profile.holding_amount or 0) > 0
        ]

    rows: list[dict] = []
    for item in items:
        amount = float(item.get("holding_amount") or 0)
        if amount <= 0:
            continue
        weight = round(amount / total_assets * 100, 2) if total_assets else 0
        rows.append(
            {
                "fund_code": item.get("fund_code"),
                "fund_name": item.get("fund_name"),
                "holding_amount": amount,
                "weight_percent": weight,
                "daily_profit": item.get("daily_profit"),
                "holding_return_percent": item.get("holding_return_percent")
                or item.get("return_percent"),
            }
        )
    rows.sort(key=lambda row: row["holding_amount"], reverse=True)
    return rows
