from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.services.fund_factor_nav import factor_input_from_points


def _points_ending(last_day: date, count: int = 250) -> list[SimpleNamespace]:
    days: list[date] = []
    cursor = last_day
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return [
        SimpleNamespace(
            date=day.isoformat(),
            nav=1.0 + index * 0.001,
            daily_return_percent=None,
        )
        for index, day in enumerate(days)
    ]


def test_domestic_target_accepts_previous_trading_day_nav() -> None:
    row = factor_input_from_points(
        "000001",
        "普通基金",
        _points_ending(date(2026, 7, 10)),
        require_complete=True,
        effective_trade_date="2026-07-13",
        fund_type="gp",
        observed_at="2026-07-13T06:00:00+00:00",
    )

    assert row.feature_freshness == "fresh"
    assert row.feature_as_of == "2026-07-10"
    assert row.nav_age_trading_days == 1
    assert row.return_coverage == 1.0
    assert row.return_6m_percent is not None
    assert row.typed_feature_meta["feature_as_of"] == "2026-07-10"


def test_qdii_accepts_two_day_lag_but_domestic_fails_closed() -> None:
    points = _points_ending(date(2026, 7, 9))
    qdii = factor_input_from_points(
        "000002",
        "海外 QDII",
        points,
        require_complete=True,
        effective_trade_date="2026-07-13",
        fund_type="qdii",
    )
    domestic = factor_input_from_points(
        "000003",
        "普通基金",
        points,
        require_complete=True,
        effective_trade_date="2026-07-13",
        fund_type="gp",
    )

    assert qdii.feature_freshness == "fresh"
    assert qdii.nav_age_trading_days == 2
    assert qdii.return_6m_percent is not None
    assert domestic.feature_freshness == "insufficient"
    assert domestic.nav_age_trading_days == 2
    assert domestic.return_3m_percent is None
    assert domestic.typed_feature_values == {}
