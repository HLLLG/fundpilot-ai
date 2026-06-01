from app.models import Holding
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    holding_analysis_payload,
    holding_daily_return_is_estimated,
)


def test_estimated_daily_is_sector_plus_settled_holding_return():
    holding = Holding(
        fund_code="000000",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=2.74,
        sector_return_percent=-0.16,
        holding_return_percent=2.74,
    )

    assert compute_estimated_daily_return_percent(holding) == 2.58
    assert holding_daily_return_is_estimated(holding) is True


def test_actual_daily_return_is_not_reestimated():
    holding = Holding(
        fund_code="000000",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=2.74,
        daily_return_percent=-0.5,
        sector_return_percent=-0.16,
        holding_return_percent=2.74,
    )

    assert compute_estimated_daily_return_percent(holding) == -0.5
    assert holding_daily_return_is_estimated(holding) is False


def test_holding_analysis_payload_includes_estimate_fields():
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=7427.01,
        return_percent=-1.12,
        sector_return_percent=-2.23,
        holding_return_percent=-1.12,
    )
    payload = holding_analysis_payload(holding)

    assert payload["estimated_daily_return_percent"] == -3.35
    assert payload["daily_return_is_estimated"] is True
