from app.models import FundProfile, Holding
from app.services.portfolio_holdings_service import merge_holdings_with_profiles


def test_merge_overwrites_holding_amount_from_profile():
    snapshot = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8000.0,
            return_percent=-0.49,
            sector_name="中证人工智能",
            sector_return_percent=4.2,
        )
    ]
    profiles = [
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=9100.5,
            holding_return_percent=-0.3,
            holding_profit=-20.0,
            sector_name="中证人工智能",
        )
    ]
    merged = merge_holdings_with_profiles(snapshot, profiles=profiles)
    assert len(merged) == 1
    assert merged[0].holding_amount == 9100.5
    assert merged[0].holding_profit == -20.0
    assert merged[0].sector_return_percent == 4.2


def test_merge_adds_profile_only_fund():
    snapshot = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8000.0,
            return_percent=0,
        )
    ]
    profiles = [
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8000.0,
        ),
        FundProfile(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            holding_amount=5000.0,
            holding_return_percent=-1.0,
            sector_name="半导体",
        ),
    ]
    merged = merge_holdings_with_profiles(snapshot, profiles=profiles)
    assert len(merged) == 2
    codes = {row.fund_code for row in merged}
    assert codes == {"008586", "519674"}
