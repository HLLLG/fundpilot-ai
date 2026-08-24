"""盘中 OCR 是上一交易日结算，持有收益必须叠加板块估算。"""

from app.models import Holding
from app.services.holding_estimates import (
    build_holding_display_metrics,
    compute_estimated_holding_return_percent,
    compute_holding_profit,
    holding_profit_is_estimated,
    holding_return_needs_session_estimate,
    overlay_official_nav_returns,
    release_stale_official_nav_to_sector,
)
from app.services.portfolio_holdings_service import _fast_overlay_cached_official_nav
from app.services.trading_session import session_blocks_official_nav


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


def _stale_official_nav_holding() -> Holding:
    return Holding(
        fund_code="017787",
        fund_name="万家宏观择时多策略混合C",
        sector_name="煤炭",
        holding_amount=2267.18,
        settled_holding_amount=2267.18,
        holding_return_percent=3.05,
        holding_profit=67.18,
        daily_profit=21.76,
        daily_return_percent=0.96,
        daily_return_percent_source="official_nav",
        sector_return_percent=-1.2,
    )


def test_session_blocks_official_nav_only_during_trading_hours() -> None:
    assert session_blocks_official_nav("trading_day_intraday") is True
    assert session_blocks_official_nav("trading_day_pre_close") is True
    assert session_blocks_official_nav("trading_day_pre_open") is False
    assert session_blocks_official_nav("trading_day_after_close") is False
    assert session_blocks_official_nav("non_trading_day") is False


def test_intraday_releases_stale_official_nav_to_sector() -> None:
    holding = _stale_official_nav_holding()
    released = release_stale_official_nav_to_sector(
        holding,
        session_kind="trading_day_intraday",
    )
    assert released.daily_return_percent_source == "sector_estimate"
    assert released.daily_return_percent == -1.2
    assert released.daily_profit == round(2267.18 * -1.2 / 100, 2)
    assert released.amount_includes_today is False


def test_after_close_keeps_official_nav() -> None:
    holding = _stale_official_nav_holding()
    kept = release_stale_official_nav_to_sector(
        holding,
        session_kind="trading_day_after_close",
    )
    assert kept.daily_return_percent_source == "official_nav"
    assert kept.daily_return_percent == 0.96
    assert kept.daily_profit == 21.76


def test_fast_overlay_strips_official_nav_intraday() -> None:
    holding = _stale_official_nav_holding()
    overlaid = _fast_overlay_cached_official_nav(
        holding,
        "2026-08-24",
        session_kind="trading_day_intraday",
    )
    assert overlaid.daily_return_percent_source == "sector_estimate"
    assert overlaid.daily_return_percent == -1.2


def test_overlay_official_nav_strips_during_intraday(monkeypatch) -> None:
    holding = _stale_official_nav_holding()
    monkeypatch.setattr(
        "app.services.trading_session.build_trading_session",
        lambda: {"session_kind": "trading_day_intraday", "effective_trade_date": "2026-08-24"},
    )
    overlaid = overlay_official_nav_returns([holding])
    assert overlaid[0].daily_return_percent_source == "sector_estimate"
    assert overlaid[0].daily_return_percent == -1.2
