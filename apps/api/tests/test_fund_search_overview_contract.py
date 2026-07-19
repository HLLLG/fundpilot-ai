from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models import FundNavHistory, FundNavPoint, Holding
from app.services import fund_code_resolver, fund_name_table_store, fund_public_overview
from app.services.fund_benchmark_sector import parse_benchmark_index
from app.services.fund_primary_sector_types import PrimarySectorRecord


def _install_name_table(monkeypatch, rows: list[tuple[str, str]]) -> None:
    monkeypatch.setattr(fund_code_resolver, "_fund_name_table_cache", rows)
    monkeypatch.setattr(fund_code_resolver, "_fund_name_index_cache", None)


def test_search_ranks_exact_partial_code_and_name_deterministically(monkeypatch) -> None:
    rows = [
        ("008585", "华夏中证人工智能主题ETF联接A"),
        ("008586", "华夏中证人工智能主题ETF联接C"),
        ("018586", "示例人工智能增强基金"),
        ("000856", "南方稳健成长基金"),
    ]
    _install_name_table(monkeypatch, rows)

    exact = fund_code_resolver.search_funds_by_keyword("008586")
    assert exact == [
        {
            "fund_code": "008586",
            "fund_name": "华夏中证人工智能主题ETF联接C",
            "match_kind": "code_exact",
        }
    ]

    prefix = fund_code_resolver.search_funds_by_keyword("0085")
    assert [item["fund_code"] for item in prefix[:2]] == ["008585", "008586"]
    assert all(item["match_kind"] == "code_prefix" for item in prefix[:2])

    contains = fund_code_resolver.search_funds_by_keyword("8586")
    assert [item["fund_code"] for item in contains[:2]] == ["008586", "018586"]
    assert all(item["match_kind"] == "code_contains" for item in contains[:2])

    names = fund_code_resolver.search_funds_by_keyword("华夏中证人工智能")
    assert [item["fund_code"] for item in names[:2]] == ["008585", "008586"]


def test_prefix_search_uses_public_suggestion_order_and_pages_all_matches(monkeypatch) -> None:
    rows = [
        ("016708", "华夏有色金属ETF联接C"),
        ("025857", "华夏中证电网设备主题ETF发起式联接C"),
        ("024239", "华夏全球科技先锋混合(QDII)C"),
        ("008888", "华夏国证半导体芯片ETF联接C"),
        ("024418", "华夏上证科创板半导体材料设备主题ETF发起式联接C"),
        ("000001", "华夏成长混合"),
    ]
    _install_name_table(monkeypatch, rows)
    monkeypatch.setattr(
        fund_code_resolver,
        "fetch_ranked_fund_suggestions",
        lambda _query: [
            {
                "fund_code": code,
                "fund_name": name,
                "fund_type": "指数型-股票",
                "provider_rank": rank,
            }
            for rank, (code, name) in enumerate(rows[:5], start=1)
        ],
    )

    first = fund_code_resolver.search_funds_page_by_keyword(
        "华夏",
        limit=5,
        include_popularity=True,
    )
    assert first["total"] == 6
    assert first["has_more"] is True
    assert [item["fund_code"] for item in first["items"]] == [
        "016708",
        "025857",
        "024239",
        "008888",
        "024418",
    ]
    assert [item["popularity_rank"] for item in first["items"]] == [1, 2, 3, 4, 5]

    remaining = fund_code_resolver.search_funds_page_by_keyword(
        "华夏",
        limit=50,
        offset=5,
        include_popularity=True,
    )
    assert [item["fund_code"] for item in remaining["items"]] == ["000001"]
    assert remaining["has_more"] is False


def test_stale_name_table_is_available_for_nonblocking_search(monkeypatch, tmp_path) -> None:
    path = tmp_path / "fund_names.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "fetched_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
                "rows": [["008586", "华夏人工智能基金"]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUND_AI_FUND_NAME_CACHE_PATH", str(path))
    monkeypatch.setenv("FUND_AI_FUND_NAME_TABLE_TTL_SECONDS", "300")

    assert fund_name_table_store.load_cached_fund_name_table() is None
    assert fund_name_table_store.load_cached_fund_name_table(allow_stale=True) == [
        ("008586", "华夏人工智能基金")
    ]

    monkeypatch.setattr(fund_code_resolver, "_fund_name_table_cache", None)
    monkeypatch.setattr(fund_code_resolver, "_fund_name_index_cache", None)
    scheduled: list[bool] = []
    monkeypatch.setattr(
        fund_code_resolver,
        "_schedule_fund_name_table_refresh",
        lambda: scheduled.append(True),
    )
    assert fund_code_resolver._fund_name_table() == [("008586", "华夏人工智能基金")]
    assert scheduled == [True]


def test_artificial_intelligence_index_identities_do_not_collide() -> None:
    theme = parse_benchmark_index("中证人工智能主题指数收益率×95%+活期存款×5%")
    industry = parse_benchmark_index("中证人工智能产业指数收益率×95%+活期存款×5%")

    assert theme is not None and theme.index_code == "930713"
    assert industry is not None and industry.index_code == "931071"


def test_sector_estimate_written_to_daily_field_stays_marked_estimated() -> None:
    from app.services.holding_estimates import holding_daily_return_is_estimated

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=1000,
        daily_return_percent=-3.2,
        daily_return_percent_source="sector_estimate",
        sector_return_percent=-3.2,
    )
    assert holding_daily_return_is_estimated(holding, profile=None) is True


def _history(code: str, name: str) -> FundNavHistory:
    points = [
        FundNavPoint(
            date=f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}",
            nav=1 + index / 1000,
            daily_return_percent=-7.61 if index == 250 else 0.1,
        )
        for index in range(251)
    ]
    return FundNavHistory(
        fund_code=code,
        fund_name=name,
        source="pytest",
        points=points,
        latest_nav=points[-1].nav,
        latest_date=points[-1].date,
    )


def _mock_overview_dependencies(monkeypatch, *, name: str, fund_type: str) -> None:
    monkeypatch.setattr(fund_public_overview, "get_fund_profile_by_code", lambda _code: None)
    monkeypatch.setattr(fund_public_overview, "lookup_fund_name_by_code", lambda _code: name)
    monkeypatch.setattr(fund_public_overview, "get_most_recent_portfolio_snapshot", lambda: None)
    monkeypatch.setattr(fund_public_overview, "get_fund_primary_sector", lambda _code: None)
    monkeypatch.setattr(fund_public_overview, "load_fresh_global_sector", lambda _code: None)
    monkeypatch.setattr(
        fund_public_overview.FundDataService,
        "get_nav_history",
        lambda _self, code, resolved_name, **_kwargs: _history(code, resolved_name),
    )
    monkeypatch.setattr(
        fund_public_overview,
        "_safe_fund_diagnostics",
        lambda _code: {"fund_type": fund_type, "management_fee": "0.50%"},
    )
    monkeypatch.setattr(
        fund_public_overview,
        "fetch_fund_benchmark_text",
        lambda _code: "中证人工智能主题指数收益率×95%+银行活期存款利率×5%",
    )
    monkeypatch.setattr(
        fund_public_overview,
        "get_fund_benchmark_fetch_metadata",
        lambda _code, _text: {
            "benchmark_text_kind": "performance_benchmark",
            "benchmark_text_source_kind": "xq_akshare_aggregator",
        },
    )


def test_public_overview_passive_fund_exposes_tracking_reference_not_nav_proxy(monkeypatch) -> None:
    _mock_overview_dependencies(
        monkeypatch,
        name="华夏中证人工智能主题ETF联接C",
        fund_type="指数型-股票",
    )

    payload = fund_public_overview.build_fund_public_overview("008586")

    assert payload["official_daily_return_percent"] == -7.61
    assert payload["relation"]["kind"] == "tracking_reference"
    assert payload["relation"]["source_code"] == "930713"
    assert payload["relation"]["evidence_source"] == "xq_akshare_aggregator"
    assert payload["data_note"].startswith("基金涨跌与收益率均来自官方净值")


def test_public_overview_active_fund_does_not_promote_benchmark_to_sector(monkeypatch) -> None:
    _mock_overview_dependencies(
        monkeypatch,
        name="示例人工智能主题混合C",
        fund_type="混合型-偏股",
    )

    payload = fund_public_overview.build_fund_public_overview("001234")

    assert payload["relation"]["status"] == "unavailable"
    assert payload["relation"]["price_proxy_eligible"] is False
    assert "暂无可靠单一板块" in payload["relation"]["note"]
    assert payload["performance_benchmark"]["symbol"] == "930713"


def test_holdings_relation_has_priority_over_third_party_benchmark(monkeypatch) -> None:
    _mock_overview_dependencies(
        monkeypatch,
        name="示例主动基金",
        fund_type="混合型-偏股",
    )
    monkeypatch.setattr(
        fund_public_overview,
        "get_fund_primary_sector",
        lambda _code: {
            "fund_code": "001234",
            "sector_name": "半导体",
            "source": "holdings_infer",
            "confidence": 0.84,
            "updated_at": "2026-07-18T00:00:00+00:00",
        },
    )

    payload = fund_public_overview.build_fund_public_overview("001234")

    assert payload["relation"]["kind"] == "holdings_exposure"
    assert payload["relation"]["label"] == "半导体"
    assert payload["relation"]["price_proxy_eligible"] is True


def test_holdings_resolution_precedes_benchmark(monkeypatch) -> None:
    from app.services import fund_primary_sector_service as service

    monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
    monkeypatch.setattr(service, "load_fresh_global_sector", lambda _code: None)
    monkeypatch.setattr(service, "try_get_request_user_id", lambda: None)
    record = PrimarySectorRecord(
        fund_code="001234",
        sector_name="半导体",
        intraday_index_name="中证半导体",
        source="holdings_infer",
        confidence=0.85,
    )
    monkeypatch.setattr(service, "_resolve_from_holdings_infer", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(
        service,
        "_resolve_from_benchmark_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("benchmark ran first")),
    )

    assert service.resolve_primary_sector(
        "001234",
        fund_name="示例主动基金",
        fetch_holdings_infer=True,
    ) == record


def test_persisted_benchmark_proxy_flag_supports_json_detail() -> None:
    from app.services import fund_primary_sector_service as service

    row = {
        "source": "benchmark_index",
        "detail": json.dumps(
            {
                "benchmark_text_kind": "performance_benchmark",
                "price_proxy_eligible": False,
            }
        ),
    }

    assert service._benchmark_row_is_price_proxy_eligible(  # noqa: SLF001
        row,
        "示例中证主题指数增强基金",
    ) is False
