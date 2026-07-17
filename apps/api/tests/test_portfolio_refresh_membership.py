from __future__ import annotations

from app.models import FundProfile, Holding
from app.services import portfolio_holdings_service, portfolio_persistence
from app.services.portfolio_holdings_service import merge_authoritative_holding_upserts


def _holding(
    code: str,
    name: str,
    *,
    amount: float = 1_000,
    daily_return: float | None = None,
) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=name,
        holding_amount=amount,
        settled_holding_amount=amount,
        return_percent=0,
        daily_return_percent=daily_return,
        daily_return_percent_source=("official_nav" if daily_return is not None else None),
    )


def _snapshot(*holdings: Holding) -> dict:
    return {
        "snapshot_date": "2026-07-17",
        "holdings": [holding.model_dump(mode="json") for holding in holdings],
    }


def test_stale_refresh_cannot_delete_a_newer_holding(monkeypatch) -> None:
    first = _holding("010236", "广发电子信息传媒股票C")
    newly_added = _holding("015945", "易方达国防军工混合C")
    stale_refresh = first.model_copy(
        update={
            "daily_return_percent": 1.25,
            "daily_return_percent_source": "official_nav",
        }
    )
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(first, newly_added),
    )

    merged = portfolio_persistence.merge_holdings_with_snapshot([stale_refresh])

    assert [item.fund_code for item in merged] == ["010236", "015945"]
    assert merged[0].daily_return_percent == 1.25


def test_stale_refresh_cannot_revive_a_deleted_holding(monkeypatch) -> None:
    retained = _holding("010236", "广发电子信息传媒股票C")
    deleted = _holding("015945", "易方达国防军工混合C")
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(retained),
    )

    merged = portfolio_persistence.merge_holdings_with_snapshot([retained, deleted])

    assert [item.fund_code for item in merged] == ["010236"]


def test_authoritative_transaction_sync_can_add_membership(monkeypatch) -> None:
    retained = _holding("010236", "广发电子信息传媒股票C")
    purchased = _holding("015945", "易方达国防军工混合C")
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(retained),
    )

    merged = portfolio_persistence.merge_holdings_with_snapshot(
        [retained, purchased],
        allow_membership_additions=True,
    )

    assert [item.fund_code for item in merged] == ["010236", "015945"]


def test_explicit_upsert_keeps_server_rows_missing_from_client() -> None:
    retained = _holding("010236", "广发电子信息传媒股票C")
    concurrent = _holding("015945", "易方达国防军工混合C")
    update = retained.model_copy(update={"holding_amount": 1_500, "settled_holding_amount": 1_500})

    merged = merge_authoritative_holding_upserts([retained, concurrent], [update])

    assert [item.fund_code for item in merged] == ["010236", "015945"]
    assert merged[0].holding_amount == 1_500


def test_transaction_profile_can_join_nonempty_snapshot_without_reviving_legacy_profile(
    monkeypatch,
) -> None:
    retained = _holding("010236", "广发电子信息传媒股票C")
    profiles = [
        FundProfile(
            fund_code="010236",
            fund_name=retained.fund_name,
            holding_amount=retained.holding_amount,
            source="manual",
        ),
        FundProfile(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=800,
            source="alipay-transaction",
        ),
        FundProfile(
            fund_code="999999",
            fund_name="已移除的历史档案",
            holding_amount=600,
            source="manual",
        ),
    ]
    captured: dict = {}

    monkeypatch.setattr(
        portfolio_holdings_service,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(retained),
    )
    monkeypatch.setattr(portfolio_holdings_service, "list_fund_profiles", lambda: profiles)
    monkeypatch.setattr(
        portfolio_holdings_service,
        "enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        portfolio_holdings_service,
        "confirm_and_compute_overrides",
        lambda holdings: {},
    )
    monkeypatch.setattr(
        portfolio_holdings_service,
        "sync_holding_amounts_from_shares",
        lambda holdings, **_kwargs: holdings,
    )

    def persist(holdings, **kwargs):
        captured.update(kwargs)
        return holdings

    monkeypatch.setattr(
        portfolio_holdings_service,
        "persist_holdings_after_sector_refresh",
        persist,
    )

    merged = portfolio_holdings_service.sync_portfolio_from_profiles(
        refresh_sectors=False,
    )

    assert [item.fund_code for item in merged] == ["010236", "015945"]
    assert captured["allow_membership_additions"] is True
