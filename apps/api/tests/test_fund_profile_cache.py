"""FundProfileService 应在实例生命周期内缓存 list_profiles。"""

from app.models import FundProfile
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
