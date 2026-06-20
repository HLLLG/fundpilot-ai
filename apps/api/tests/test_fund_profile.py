from datetime import date, timedelta

from app.config import refresh_settings
from app.models import FundProfile, Holding
from app.services.fund_profile import (
    FundProfileService,
    parse_profile_from_text,
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


def test_save_profile_keeps_existing_first_seen_on_reupload():
    service = FundProfileService()
    service.save_profile(
        FundProfile(fund_code="000003", fund_name="老基金", first_seen_date="2025-01-01")
    )
    again = service.save_profile(FundProfile(fund_code="000003", fund_name="老基金"))
    assert again.first_seen_date == "2025-01-01"

DETAIL_TEXT = """
华夏中证电网设备主题ETF联接A
025856
持有金额
15,075.46
10,645.76
52.76%
持有收益
+401.80
+2.74%
关联板块
中证电网设备▼-0.59%
"""

YANGJIBAO_BOTTOM_LAYOUT = """
华夏中证电网设备主题ETF联接A
025856
持有金额
12,406.59
关联板块
业绩走势
06-03
中证电网设备 +1.59%
关联板块
电网设备
"""


def test_parse_yangjibao_detail_profile_text():
    profile = parse_profile_from_text(DETAIL_TEXT)
    assert profile is not None
    assert profile.fund_code == "025856"
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_name == "电网设备"
    assert profile.holding_amount == 15075.46


def test_parse_detail_multiline_related_board_name_separate():
    text = """
华夏人工智能ETF联接C
008586
关联板块
业绩走势
中证人工智能
+3.27%
"""
    profile = parse_profile_from_text(text)
    assert profile is not None
    assert profile.sector_name == "人工智能"
    assert profile.intraday_index_name == "中证人工智能"


def test_merge_detail_profile_preserves_sector_when_ocr_misses(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    full = parse_profile_from_text(YANGJIBAO_BOTTOM_LAYOUT)
    assert full is not None
    service.save_profile(full)

    partial = parse_profile_from_text(
        "华夏中证电网设备主题ETF联接A\n025856\n持有金额\n12,500.00"
    )
    assert partial is not None
    merged = service.save_profile(partial)
    assert merged.sector_name == "电网设备"
    assert merged.intraday_index_name == "中证电网设备"


def test_resolve_overview_holding_with_saved_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    profile = parse_profile_from_text(DETAIL_TEXT)
    assert profile is not None
    service.save_profile(profile)

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
        parse_profile_from_text("华夏人工智能ETF联接C\n008586\n持有金额\n7,427.01")
    )

    resolved = service.resolve_holding(
        Holding(
            fund_code="000000",
            fund_name="华夏人工智能ETF.",
            holding_amount=7701.83,
        )
    )
    assert resolved.fund_code == "008586"
