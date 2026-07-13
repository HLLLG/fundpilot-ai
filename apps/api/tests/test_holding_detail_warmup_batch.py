from __future__ import annotations

from datetime import date, timedelta

from app.models import FundNavHistory, FundProfile, Holding
from app.services import holding_detail_service as detail_module
from app.services import holding_intraday_warmup as warmup_module
from app.services import fund_primary_sector_service as primary_sector_module


def _holdings() -> list[Holding]:
    return [
        Holding(fund_code="000001", fund_name="A", holding_amount=100),
        Holding(fund_code="000002", fund_name="B", holding_amount=200),
        Holding(fund_code="000003", fund_name="C", holding_amount=300),
    ]


def _disable_user_context(monkeypatch) -> None:
    monkeypatch.setattr(warmup_module, "set_request_user_id", lambda _user_id: object())
    monkeypatch.setattr(warmup_module, "reset_request_user_id", lambda _token: None)
    monkeypatch.setattr(warmup_module.time, "sleep", lambda _seconds: None)


def test_warm_holding_details_preloads_profiles_and_snapshots_once(monkeypatch):
    profiles_calls = 0
    snapshot_calls: list[int] = []
    saved: list[dict] = []
    profiles = [
        FundProfile(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            yesterday_profit=float(index),
            holding_days=index + 5,
            holding_days_as_of=date.today().isoformat(),
        )
        for index, holding in enumerate(_holdings(), start=1)
    ]

    def _list_profiles() -> list[FundProfile]:
        nonlocal profiles_calls
        profiles_calls += 1
        return profiles

    def _list_snapshots(*, limit: int = 30, include_holdings: bool = True) -> list[dict]:
        snapshot_calls.append(limit)
        return []

    def _resolve_with_profile(self, holding, profile, **_kwargs):
        assert profile is not None
        assert profile.fund_code == holding.fund_code
        return holding

    _disable_user_context(monkeypatch)
    monkeypatch.setattr(warmup_module, "get_cached_holding_detail", lambda *_args: None)
    monkeypatch.setattr(
        warmup_module,
        "save_cached_holding_detail",
        lambda _code, _fingerprint, payload: saved.append(payload),
    )
    monkeypatch.setattr(detail_module, "list_fund_profiles", _list_profiles)
    monkeypatch.setattr(detail_module, "list_portfolio_daily_snapshots", _list_snapshots)
    monkeypatch.setattr(
        detail_module.PrimarySectorBatchContext,
        "load",
        classmethod(
            lambda cls, _codes, *, profiles=(): cls(
                profiles_by_code={profile.fund_code: profile for profile in profiles}
            )
        ),
    )
    monkeypatch.setattr(
        detail_module,
        "get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("point profile query is forbidden")),
    )
    monkeypatch.setattr(
        detail_module.FundProfileService,
        "_resolve_holding_with_profile",
        _resolve_with_profile,
    )
    monkeypatch.setattr(
        detail_module.FundDataService,
        "get_nav_history",
        lambda _self, code, name, **_kwargs: FundNavHistory(
            fund_code=code,
            fund_name=name,
            source="unavailable",
        ),
    )

    assert warmup_module.warm_holding_details(_holdings(), user_id=42) == 3
    assert profiles_calls == 1
    assert snapshot_calls == [365]
    assert [payload["yesterday_profit"] for payload in saved] == [1.0, 2.0, 3.0]
    assert [payload["holding_days"] for payload in saved] == [6, 7, 8]
    assert all(payload["provenance"]["yesterday_profit"] == "ocr_detail" for payload in saved)
    assert all(payload["provenance"]["holding_days"] == "ocr_detail" for payload in saved)


def test_warm_holding_details_all_cache_hits_do_not_preload(monkeypatch):
    _disable_user_context(monkeypatch)
    monkeypatch.setattr(
        warmup_module,
        "get_cached_holding_detail",
        lambda *_args: {"cached": True},
    )
    monkeypatch.setattr(
        detail_module,
        "list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("profiles must not be preloaded")),
    )
    monkeypatch.setattr(
        detail_module,
        "list_portfolio_daily_snapshots",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("snapshots must not be preloaded")),
    )
    monkeypatch.setattr(
        warmup_module,
        "build_holding_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("detail must not build")),
    )

    assert warmup_module.warm_holding_details(_holdings(), user_id=42) == 0


def test_single_holding_detail_reuses_profile_and_one_snapshot_window(monkeypatch):
    holding = Holding(fund_code="000001", fund_name="A", holding_amount=100)
    profile = FundProfile(fund_code="000001", fund_name="A")
    point_calls = 0
    snapshot_calls: list[int] = []
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    older = (date.today() - timedelta(days=20)).isoformat()
    snapshots = [
        {"snapshot_date": date.today().isoformat(), "holdings": []},
        {
            "snapshot_date": yesterday,
            "holdings": [{"fund_code": "000001", "fund_name": "A", "daily_profit": 12.34}],
        },
        {
            "snapshot_date": older,
            "holdings": [{"fund_code": "000001", "fund_name": "A", "daily_profit": 2.0}],
        },
    ]

    def _get_profile(_code: str) -> FundProfile:
        nonlocal point_calls
        point_calls += 1
        return profile

    def _list_snapshots(*, limit: int = 30, include_holdings: bool = True) -> list[dict]:
        snapshot_calls.append(limit)
        return snapshots

    monkeypatch.setattr(detail_module, "get_fund_profile_by_code", _get_profile)
    monkeypatch.setattr(detail_module, "list_portfolio_daily_snapshots", _list_snapshots)
    monkeypatch.setattr(
        detail_module.FundProfileService,
        "_resolve_holding_with_profile",
        lambda _self, current, resolved_profile, **_kwargs: (
            current if resolved_profile is profile else None
        ),
    )
    monkeypatch.setattr(
        detail_module.FundDataService,
        "get_nav_history",
        lambda _self, code, name, **_kwargs: FundNavHistory(
            fund_code=code,
            fund_name=name,
            source="unavailable",
        ),
    )
    monkeypatch.setattr(detail_module, "compute_yesterday_profit", lambda _holding: None)

    detail = detail_module.build_holding_detail([holding], 0)

    assert point_calls == 1
    assert snapshot_calls == [365]
    assert detail.yesterday_profit == 12.34
    assert detail.holding_days == 1
    assert detail.provenance == {
        "yesterday_profit": "snapshot",
        "holding_days": "snapshot",
    }


def test_warm_holding_details_degrades_when_bulk_preload_fails(monkeypatch):
    holding = _holdings()[0]
    saved: list[dict] = []
    _disable_user_context(monkeypatch)
    monkeypatch.setattr(warmup_module, "get_cached_holding_detail", lambda *_args: None)
    monkeypatch.setattr(detail_module, "list_fund_profiles", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(
        detail_module,
        "list_portfolio_daily_snapshots",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db")),
    )
    monkeypatch.setattr(
        warmup_module,
        "build_holding_detail",
        lambda _holdings, index, **_kwargs: type(
            "Detail",
            (),
            {"model_dump": lambda self, mode="json": {"index": index}},
        )(),
    )
    monkeypatch.setattr(
        warmup_module,
        "save_cached_holding_detail",
        lambda _code, _fingerprint, payload: saved.append(payload),
    )

    assert warmup_module.warm_holding_details([holding], user_id=42) == 1
    assert saved == [{"index": 0}]


def test_collect_intraday_queries_loads_profiles_once(monkeypatch):
    calls = 0
    profiles = [
        FundProfile(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            sector_name="shared-sector" if holding.fund_code != "000003" else "other-sector",
        )
        for holding in _holdings()
    ]

    def _list_profiles() -> list[FundProfile]:
        nonlocal calls
        calls += 1
        return profiles

    monkeypatch.setattr(detail_module, "list_fund_profiles", _list_profiles)
    monkeypatch.setattr(
        warmup_module,
        "_resolve_intraday_for_holding",
        lambda _holding, profile: ("index", profile.sector_name) if profile else None,
    )
    monkeypatch.setattr(
        detail_module,
        "get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("point profile query is forbidden")),
    )

    assert warmup_module.collect_intraday_queries(_holdings()) == [
        ("index", "shared-sector"),
        ("index", "other-sector"),
    ]
    assert calls == 1


def test_warm_holdings_cache_shares_profile_context_between_layers(monkeypatch):
    calls = 0
    contexts: list[detail_module.HoldingDetailDataContext] = []

    def _list_profiles() -> list[FundProfile]:
        nonlocal calls
        calls += 1
        return []

    def _warm_intraday(_holdings, *, user_key, data_context):
        data_context.preload_profiles()
        contexts.append(data_context)
        return 0

    def _warm_details(_holdings, *, user_id, portfolio_summary, data_context):
        data_context.preload_profiles()
        contexts.append(data_context)
        return 0

    monkeypatch.setattr(detail_module, "list_fund_profiles", _list_profiles)
    monkeypatch.setattr(warmup_module, "warm_fund_nav_histories", lambda _holdings: 0)
    monkeypatch.setattr(warmup_module, "warm_holdings_intraday", _warm_intraday)
    monkeypatch.setattr(warmup_module, "warm_holding_details", _warm_details)

    assert warmup_module.warm_holdings_cache(_holdings(), user_id=42) == {
        "nav": 0,
        "intraday": 0,
        "detail": 0,
    }
    assert calls == 1
    assert contexts[0] is contexts[1]


def test_warm_holding_details_preloads_primary_sector_rows_once(monkeypatch):
    profiles_calls = 0
    user_rows_calls = 0
    global_rows_calls = 0
    saved: list[dict] = []
    holdings = _holdings()
    profiles = [
        FundProfile(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            yesterday_profit=float(index),
            first_purchase_date=date.today().isoformat(),
        )
        for index, holding in enumerate(holdings, start=1)
    ]
    sector_names = ["人工智能", "半导体", "新能源"]
    rows = [
        {
            "fund_code": holding.fund_code,
            "sector_name": sector_names[index - 1],
            "source": "manual",
            "confidence": 1.0,
        }
        for index, holding in enumerate(holdings, start=1)
    ]

    def _list_profiles() -> list[FundProfile]:
        nonlocal profiles_calls
        profiles_calls += 1
        return profiles

    def _list_user_rows() -> list[dict]:
        nonlocal user_rows_calls
        user_rows_calls += 1
        return rows

    def _list_global_rows(_codes) -> dict[str, dict]:
        nonlocal global_rows_calls
        global_rows_calls += 1
        return {}

    def _forbid_point_lookup(*_args, **_kwargs):
        raise AssertionError("batch warmup must not use primary-sector point lookups")

    _disable_user_context(monkeypatch)
    monkeypatch.setattr(warmup_module, "get_cached_holding_detail", lambda *_args: None)
    monkeypatch.setattr(
        warmup_module,
        "save_cached_holding_detail",
        lambda _code, _fingerprint, payload: saved.append(payload),
    )
    monkeypatch.setattr(detail_module, "list_fund_profiles", _list_profiles)
    monkeypatch.setattr(detail_module, "list_portfolio_daily_snapshots", lambda **_kwargs: [])
    monkeypatch.setattr(detail_module, "get_fund_profile_by_code", _forbid_point_lookup)
    monkeypatch.setattr(primary_sector_module, "list_fund_primary_sectors", _list_user_rows)
    monkeypatch.setattr(
        primary_sector_module,
        "get_fund_primary_sectors_global_by_codes",
        _list_global_rows,
    )
    monkeypatch.setattr(primary_sector_module, "get_fund_primary_sector", _forbid_point_lookup)
    monkeypatch.setattr(primary_sector_module, "load_fresh_global_sector", _forbid_point_lookup)
    monkeypatch.setattr(primary_sector_module, "get_fund_profile_by_code", _forbid_point_lookup)
    monkeypatch.setattr(
        detail_module.FundDataService,
        "get_nav_history",
        lambda _self, code, name, **_kwargs: FundNavHistory(
            fund_code=code,
            fund_name=name,
            source="unavailable",
        ),
    )

    assert warmup_module.warm_holding_details(holdings, user_id=42) == 3
    assert profiles_calls == 1
    assert user_rows_calls == 1
    assert global_rows_calls == 1
    assert [payload["holding"]["sector_name"] for payload in saved] == sector_names


def test_primary_sector_context_only_applies_to_preloaded_codes(monkeypatch):
    profile = FundProfile(
        fund_code="000001",
        fund_name="Alpha",
        aliases=["Alpha alias"],
    )
    alias_holding = Holding(
        fund_code="000000",
        fund_name="Alpha alias",
        holding_amount=100,
    )
    direct_holding = Holding(fund_code="000002", fund_name="Beta", holding_amount=200)
    loaded_codes: list[set[str]] = []

    def _load_context(cls, codes, *, profiles=()):
        loaded_codes.append(set(codes))
        return cls(profiles_by_code={item.fund_code: item for item in profiles})

    monkeypatch.setattr(detail_module, "list_fund_profiles", lambda: [profile])
    monkeypatch.setattr(
        detail_module.PrimarySectorBatchContext,
        "load",
        classmethod(_load_context),
    )
    context = detail_module.HoldingDetailDataContext()
    context.preload_profiles()
    context.preload_primary_sectors([alias_holding, direct_holding])

    assert loaded_codes == [{"000001", "000002"}]
    assert context.primary_sector_context_for(alias_holding, profile) is not None
    assert context.primary_sector_context_for(direct_holding, None) is not None

    new_profile = FundProfile(fund_code="000003", fund_name="Gamma")
    context.remember_profile(new_profile)
    new_holding = Holding(fund_code="000003", fund_name="Gamma", holding_amount=300)
    assert context.primary_sector_context_for(new_holding, new_profile) is None


def test_warmup_state_prunes_entries_older_than_ttl(monkeypatch):
    monkeypatch.setattr(warmup_module, "_WARMUP_STATE_TTL_SECONDS", 10.0)
    monkeypatch.setattr(warmup_module, "_MAX_WARMUP_KEYS", 10)
    monkeypatch.setattr(
        warmup_module,
        "_LAST_WARMUP_AT",
        {"expired": 89.0, "boundary": 90.0, "fresh": 95.0},
    )

    warmup_module._prune_warmup_state(100.0, incoming_key="next")

    assert warmup_module._LAST_WARMUP_AT == {"fresh": 95.0}


def test_warmup_state_reserves_capacity_for_new_key(monkeypatch):
    monkeypatch.setattr(warmup_module, "_WARMUP_STATE_TTL_SECONDS", 1000.0)
    monkeypatch.setattr(warmup_module, "_MAX_WARMUP_KEYS", 2)
    monkeypatch.setattr(
        warmup_module,
        "_LAST_WARMUP_AT",
        {"oldest": 10.0, "newer": 20.0},
    )

    warmup_module._prune_warmup_state(30.0, incoming_key="next")
    warmup_module._LAST_WARMUP_AT["next"] = 30.0

    assert warmup_module._LAST_WARMUP_AT == {"newer": 20.0, "next": 30.0}
