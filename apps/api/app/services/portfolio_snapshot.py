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
        "trend_context": build_portfolio_trend_context(history_rows),
    }


def build_portfolio_trend_context(
  history_rows: list[dict] | None = None,
  *,
  lookback_days: int = 7,
) -> dict:
    rows = history_rows if history_rows is not None else list_portfolio_daily_snapshots(
        limit=lookback_days
    )
    if len(rows) < 2:
        return {
            "has_history": False,
            "lookback_days": lookback_days,
            "message": "历史快照不足，无法计算近一周组合走势。",
        }

    latest = rows[0]
    oldest = rows[min(len(rows) - 1, lookback_days - 1)]
    latest_assets = latest.get("total_assets")
    oldest_assets = oldest.get("total_assets")
    assets_delta_percent = None
    if latest_assets and oldest_assets and oldest_assets > 0:
        assets_delta_percent = round(
            (float(latest_assets) - float(oldest_assets)) / float(oldest_assets) * 100,
            2,
        )

    daily_returns = [
        row["daily_return_percent"]
        for row in rows[:lookback_days]
        if row.get("daily_return_percent") is not None
    ]
    cumulative_return_percent = (
        round(sum(float(value) for value in daily_returns), 2) if daily_returns else None
    )

    summary_line = _format_trend_summary(
        span_days=min(len(rows), lookback_days),
        assets_delta_percent=assets_delta_percent,
        cumulative_return_percent=cumulative_return_percent,
        latest_date=str(latest.get("snapshot_date") or ""),
    )

    return {
        "has_history": True,
        "lookback_days": lookback_days,
        "snapshot_count": len(rows),
        "latest_snapshot_date": latest.get("snapshot_date"),
        "oldest_snapshot_date_in_window": oldest.get("snapshot_date"),
        "assets_delta_percent": assets_delta_percent,
        "cumulative_daily_return_percent": cumulative_return_percent,
        "summary_line": summary_line,
    }


def _format_trend_summary(
    *,
    span_days: int,
    assets_delta_percent: float | None,
    cumulative_return_percent: float | None,
    latest_date: str,
) -> str:
    parts: list[str] = [f"近 {span_days} 个交易日（至 {latest_date}）"]
    if assets_delta_percent is not None:
        parts.append(f"组合资产变化约 {assets_delta_percent:+.2f}%")
    if cumulative_return_percent is not None:
        parts.append(f"累计当日收益率合计约 {cumulative_return_percent:+.2f}%（为日度相加近似）")
    return "，".join(parts) + "。"


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
