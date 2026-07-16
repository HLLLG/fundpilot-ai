from __future__ import annotations

from datetime import date

import pytest

from app.services import portfolio_profit_analysis as service


def _snapshot(day: str, daily_return: float) -> dict:
    return {
        "snapshot_date": day,
        "daily_return_percent": daily_return,
    }


def test_filter_snapshots_uses_calendar_week_and_excludes_future_rows() -> None:
    rows = [
        _snapshot("2026-07-17", 9.0),
        _snapshot("2026-07-16", 1.0),
        _snapshot("2026-07-14", 2.0),
        _snapshot("2026-07-12", 3.0),
        _snapshot("2026-06-17", 4.0),
    ]

    filtered = service.filter_snapshots_by_range(
        rows,
        "week",
        anchor_date=date(2026, 7, 16),
    )

    assert [row["snapshot_date"] for row in filtered] == ["2026-07-14", "2026-07-16"]


def test_filter_snapshots_uses_calendar_month_and_year_boundaries() -> None:
    rows = [
        _snapshot("2026-07-16", 1.0),
        _snapshot("2026-07-01", 2.0),
        _snapshot("2026-06-30", 3.0),
        _snapshot("2025-12-31", 4.0),
    ]
    anchor = date(2026, 7, 16)

    assert [
        row["snapshot_date"]
        for row in service.filter_snapshots_by_range(rows, "month", anchor_date=anchor)
    ] == ["2026-07-01", "2026-07-16"]
    assert [
        row["snapshot_date"]
        for row in service.filter_snapshots_by_range(rows, "year", anchor_date=anchor)
    ] == ["2026-06-30", "2026-07-01", "2026-07-16"]


def test_daily_trend_compounds_portfolio_and_index_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "fetch_index_daily_history",
        lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-07-13", "close": 100.0},
                {"date": "2026-07-14", "close": 110.0},
                {"date": "2026-07-15", "close": 99.0},
            ]
        },
    )

    series = service.build_daily_trend_series(
        [_snapshot("2026-07-14", 10.0), _snapshot("2026-07-15", -10.0)]
    )

    assert series[-1]["portfolio_percent"] == pytest.approx(-1.0)
    assert series[-1]["index_percent"] == pytest.approx(-1.0)


def test_empty_daily_range_does_not_fall_back_to_today_return() -> None:
    footer = service.summarize_trend_footer(
        {"kind": "daily", "points": []},
        summary_daily_return=2.5,
    )

    assert footer == {
        "portfolio_return_percent": None,
        "index_return_percent": None,
        "alpha_percent": None,
    }


def test_calendar_month_uses_the_same_compound_return_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "fetch_index_daily_history",
        lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-07-13", "close": 100.0},
                {"date": "2026-07-14", "close": 110.0},
                {"date": "2026-07-15", "close": 99.0},
            ]
        },
    )

    calendar = service.build_calendar_month(
        year=2026,
        month=7,
        snapshots=[_snapshot("2026-07-14", 10.0), _snapshot("2026-07-15", -10.0)],
        trade_dates=frozenset({"2026-07-14", "2026-07-15"}),
    )

    assert calendar["month_cumulative_return_percent"] == pytest.approx(-1.0)
    assert calendar["month_index_return_percent"] == pytest.approx(-1.0)
