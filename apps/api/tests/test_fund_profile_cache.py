"""FundProfileService 应在实例生命周期内缓存 list_profiles。"""

from app.models import FundProfile, Holding
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.fund_profile import FundProfileService


def test_list_profiles_cached_per_service_instance(monkeypatch):
    calls = {"n": 0}

    def fake_list():
        calls["n"] += 1
        return [
            FundProfile(
                fund_code="025857",
                fund_name="华夏中证电网设备主题ETF联接C",
                aliases=[],
            )
        ]

    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        fake_list,
    )
    svc = FundProfileService()
    assert svc.list_profiles()
    assert svc.list_profiles()
    assert svc.find_match("华夏中证电网设备主题ETF联接C") is not None
    assert calls["n"] == 1


def test_save_profile_invalidates_cache(monkeypatch):
    calls = {"n": 0}

    def fake_list():
        calls["n"] += 1
        return []

    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        fake_list,
    )
    monkeypatch.setattr(
        "app.services.fund_profile.save_fund_profile",
        lambda profile: profile,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.upsert_primary_sector_from_profile",
        lambda *args, **kwargs: None,
    )

    svc = FundProfileService()
    svc.list_profiles()
    svc.save_profile(
        FundProfile(fund_code="000001", fund_name="测试基金A", aliases=[])
    )
    svc.list_profiles()
    assert calls["n"] == 2


def test_save_overview_profile_upserts_low_trust_primary_sector_source(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_profile.save_fund_profile",
        lambda profile: profile,
    )

    def fake_upsert(_profile, *, source="ocr_detail"):
        captured["source"] = source

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.upsert_primary_sector_from_profile",
        fake_upsert,
    )

    FundProfileService().save_profile(
        FundProfile(
            fund_code="123456",
            fund_name="华夏全球科技先锋混合(QDII)C",
            aliases=[],
            sector_name="电子",
            source="alipay-overview",
        )
    )

    assert captured["source"] == "alipay_overview"


def test_resolve_holdings_loads_profiles_once_without_point_queries(monkeypatch):
    calls = {"list": 0, "get": 0}
    profile = FundProfile(
        fund_code="123456",
        fund_name="Alpha Growth Fund",
        aliases=["Alpha Growth Alias"],
        sector_name="technology",
        intraday_index_name="technology index",
    )

    def fake_list():
        calls["list"] += 1
        return [profile]

    def fake_get(_code):
        calls["get"] += 1
        raise AssertionError("batch resolution must not issue point profile queries")

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", fake_list)
    monkeypatch.setattr("app.services.fund_profile.get_fund_profile_by_code", fake_get)
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *args, **kwargs: None,
    )

    holdings = [
        Holding(
            fund_code="123456",
            fund_name="Alpha Growth Fund",
            holding_amount=100,
            sector_name="technology",
            intraday_index_name="technology index",
        ),
        Holding(
            fund_code="000000",
            fund_name="Alpha Growth Alias",
            holding_amount=200,
            sector_name="technology",
            intraday_index_name="technology index",
        ),
    ]

    resolved = FundProfileService().resolve_holdings(holdings, fetch_benchmark=False)

    assert calls == {"list": 1, "get": 0}
    assert [holding.fund_code for holding in resolved] == ["123456", "123456"]


def test_resolve_holdings_reuses_profile_saved_earlier_in_same_batch(monkeypatch):
    profile = FundProfile(
        fund_code="123456",
        fund_name="Alpha Growth Fund",
        aliases=[],
    )
    saved: list[FundProfile] = []

    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(
            AssertionError("batch resolution must not issue point profile queries")
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *args, **kwargs: PrimarySectorRecord(
            fund_code="123456",
            sector_name="半导体",
            intraday_index_name="半导体指数",
            source="benchmark_index",
        ),
    )

    def fake_save(updated: FundProfile) -> FundProfile:
        saved.append(updated)
        return updated

    monkeypatch.setattr("app.services.fund_profile.save_fund_profile", fake_save)
    holding = Holding(
        fund_code="123456",
        fund_name="Alpha Growth Fund",
        holding_amount=100,
    )

    resolved = FundProfileService().resolve_holdings([holding, holding])

    assert len(saved) == 1
    assert [item.sector_name for item in resolved] == ["半导体", "半导体"]
    assert [item.intraday_index_name for item in resolved] == [
        "半导体指数",
        "半导体指数",
    ]


def test_resolve_holdings_empty_batch_does_not_load_profiles(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("empty batches must not query profiles")),
    )

    assert FundProfileService().resolve_holdings([]) == []
