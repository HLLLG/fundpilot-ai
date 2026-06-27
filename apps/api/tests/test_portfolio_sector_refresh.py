from __future__ import annotations

from unittest.mock import patch

from app.models import Holding
from app.services.portfolio_holdings_service import apply_server_sector_cache_to_holdings


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
