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


def test_merge_persists_holdings_estimate_without_sector(monkeypatch) -> None:
    """无主题灵活配置没有板块涨跌，重仓加权当日收益仍须写回快照。"""

    base = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=2227.19,
        settled_holding_amount=2227.19,
        return_percent=-25.76,
    )
    patch = base.model_copy(
        update={
            "daily_profit": -50.21,
            "daily_return_percent": -2.25,
            "daily_return_percent_source": "holdings_estimate",
        }
    )
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(base),
    )

    merged = portfolio_persistence.merge_holdings_with_snapshot([patch])

    assert merged[0].daily_return_percent_source == "holdings_estimate"
    assert merged[0].daily_return_percent == -2.25
    assert merged[0].daily_profit == -50.21
    assert merged[0].sector_return_percent is None


def test_merge_keeps_holdings_estimate_when_refresh_misses(monkeypatch) -> None:
    existing = Holding(
        fund_code="017787",
        fund_name="万家宏观择时多策略混合C",
        holding_amount=12287.02,
        settled_holding_amount=12287.02,
        return_percent=3.05,
        daily_profit=-93.25,
        daily_return_percent=-0.76,
        daily_return_percent_source="holdings_estimate",
    )
    missed = existing.model_copy(
        update={
            "daily_profit": None,
            "daily_return_percent": None,
            "daily_return_percent_source": None,
        }
    )
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(existing),
    )

    merged = portfolio_persistence.merge_holdings_with_snapshot([missed])

    assert merged[0].daily_return_percent_source == "holdings_estimate"
    assert merged[0].daily_return_percent == -0.76
    assert merged[0].daily_profit == -93.25


def test_persist_keeps_holdings_estimate_after_enrich(monkeypatch) -> None:
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=2227.19,
        settled_holding_amount=2227.19,
        return_percent=-25.76,
    )
    estimated = holding.model_copy(
        update={
            "daily_profit": -50.21,
            "daily_return_percent": -2.25,
            "daily_return_percent_source": "holdings_estimate",
        }
    )
    captured: dict = {}
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(holding),
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.promote_pending_transactions_into_holdings",
        lambda rows: (list(rows), {}),
    )
    monkeypatch.setattr(
        portfolio_persistence,
        "sync_holding_amounts_from_shares",
        lambda rows, **_kwargs: list(rows),
    )
    monkeypatch.setattr(portfolio_persistence, "enrich_holdings_estimates", lambda rows: list(rows))
    monkeypatch.setattr(
        portfolio_persistence,
        "overlay_holdings_daily_estimates",
        lambda rows, **_kwargs: [estimated],
    )
    monkeypatch.setattr(portfolio_persistence, "list_fund_profiles", lambda: [])
    monkeypatch.setattr(
        portfolio_persistence,
        "match_profiles_to_holdings",
        lambda rows, _profiles: [None] * len(rows),
    )
    monkeypatch.setattr(portfolio_persistence, "get_portfolio_summary", lambda: None)
    monkeypatch.setattr(portfolio_persistence, "save_portfolio_summary", lambda summary: None)
    monkeypatch.setattr(
        portfolio_persistence,
        "save_daily_snapshot",
        lambda rows, _summary: captured.setdefault("holdings", list(rows)),
    )
    monkeypatch.setattr(portfolio_persistence, "persist_intraday_curve", lambda *_args, **_kwargs: None)

    result = portfolio_persistence._persist_holdings_after_sector_refresh_unlocked(
        [holding],
        with_official_nav=False,
    )

    assert result[0].daily_return_percent_source == "holdings_estimate"
    assert captured["holdings"][0].daily_return_percent_source == "holdings_estimate"


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
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        portfolio_holdings_service,
        "promote_pending_transactions_into_holdings",
        lambda holdings: (holdings, {}),
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


def test_drop_keeps_newly_confirmed_transaction_positions(monkeypatch) -> None:
    retained = _holding("010236", "广发电子信息传媒股票C")
    purchased = _holding("021959", "南方黄金ETF联接C")
    monkeypatch.setattr(
        portfolio_persistence,
        "get_most_recent_portfolio_snapshot",
        lambda: _snapshot(retained),
    )

    kept = portfolio_persistence._drop_holdings_removed_during_refresh(
        [retained, purchased],
        extra_allowed_codes={"021959"},
    )

    assert [item.fund_code for item in kept] == ["010236", "021959"]


def test_settle_persists_promoted_in_progress_even_without_official_nav(monkeypatch) -> None:
    from app.services import official_nav_settlement as settlement

    existing = _holding("000001", "已有基金")
    promoted = _holding("021959", "南方黄金股C", amount=500)
    captured: dict = {}

    monkeypatch.setattr(
        settlement,
        "build_trading_session",
        lambda: {
            "session_kind": "trading_day_after_close",
            "effective_trade_date": "2026-08-14",
        },
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.confirm_pending_transactions",
        lambda: 1,
    )
    monkeypatch.setattr(
        settlement,
        "_load_settlement_holdings",
        lambda: ([existing], "snapshot", "2026-08-14", None),
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.absorb_confirmed_transaction_positions",
        lambda holdings: list(holdings) + [promoted],
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.compute_effective_shares_map",
        lambda _codes, **_kwargs: {},
    )
    monkeypatch.setattr(settlement, "prime_official_nav_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        settlement,
        "settle_official_nav_for_holdings",
        lambda holdings, **_kwargs: (list(holdings), 0),
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.sync_holding_amounts_from_shares",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        settlement,
        "_persist_settlement_holdings",
        lambda holdings, **_kwargs: (
            captured.update({"codes": [item.fund_code for item in holdings]})
            or (holdings, {"total_assets": 1500})
        ),
    )

    result = settlement.settle_official_nav_for_portfolio()

    assert result["skipped"] is False
    assert captured["codes"] == ["000001", "021959"]
