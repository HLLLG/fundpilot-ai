from app.models import Holding
from app.models import ApplyHoldingsRequest, FundProfile
import pytest
from app.main import apply_portfolio_holdings
from app.services.ocr_pipeline import apply_confirmed_holdings


def test_apply_confirmed_holdings_skips_network_bootstrap(monkeypatch):
    fetch_calls = {"estimate": 0, "nav": 0}

    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *args, **kwargs: fetch_calls.__setitem__("estimate", fetch_calls["estimate"] + 1) or {},
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *args, **kwargs: fetch_calls.__setitem__("nav", fetch_calls["nav"] + 1) or None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [
            FundProfile(
                fund_code="001234",
                fund_name="测试基金混合A",
                holding_shares=None,
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.apply_primary_sector_to_holdings",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.FundProfileService",
        lambda: type(
            "StubProfileService",
            (),
            {
                "sync_profiles_from_holdings": lambda self, holdings: type(
                    "SyncResult", (), {"model_dump": lambda self: {"updated": 0, "created": 0}}
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.refresh_holdings_sector_quotes",
        lambda holdings, **kwargs: {
            "ok": True,
            "holdings": [
                {
                    **holding.model_dump(),
                    "sector_return_percent": 1.5,
                    "sector_return_percent_source": "realtime",
                }
                for holding in holdings
            ],
            "summary": {"matched": len(holdings)},
            "message": "cache hit",
        },
    )

    holdings = [
        Holding(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000.0,
            return_percent=1.0,
        )
    ]
    result = apply_confirmed_holdings(holdings)

    assert fetch_calls["estimate"] == 0
    assert fetch_calls["nav"] == 0
    assert result["sector_refresh"]["cache_only"] is True
    assert result["sector_refresh"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 1.5
    assert result["holdings"][0]["daily_profit"] is not None


def test_apply_confirmed_holdings_skips_nav_prime_when_ocr_has_official_daily(monkeypatch):
    prime_calls: list[list[str]] = []

    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.apply_primary_sector_to_holdings",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.FundProfileService",
        lambda: type(
            "StubProfileService",
            (),
            {
                "list_profiles": lambda self: [],
                "sync_profiles_from_holdings": lambda self, holdings: type(
                    "SyncResult", (), {"model_dump": lambda self: {"updated": 0, "created": 0}}
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.bootstrap_holding_baselines",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.enrich_loaded_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.refresh_holdings_sector_quotes",
        lambda holdings, **kwargs: {"ok": True, "holdings": holdings, "summary": {"matched": 0}},
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: "2026-06-29",
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda codes, _date: prime_calls.append(list(codes)) or {},
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service._fast_overlay_cached_official_nav",
        lambda holding, _date: holding.model_copy(update={"daily_return_percent": -99.0}),
    )
    monkeypatch.setattr("app.services.ocr_pipeline.save_portfolio_summary", lambda _summary: None)
    monkeypatch.setattr("app.services.ocr_pipeline.save_daily_snapshot", lambda *_args: None)

    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        holding_profit=142.18,
        holding_return_percent=1.30,
        daily_profit=-123.22,
        daily_return_percent_source="official_nav",
        amount_includes_today=True,
    )
    result = apply_confirmed_holdings([holding])

    assert prime_calls == []
    assert result["holdings"][0]["daily_profit"] == -123.22
    assert result["holdings"][0]["estimated_holding_profit"] == pytest.approx(142.18, abs=0.1)


def test_apply_portfolio_holdings_updates_holdings_cache(monkeypatch):
    holding = Holding(
        fund_code="001234",
        fund_name="测试基金混合A",
        holding_amount=13671.67,
    )
    payload = {
        "holdings": [holding.model_dump(mode="json")],
        "portfolio_summary": {"total_assets": 13671.67, "holding_count": 1},
    }
    saved: list[dict] = []

    monkeypatch.setattr("app.main.apply_confirmed_holdings", lambda _holdings: payload)
    monkeypatch.setattr("app.main.save_cached_holdings_response", lambda item: saved.append(item))

    result = apply_portfolio_holdings(ApplyHoldingsRequest(holdings=[holding]))

    assert result is payload
    assert saved == [payload]


def test_apply_confirmed_holdings_applies_semantic_sector_without_benchmark_fetch(monkeypatch):
    benchmark_calls: list[str] = []

    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.save_portfolio_summary",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.save_daily_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: benchmark_calls.append(code) or None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.bootstrap_holding_baselines",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.enrich_loaded_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.FundProfileService",
        lambda: type(
            "StubProfileService",
            (),
            {
                "sync_profiles_from_holdings": lambda self, holdings: type(
                    "SyncResult", (), {"model_dump": lambda self: {"updated": 0, "created": 0}}
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.refresh_holdings_sector_quotes",
        lambda holdings, **kwargs: {
            "ok": True,
            "holdings": [holding.model_dump(mode="json") for holding in holdings],
            "summary": {"matched": 0},
        },
    )

    result = apply_confirmed_holdings(
        [
            Holding(
                fund_code="021277",
                fund_name="广发全球精选股票(QDII)人民币C",
                holding_amount=100.0,
            )
        ]
    )

    assert benchmark_calls == []
    # "全球精选股票"只是"全球"+泛化描述词组合，不是真实主题，退回"海外基金"。
    assert result["holdings"][0]["sector_name"] == "海外基金"
