from datetime import datetime, timezone

from app.config import refresh_settings
from app.database import delete_portfolio_snapshots_on_or_before, list_portfolio_daily_snapshots
from app.models import Holding, PortfolioSummary
from app.services.portfolio_snapshot import (
    build_dashboard_payload,
    save_daily_snapshot,
    snapshot_date_key,
)


def test_snapshot_roundtrip_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_profit_trend",
        lambda **_kwargs: {"kind": "intraday", "points": []},
    )

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能",
            holding_amount=7250.12,
            return_percent=-3.47,
            daily_profit=-176.88,
            daily_return_percent=-2.38,
        )
    ]
    summary = PortfolioSummary(
        total_assets=28090.36,
        daily_profit=-482.0,
        daily_return_percent=-1.72,
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    snapshot = save_daily_snapshot(holdings, summary)

    rows = list_portfolio_daily_snapshots()
    matching = [row for row in rows if row["snapshot_date"] == snapshot.snapshot_date]
    assert len(matching) == 1
    assert matching[0]["total_assets"] == 28090.36

    monkeypatch.setattr(
        "app.services.portfolio_snapshot.list_portfolio_daily_snapshots",
        lambda **kwargs: matching,
    )

    payload = build_dashboard_payload(summary=summary, profiles=[])
    assert len(payload["history"]) == 1
    assert payload["history"][0]["total_assets"] == 28090.36
    assert len(payload["allocation"]) == 1
    assert payload["allocation"][0]["weight_percent"] > 0


def test_delete_snapshots_on_or_before(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    from app.database import save_portfolio_daily_snapshot
    from app.models import PortfolioDailySnapshot

    for day, profit in [("2026-06-08", 10), ("2026-06-09", 20), ("2026-06-10", -5)]:
        save_portfolio_daily_snapshot(
            PortfolioDailySnapshot(
                snapshot_date=day,
                total_assets=1000,
                daily_profit=profit,
                holdings=[],
            )
        )

    purge = delete_portfolio_snapshots_on_or_before("2026-06-09")
    assert purge["daily_snapshots_deleted"] == 2

    remaining = list_portfolio_daily_snapshots(limit=10)
    assert len(remaining) == 1
    assert remaining[0]["snapshot_date"] == "2026-06-10"


def test_snapshot_date_key_uses_utc_date():
    moment = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    assert snapshot_date_key(moment) == "2026-06-01"
