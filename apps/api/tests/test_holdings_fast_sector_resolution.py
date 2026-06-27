from __future__ import annotations

import pytest

from app.models import Holding


@pytest.fixture(autouse=True)
def _clear_benchmark_miss_cache():
    from app.services import fund_primary_sector_service

    fund_primary_sector_service._benchmark_miss_cache.clear()
    yield
    fund_primary_sector_service._benchmark_miss_cache.clear()


def _holding(**updates) -> Holding:
    defaults = {
        "fund_code": "021533",
        "fund_name": "天弘半导体设备指数C",
        "holding_amount": 3000.0,
        "sector_name": "半导体",
        "intraday_index_name": None,
    }
    defaults.update(updates)
    return Holding(**defaults)


def test_refresh_benchmark_sectors_fast_mode_keeps_existing_sector_without_fetch(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )

    def _fetch(code: str) -> str | None:
        calls.append(code)
        return "中证半导体材料设备主题指数收益率×95%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        _fetch,
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    result = refresh_benchmark_sectors_for_holdings(
        [_holding()],
        fetch_missing_benchmark=False,
    )

    assert calls == []
    assert result[0].sector_name == "半导体"
    assert result[0].intraday_index_name is None


def test_refresh_benchmark_sectors_fast_mode_uses_cached_benchmark(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "021533",
            "sector_name": "半导体材料",
            "intraday_index_name": "中证半导体材料设备主题指数",
            "source": "benchmark_index",
            "confidence": 0.82,
            "detail": {},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: (_ for _ in ()).throw(AssertionError("benchmark fetch should be skipped")),
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    result = refresh_benchmark_sectors_for_holdings(
        [_holding()],
        fetch_missing_benchmark=False,
    )

    assert result[0].sector_name == "半导体材料"
    assert result[0].intraday_index_name == "中证半导体材料设备主题指数"


def test_refresh_holdings_sector_quotes_cache_only_skips_missing_benchmark_fetch(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )

    def _fetch(code: str) -> str | None:
        calls.append(code)
        return "中证半导体材料设备主题指数收益率×95%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        _fetch,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    refresh_holdings_sector_quotes([_holding()], cache_only=True)

    assert calls == []


def test_failed_benchmark_fetch_is_miss_cached_for_accurate_mode(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )

    def _fetch(code: str) -> str | None:
        calls.append(code)
        return None

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        _fetch,
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    refresh_benchmark_sectors_for_holdings([_holding()], fetch_missing_benchmark=True)
    refresh_benchmark_sectors_for_holdings([_holding()], fetch_missing_benchmark=True)

    assert calls == ["021533"]


def test_apply_confirmed_holdings_fast_enrichment_skips_missing_benchmark_fetch(monkeypatch):
    calls: list[str] = []

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
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: calls.append(code) or None,
    )

    from app.services.ocr_pipeline import apply_confirmed_holdings

    apply_confirmed_holdings(
        [
            _holding(
                sector_name=None,
                intraday_index_name=None,
                sector_return_percent=1.2,
            )
        ]
    )

    assert calls == []


def test_background_portfolio_sector_refresh_loads_holdings_without_missing_benchmark_fetch(monkeypatch):
    from app.models import FundProfile

    calls: list[str] = []
    persisted: list[Holding] = []

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [
            FundProfile(
                fund_code="021533",
                fund_name="天弘半导体设备指数C",
                holding_amount=3000.0,
            )
        ],
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
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: calls.append(code) or None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_sector_refresh.set_request_user_id",
        lambda _user_id: object(),
    )
    monkeypatch.setattr(
        "app.services.portfolio_sector_refresh.reset_request_user_id",
        lambda _token: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_sector_refresh.refresh_holdings_sector_quotes",
        lambda holdings, **_kwargs: {
            "ok": True,
            "holdings": [holding.model_dump() for holding in holdings],
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_sector_refresh.persist_holdings_after_sector_refresh",
        lambda holdings, **_kwargs: persisted.extend(holdings) or holdings,
    )

    from app.services.portfolio_sector_refresh import refresh_portfolio_sectors_for_user

    refresh_portfolio_sectors_for_user(1)

    assert calls == []
    assert [holding.fund_code for holding in persisted] == ["021533"]


def test_refresh_holdings_sector_quotes_fast_timeout_skips_benchmark_fetch_but_accurate_fetches(monkeypatch):
    from app.services.sector_quote_service import SpotBoardFetchResult

    calls: list[str] = []

    class FakeProfileService:
        def resolve_holding(self, holding, **_kwargs):
            return holding

        def _find_profile_for_holding(self, _holding):
            return None

    monkeypatch.setattr(
        "app.services.sector_quote_service.FundProfileService",
        FakeProfileService,
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
        "app.services.fund_benchmark_sector.resolve_sector_from_benchmark",
        lambda _text: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.labels_need_spot_boards",
        lambda _labels: False,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards_result",
        lambda **_kwargs: SpotBoardFetchResult(
            boards={"index": {}, "concept": {}, "industry": {}},
            provider_path="cache_miss",
            live_attempted=False,
            elapsed_seconds=0.0,
        ),
    )

    def _fetch(code: str) -> str | None:
        calls.append(code)
        return "benchmark text without a sector match"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        _fetch,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    holding = _holding(fund_code="123456", sector_name=None, intraday_index_name=None)
    refresh_holdings_sector_quotes([holding], timeout_seconds=8.0)
    assert calls == []

    refresh_holdings_sector_quotes([holding], timeout_seconds=None)
    assert calls == ["123456"]


def test_portfolio_holdings_cache_miss_loads_without_benchmark_fetch(monkeypatch):
    from app.models import Holding
    from app import main

    load_fetch_flags: list[bool] = []
    resolve_fetch_flags: list[bool] = []

    holding = Holding(
        fund_code="123456",
        fund_name="Test Index Fund",
        holding_amount=1000.0,
        sector_name=None,
    )

    class FakeProfileService:
        def resolve_holdings(self, holdings, **kwargs):
            resolve_fetch_flags.append(kwargs.get("fetch_benchmark"))
            return holdings

        def list_profiles(self):
            return []

    def _load_persisted_holdings(*, fetch_benchmark=True):
        load_fetch_flags.append(fetch_benchmark)
        return [holding], "snapshot", "2026-06-03", None

    monkeypatch.setattr(main, "get_cached_holdings_response", lambda: None)
    monkeypatch.setattr(main, "load_persisted_holdings", _load_persisted_holdings)
    monkeypatch.setattr(main, "apply_server_sector_cache_to_holdings", lambda holdings: holdings)
    monkeypatch.setattr(main, "save_cached_holdings_response", lambda _payload: None)
    monkeypatch.setattr(main, "schedule_warm_holdings_intraday", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "get_request_user_id", lambda: 1)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.FundProfileService",
        FakeProfileService,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_portfolio_summary",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: (_ for _ in ()).throw(AssertionError("benchmark fetch should be skipped")),
    )

    payload = main.portfolio_holdings()

    assert load_fetch_flags == [False]
    assert resolve_fetch_flags == [False]
    assert payload["source"] == "snapshot"
    assert payload["holdings"][0]["fund_code"] == "123456"


def test_load_persisted_holdings_snapshot_merge_respects_fetch_benchmark_false(monkeypatch):
    from app.models import FundProfile
    from app.services.portfolio_holdings_service import load_persisted_holdings

    enrich_fetch_flags: list[bool] = []
    resolve_fetch_flags: list[bool] = []

    snapshot_holding = Holding(
        fund_code="123456",
        fund_name="Test Index Fund",
        holding_amount=1000.0,
        sector_name=None,
    )
    profile = FundProfile(
        fund_code="123456",
        fund_name="Test Index Fund",
        holding_amount=1000.0,
        sector_name=None,
    )

    class FakeProfileService:
        def resolve_holdings(self, holdings, **kwargs):
            resolve_fetch_flags.append(kwargs.get("fetch_benchmark"))
            return holdings

    def _enrich_from_profiles(holdings, **kwargs):
        enrich_fetch_flags.append(kwargs.get("fetch_benchmark"))
        return holdings

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-06-03",
            "captured_at": "2026-06-03T08:15:00+00:00",
            "holdings": [snapshot_holding.model_dump()],
            "total_assets": 1000.0,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.enrich_holdings_from_profiles",
        _enrich_from_profiles,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.FundProfileService",
        FakeProfileService,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: (_ for _ in ()).throw(AssertionError("benchmark fetch should be skipped")),
    )

    holdings, source, snapshot_date, _ = load_persisted_holdings(fetch_benchmark=False)

    assert source == "snapshot"
    assert snapshot_date == "2026-06-03"
    assert [holding.fund_code for holding in holdings] == ["123456"]
    assert resolve_fetch_flags == [False]
    assert enrich_fetch_flags == [False, False]
