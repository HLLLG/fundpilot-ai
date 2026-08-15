from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.models import FundNavHistory, FundNavPoint, Holding
from app.services.trading_session import CN_TZ, build_trading_session, needed_official_nav_date
from app.services import shared_holding_market_cache as cache_mod
from app.services.fund_data import FundDataService


HOLDING = Holding(
    fund_code="008586",
    fund_name="华夏人工智能ETF联接C",
    holding_amount=1000,
    return_percent=0,
)


def _history(*, latest_date: str) -> FundNavHistory:
    return FundNavHistory(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        source="akshare",
        points=[FundNavPoint(date=latest_date, nav=1.23)],
        latest_nav=1.23,
        latest_date=latest_date,
    )


def test_needed_nav_date_weekend_is_last_trade_date() -> None:
    session = build_trading_session(datetime(2026, 6, 13, 12, 0, tzinfo=CN_TZ))
    assert session["session_kind"] == "non_trading_day"
    assert needed_official_nav_date(session) == "2026-06-12"


def test_needed_nav_date_intraday_is_previous_close() -> None:
    session = build_trading_session(datetime(2026, 6, 8, 10, 0, tzinfo=CN_TZ))
    assert session["session_kind"] == "trading_day_intraday"
    assert session["is_continuous_trading"] is True
    assert needed_official_nav_date(session) == "2026-06-05"


def test_needed_nav_date_lunch_does_not_wait_for_today() -> None:
    session = build_trading_session(datetime(2026, 6, 8, 12, 0, tzinfo=CN_TZ))
    assert session["market_phase"] == "lunch_break"
    assert session["is_continuous_trading"] is False
    assert needed_official_nav_date(session) == "2026-06-05"


def test_needed_nav_date_after_close_is_today() -> None:
    session = build_trading_session(datetime(2026, 6, 8, 16, 0, tzinfo=CN_TZ))
    assert session["session_kind"] == "trading_day_after_close"
    assert needed_official_nav_date(session) == "2026-06-08"


def test_intraday_refresh_skipped_when_market_closed(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 13, 10, 0, tzinfo=CN_TZ))
    calls: list[int] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_holdings_intraday",
        lambda *args, **kwargs: calls.append(1) or 0,
    )
    result = cache_mod.refresh_shared_intraday_charts(holdings=[HOLDING], session=session)
    assert result["skipped"] is True
    assert result["reason"] == "session_closed"
    assert calls == []


def test_intraday_after_close_finalizes_once(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 8, 16, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(
        cache_mod,
        "get_settings",
        lambda: SimpleNamespace(
            sector_quotes_enabled=True,
            holding_intraday_refresh_interval_seconds=120,
        ),
    )
    cache_mod._LAST_INTRADAY_FETCH_AT = 0.0
    cache_mod._LAST_INTRADAY_FINALIZE_DATE = None
    calls: list[bool] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_holdings_intraday",
        lambda holdings, force_refresh=False, **kwargs: calls.append(force_refresh) or 1,
    )
    monkeypatch.setattr(cache_mod, "collect_intraday_queries", lambda holdings: [("index", "中证人工智能")])
    first = cache_mod.refresh_shared_intraday_charts(holdings=[HOLDING], session=session, now=1.0)
    second = cache_mod.refresh_shared_intraday_charts(holdings=[HOLDING], session=session, now=2.0)
    assert first["skipped"] is False
    assert second["reason"] == "intraday_finalized"
    assert calls == [True]


def test_intraday_refresh_runs_during_continuous_trading(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 8, 10, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(
        cache_mod,
        "get_settings",
        lambda: SimpleNamespace(sector_quotes_enabled=True, holding_intraday_refresh_interval_seconds=120),
    )
    cache_mod._LAST_INTRADAY_FETCH_AT = 0.0
    calls: list[bool] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_holdings_intraday",
        lambda holdings, force_refresh=False, **kwargs: calls.append(force_refresh) or 1,
    )
    monkeypatch.setattr(cache_mod, "collect_intraday_queries", lambda holdings: [("index", "中证人工智能")])
    result = cache_mod.refresh_shared_intraday_charts(
        holdings=[HOLDING],
        session=session,
        now=1.0,
        force=True,
    )
    assert result["skipped"] is False
    assert calls == [True]


def test_nav_refresh_skips_when_latest_covers_needed_date(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 13, 12, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(cache_mod, "get_cached_fund_nav", lambda *args, **kwargs: _history(latest_date="2026-06-12"))
    calls: list[str] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_fund_nav",
        lambda code, *args, **kwargs: calls.append(code) or True,
    )
    result = cache_mod.refresh_shared_nav_histories([HOLDING], session=session)
    assert result["reason"] == "covers_needed_date"
    assert calls == []


def test_nav_refresh_fetches_cold_cache_even_on_weekend(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 13, 12, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(cache_mod, "get_cached_fund_nav", lambda *args, **kwargs: None)
    cache_mod._LAST_NAV_FETCH_AT = 0.0
    calls: list[str] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_fund_nav",
        lambda code, *args, **kwargs: calls.append(code) or True,
    )
    result = cache_mod.refresh_shared_nav_histories([HOLDING], session=session, now=10.0)
    assert result["skipped"] is False
    assert calls == ["008586"]


def test_stale_nav_retries_are_throttled_after_close(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 8, 16, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(
        cache_mod,
        "get_cached_fund_nav",
        lambda *args, **kwargs: _history(latest_date="2026-06-05"),
    )
    cache_mod._LAST_NAV_FETCH_AT = 0.0
    calls: list[str] = []
    monkeypatch.setattr(
        cache_mod,
        "warm_fund_nav",
        lambda code, *args, **kwargs: calls.append(code) or True,
    )
    first = cache_mod.refresh_shared_nav_histories(
        [HOLDING],
        session=session,
        now=1000.0,
        min_interval_seconds=900,
    )
    second = cache_mod.refresh_shared_nav_histories(
        [HOLDING],
        session=session,
        now=1100.0,
        min_interval_seconds=900,
    )
    assert first["skipped"] is False
    assert second["reason"] == "nav_retry_throttled"
    assert calls == ["008586"]


def test_cache_only_nav_history_does_not_hit_akshare(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_cached_fund_nav",
        lambda *args, **kwargs: None,
    )

    def _boom(*args, **kwargs):
        raise AssertionError("request path must not fetch AkShare")

    monkeypatch.setattr("app.services.akshare_subprocess.fetch_fund_nav_history", _boom)
    history = FundDataService().get_nav_history("008586", "测试", trading_days=252, cache_only=True)
    assert history.source == "unavailable"
    assert history.points == []


def test_nav_cache_is_shared_across_day_windows(monkeypatch) -> None:
    store: dict[str, dict] = {}
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_spot_snapshot",
        lambda key, ttl_seconds=None: store.get(key),
    )
    monkeypatch.setattr(
        "app.services.fund_nav_cache.save_spot_snapshot",
        lambda key, payload: store.__setitem__(key, dict(payload)),
    )
    from app.services.fund_nav_cache import get_cached_fund_nav, save_cached_fund_nav

    points = [
        FundNavPoint(date=f"2026-06-{day:02d}", nav=1.0 + day / 100)
        for day in range(1, 11)
    ]
    save_cached_fund_nav(
        "008586",
        800,
        FundNavHistory(
            fund_code="008586",
            fund_name="测试",
            source="akshare",
            points=points,
            latest_nav=points[-1].nav,
            latest_date=points[-1].date,
        ),
    )
    sliced = get_cached_fund_nav("008586", 3)
    assert sliced is not None
    assert [point.date for point in sliced.points] == ["2026-06-08", "2026-06-09", "2026-06-10"]
    full = get_cached_fund_nav("008586", 90)
    assert full is not None
    assert len(full.points) == 10


def test_nav_cache_reads_legacy_v1_key(monkeypatch) -> None:
    from app.services.fund_nav_cache import get_cached_fund_nav

    points = [
        FundNavPoint(date="2026-08-13", nav=1.1),
        FundNavPoint(date="2026-08-14", nav=1.12),
    ]
    history = FundNavHistory(
        fund_code="011373",
        fund_name="招商前沿医疗保健股票A",
        source="akshare",
        points=points,
        latest_nav=1.12,
        latest_date="2026-08-14",
    )
    store = {"fund:nav:v1:011373:90": history.model_dump(mode="json")}
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_spot_snapshot",
        lambda key, ttl_seconds=None: store.get(key),
    )

    cached = get_cached_fund_nav("011373", 63)
    assert cached is not None
    assert cached.latest_date == "2026-08-14"
    assert len(cached.points) == 2


def test_nav_history_fetch_on_miss_hits_akshare(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_cache.get_cached_fund_nav",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_cache.save_cached_fund_nav",
        lambda *args, **kwargs: None,
    )
    calls: list[tuple[str, int]] = []

    def _fetch(fund_code: str, trading_days: int = 90) -> dict:
        calls.append((fund_code, trading_days))
        return {
            "data": [
                {"date": "2026-08-13", "nav": 1.10},
                {"date": "2026-08-14", "nav": 1.12},
            ]
        }

    monkeypatch.setattr("app.services.akshare_subprocess.fetch_fund_nav_history", _fetch)
    history = FundDataService().get_nav_history("011373", "招商前沿医疗保健股票A", trading_days=63)
    assert calls
    assert calls[0][0] == "011373"
    assert history.source == "akshare"
    assert history.latest_date == "2026-08-14"
    assert len(history.points) == 2
