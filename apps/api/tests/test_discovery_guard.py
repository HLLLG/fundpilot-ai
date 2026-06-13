from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_guard import apply_discovery_guards


def test_discovery_guard_rejects_pool_outsider():
    rec = DiscoveryRecommendation(
        fund_code="999999",
        fund_name="假基金",
        sector_name="半导体",
        action="分批买入",
    )
    pool = [{"fund_code": "519674", "fund_name": "银河创新成长"}]
    guarded, caveats = apply_discovery_guards(
        [rec],
        candidate_pool=pool,
        held_codes=set(),
        profile=InvestorProfile(),
        budget_yuan=10000,
        sector_heat=[],
    )
    assert guarded == []
    assert any("池外" in line for line in caveats)


def test_discovery_guard_avoid_chasing():
    rec = DiscoveryRecommendation(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        action="分批买入",
    )
    pool = [{"fund_code": "519674", "fund_name": "银河创新成长"}]
    profile = InvestorProfile(avoid_chasing=True)
    guarded, _ = apply_discovery_guards(
        [rec],
        candidate_pool=pool,
        held_codes=set(),
        profile=profile,
        budget_yuan=10000,
        sector_heat=[{"sector_label": "半导体", "change_1d_percent": 5.0}],
    )
    assert guarded[0].action == "等待回调"
