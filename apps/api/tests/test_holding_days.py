from datetime import date

from app.models import FundProfile, Holding
from app.services.holding_detail_service import (
    _resolve_holding_days,
    resolve_holding_days_for_list,
    resolve_holding_start_date,
)


def _holding(fund_code: str = "015788") -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name="鹏扬中证数字经济主题ETF联接C",
        holding_amount=500,
        settled_holding_amount=500,
    )


def test_later_purchase_date_does_not_override_earlier_first_seen() -> None:
    profile = FundProfile(
        fund_code="015788",
        fund_name="鹏扬中证数字经济主题ETF联接C",
        first_purchase_date="2026-08-14",
        first_seen_date="2026-06-20",
    )
    today = date.fromisoformat("2026-08-18")

    start, source = resolve_holding_start_date(profile, _holding())
    days, days_source = _resolve_holding_days(
        profile,
        _holding(),
        snapshot_loader=lambda: [],
        today=today,
    )

    assert start == date.fromisoformat("2026-06-20")
    assert source == "first_seen"
    assert days == 59
    assert days_source == "first_seen"


def test_ocr_start_beats_recent_imported_purchase() -> None:
    profile = FundProfile(
        fund_code="002610",
        fund_name="博时黄金ETF联接A",
        first_purchase_date="2026-08-14",
        holding_days=40,
        holding_days_as_of="2026-08-10",
    )
    today = date.fromisoformat("2026-08-18")
    days, source = _resolve_holding_days(
        profile,
        _holding("002610"),
        snapshot_loader=lambda: [],
        today=today,
    )
    assert source == "ocr_detail"
    assert days == 48


def test_snapshot_start_used_when_purchase_is_later() -> None:
    profile = FundProfile(
        fund_code="015788",
        fund_name="鹏扬中证数字经济主题ETF联接C",
        first_purchase_date="2026-08-14",
    )
    snapshots = [
        {
            "snapshot_date": "2026-07-01",
            "holdings": [{"fund_code": "015788", "fund_name": "鹏扬中证数字经济主题ETF联接C"}],
        }
    ]
    today = date.fromisoformat("2026-08-18")
    days, source = _resolve_holding_days(
        profile,
        _holding(),
        snapshot_loader=lambda: snapshots,
        today=today,
    )
    assert source == "snapshot"
    assert days == 48


def test_shares_baseline_is_not_a_holding_start() -> None:
    profile = FundProfile(
        fund_code="015788",
        fund_name="鹏扬中证数字经济主题ETF联接C",
        first_seen_date="2026-06-20",
        shares_baseline_date="2026-08-18",
    )
    start, source = resolve_holding_start_date(profile, _holding())
    assert start == date.fromisoformat("2026-06-20")
    assert source == "first_seen"


def test_list_days_ignore_empty_snapshots() -> None:
    profile = FundProfile(
        fund_code="015788",
        fund_name="鹏扬中证数字经济主题ETF联接C",
        first_purchase_date="2026-08-02",
    )
    assert resolve_holding_days_for_list(profile, _holding()) == (
        date.today() - date.fromisoformat("2026-08-02")
    ).days
