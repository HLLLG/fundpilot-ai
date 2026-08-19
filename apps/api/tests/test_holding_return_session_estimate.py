"""盘中 OCR 是上一交易日结算，持有收益必须叠加板块估算。"""

from app.models import Holding
from app.services.holding_estimates import (
    build_holding_display_metrics,
    compute_estimated_holding_return_percent,
    compute_holding_profit,
    holding_profit_is_estimated,
    holding_return_needs_session_estimate,
)


def _ocr_holding() -> Holding:
    return Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=10_000.0,
        holding_return_percent=10.0,
        holding_profit=909.09,
        sector_return_percent=3.0,
        daily_return_percent_source="sector_estimate",
    )


def test_intraday_ocr_adds_sector_estimate() -> None:
    holding = _ocr_holding()
    assert holding_return_needs_session_estimate(holding, session_kind="trading_day_intraday")
    assert compute_estimated_holding_return_percent(
        holding,
        session_kind="trading_day_intraday",
    ) == 13.0
    assert compute_holding_profit(holding, session_kind="trading_day_intraday") == 1209.09
    assert holding_profit_is_estimated(holding, session_kind="trading_day_intraday") is True


def test_after_close_before_nav_still_adds_close_estimate() -> None:
    holding = _ocr_holding()
    metrics = build_holding_display_metrics(holding, session_kind="trading_day_after_close")
    assert metrics["estimated_holding_return_percent"] == 13.0
    assert metrics["estimated_holding_profit"] == 1209.09
    assert metrics["holding_return_is_estimated"] is True


def test_official_nav_does_not_add_sector() -> None:
    holding = _ocr_holding().model_copy(
        update={
            "daily_return_percent": 3.0,
            "daily_return_percent_source": "official_nav",
        }
    )
    metrics = build_holding_display_metrics(holding, session_kind="trading_day_after_close")
    assert metrics["estimated_holding_return_percent"] == 10.0
    assert metrics["estimated_holding_profit"] == 909.09
    assert metrics["holding_return_is_estimated"] is False


def test_pre_open_does_not_add_previous_close_sector() -> None:
    holding = _ocr_holding()
    metrics = build_holding_display_metrics(holding, session_kind="trading_day_pre_open")
    assert metrics["estimated_holding_return_percent"] == 10.0
    assert metrics["estimated_holding_profit"] == 909.09
    assert metrics["holding_return_is_estimated"] is False


def test_non_trading_day_does_not_add_previous_close_sector() -> None:
    holding = _ocr_holding()
    metrics = build_holding_display_metrics(holding, session_kind="non_trading_day")
    assert metrics["estimated_holding_return_percent"] == 10.0
    assert metrics["estimated_holding_profit"] == 909.09
    assert metrics["holding_return_is_estimated"] is False
