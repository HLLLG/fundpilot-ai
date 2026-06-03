from app.models import Holding
from app.services.holding_filters import is_test_holding, without_test_holdings


def test_is_test_holding_by_code_and_name():
    assert is_test_holding(
        Holding(fund_code="000001", fund_name="测试基金A", holding_amount=1000, return_percent=0)
    )
    assert is_test_holding(
        Holding(fund_code="015608", fund_name="新基金", holding_amount=0, return_percent=0)
    )
    assert not is_test_holding(
        Holding(fund_code="008586", fund_name="华夏人工智能ETF联接C", holding_amount=100, return_percent=0)
    )


def test_without_test_holdings():
    rows = [
        Holding(fund_code="000001", fund_name="测试基金A", holding_amount=1000, return_percent=0),
        Holding(fund_code="008586", fund_name="华夏人工智能ETF联接C", holding_amount=100, return_percent=0),
    ]
    filtered = without_test_holdings(rows)
    assert len(filtered) == 1
    assert filtered[0].fund_code == "008586"
