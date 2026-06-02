from app.models import Holding
from app.services.penetration_daily_allocator import allocate_penetration_daily_profit


def test_allocate_by_sector_contribution_sums_to_account_daily():
    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能",
            holding_amount=8050.12,
            return_percent=-3.14,
            sector_return_percent=2.87,
        ),
        Holding(
            fund_code="015945",
            fund_name="易方达国防",
            holding_amount=1192.64,
            return_percent=-7.14,
            sector_return_percent=0.51,
        ),
        Holding(
            fund_code="025856",
            fund_name="电网设备",
            holding_amount=15075.46,
            return_percent=2.74,
            sector_return_percent=0.49,
        ),
    ]

    updated = allocate_penetration_daily_profit(holdings, 369.84)

    total = round(sum(item.daily_profit or 0 for item in updated), 2)
    assert total == 369.84
    assert all(item.daily_profit is not None for item in updated)
    assert all(item.daily_return_percent is not None for item in updated)
    # 板块涨幅更大的 AI 主题应分到更多当日收益
    ai = next(item for item in updated if item.fund_code == "008586")
    defense = next(item for item in updated if item.fund_code == "015945")
    assert (ai.daily_profit or 0) > (defense.daily_profit or 0)


def test_allocate_falls_back_to_amount_weights_without_sector():
    holdings = [
        Holding(
            fund_code="000001",
            fund_name="A",
            holding_amount=3000,
            return_percent=1,
        ),
        Holding(
            fund_code="000002",
            fund_name="B",
            holding_amount=7000,
            return_percent=1,
        ),
    ]

    updated = allocate_penetration_daily_profit(holdings, 100.0)

    assert round(sum(item.daily_profit or 0 for item in updated), 2) == 100.0
    b = next(item for item in updated if item.fund_code == "000002")
    a = next(item for item in updated if item.fund_code == "000001")
    assert (b.daily_profit or 0) > (a.daily_profit or 0)
