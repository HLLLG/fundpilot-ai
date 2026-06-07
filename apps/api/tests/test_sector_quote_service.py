import pytest

from app.models import Holding
from app.services.sector_quote_provider import SpotBoardFetchResult
from app.services.sector_quote_service import refresh_holdings_sector_quotes


@pytest.fixture(autouse=True)
def disable_live_kline_quotes(monkeypatch):
    """单测不访问东财 K 线或官方净值；需相关行为的用例自行覆盖 mock。"""
    monkeypatch.setattr(
        "app.services.sector_canonical.fetch_eastmoney_kline_close_percent",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda fund_code, trade_date: None,
    )


def test_refresh_sector_quotes_updates_matched(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards_result",
        lambda **kwargs: SpotBoardFetchResult(
            boards={
                "index": {},
                "concept": {"半导体": 4.57},
                "industry": {},
            },
            provider_path="eastmoney_live",
        ),
    )
    monkeypatch.setattr("app.services.sector_quote_service.get_sector_mapping", lambda _key: None)
    monkeypatch.setattr("app.services.sector_quote_service.save_sector_mapping", lambda _record: None)

    holdings = [
        Holding(
            fund_code="015608",
            fund_name="测试基金",
            holding_amount=1000,
            return_percent=1,
            sector_name="半导体",
            sector_return_percent=1.0,
        )
    ]

    result = refresh_holdings_sector_quotes(holdings)

    assert result["ok"] is True
    assert result["summary"]["matched"] == 1
    assert result["summary"]["estimate_fallback"] == 0
    assert result["holdings"][0]["sector_return_percent"] == 4.57
    assert result["items"][0]["sector_quote_meta"]["source"] == "live"


def test_refresh_sector_quotes_auto_maps_csi_grid_equipment(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards_result",
        lambda **kwargs: SpotBoardFetchResult(
            boards={
                "index": {"中证电网设备": 1.59, "电力设备主题": 1.5, "中证全指电网": 0.97},
                "concept": {"电网设备": 1.1, "电网设备ETF": 1.2},
                "industry": {"电网设备": 0.9},
            },
            provider_path="eastmoney_live",
        ),
    )
    monkeypatch.setattr("app.services.sector_quote_service.get_sector_mapping", lambda _key: None)
    monkeypatch.setattr("app.services.sector_quote_service.save_sector_mapping", lambda _record: None)

    holdings = [
        Holding(
            fund_code="015608",
            fund_name="测试基金",
            holding_amount=1000,
            return_percent=1,
            sector_name="电网设备",
            intraday_index_name="中证电网设备",
            sector_return_percent=0.5,
        )
    ]

    result = refresh_holdings_sector_quotes(holdings)

    assert result["summary"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 1.59


def test_refresh_sector_quotes_reports_stale_cache_provider(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="015608",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=0,
        sector_name="半导体",
        sector_return_percent=0.1,
    )

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={"concept": {"半导体": 1.23}, "industry": {}, "index": {}},
            provider_path="stale_cache",
            from_stale_cache=True,
            live_attempted=True,
            elapsed_seconds=0.02,
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(service, "fetch_fund_estimate_quotes", lambda *_args, **_kwargs: {})

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["ok"] is True
    assert result["provider_path"] == "stale_cache"
    assert result["from_stale_cache"] is True
    assert result["summary"]["provider_path"] == "stale_cache"
    assert result["summary"]["estimate_fallback"] == 0


def test_refresh_sector_quotes_skips_on_demand_when_timeout_budget_is_set(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=1188.96,
        return_percent=-7.43,
        sector_name="商业航天",
        sector_return_percent=2.29,
    )

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={"concept": {"半导体": 1.23}, "industry": {}, "index": {}},
            provider_path="stale_cache",
            from_stale_cache=True,
            live_attempted=True,
            elapsed_seconds=0.2,
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)

    on_demand_called = {"value": False}

    def fake_on_demand(*_args, **_kwargs):
        on_demand_called["value"] = True
        return None

    monkeypatch.setattr(service, "fetch_sector_on_demand", fake_on_demand)
    monkeypatch.setattr(service, "fetch_fund_estimate_quotes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(service, "prefetch_canonical_kline_quotes", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(service.FundProfileService, "resolve_holding", lambda self, holding: holding)
    monkeypatch.setattr(
        "app.services.sector_quote_resolver.fetch_canonical_sector_quote",
        lambda *_args, **_kwargs: None,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["ok"] is True
    assert result["summary"]["unresolved"] == 1
    assert result["provider_path"] == "stale_cache"
    assert result["summary"]["estimate_fallback"] == 0
    assert on_demand_called["value"] is False


def test_refresh_sector_quotes_falls_back_to_fund_estimate_when_boards_unavailable(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8270.43,
        return_percent=2.77,
        sector_name="人工智能",
        sector_return_percent=-2.52,
    )

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={"concept": {}, "industry": {}, "index": {}},
            provider_path="empty",
            live_attempted=True,
            elapsed_seconds=0.2,
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(
        service,
        "fetch_fund_estimate_quotes",
        lambda _holdings, **_: {
            "008586": {"change_percent": 3.27, "provider": "tiantian-fund-estimate"},
        },
        raising=False,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["ok"] is True
    assert result["summary"]["matched"] == 1
    assert result["summary"]["unresolved"] == 0
    assert result["summary"]["estimate_fallback"] == 1
    assert result["provider_path"] == "fund_estimate_live"
    assert result["holdings"][0]["sector_return_percent"] == 3.27
    assert result["items"][0]["sector_quote_meta"]["source"] == "live"
    assert result["items"][0]["sector_quote_meta"]["provider"] == "tiantian-fund-estimate"


def test_refresh_sector_quotes_prefers_real_boards_before_fund_estimate(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8270.43,
        return_percent=2.77,
        sector_name="人工智能",
        sector_return_percent=-2.52,
    )

    board_fetch_called = {"value": False}

    def fake_fetch_boards(**_kwargs):
        board_fetch_called["value"] = True
        return SpotBoardFetchResult(
            boards={"concept": {}, "industry": {}, "index": {"人工智能": 1.68}},
            provider_path="eastmoney_live",
            live_attempted=True,
            elapsed_seconds=0.2,
        )

    monkeypatch.setattr(service, "fetch_spot_boards_result", fake_fetch_boards)
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(service.FundProfileService, "resolve_holding", lambda self, holding: holding)
    monkeypatch.setattr(
        "app.services.sector_quote_resolver.fetch_canonical_sector_quote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "fetch_fund_estimate_quotes",
        lambda _holdings, **_: {
            "008586": {"change_percent": 3.27, "provider": "tiantian-fund-estimate"},
        },
        raising=False,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["summary"]["matched"] == 1
    assert result["summary"]["estimate_fallback"] == 0
    assert result["provider_path"] == "eastmoney_live"
    assert result["holdings"][0]["sector_return_percent"] == 1.68
    assert board_fetch_called["value"] is True
    assert result["items"][0]["sector_quote_meta"]["provider"] == "eastmoney-akshare"
    assert result["message"] == "已刷新 1 只，0 只需选择映射，0 只未匹配"


def test_refresh_sector_quotes_uses_secid_before_fund_estimate(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=1188.96,
        return_percent=-7.43,
        sector_name="商业航天",
        sector_return_percent=2.29,
    )

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={"concept": {}, "industry": {}, "index": {}},
            provider_path="empty",
            live_attempted=True,
            elapsed_seconds=0.2,
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(service, "prefetch_canonical_kline_quotes", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        service,
        "fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fetch estimates")),
    )

    def fake_resolve(sector_name, boards, *, persisted_mapping=None, quote_label=None):
        if boards.get("concept", {}).get("商业航天") == 2.35:
            from app.services.sector_quote_resolver import SectorResolveResult

            return SectorResolveResult(
                confidence="high",
                change_percent=2.35,
                matched_name="商业航天",
                source_type="concept",
                source_code="BK0963",
                message="东财K线收盘 90.BK0963",
            )
        from app.services.sector_quote_resolver import SectorResolveResult

        return SectorResolveResult(confidence="none", message="miss")

    def fake_prefetch(labels, boards, *, timeout_seconds=None):
        boards.setdefault("concept", {})["商业航天"] = 2.35
        return 1

    monkeypatch.setattr(service, "prefetch_canonical_kline_quotes", fake_prefetch)
    monkeypatch.setattr(service, "resolve_sector_quote", fake_resolve)

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=8.0)

    assert result["summary"]["matched"] == 1
    assert result["summary"]["estimate_fallback"] == 0
    assert result["summary"]["secid_matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 2.35


def test_refresh_sector_quotes_skips_spot_boards_when_all_canonical(monkeypatch):
    from app.services import sector_quote_service as service

    board_fetch_called = {"value": False}

    def fail_board_fetch(**_kwargs):
        board_fetch_called["value"] = True
        raise AssertionError("should not fetch full spot boards")

    monkeypatch.setattr(service, "fetch_spot_boards_result", fail_board_fetch)
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)

    def fake_kline_prefetch(_labels, boards, **_kwargs):
        boards.setdefault("index", {})["中证电网设备"] = 0.1549
        return 1

    monkeypatch.setattr(service, "prefetch_canonical_kline_quotes", fake_kline_prefetch)
    monkeypatch.setattr(
        "app.services.sector_canonical.fetch_eastmoney_kline_close_percent",
        lambda *_args, **_kwargs: 0.1549,
    )
    monkeypatch.setattr(
        service,
        "fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no estimate")),
    )

    holding = Holding(
        fund_code="025856",
        fund_name="华夏电网联接",
        holding_amount=9000,
        return_percent=4,
        sector_name="电网设备",
        intraday_index_name="中证电网设备",
        sector_return_percent=0.1,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=8.0)

    assert board_fetch_called["value"] is False
    assert result["summary"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 0.1549
    assert result["provider_path"] == "eastmoney_kline"
    assert result["items"][0]["sector_quote_meta"]["provider"] == "eastmoney-kline"


def test_refresh_sector_quotes_uses_estimate_for_unmatched_non_canonical(monkeypatch):
    from app.services import sector_quote_service as service

    holding = Holding(
        fund_code="008586",
        fund_name="某冷门主题基金",
        holding_amount=8270.43,
        return_percent=2.77,
        sector_name="稀有金属",
        sector_return_percent=-2.52,
    )
    monkeypatch.setattr(service, "prefetch_canonical_kline_quotes", lambda *_args, **_kwargs: 0)
    dense_unrelated_boards = {
        "concept": {f"无关板块{index}": 0.1 for index in range(8)},
        "industry": {},
        "index": {},
    }

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards=dense_unrelated_boards,
            provider_path="eastmoney_live",
            live_attempted=True,
            elapsed_seconds=0.2,
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(
        service,
        "fetch_fund_estimate_quotes",
        lambda _holdings, **_: {
            "008586": {"change_percent": 3.27, "provider": "tiantian-fund-estimate"},
        },
        raising=False,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["summary"]["matched"] == 1
    assert result["summary"]["estimate_fallback"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 3.27
    assert result["items"][0]["sector_quote_meta"]["provider"] == "tiantian-fund-estimate"


def test_official_nav_applies_to_daily_not_sector(monkeypatch):
    """Official NAV updates daily return only; sector_return_percent stays on East Money board quote."""
    from app.services import sector_quote_service as service
    from app.services.sector_quote_service import refresh_holdings_sector_quotes
    from app.models import Holding

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={
                "index": {},
                "concept": {"商业航天": 1.36},
                "industry": {},
            },
            provider_path="eastmoney_live",
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(service, "get_official_nav_return", lambda fund_code, trade_date: -2.45)

    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=10000,
            sector_name="商业航天",
            sector_return_percent=1.36,
        )
    ]

    result = refresh_holdings_sector_quotes(holdings, force_refresh=True)
    assert result["ok"] is True
    updated = [Holding.model_validate(h) for h in result["holdings"]]
    fund = next(h for h in updated if h.fund_code == "015945")
    assert fund.sector_return_percent == pytest.approx(1.36)
    assert fund.sector_return_percent_source == "closing_estimate"
    assert fund.daily_return_percent == pytest.approx(-2.45)
    assert fund.daily_return_percent_source == "official_nav"
    assert fund.daily_profit == pytest.approx(-251.15)


def test_closing_estimate_source_when_nav_not_published(monkeypatch):
    """When official NAV is not yet published and market is closed, source = closing_estimate."""
    from app.services import sector_quote_service as service
    from app.services.sector_quote_service import refresh_holdings_sector_quotes
    from app.models import Holding

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={
                "index": {},
                "concept": {"商业航天": 1.36},
                "industry": {},
            },
            provider_path="eastmoney_live",
        ),
    )
    monkeypatch.setattr(service, "get_sector_mapping", lambda _key: None)
    monkeypatch.setattr(service, "save_sector_mapping", lambda _record: None)
    monkeypatch.setattr(service, "get_official_nav_return", lambda fund_code, trade_date: None)
    monkeypatch.setattr(service, "_is_trading_hours", lambda: False)

    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=10000,
            sector_name="商业航天",
            sector_return_percent=1.36,
        )
    ]

    result = refresh_holdings_sector_quotes(holdings, force_refresh=True)
    assert result["ok"] is True
    updated = [Holding.model_validate(h) for h in result["holdings"]]
    fund = next(h for h in updated if h.fund_code == "015945")
    assert fund.sector_return_percent_source == "closing_estimate"
