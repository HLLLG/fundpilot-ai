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


def test_discovery_guard_avoid_chasing_high_1y():
    rec = DiscoveryRecommendation(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        action="分批买入",
    )
    pool = [
        {
            "fund_code": "519674",
            "fund_name": "银河创新成长",
            "return_1y_percent": 120.0,
            "nav_trend": {"distance_from_high_percent": -2.0},
        }
    ]
    profile = InvestorProfile(avoid_chasing=True)
    guarded, _ = apply_discovery_guards(
        [rec],
        candidate_pool=pool,
        held_codes=set(),
        profile=profile,
        budget_yuan=10000,
        sector_heat=[],
    )
    assert guarded[0].action == "等待回调"


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


def test_discovery_guard_dip_swing_rejects_recent_spike():
    rec = DiscoveryRecommendation(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        action="分批买入",
    )
    pool = [
        {
            "fund_code": "519674",
            "fund_name": "银河创新成长",
            "nav_trend": {"recent_5d_daily_change_percent": [-2.0, 4.5]},
        }
    ]
    guarded, _ = apply_discovery_guards(
        [rec],
        candidate_pool=pool,
        held_codes=set(),
        profile=InvestorProfile(),
        budget_yuan=10000,
        sector_heat=[],
        scan_mode="dip_swing",
    )
    assert guarded[0].action == "建议关注"
