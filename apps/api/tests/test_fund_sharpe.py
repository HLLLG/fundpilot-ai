from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import FundNavHistory, FundNavPoint
from app.services.discovery_candidate_llm import slim_candidate_for_llm
from app.services.discovery_candidate_pool import (
    _NAV_LOOKBACK_TRADING_DAYS,
    enrich_candidates,
)
from app.services.fund_nav_cache import CANONICAL_NAV_TRADING_DAYS
from app.database import list_fund_risk_metrics_by_codes
from app.services.fund_sharpe import (
    SHARPE_SCHEMA_VERSION,
    attach_alipay_style_sharpes,
    compute_alipay_style_sharpe,
    compute_window_max_drawdown_percent,
    shift_calendar_years,
)


_DECISION_AT = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _point(day: str, nav: float, daily: float | None = None) -> FundNavPoint:
    return FundNavPoint(date=day, nav=nav, daily_return_percent=daily)


def test_lookback_uses_canonical_nav_window() -> None:
    assert _NAV_LOOKBACK_TRADING_DAYS == CANONICAL_NAV_TRADING_DAYS == 800


def test_shift_calendar_years_maps_feb_29_to_feb_28() -> None:
    assert shift_calendar_years(date(2024, 2, 29), 1) == date(2023, 2, 28)
    assert shift_calendar_years(date(2026, 8, 25), 3) == date(2023, 8, 25)


def test_alipay_style_sharpe_matches_hand_calculation() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2025-11-25", 1.04, 4.0),
        _point("2026-02-25", 1.0192, -2.0),
        _point("2026-05-25", 1.059968, 4.0),
        _point("2026-08-25", 1.03876864, -2.0),
    ]

    result = compute_alipay_style_sharpe(
        points,
        years=1,
        as_of=date(2026, 8, 25),
        min_daily_returns=4,
    )

    assert result is not None
    assert result["sample_days"] == 4
    assert result["calendar_days"] == 365
    assert result["period_return_percent"] == 3.8769
    assert result["annualized_return_percent"] == 3.8769
    assert result["annualized_volatility_percent"] == 47.4342
    assert result["sharpe"] == 0.05


def test_insufficient_samples_stay_empty() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2025-11-25", 1.04, 4.0),
        _point("2026-02-25", 1.0192, -2.0),
        _point("2026-05-25", 1.059968, 4.0),
        _point("2026-08-25", 1.03876864, -2.0),
    ]
    row: dict = {}
    attach_alipay_style_sharpes(row, points, as_of=date(2026, 8, 25))

    assert row["sharpe_1y"] is None
    assert row["sharpe_3y"] is None
    assert row["sharpe_research"]["schema_version"] == SHARPE_SCHEMA_VERSION
    assert row["sharpe_research"]["horizons"]["1y"] is None
    assert row["sharpe_research"]["horizons"]["3y"] is None


def test_zero_volatility_is_not_filled_as_zero_sharpe() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2026-02-25", 1.01, 1.0),
        _point("2026-08-25", 1.0201, 1.0),
    ]

    assert (
        compute_alipay_style_sharpe(
            points,
            years=1,
            as_of=date(2026, 8, 25),
            min_daily_returns=2,
        )
        is None
    )


def test_three_year_horizon_stays_empty_when_history_is_short() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2025-11-25", 1.04, 4.0),
        _point("2026-02-25", 1.0192, -2.0),
        _point("2026-05-25", 1.059968, 4.0),
        _point("2026-08-25", 1.03876864, -2.0),
    ]

    assert (
        compute_alipay_style_sharpe(
            points,
            years=3,
            as_of=date(2026, 8, 25),
            min_daily_returns=4,
        )
        is not None
    )
    assert (
        compute_alipay_style_sharpe(
            points,
            years=3,
            as_of=date(2026, 8, 25),
        )
        is None
    )


def test_window_drawdown_matches_hand_calculation() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2025-11-25", 1.20, 20.0),
        _point("2026-02-25", 0.96, -20.0),
        _point("2026-05-25", 1.008, 5.0),
        _point("2026-08-25", 1.0584, 5.0),
    ]

    assert (
        compute_window_max_drawdown_percent(
            points,
            years=1,
            as_of=date(2026, 8, 25),
            min_daily_returns=4,
        )
        == -20.0
    )


def test_window_drawdown_uses_same_daily_return_floor_as_sharpe() -> None:
    start = date(2025, 8, 26)
    points = [
        _point((start + timedelta(days=index)).isoformat(), 1.0 + index * 0.001)
        for index in range(180)
    ]

    assert (
        compute_window_max_drawdown_percent(
            points,
            years=1,
            as_of=date(2026, 8, 25),
            min_daily_returns=180,
        )
        is None
    )
    points.append(_point("2026-08-25", 1.20))
    assert (
        compute_window_max_drawdown_percent(
            points,
            years=1,
            as_of=date(2026, 8, 25),
            min_daily_returns=180,
        )
        is not None
    )


def test_as_of_clips_to_last_nav_and_drops_future_points() -> None:
    points = [
        _point("2025-08-25", 1.00),
        _point("2025-11-25", 1.04, 4.0),
        _point("2026-02-25", 1.0192, -2.0),
        _point("2026-05-25", 1.059968, 4.0),
        _point("2026-08-25", 1.03876864, -2.0),
        _point("2026-08-26", 1.10, 5.9),
    ]
    result = compute_alipay_style_sharpe(
        points,
        years=1,
        as_of=date(2026, 8, 25),
        min_daily_returns=4,
    )

    assert result is not None
    assert result["end_date"] == "2026-08-25"
    assert result["sharpe"] == 0.05


def _history_with_trading_days(
    count: int, *, end: date = date(2026, 8, 25)
) -> FundNavHistory:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    points: list[FundNavPoint] = []
    nav = 1.0
    for index, day in enumerate(days):
        growth = 0.4 if index % 3 else -0.2
        if index:
            nav = round(nav * (1.0 + growth / 100.0), 6)
            points.append(
                FundNavPoint(
                    date=day.isoformat(),
                    nav=nav,
                    daily_return_percent=growth,
                )
            )
        else:
            points.append(FundNavPoint(date=day.isoformat(), nav=nav))
    return FundNavHistory(
        fund_code="020356",
        fund_name="半导体ETF联接A",
        source="test",
        points=points,
        latest_nav=points[-1].nav,
        latest_date=points[-1].date,
    )


def test_enrich_attaches_research_sharpe_and_requests_canonical_nav(
    monkeypatch,
) -> None:
    history = _history_with_trading_days(220)
    captured: dict = {}

    def _snapshot_and_trend(*_args, **kwargs):
        captured.update(kwargs)
        return (
            SimpleNamespace(
                return_1y_percent=25.0,
                max_drawdown_1y_percent=-20.0,
                fund_scale_yi=None,
                management_fee=None,
                fund_type="股票型",
                latest_nav=history.latest_nav,
                nav_date=history.latest_date,
            ),
            history,
        )

    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        _snapshot_and_trend,
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020356": {
                "fund_code": "020356",
                "fund_scale_yi": 3.55,
                "fund_category": "股票型",
                "fund_manager": "测试经理",
                "established_date": "2024-01-23",
                "profile_updated_at": "2026-07-10",
            }
        },
    )

    item = enrich_candidates(
        [
            {
                "fund_code": "020356",
                "fund_name": "半导体ETF联接A",
                "sector_label": "半导体",
                "return_3m_percent": 18.0,
                "return_6m_percent": 35.0,
            }
        ],
        decision_at=_DECISION_AT,
    )[0]

    assert captured["trading_days"] == CANONICAL_NAV_TRADING_DAYS
    assert captured["canonical_backfill"] is True
    assert item["sharpe_1y"] is not None
    assert item["sharpe_3y"] is None
    assert item["sharpe_research"]["schema_version"] == SHARPE_SCHEMA_VERSION
    assert item["quality_gate"]["status"] == "eligible"
    assert "sharpe_1y" not in item["quality_gate"]["missing_fields"]
    assert item["max_drawdown_1y_percent"] is not None
    assert -100.0 <= item["max_drawdown_1y_percent"] <= 0.0
    persisted = list_fund_risk_metrics_by_codes(["020356"])["020356"]
    assert persisted["max_drawdown_1y_percent"] == item["max_drawdown_1y_percent"]
    assert persisted["source"] == "computed_nav"
    assert persisted["schema_version"] == SHARPE_SCHEMA_VERSION


def test_llm_keeps_sharpe_scalars_and_drops_research_blob() -> None:
    projected = slim_candidate_for_llm(
        {
            "fund_code": "000001",
            "sharpe_1y": 0.74,
            "sharpe_3y": 0.49,
            "sharpe_research": {
                "schema_version": SHARPE_SCHEMA_VERSION,
                "horizons": {"1y": {"sharpe": 0.74, "sample_days": 243}},
            },
        },
        sector_change_index={},
        trade_date=None,
    )

    assert projected["sharpe_1y"] == 0.74
    assert projected["sharpe_3y"] == 0.49
    assert "sharpe_research" not in projected
