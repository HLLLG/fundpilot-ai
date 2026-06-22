from datetime import date

from app.config import refresh_settings
from app.models import FundProfile, Holding
from app.services.fund_profile import (
    FundProfileService,
    resolve_first_seen_anchor,
)


def test_resolve_first_seen_anchor_prefers_purchase_date():
    profile = FundProfile(fund_code="000001", fund_name="A", first_purchase_date="2020-01-01")
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2020-01-01"


def test_resolve_first_seen_anchor_backdates_from_ocr_holding_days():
    profile = FundProfile(fund_code="000001", fund_name="A", holding_days=30)
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2026-05-21"


def test_resolve_first_seen_anchor_defaults_to_today():
    profile = FundProfile(fund_code="000001", fund_name="A")
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2026-06-20"


def test_save_profile_stamps_first_seen_for_new_profile():
    service = FundProfileService()
    saved = service.save_profile(FundProfile(fund_code="000002", fund_name="新基金"))
    assert saved.first_seen_date == date.today().isoformat()


def test_sync_profiles_from_holdings_stamps_first_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()
    service = FundProfileService()
    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8789.79,
            return_percent=6.88,
            holding_return_percent=6.88,
        )
    ]
    result = service.sync_profiles_from_holdings(holdings)
    assert result.created == 1
    saved = service._find_profile_for_holding(holdings[0])
    assert saved is not None
    assert saved.first_seen_date == date.today().isoformat()


def test_save_profile_keeps_existing_first_seen_on_reupload():
    service = FundProfileService()
    service.save_profile(
        FundProfile(fund_code="000003", fund_name="老基金", first_seen_date="2025-01-01")
    )
    again = service.save_profile(FundProfile(fund_code="000003", fund_name="老基金"))
    assert again.first_seen_date == "2025-01-01"


def test_resolve_overview_holding_with_saved_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            holding_amount=15075.46,
            holding_shares=10645.76,
            position_percent=52.76,
            sector_name="电网设备",
            intraday_index_name="中证电网设备",
        )
    )

    holding = Holding(
        fund_code="000000",
        fund_name="华夏中证电网设备...",
        holding_amount=15161.69,
        return_percent=0.87,
    )
    resolved = service.resolve_holding(holding)
    assert resolved.fund_code == "025856"
    assert resolved.fund_name == "华夏中证电网设备主题ETF联接A"


def test_resolve_truncated_overview_names_with_profile_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=7427.01,
        )
    )

    resolved = service.resolve_holding(
        Holding(
            fund_code="000000",
            fund_name="华夏人工智能ETF.",
            holding_amount=7701.83,
        )
    )
    assert resolved.fund_code == "008586"
