from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.models import FundNavPoint
from app.services.fund_scale import (
    NAV_TIMES_LATEST_SHARES_BASIS,
    QUARTERLY_NET_ASSETS_BASIS,
    apply_quarterly_net_assets_to_row,
    attach_quarterly_net_assets,
    latest_disclosed_quarter_end,
    latest_nav_from_points,
    latest_nav_times_shares,
    nav_on_or_before,
    profile_has_scale_input,
    quarterly_net_assets_from_points,
    quarterly_net_assets_yi,
)


def test_latest_disclosed_quarter_end_uses_filing_calendar() -> None:
    assert latest_disclosed_quarter_end(date(2026, 7, 14)) == date(2026, 3, 31)
    assert latest_disclosed_quarter_end(date(2026, 8, 15)) == date(2026, 6, 30)
    assert latest_disclosed_quarter_end(date(2026, 8, 26)) == date(2026, 6, 30)
    assert latest_disclosed_quarter_end(date(2026, 10, 20)) == date(2026, 6, 30)
    assert latest_disclosed_quarter_end(date(2026, 10, 27)) == date(2026, 9, 30)
    assert latest_disclosed_quarter_end(date(2027, 3, 30)) == date(2026, 9, 30)
    assert latest_disclosed_quarter_end(date(2027, 3, 31)) == date(2026, 12, 31)


def test_nav_on_or_before_accepts_same_day_and_holiday_gap() -> None:
    points = [
        FundNavPoint(date="2026-06-26", nav=1.10),
        FundNavPoint(date="2026-06-30", nav=1.637),
        FundNavPoint(date="2026-08-25", nav=1.287),
    ]
    assert nav_on_or_before(points, date(2026, 6, 30)) == (date(2026, 6, 30), 1.637)

    holiday_points = [
        FundNavPoint(date="2026-06-26", nav=1.10),
        FundNavPoint(date="2026-08-25", nav=1.287),
    ]
    assert nav_on_or_before(holiday_points, date(2026, 6, 30)) == (
        date(2026, 6, 26),
        1.10,
    )


def test_quarterly_net_assets_uses_report_nav_not_latest() -> None:
    points = [
        FundNavPoint(date="2026-06-30", nav=1.637),
        FundNavPoint(date="2026-08-25", nav=1.287),
    ]
    payload = quarterly_net_assets_from_points(
        24.06,
        points,
        as_of=date(2026, 8, 26),
    )
    assert payload is not None
    assert payload["fund_scale_basis"] == QUARTERLY_NET_ASSETS_BASIS
    assert payload["fund_scale_yi"] == 39.3862
    assert payload["fund_scale_report_date"] == "2026-06-30"
    assert quarterly_net_assets_yi(24.06, 1.287) == 30.9652
    assert latest_nav_from_points(points) == (date(2026, 8, 25), 1.287)
    assert latest_nav_times_shares(24.06, 1.287, nav_date=date(2026, 8, 25)) == {
        "fund_scale_yi": 30.9652,
        "fund_scale_basis": NAV_TIMES_LATEST_SHARES_BASIS,
        "fund_scale_as_of": "2026-08-25",
    }


def test_apply_falls_back_to_latest_nav_when_report_nav_missing() -> None:
    row = {
        "fund_code": "000001",
        "fund_shares_yi": 24.06,
        "fund_scale_yi": 8.5,
        "fund_scale_basis": "legacy",
    }
    apply_quarterly_net_assets_to_row(
        row,
        points=[FundNavPoint(date="2026-08-25", nav=1.287)],
        as_of=date(2026, 8, 26),
    )
    assert row["fund_scale_yi"] == 30.9652
    assert row["fund_scale_basis"] == NAV_TIMES_LATEST_SHARES_BASIS


def test_apply_does_not_downgrade_existing_quarterly_scale() -> None:
    row = {
        "fund_code": "000001",
        "fund_shares_yi": 24.06,
        "fund_scale_yi": 39.3862,
        "fund_scale_basis": QUARTERLY_NET_ASSETS_BASIS,
        "latest_nav": 1.287,
    }
    apply_quarterly_net_assets_to_row(
        row,
        points=[FundNavPoint(date="2026-08-25", nav=1.287)],
        as_of=date(2026, 8, 26),
    )
    assert row["fund_scale_yi"] == 39.3862
    assert row["fund_scale_basis"] == QUARTERLY_NET_ASSETS_BASIS


def test_attach_uses_row_latest_nav_when_report_nav_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_cached_fund_nav",
        lambda *_args, **_kwargs: None,
    )
    rows = [
        {
            "fund_code": "000001",
            "fund_shares_yi": 24.06,
            "latest_nav": 1.287,
            "profile_updated_at": "2026-08-25",
            "fund_scale_yi": None,
            "fund_scale_basis": None,
        }
    ]
    attach_quarterly_net_assets(rows, as_of=date(2026, 8, 26))
    assert rows[0]["fund_scale_yi"] == 30.9652
    assert rows[0]["fund_scale_basis"] == NAV_TIMES_LATEST_SHARES_BASIS
    assert rows[0]["fund_scale_as_of"] == "2026-08-25"


def test_attach_clears_scale_without_report_or_latest_nav(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_cached_fund_nav",
        lambda *_args, **_kwargs: None,
    )
    rows = [
        {
            "fund_code": "000001",
            "fund_shares_yi": 24.06,
            "fund_scale_yi": 30.97,
            "fund_scale_basis": NAV_TIMES_LATEST_SHARES_BASIS,
        }
    ]
    attach_quarterly_net_assets(rows, as_of=date(2026, 8, 26))
    assert rows[0]["fund_scale_yi"] is None
    assert rows[0]["fund_scale_basis"] is None


def test_attach_reads_cached_nav_series(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_cached_fund_nav",
        lambda *_args, **_kwargs: SimpleNamespace(
            points=[FundNavPoint(date="2026-06-30", nav=2.0)]
        ),
    )
    rows = [{"fund_code": "000311", "fund_shares_yi": 10.0, "latest_nav": 1.1}]
    attach_quarterly_net_assets(rows, as_of=date(2026, 8, 26))
    assert rows[0]["fund_scale_yi"] == 20.0
    assert rows[0]["fund_scale_basis"] == QUARTERLY_NET_ASSETS_BASIS


def test_profile_shares_count_as_scale_input() -> None:
    assert profile_has_scale_input({"fund_shares_yi": 2.0}) is True
    assert profile_has_scale_input({"fund_scale_yi": 3.2}) is True
    assert profile_has_scale_input({"fund_shares_yi": 0}) is False
    assert profile_has_scale_input({}) is False
