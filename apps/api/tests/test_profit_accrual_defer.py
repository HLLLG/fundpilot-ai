from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models import FundProfile, Holding
from app.services.alipay_holdings_parser import parse_alipay_holdings_page
from app.services.fund_profile import FundProfileService, merge_holding_into_profile
from app.services.holding_estimates import (
    apply_sector_daily_estimates,
    compute_holding_profit,
    enrich_holding_estimates,
    sum_daily_profit,
)
from app.services.profit_accrual_defer import (
    is_profit_accrual_deferred,
    ocr_signals_pending_profit_accrual,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_ocr_detects_pending_profit_from_user_fixture():
    text = (FIXTURES / "alipay_overview_holdings_4_user_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_alipay_holdings_page(text)

    avic = next(item for item in holdings if "中航机遇" in item.fund_name)
    grid = next(item for item in holdings if "电网设备" in item.fund_name)
    ai = next(item for item in holdings if "人工智能指数" in item.fund_name)

    assert ocr_signals_pending_profit_accrual(avic) is False
    assert ocr_signals_pending_profit_accrual(grid) is True
    assert ocr_signals_pending_profit_accrual(ai) is True


def test_apply_sector_daily_estimates_respects_deferred_profile(monkeypatch):
    trade_date = "2026-06-23"
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: trade_date,
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        profit_accrual_deferred_until=trade_date,
    )
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        lambda holding: profile,
    )

    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        holding_amount=2000,
        return_percent=0,
        holding_return_percent=0,
        holding_profit=0,
        yesterday_profit=0,
        sector_return_percent=-2.85,
    )
    estimated = apply_sector_daily_estimates(holding)

    assert estimated.daily_profit == 0.0
    assert estimated.daily_return_percent == 0.0
    assert estimated.daily_return_percent_source == "pending_accrual"
    assert estimated.sector_return_percent == -2.85


def test_deferred_expires_after_trade_date(monkeypatch):
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        profit_accrual_deferred_until="2026-06-23",
    )
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: "2026-06-24",
    )
    assert is_profit_accrual_deferred(profile) is False


def test_sync_profile_stamps_defer_for_pending_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: "2026-06-23",
    )

    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        holding_amount=2000,
        return_percent=0,
        holding_return_percent=0,
        holding_profit=0,
        yesterday_profit=0,
    )
    service = FundProfileService()
    service.sync_profiles_from_holdings([holding])

    from app.database import get_fund_profile_by_code

    saved = get_fund_profile_by_code("025856")
    assert saved is not None
    assert saved.profit_accrual_deferred_until == "2026-06-23"


def test_enrich_holding_keeps_zero_profit_when_deferred(monkeypatch):
    trade_date = "2026-06-23"
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: trade_date,
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        profit_accrual_deferred_until=trade_date,
    )
    monkeypatch.setattr("app.database.get_fund_profile_by_code", lambda code: profile)
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        lambda holding: profile,
    )

    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        holding_amount=2000,
        return_percent=0,
        holding_return_percent=0,
        holding_profit=0,
        yesterday_profit=0,
        sector_return_percent=-2.85,
    )
    enriched = enrich_holding_estimates(holding)

    assert enriched.daily_profit == 0.0
    assert compute_holding_profit(enriched) == 0.0


def test_overview_pipeline_mixed_defer_and_sector_sum(monkeypatch):
    from app.services.overview_pipeline import process_overview_holdings

    trade_date = "2026-06-23"
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda: trade_date,
    )

    deferred_profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接C",
        profit_accrual_deferred_until=trade_date,
    )
    active_profile = FundProfile(
        fund_code="001234",
        fund_name="中航机遇领航混合C",
        profit_accrual_deferred_until=None,
    )

    def _profile(code):
        if code == "025856":
            return deferred_profile
        if code == "001234":
            return active_profile
        return None

    monkeypatch.setattr("app.database.get_fund_profile_by_code", _profile)
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        lambda holding: _profile(holding.fund_code),
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.bootstrap_holding_baselines",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.sync_holding_amounts_from_shares",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.overlay_official_nav_returns",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.refresh_holdings_sector_quotes",
        lambda holdings, force_refresh=True: {
            "ok": True,
            "message": "ok",
            "holdings": [
                holding.model_copy(
                    update={"sector_return_percent": -2.85 if holding.fund_code == "025856" else 0.19}
                ).model_dump()
                for holding in holdings
            ],
            "items": [],
            "summary": {"matched": 2, "unresolved": 0, "needs_mapping": 0},
        },
    )

    holdings = [
        Holding(
            fund_code="001234",
            fund_name="中航机遇领航混合C",
            holding_amount=10018.60,
            return_percent=0.19,
            holding_return_percent=0.19,
            holding_profit=18.60,
            yesterday_profit=18.60,
        ),
        Holding(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接C",
            holding_amount=2000,
            return_percent=0,
            holding_return_percent=0,
            holding_profit=0,
            yesterday_profit=0,
        ),
    ]
    result, _sector, _summary = process_overview_holdings(holdings)

    deferred = next(item for item in result if item.fund_code == "025856")
    active = next(item for item in result if item.fund_code == "001234")

    assert deferred.daily_profit == 0.0
    assert deferred.daily_return_percent_source == "pending_accrual"
    assert active.daily_profit == pytest.approx(19.04, abs=0.1)
    assert sum_daily_profit(result) == pytest.approx(active.daily_profit or 0, abs=0.1)


def test_merge_profile_clears_defer_when_ocr_shows_profit():
    profile = FundProfile(
        fund_code="001234",
        fund_name="中航机遇领航混合C",
        profit_accrual_deferred_until="2026-06-23",
    )
    holding = Holding(
        fund_code="001234",
        fund_name="中航机遇领航混合C",
        holding_amount=10018.60,
        return_percent=0.19,
        holding_return_percent=0.19,
        holding_profit=18.60,
        yesterday_profit=18.60,
    )
    merged = merge_holding_into_profile(profile, holding)
    assert merged.profit_accrual_deferred_until is None
