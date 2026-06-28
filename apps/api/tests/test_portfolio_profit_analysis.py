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
    assert june_4["daily_profit"] == 0.0
    assert june_4["daily_return_percent"] == 0.0
    assert calendar["month_cumulative_profit"] == -10


def test_build_calendar_month_weekend_profit_is_zero_not_carried():
    snapshots = [
        {
            "snapshot_date": "2026-06-20",
            "daily_profit": 640.79,
            "daily_return_percent": 2.5,
        }
    ]
    trade_dates = frozenset({"2026-06-19"})
    calendar = build_calendar_month(
        year=2026,
        month=6,
        snapshots=snapshots,
        trade_dates=trade_dates,
    )
    saturday = next(day for day in calendar["days"] if day["date"] == "2026-06-20")
    sunday = next(day for day in calendar["days"] if day["date"] == "2026-06-21")
    assert saturday["is_trading_day"] is False
    assert saturday["daily_profit"] == 0.0
    assert sunday["daily_profit"] == 0.0
    assert calendar["month_cumulative_profit"] == 0.0


def test_build_calendar_month_today_pending_until_official_nav(monkeypatch):
    today = date.today()
    today_key = today.isoformat()
    snapshots = [
        {
            "snapshot_date": today_key,
            "daily_profit": -629.87,
            "daily_return_percent": -1.2,
        }
    ]
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=1437.88,
            daily_profit=-28.88,
            daily_return_percent=-2.01,
            daily_return_percent_source="sector_estimate",
        )
    ]
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.get_trade_date_set",
        lambda: frozenset({today_key}),
    )
    calendar = build_calendar_month(
        year=today.year,
        month=today.month,
        snapshots=snapshots,
        trade_dates=frozenset({today_key}),
        holdings=holdings,
    )
    today_day = next(day for day in calendar["days"] if day["date"] == today_key)
    assert today_day["is_pending_update"] is True
    assert today_day["daily_profit"] is None
    assert today_day not in [
        day
        for day in calendar["days"]
        if day.get("daily_profit") == -629.87
    ]


def test_build_calendar_month_today_shows_official_nav_profit(monkeypatch):
    today = date.today()
    today_key = today.isoformat()
    snapshots = [
        {
            "snapshot_date": today_key,
            "daily_profit": -629.87,
            "daily_return_percent": -1.2,
        }
    ]
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=1437.88,
            settled_holding_amount=1400.0,
            daily_profit=-18.5,
            daily_return_percent=-1.3,
            daily_return_percent_source="official_nav",
        )
    ]
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.get_trade_date_set",
        lambda: frozenset({today_key}),
    )
    calendar = build_calendar_month(
        year=today.year,
        month=today.month,
        snapshots=snapshots,
        trade_dates=frozenset({today_key}),
        holdings=holdings,
    )
    today_day = next(day for day in calendar["days"] if day["date"] == today_key)
    assert today_day["is_pending_update"] is False
    assert today_day["daily_profit"] is not None
    assert today_day["daily_profit"] != -629.87


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
        "app.services.portfolio_profit_analysis.get_portfolio_intraday_curve_entry",
        lambda trade_date: {
            "points": [
                {"time": "09:31", "portfolio_percent": 1.0, "index_percent": None},
                {"time": "15:00", "portfolio_percent": 2.0, "index_percent": None},
            ],
            "holdings_fingerprint": "0000000000000000",
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis._holdings_intraday_fingerprint",
        lambda *_args, **_kwargs: "0000000000000000",
    )
    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis._fetch_cached_index_intraday",
        lambda: [
            {"time": "09:31", "percent": 0.5},
            {"time": "15:00", "percent": 1.01},
        ],
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


def test_load_or_build_intraday_curve_cache_only_skips_build(monkeypatch):
    from app.services.portfolio_profit_analysis import load_or_build_intraday_curve

    monkeypatch.setattr(
        "app.services.portfolio_profit_analysis.get_portfolio_intraday_curve_entry",
        lambda trade_date: None,
    )
    blend = {"called": False}

    def _blend(*_args, **_kwargs):
        blend["called"] = True
        return []

    monkeypatch.setattr("app.services.portfolio_profit_analysis._blend_portfolio_rows", _blend)
    points, _ = load_or_build_intraday_curve([], {}, cache_only=True)
    assert points == []
    assert blend["called"] is False


def test_dashboard_summary_uses_live_holdings_for_today(monkeypatch):
    from app.models import Holding, PortfolioSummary
    from app.services.portfolio_snapshot import build_dashboard_payload

    live = [
        Holding(
            fund_code="000001",
            fund_name="测试A",
            holding_amount=10000,
            daily_profit=-100.0,
            daily_return_percent=-1.0,
            return_percent=0,
        ),
        Holding(
            fund_code="000002",
            fund_name="测试B",
            holding_amount=5000,
            daily_profit=-50.0,
            daily_return_percent=-1.0,
            return_percent=0,
        ),
    ]
    summary = PortfolioSummary(
        total_assets=15000,
        daily_profit=999.0,
        daily_return_percent=9.99,
        holding_count=2,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.load_dashboard_holdings",
        lambda: (live, "snapshot", "2026-06-28", None),
    )
    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_profit_trend",
        lambda **kwargs: {"kind": "intraday", "trade_date": "2026-06-28", "points": []},
    )
    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_calendar_month",
        lambda **kwargs: {"year": 2026, "month": 6, "days": []},
    )
    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_risk_metrics_payload",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.portfolio_snapshot.list_portfolio_daily_snapshots",
        lambda limit=400, include_holdings=True: [],
    )
    monkeypatch.setattr(
        "app.services.portfolio_snapshot.get_most_recent_portfolio_snapshot",
        lambda: None,
    )

    payload = build_dashboard_payload(
        summary=summary,
        profiles=[],
        profit_range="today",
        calendar_year=2026,
        calendar_month=6,
    )
    assert payload["summary"]["daily_profit"] == -150.0
    assert payload["summary"]["daily_return_percent"] != 9.99
