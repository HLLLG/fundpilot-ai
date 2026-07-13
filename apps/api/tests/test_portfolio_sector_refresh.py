from __future__ import annotations

from unittest.mock import patch

from app.models import FundProfile, Holding, RefreshSectorQuotesRequest
from app.main import refresh_sector_quotes
from app.services.portfolio_holdings_service import apply_server_sector_cache_to_holdings
from app.services.portfolio_persistence import persist_holdings_after_sector_refresh


def test_apply_server_sector_cache_to_holdings_uses_cache_only_refresh() -> None:
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            holding_amount=1000,
            sector_name="半导体",
        )
    ]
    patched = holdings[0].model_copy(update={"sector_return_percent": 2.5})

    with patch(
        "app.services.portfolio_holdings_service.refresh_holdings_sector_quotes",
        return_value={
            "holdings": [patched.model_dump()],
            "summary": {"provider_path": "fresh_cache"},
        },
    ) as refresh_mock:
        with patch(
            "app.services.portfolio_holdings_service.enrich_holdings_estimates",
            side_effect=lambda items: items,
        ):
            result = apply_server_sector_cache_to_holdings(holdings)

    refresh_mock.assert_called_once_with(holdings, cache_only=True)
    assert result[0].sector_return_percent == 2.5


def test_fast_sector_refresh_persistence_skips_nav_and_estimate_network(monkeypatch) -> None:
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=1000,
        sector_name="半导体",
        sector_return_percent=1.2,
    )
    sync_calls: list[dict] = []

    def fake_sync(holdings, **kwargs):
        sync_calls.append(kwargs)
        return holdings

    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_most_recent_portfolio_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.confirm_and_compute_overrides",
        lambda _holdings: {},
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares",
        fake_sync,
    )
    monkeypatch.setattr("app.services.portfolio_persistence.get_portfolio_summary", lambda: None)
    monkeypatch.setattr("app.services.portfolio_persistence.save_portfolio_summary", lambda _summary: None)
    monkeypatch.setattr("app.services.portfolio_persistence.save_daily_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.portfolio_persistence.list_fund_profiles", lambda: [])
    monkeypatch.setattr("app.services.portfolio_persistence.persist_intraday_curve", lambda *_args, **_kwargs: None)

    persist_holdings_after_sector_refresh([holding], with_official_nav=False)

    assert sync_calls == [
        {
            "shares_override": {},
            "estimate_quotes": {},
            "allow_nav_fetch": False,
        }
    ]


def test_sector_refresh_bulk_loads_profiles_once(monkeypatch) -> None:
    holdings = [
        Holding(
            fund_code=f"{100000 + index:06d}",
            fund_name=f"样本基金 {index}",
            holding_amount=1000,
            sector_name="半导体",
            sector_return_percent=1.2,
        )
        for index in range(1, 6)
    ]
    profiles = [
        FundProfile(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            holding_amount=holding.holding_amount,
        )
        for holding in holdings
    ]
    calls = {"profiles": 0}
    persisted_profiles: list[dict[str, FundProfile]] = []

    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_most_recent_portfolio_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.confirm_and_compute_overrides",
        lambda _holdings: {},
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares",
        lambda items, **_kwargs: items,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_portfolio_summary",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.save_portfolio_summary",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.save_daily_snapshot",
        lambda *_args, **_kwargs: None,
    )

    def load_profiles() -> list[FundProfile]:
        calls["profiles"] += 1
        return profiles

    monkeypatch.setattr(
        "app.services.portfolio_persistence.list_fund_profiles",
        load_profiles,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.persist_intraday_curve",
        lambda _holdings, profile_map: persisted_profiles.append(profile_map),
    )

    persist_holdings_after_sector_refresh(holdings, with_official_nav=False)

    assert calls == {"profiles": 1}
    assert set(persisted_profiles[0]) == {holding.fund_code for holding in holdings}


def test_refresh_sector_quotes_updates_holdings_cache(monkeypatch) -> None:
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=1000,
        sector_name="半导体",
        sector_return_percent=1.2,
    )
    cached: list[dict] = []

    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: type("Settings", (), {"sector_quotes_enabled": True})(),
    )
    monkeypatch.setattr(
        "app.main.refresh_holdings_sector_quotes",
        lambda *_args, **_kwargs: {
            "ok": True,
            "holdings": [holding.model_dump(mode="json")],
            "fetched_at": "2026-06-29T03:49:26+00:00",
        },
    )
    monkeypatch.setattr(
        "app.main.persist_holdings_after_sector_refresh",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr("app.main.serialize_holdings_for_client", lambda holdings: [h.model_dump(mode="json") for h in holdings])
    monkeypatch.setattr("app.main.get_request_user_id", lambda: "test-user")
    monkeypatch.setattr("app.main.schedule_warm_holdings_intraday", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.main.save_cached_holdings_response", lambda payload: cached.append(payload))

    result = refresh_sector_quotes(
        RefreshSectorQuotesRequest(
            holdings=[holding],
            force_refresh=False,
            budget="fast",
        )
    )

    assert result["holdings"][0]["fund_code"] == "519674"
    assert cached == [{"holdings": result["holdings"]}]


def test_stale_sector_refresh_cannot_restore_deleted_snapshot_member(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.models import Holding, PortfolioSummary
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
    from app.services.portfolio_snapshot import save_daily_snapshot
    from app.services.portfolio_holdings_service import load_persisted_holdings
    monkeypatch.setattr("app.services.portfolio_persistence.persist_intraday_curve", lambda *_args, **_kwargs: None)

    deleted = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        holding_amount=100.0,
        return_percent=0,
    )
    kept = Holding(
        fund_code="022184",
        fund_name="富国全球科技互联网股票(QDII)C",
        holding_amount=1000.0,
        return_percent=0,
    )
    save_daily_snapshot([kept], PortfolioSummary(total_assets=1000.0, holding_count=1))

    enriched = persist_holdings_after_sector_refresh(
        [
            deleted.model_copy(update={"sector_return_percent": 1.0}),
            kept.model_copy(update={"sector_return_percent": 2.0}),
        ],
        with_official_nav=False,
    )

    assert [item.fund_code for item in enriched] == ["022184"]
    loaded, source, _, _ = load_persisted_holdings(fetch_benchmark=False)
    assert [item.fund_code for item in loaded] == ["022184"]
    assert source == "snapshot"


def test_stale_sector_refresh_cannot_restore_after_empty_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.models import Holding, PortfolioSummary
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
    from app.services.portfolio_snapshot import save_daily_snapshot
    from app.services.portfolio_holdings_service import load_persisted_holdings
    monkeypatch.setattr("app.services.portfolio_persistence.persist_intraday_curve", lambda *_args, **_kwargs: None)

    stale = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        holding_amount=100.0,
        return_percent=0,
    )
    save_daily_snapshot([], PortfolioSummary(total_assets=0.0, holding_count=0))

    enriched = persist_holdings_after_sector_refresh(
        [stale.model_copy(update={"sector_return_percent": 1.0})],
        with_official_nav=False,
    )

    assert enriched == []
    loaded, source, _, _ = load_persisted_holdings(fetch_benchmark=False)
    assert loaded == []
    assert source == "snapshot"


def test_stale_sector_refresh_cannot_resurrect_fund_deleted_mid_flight(tmp_path, monkeypatch) -> None:
    """板块刷新耗时期间用户删除了基金：写回快照前必须再对账一次，不能把它复活。

    与 ``test_stale_sector_refresh_cannot_restore_deleted_snapshot_member`` 的区别：
    那个测试模拟"进入刷新前快照已经变了"；这里模拟更隐蔽的竞态——快照在刷新
    *进行中*（份额同步这一步耗时网络调用期间）才被删除，此时函数手里已经拿着
    基于旧快照算出来的 merged 列表，如果不在写回前再看一眼最新快照，就会把
    刚删除的基金重新写回去。
    """
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.models import Holding, PortfolioSummary
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
    from app.services.portfolio_holdings_service import (
        load_persisted_holdings,
        remove_holding_from_portfolio,
    )
    from app.services.portfolio_snapshot import save_daily_snapshot

    monkeypatch.setattr(
        "app.services.portfolio_persistence.persist_intraday_curve", lambda *_args, **_kwargs: None
    )

    deleted = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        holding_amount=100.0,
        return_percent=0,
    )
    kept = Holding(
        fund_code="022184",
        fund_name="富国全球科技互联网股票(QDII)C",
        holding_amount=1000.0,
        return_percent=0,
    )
    save_daily_snapshot([deleted, kept], PortfolioSummary(total_assets=1100.0, holding_count=2))

    from app.services import portfolio_persistence

    original_sync = portfolio_persistence.sync_holding_amounts_from_shares

    def fake_sync(holdings, **kwargs):
        # 模拟刷新处理期间（网络耗时）用户在另一个请求里把基金删掉了。
        remove_holding_from_portfolio("016665", fund_name="天弘全球高端制造混合(QDII)C")
        return original_sync(holdings, **kwargs)

    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares", fake_sync
    )

    enriched = persist_holdings_after_sector_refresh(
        [
            deleted.model_copy(update={"sector_return_percent": 1.0}),
            kept.model_copy(update={"sector_return_percent": 2.0}),
        ],
        with_official_nav=False,
    )

    assert [item.fund_code for item in enriched] == ["022184"]
    loaded, source, _, _ = load_persisted_holdings(fetch_benchmark=False)
    assert [item.fund_code for item in loaded] == ["022184"]
    assert source == "snapshot"


def test_apply_server_sector_cache_falls_back_to_network_on_cache_miss() -> None:
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            holding_amount=1000,
            sector_name="半导体",
        )
    ]
    patched = holdings[0].model_copy(update={"sector_return_percent": 1.8})
    cache_miss = {
        "holdings": [holdings[0].model_dump()],
        "message": "板块缓存未命中，后台将刷新",
        "summary": {"provider_path": "cache_miss"},
    }
    live = {"holdings": [patched.model_dump()], "summary": {"provider_path": "eastmoney"}}

    with patch(
        "app.services.portfolio_holdings_service.refresh_holdings_sector_quotes",
        side_effect=[cache_miss, live],
    ) as refresh_mock:
        with patch(
            "app.services.portfolio_holdings_service._intraday_sector_window",
            return_value=True,
        ):
            with patch(
                "app.services.portfolio_holdings_service.enrich_holdings_estimates",
                side_effect=lambda items: items,
            ):
                result = apply_server_sector_cache_to_holdings(holdings)

    assert refresh_mock.call_count == 2
    refresh_mock.assert_any_call(holdings, cache_only=True)
    refresh_mock.assert_any_call(
        holdings,
        cache_only=False,
        timeout_seconds=8.0,
        force_refresh=False,
    )
    assert result[0].sector_return_percent == 1.8


def test_apply_server_sector_cache_skips_network_when_disabled() -> None:
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            holding_amount=1000,
            sector_name="半导体",
        )
    ]
    cache_miss = {
        "holdings": [holdings[0].model_dump()],
        "message": "板块缓存未命中，后台将刷新",
        "summary": {"provider_path": "cache_miss"},
    }

    with patch(
        "app.services.portfolio_holdings_service.refresh_holdings_sector_quotes",
        return_value=cache_miss,
    ) as refresh_mock:
        with patch(
            "app.services.portfolio_holdings_service._intraday_sector_window",
            return_value=True,
        ):
            with patch(
                "app.services.portfolio_holdings_service.enrich_holdings_estimates",
                side_effect=lambda items: items,
            ):
                apply_server_sector_cache_to_holdings(holdings, network_fallback=False)

    refresh_mock.assert_called_once_with(holdings, cache_only=True)


def test_refresh_all_portfolio_sectors_iterates_users() -> None:
    from app.services.portfolio_sector_refresh import refresh_all_portfolio_sectors

    with patch(
        "app.services.portfolio_sector_refresh.list_distinct_portfolio_user_ids",
        return_value=[1, 2],
    ):
        with patch(
            "app.services.portfolio_sector_refresh.refresh_shared_spot_boards",
        ) as spot_mock:
            with patch(
                "app.services.portfolio_sector_refresh.refresh_portfolio_sectors_for_user",
            ) as user_mock:
                refresh_all_portfolio_sectors()

    spot_mock.assert_called_once_with(force_refresh=True)
    assert user_mock.call_count == 2
    user_mock.assert_any_call(1)
    user_mock.assert_any_call(2)
