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


def test_refresh_benchmark_sectors_fast_mode_applies_semantic_name_without_fetch(monkeypatch):
    calls: list[str] = []

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
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    result = refresh_benchmark_sectors_for_holdings(
        [
            _holding(
                fund_code="026790",
                fund_name="中欧上证科创板人工智能指数C",
                sector_name=None,
                intraday_index_name=None,
            )
        ],
        fetch_missing_benchmark=False,
    )

    assert calls == []
    assert result[0].sector_name == "人工智能"


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


def test_refresh_benchmark_sectors_keeps_fresh_benchmark_before_holdings_infer(monkeypatch):
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holdings_infer_calls: list[str] = []

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
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda code, **_kwargs: PrimarySectorRecord(
            fund_code=code,
            sector_name="人工智能",
            intraday_index_name="中证人工智能",
            source="benchmark_index",
            confidence=0.82,
        ),
    )

    def _holdings_infer(code: str, **_kwargs):
        holdings_infer_calls.append(code)
        return PrimarySectorRecord(
            fund_code=code,
            sector_name="半导体",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.9,
        )

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_holdings_infer",
        _holdings_infer,
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    result = refresh_benchmark_sectors_for_holdings(
        [
            _holding(
                fund_code="026790",
                fund_name="中欧上证科创板人工智能指数C",
                sector_name=None,
                intraday_index_name=None,
            )
        ],
        fetch_missing_benchmark=True,
        fetch_holdings_infer=True,
    )

    assert holdings_infer_calls == []
    assert result[0].sector_name == "人工智能"
    assert result[0].intraday_index_name == "中证人工智能"


def test_holdings_infer_does_not_persist_or_promote_over_benchmark_user_record(monkeypatch):
    saved: list[dict] = []
    promoted: list[object] = []

    monkeypatch.setattr(
        "app.services.fund_holdings_sector_infer.fetch_portfolio_stocks_with_industry",
        lambda _code: [object()],
    )
    monkeypatch.setattr(
        "app.services.fund_holdings_sector_infer.infer_sector_from_portfolio_stocks",
        lambda _code, _stocks: ("半导体", {"半导体": 45.0}, []),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "026790",
            "sector_name": "人工智能",
            "intraday_index_name": "中证人工智能",
            "source": "benchmark_index",
            "confidence": 0.82,
            "detail": {},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.try_get_request_user_id",
        lambda: 1,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: saved.append(kwargs),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.promote_record_to_global",
        lambda record: promoted.append(record),
    )

    from app.services.fund_primary_sector_service import _resolve_from_holdings_infer

    record = _resolve_from_holdings_infer("026790", persist=True)

    assert record is not None
    assert record.source == "holdings_infer"
    assert record.sector_name == "半导体"
    assert saved == []
    assert promoted == []


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


def test_refresh_holdings_sector_quotes_fast_and_accurate_fetch_benchmark(monkeypatch):
    from app.services.sector_quote_service import SpotBoardFetchResult

    calls: list[str] = []

    class FakeProfileService:
        def list_profiles(self):
            return []

        def resolve_holdings_with_profiles(self, holdings, **_kwargs):
            return holdings, [None] * len(holdings)

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
    holdings_infer_calls: list[str] = []

    def _fetch_portfolio_stocks(code: str):
        holdings_infer_calls.append(code)
        return []

    monkeypatch.setattr(
        "app.services.fund_holdings_sector_infer.fetch_portfolio_stocks_with_industry",
        _fetch_portfolio_stocks,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    holding = _holding(fund_code="123456", sector_name=None, intraday_index_name=None)
    fast_result = refresh_holdings_sector_quotes([holding], timeout_seconds=8.0)
    assert calls == ["123456"]
    assert holdings_infer_calls == []
    assert fast_result["ok"] is False
    assert fast_result["provider_failed"] is True
    assert fast_result["holdings"][0]["fund_code"] == "123456"

    from app.services import fund_primary_sector_service

    fund_primary_sector_service._benchmark_miss_cache.clear()
    calls.clear()
    accurate_result = refresh_holdings_sector_quotes([holding], timeout_seconds=None)
    assert calls == ["123456"]
    assert holdings_infer_calls == ["123456"]
    assert accurate_result["ok"] is False
    assert accurate_result["provider_failed"] is True


def test_portfolio_holdings_cache_miss_loads_without_benchmark_fetch(monkeypatch):
    from app.models import Holding
    from app import main

    load_fetch_flags: list[bool] = []
    resolve_fetch_flags: list[bool] = []
    sector_network_fallback_flags: list[bool | None] = []

    holding = Holding(
        fund_code="123456",
        fund_name="Test Index Fund",
        holding_amount=1000.0,
        sector_name=None,
    )

    class FakeProfileService:
        def resolve_holdings_with_profiles(self, holdings, **kwargs):
            resolve_fetch_flags.append(kwargs.get("fetch_benchmark"))
            return holdings, [None] * len(holdings)

        def list_profiles(self):
            return []

    def _load_persisted_holdings(*, fetch_benchmark=True):
        load_fetch_flags.append(fetch_benchmark)
        return [holding], "snapshot", "2026-06-03", None

    def _apply_server_sector_cache(holdings, **kwargs):
        sector_network_fallback_flags.append(kwargs.get("network_fallback"))
        return holdings

    monkeypatch.setattr(main, "get_cached_holdings_response", lambda: None)
    monkeypatch.setattr(main, "load_persisted_holdings", _load_persisted_holdings)
    monkeypatch.setattr(main, "apply_server_sector_cache_to_holdings", _apply_server_sector_cache)
    monkeypatch.setattr(main, "save_cached_holdings_response", lambda _payload, **_kwargs: None)
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

    payload = main._portfolio_holdings_sync()

    assert load_fetch_flags == [False]
    assert resolve_fetch_flags == [False]
    assert sector_network_fallback_flags == [False]


def test_fund_estimate_fallback_updates_daily_and_sector(monkeypatch):
    """天天基金净值估值兜底时，daily_return_percent 固定按 sector_estimate 记账；
    sector_return_percent 现在也应该一起写回同一个估算值（source 落在
    realtime/closing_estimate），否则同样落在「海外基金」这类无真实板块可查的
    持仓里，会出现有的基金（历史上曾匹配过板块、残留旧数据）显示数字、有的
    （从未匹配过）一直空白的不一致假象——前端用 sectorMeta.provider 单独标
    "估值兜底"角标区分数据来源，不会和真实板块行情混淆。"""
    from app.models import Holding
    from app.services.sector_quote_provider import SpotBoardFetchResult
    from app.services.sector_quote_resolver import SectorResolveResult

    class FakeProfileService:
        def list_profiles(self):
            return []

        def resolve_holdings_with_profiles(self, holdings, **_kwargs):
            return holdings, [None] * len(holdings)

    monkeypatch.setattr(
        "app.services.sector_quote_service.FundProfileService",
        FakeProfileService,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.refresh_benchmark_sectors_for_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.primary_sector_fields_for_holding",
        lambda *_args, **_kwargs: {},
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
        "app.services.sector_quote_service.resolve_sector_quote",
        lambda *_args, **_kwargs: SectorResolveResult(confidence="none"),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {
            "123456": {"change_percent": 3.66, "fund_name": "Fallback Fund"}
        },
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.save_sector_mapping",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_spot_boards_result",
        lambda **_kwargs: SpotBoardFetchResult(
            boards={"index": {}, "concept": {}, "industry": {}},
            provider_path="empty",
            live_attempted=True,
            elapsed_seconds=0.0,
        ),
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    result = refresh_holdings_sector_quotes(
        [
            Holding(
                fund_code="123456",
                fund_name="Fallback Fund",
                holding_amount=1000,
            )
        ],
        timeout_seconds=8.0,
    )
    holding = Holding.model_validate(result["holdings"][0])

    assert holding.sector_return_percent == 3.66
    assert holding.sector_return_percent_source in {"realtime", "closing_estimate"}
    assert holding.daily_return_percent == 3.66
    assert holding.daily_return_percent_source == "sector_estimate"
    assert holding.daily_profit == 36.6


def test_official_nav_updates_daily_while_board_keeps_close_change(monkeypatch):
    from app.models import Holding
    from app.services.sector_quote_resolver import SectorResolveResult

    class FakeProfileService:
        def list_profiles(self):
            return []

        def resolve_holdings_with_profiles(self, holdings, **_kwargs):
            return holdings, [None] * len(holdings)

    monkeypatch.setattr(
        "app.services.sector_quote_service.FundProfileService",
        FakeProfileService,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.refresh_benchmark_sectors_for_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.primary_sector_fields_for_holding",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.labels_need_spot_boards",
        lambda _labels: False,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.resolve_sector_quote",
        lambda *_args, **_kwargs: SectorResolveResult(
            confidence="high",
            change_percent=-4.62,
            matched_name="人工智能",
            source_type="index",
            source_code="930713",
            message="东财K线",
            candidates=[],
        ),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda *_args, **_kwargs: 3.66,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.save_sector_mapping",
        lambda *_args, **_kwargs: None,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    result = refresh_holdings_sector_quotes(
        [
            Holding(
                fund_code="008586",
                fund_name="华夏人工智能ETF联接C",
                holding_amount=8671.67,
                sector_name="人工智能",
                intraday_index_name="中证人工智能",
            )
        ],
        timeout_seconds=8.0,
    )
    holding = Holding.model_validate(result["holdings"][0])

    assert holding.sector_return_percent == -4.62
    assert holding.daily_return_percent == 3.66
    assert holding.daily_return_percent_source == "official_nav"


def test_sector_refresh_reuses_profiles_session_and_mapping(monkeypatch):
    from app.models import Holding
    from app.services.sector_quote_resolver import SectorResolveResult

    profile_snapshot_reads: list[str] = []
    session_calls: list[str] = []
    mapping_reads: list[str] = []
    persisted_mappings: list[dict | None] = []
    nav_calls: list[tuple[str, str]] = []

    class FakeProfileService:
        def list_profiles(self):
            profile_snapshot_reads.append("list")
            return []

        def resolve_holdings_with_profiles(self, holdings, **kwargs):
            assert kwargs["profiles_snapshot"] == []
            return holdings, [None] * len(holdings)

    monkeypatch.setattr(
        "app.services.sector_quote_service.FundProfileService",
        FakeProfileService,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.build_trading_session",
        lambda: session_calls.append("called")
        or {
            "session_kind": "trading_day_after_close",
            "effective_trade_date": "2026-07-13",
        },
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_effective_trade_date",
        lambda: (_ for _ in ()).throw(
            AssertionError("session effective_trade_date should be reused")
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.refresh_benchmark_sectors_for_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.primary_sector_fields_for_holding",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.labels_need_spot_boards",
        lambda _labels: False,
    )

    def resolve_quote(*_args, persisted_mapping=None, **_kwargs):
        persisted_mappings.append(persisted_mapping)
        return SectorResolveResult(
            confidence="high",
            change_percent=1.2,
            matched_name="人工智能",
            source_type="index",
            source_code="930713",
            message="东财K线",
            candidates=[],
        )

    monkeypatch.setattr(
        "app.services.sector_quote_service.resolve_sector_quote",
        resolve_quote,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_sector_mapping",
        lambda label: mapping_reads.append(label) or None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.save_sector_mapping",
        lambda record: record,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda code, trade_date: nav_calls.append((code, trade_date)) or 0.8,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    holdings = [
        Holding(
            fund_code=code,
            fund_name=f"人工智能基金 {code}",
            holding_amount=1000,
            sector_name="人工智能",
        )
        for code in ("100001", "100002")
    ]
    refresh_holdings_sector_quotes(holdings, timeout_seconds=8.0)

    assert profile_snapshot_reads == ["list"]
    assert session_calls == ["called"]
    assert mapping_reads == ["人工智能"]
    assert persisted_mappings[0] is None
    assert persisted_mappings[1] is not None
    assert nav_calls == [
        ("100001", "2026-07-13"),
        ("100002", "2026-07-13"),
    ]


def test_sector_refresh_batches_profile_and_primary_sector_snapshots_for_duplicate_aliases(
    monkeypatch,
):
    from app.models import FundProfile
    from app.services.sector_quote_resolver import SectorResolveResult

    calls = {"profiles": 0, "primary_user": 0, "primary_global": 0}
    profile = FundProfile(
        fund_code="123456",
        fund_name="人工智能主题基金",
        aliases=["AI别名基金"],
        sector_name="人工智能",
        intraday_index_name="中证人工智能",
        source="ocr-detail",
    )

    def list_profiles():
        calls["profiles"] += 1
        return [profile]

    def list_primary_user():
        calls["primary_user"] += 1
        return [
            {
                "fund_code": "123456",
                "sector_name": "人工智能",
                "intraday_index_name": "中证人工智能",
                "source": "ocr_detail",
                "confidence": 0.95,
            }
        ]

    def list_primary_global(codes):
        calls["primary_global"] += 1
        assert set(codes) == {"123456"}
        return {}

    def point_lookup_forbidden(*_args, **_kwargs):
        raise AssertionError("batch sector refresh must not use a point lookup")

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", list_profiles)
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        point_lookup_forbidden,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.list_fund_primary_sectors",
        list_primary_user,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sectors_global_by_codes",
        list_primary_global,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        point_lookup_forbidden,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        point_lookup_forbidden,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        point_lookup_forbidden,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.labels_need_spot_boards",
        lambda _labels: False,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.resolve_sector_quote",
        lambda *_args, **_kwargs: SectorResolveResult(
            confidence="high",
            change_percent=1.25,
            matched_name="人工智能",
            source_type="index",
            source_code="930713",
            message="东财K线",
            candidates=[],
        ),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.save_sector_mapping",
        lambda record: record,
    )

    from app.services.sector_quote_service import refresh_holdings_sector_quotes

    result = refresh_holdings_sector_quotes(
        [
            Holding(
                fund_code="123456",
                fund_name="人工智能主题基金",
                holding_amount=1000,
            ),
            Holding(
                fund_code="123456",
                fund_name="AI别名基金",
                holding_amount=500,
            ),
            Holding(
                fund_code="000000",
                fund_name="AI别名基金",
                holding_amount=250,
            ),
        ],
        timeout_seconds=8.0,
    )

    assert result["ok"] is True
    assert [item["fund_code"] for item in result["holdings"]] == [
        "123456",
        "123456",
        "123456",
    ]
    assert calls == {"profiles": 1, "primary_user": 1, "primary_global": 1}


def test_primary_sector_batch_context_reuses_saved_row_for_source_priority(monkeypatch):
    from app.services.fund_primary_sector_service import (
        PrimarySectorBatchContext,
        upsert_primary_sector_from_holding,
    )

    saves: list[dict] = []

    def save_primary(**payload):
        saved = {**payload, "updated_at": "2026-07-13T00:00:00+00:00"}
        saves.append(saved)
        return saved

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        save_primary,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("batch context must reuse the saved row")
        ),
    )
    context = PrimarySectorBatchContext()
    holding = Holding(
        fund_code="123456",
        fund_name="人工智能主题基金",
        holding_amount=1000,
        sector_name="人工智能",
    )

    upsert_primary_sector_from_holding(
        holding,
        source="alipay_overview",
        batch_context=context,
    )
    upsert_primary_sector_from_holding(
        holding.model_copy(update={"sector_name": "半导体"}),
        source="name_infer",
        batch_context=context,
    )

    assert len(saves) == 1
    assert context.user_row("123456")["sector_name"] == "人工智能"


def test_save_user_primary_sector_does_not_read_back(monkeypatch):
    from app.database import save_fund_primary_sector

    monkeypatch.setattr(
        "app.database.get_fund_primary_sector",
        lambda _code: (_ for _ in ()).throw(
            AssertionError("write must return its known payload without a point read")
        ),
    )

    saved = save_fund_primary_sector(
        fund_code="123456",
        sector_name="人工智能",
        source="benchmark_index",
        confidence=0.82,
    )

    assert saved["fund_code"] == "123456"
    assert saved["sector_name"] == "人工智能"
    assert saved["source"] == "benchmark_index"

