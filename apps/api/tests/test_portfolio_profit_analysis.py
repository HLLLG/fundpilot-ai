from datetime import date

from app.models import Holding
from app.services.portfolio_profit_analysis import (
    build_calendar_month,
    build_daily_top5,
    build_daily_trend_series,
    filter_snapshots_by_range,
    summarize_trend_footer,
)


def test_filter_snapshots_by_range_month():
    rows = [
        {"snapshot_date": "2026-06-03", "daily_profit": 10},
        {"snapshot_date": "2026-06-02", "daily_profit": -5},
        {"snapshot_date": "2026-05-30", "daily_profit": 1},
    ]
    filtered = filter_snapshots_by_range(rows, "month", anchor_date=date(2026, 6, 10))
    assert [row["snapshot_date"] for row in filtered] == ["2026-06-02", "2026-06-03"]


def test_build_daily_top5_splits_gainers_and_losers():
    holdings = [
        Holding(fund_code="111111", fund_name="A", holding_amount=1000, daily_profit=12.5),
        Holding(fund_code="222222", fund_name="B", holding_amount=1000, daily_profit=-8.0),
        Holding(fund_code="333333", fund_name="C", holding_amount=1000, daily_profit=3.0),
        Holding(fund_code="444444", fund_name="D", holding_amount=1000, daily_profit=None),
    ]
    result = build_daily_top5(holdings)
    assert result["gainers"][0]["fund_name"] == "A"
    assert result["losers"][0]["fund_name"] == "B"


def test_build_daily_trend_series_accumulates_returns(monkeypatch):
    snapshots = [
        {"snapshot_date": "2026-06-02", "daily_return_percent": 1.0},
        {"snapshot_date": "2026-06-03", "daily_return_percent": -0.5},
    ]

    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.fetch_index_daily_history",
        lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-06-02", "close": 100.0},
                {"date": "2026-06-03", "close": 99.0},
            ]
        },
    )

    series = build_daily_trend_series(snapshots)
    assert series[0]["portfolio_percent"] == 1.0
    assert series[1]["portfolio_percent"] == 0.5


def test_build_calendar_month_marks_holiday():
    snapshots = [{"snapshot_date": "2026-06-03", "daily_profit": -10, "daily_return_percent": -1.0}]
    trade_dates = frozenset({"2026-06-03"})
    calendar = build_calendar_month(
        year=2026,
        month=6,
        snapshots=snapshots,
        trade_dates=trade_dates,
    )
    june_4 = next(day for day in calendar["days"] if day["date"] == "2026-06-04")
    assert june_4["is_holiday"] is True
    assert calendar["month_cumulative_profit"] == -10


def test_summarize_trend_footer_alpha():
    trend = {
        "kind": "intraday",
        "points": [{"portfolio_percent": -1.25, "index_percent": -0.53}],
    }
    footer = summarize_trend_footer(trend, summary_daily_return=-1.25)
    assert footer["alpha_percent"] == -0.72


def test_load_or_build_intraday_curve_refreshes_index_on_cache(monkeypatch):
    from app.services.portfolio_profit_analysis import load_or_build_intraday_curve

    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.get_portfolio_intraday_curve",
        lambda trade_date: [
            {"time": "09:31", "portfolio_percent": 1.0, "index_percent": -0.9},
            {"time": "15:00", "portfolio_percent": 2.0, "index_percent": -0.9},
        ],
    )
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.fetch_sector_intraday",
        lambda *args, **kwargs: (
            [
                {"time": "09:31", "percent": 0.5},
                {"time": "15:00", "percent": 1.01},
            ],
            None,
            "2026-06-14",
            1.01,
        ),
    )
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.build_trading_session",
        lambda: {"session_kind": "trading_day_after_close"},
    )
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.get_effective_trade_date",
        lambda **kwargs: "2026-06-14",
    )

    points, trade_date = load_or_build_intraday_curve([], {})
    assert trade_date == "2026-06-14"
    assert points[-1]["index_percent"] == 1.01
