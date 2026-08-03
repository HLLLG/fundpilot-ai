from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import fund_return_distribution, market_breadth_signal, trading_session


CN_TZ = ZoneInfo("Asia/Shanghai")


def test_fund_distribution_rejects_inconsistent_coverage_metadata():
    payload = {
        "source_row_count": 20,
        "valid_count": 9,
        "missing_count": 11,
        "coverage_percent": 75.0,
        "advance_count": 5,
        "decline_count": 3,
        "flat_count": 1,
        "bins": {
            "le_neg5": 0,
            "neg5_neg3": 0,
            "neg3_neg1": 1,
            "neg1_zero": 2,
            "zero": 1,
            "zero_one": 3,
            "one_three": 2,
            "three_five": 0,
            "ge_five": 0,
        },
    }

    assert fund_return_distribution._normalize_distribution_counts(payload) is None


def test_trading_session_identifies_midday_break_without_changing_trade_date(monkeypatch):
    monkeypatch.setattr(trading_session, "_is_trading_day", lambda _day: True)

    session = trading_session.build_trading_session(
        datetime(2026, 7, 17, 12, 15, tzinfo=CN_TZ)
    )

    assert session["session_kind"] == "trading_day_intraday"
    assert session["market_phase"] == "lunch_break"
    assert session["is_continuous_trading"] is False
    assert session["effective_trade_date"] == "2026-07-17"
    assert "午间休市" in session["decision_window"]


def test_intraday_freshness_uses_trading_clock_during_midday_break(monkeypatch):
    now = datetime(2026, 7, 17, 12, 30, tzinfo=CN_TZ)
    monkeypatch.setattr(market_breadth_signal, "_now_cn", lambda: now)
    session = {
        "session_kind": "trading_day_intraday",
        "market_phase": "lunch_break",
    }

    fresh = market_breadth_signal._refresh_intraday_metadata(
        {"as_of_datetime": "2026-07-17T11:30:00+08:00"},
        anchor="2026-07-17",
        session=session,
    )
    old = market_breadth_signal._refresh_intraday_metadata(
        {"as_of_datetime": "2026-07-17T11:05:00+08:00"},
        anchor="2026-07-17",
        session=session,
    )

    assert fresh["freshness_seconds"] == 0
    assert fresh["decision_eligible"] is True
    assert fresh["decision_status"] == "eligible_lunch_break"
    assert old["freshness_seconds"] == 1500
    assert old["decision_eligible"] is False
    assert old["decision_status"] == "ineligible_stale"


def test_intraday_breadth_preserves_suspended_denominator_and_display_tone(monkeypatch):
    monkeypatch.setattr(
        market_breadth_signal,
        "run_akshare_json_script",
        lambda *args, **kwargs: {
            "advance_count": 2344,
            "decline_count": 2695,
            "flat_count": 158,
            "suspended_count": 4,
            "limit_up_count": 46,
            "limit_down_count": 41,
            "real_limit_up_count": 42,
            "real_limit_down_count": 31,
            "activity_percent": 45.07,
            "as_of_datetime": "2026-07-16 15:00:00",
        },
    )

    activity = market_breadth_signal._fetch_intraday_market_activity(timeout=1.0)

    assert activity is not None
    assert activity["universe_scope"] == "沪深两市"
    assert activity["traded_sample_count"] == 5197
    assert activity["market_sample_count"] == 5201
    assert activity["suspended_count"] == 4
    assert activity["activity_percent"] == 45.07
    assert activity["advance_ratio_percent"] == 45.1
    assert activity["decline_ratio_percent"] == 51.86

    signal = market_breadth_signal._compose_intraday_signal(
        activity,
        closing={},
        anchor="2026-07-16",
        session={},
        final=True,
    )
    assert signal["breadth_tone"] == "分化偏弱"
    # 兼容既有确定性守卫；展示语义和守卫粗粒度档位分开。
    assert signal["sentiment_level"] == "中性"
    assert "沪深个股广度分化偏弱" in signal["interpretation"]


def test_official_fund_distribution_requires_conservation_and_records_scope(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {"is_continuous_trading": False},
    )
    saved: dict = {}
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "save_spot_snapshot",
        lambda key, payload: saved.update({"key": key, "payload": payload}),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-16",
            "source_row_count": 12,
            "valid_count": 9,
            "missing_count": 3,
            "coverage_percent": 75.0,
            "advance_count": 4,
            "decline_count": 4,
            "flat_count": 1,
            "bins": {
                "le_neg5": 1,
                "neg5_neg3": 0,
                "neg3_neg1": 1,
                "neg1_zero": 2,
                "zero": 1,
                "zero_one": 2,
                "one_three": 1,
                "three_five": 0,
                "ge_five": 1,
            },
        },
    )

    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)

    assert result["available"] is True
    assert result["source_mode"] == "official_nav"
    assert result["valid_count"] == 9
    assert sum(result["bins"].values()) == result["valid_count"]
    assert "份额代码" in result["universe_scope"]
    assert saved["payload"]["as_of_date"] == "2026-07-16"


def test_official_fund_distribution_rejects_non_conserving_payload(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {"is_continuous_trading": False},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot_any_age", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-16",
            "valid_count": 2,
            "advance_count": 1,
            "decline_count": 1,
            "flat_count": 0,
            "bins": {"zero": 1},
        },
    )

    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)

    assert result["available"] is False
    assert "暂未取得" in result["message"]


def test_intraday_estimate_fetcher_bins_estimated_growth(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26",
            "source_row_count": 12,
            "valid_count": 9,
            "missing_count": 3,
            "coverage_percent": 75.0,
            "advance_count": 4,
            "decline_count": 4,
            "flat_count": 1,
            "bins": {
                "le_neg5": 1,
                "neg5_neg3": 0,
                "neg3_neg1": 1,
                "neg1_zero": 2,
                "zero": 1,
                "zero_one": 2,
                "one_three": 1,
                "three_five": 0,
                "ge_five": 1,
            },
        },
    )
    result = fund_return_distribution._fetch_intraday_estimate_distribution(timeout=1.0)

    assert result is not None
    assert result["as_of_date"] == "2026-07-26"
    assert result["valid_count"] == 9
    assert sum(result["bins"].values()) == 9


def test_intraday_estimate_fetcher_rejects_non_conserving_payload(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26",
            "valid_count": 9,
            "advance_count": 4,
            "decline_count": 4,
            "flat_count": 1,
            "bins": {"zero": 1},  # 合计 != valid_count
        },
    )
    result = fund_return_distribution._fetch_intraday_estimate_distribution(timeout=1.0)

    assert result is None


def test_intraday_session_routes_to_estimate_distribution(monkeypatch):
    monkeypatch.setattr(fund_return_distribution, "_MIN_INTRADAY_VALID_COUNT", 1)
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot_any_age", lambda *a, **k: None)
    saved: dict = {}
    monkeypatch.setattr(
        fund_return_distribution,
        "save_spot_snapshot",
        lambda key, payload: saved.update({"key": key, "payload": payload}),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26",
            "source_row_count": 12,
            "valid_count": 9,
            "missing_count": 3,
            "coverage_percent": 75.0,
            "advance_count": 4,
            "decline_count": 4,
            "flat_count": 1,
            "bins": {
                "le_neg5": 1,
                "neg5_neg3": 0,
                "neg3_neg1": 1,
                "neg1_zero": 2,
                "zero": 1,
                "zero_one": 2,
                "one_three": 1,
                "three_five": 0,
                "ge_five": 1,
            },
        },
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["source_mode"] == "intraday_estimate"
    assert result["available"] is True
    assert saved["key"] == "fund:return-distribution:intraday:v3"
    assert "新浪" in result["source_name"]


def test_intraday_fetch_failure_falls_back_to_stale_snapshot(monkeypatch):
    monkeypatch.setattr(fund_return_distribution, "_MIN_INTRADAY_VALID_COUNT", 1)
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda key: (
            {
                "available": True,
                "source_mode": "intraday_estimate",
                "valid_count": 9,
                "bins": {"zero": 9},
                "advance_count": 0,
                "decline_count": 0,
                "flat_count": 9,
                "as_of_date": "2026-07-25",
            }
            if key == fund_return_distribution._INTRADAY_CACHE_KEY
            else None
        ),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {"error": "boom"},
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["stale"] is True
    assert result["available"] is True
    assert "上次成功统计" in result["message"]


def test_intraday_fetch_failure_without_stale_returns_unavailable(monkeypatch):
    monkeypatch.setattr(fund_return_distribution, "_MIN_INTRADAY_VALID_COUNT", 1)
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot_any_age", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {"error": "boom"},
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["available"] is False
    assert result["source_mode"] == "intraday_estimate"


def _distribution_cache_payload(
    *,
    as_of_date: str,
    source_mode: str,
    valid_count: int = 12_000,
    source_row_count: int = 15_000,
) -> dict:
    return {
        "available": True,
        "stale": False,
        "source_mode": source_mode,
        "as_of_date": as_of_date,
        "as_of_datetime": f"{as_of_date} 11:30:00",
        "source_row_count": source_row_count,
        "valid_count": valid_count,
        "missing_count": source_row_count - valid_count,
        "coverage_percent": round(valid_count / source_row_count * 100, 2),
        "advance_count": 0,
        "decline_count": 0,
        "flat_count": valid_count,
        "bins": {"zero": valid_count},
    }


def _current_trade_session(*, phase: str, kind: str) -> dict:
    return {
        "is_trading_day": True,
        "is_continuous_trading": phase == "continuous",
        "calendar_date": "2026-08-03",
        "effective_trade_date": "2026-08-03",
        "session_kind": kind,
        "market_phase": phase,
    }


def test_request_cache_miss_does_not_synchronously_fetch_provider(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: {
            "is_trading_day": False,
            "is_continuous_trading": False,
            "session_kind": "non_trading_day",
        },
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda *a, **k: None,
    )

    def fail_if_called(*, timeout):
        raise AssertionError(f"provider called on request path: timeout={timeout}")

    monkeypatch.setattr(
        fund_return_distribution,
        "_fetch_official_distribution",
        fail_if_called,
    )

    result = fund_return_distribution.build_fund_return_distribution()

    assert result["available"] is False
    assert result["source_mode"] == "official_nav"


def test_lunch_break_uses_same_day_intraday_cache(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: _current_trade_session(
            phase="lunch_break",
            kind="trading_day_intraday",
        ),
    )
    snapshots = {
        fund_return_distribution._CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-07-31",
            source_mode="official_nav",
            valid_count=13_000,
        ),
        fund_return_distribution._INTRADAY_CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-08-03",
            source_mode="intraday_estimate",
        ),
    }
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot",
        lambda key, **_kwargs: snapshots.get(key),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda key: snapshots.get(key),
    )

    result = fund_return_distribution.build_fund_return_distribution()

    assert result["available"] is True
    assert result["source_mode"] == "intraday_estimate"
    assert result["as_of_date"] == "2026-08-03"


def test_current_trade_day_rejects_previous_day_snapshots(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: _current_trade_session(
            phase="lunch_break",
            kind="trading_day_intraday",
        ),
    )
    snapshots = {
        fund_return_distribution._CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-07-31",
            source_mode="official_nav",
            valid_count=13_000,
        ),
        fund_return_distribution._INTRADAY_CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-07-31",
            source_mode="intraday_estimate",
        ),
    }
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot",
        lambda key, **_kwargs: snapshots.get(key),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda key: snapshots.get(key),
    )

    result = fund_return_distribution.build_fund_return_distribution()

    assert result["available"] is False
    assert result["as_of_date"] == "2026-08-03"
    assert "不使用上一交易日" in result["message"]


def test_after_close_same_day_official_cache_replaces_estimate(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "build_trading_session",
        lambda: _current_trade_session(
            phase="after_close",
            kind="trading_day_after_close",
        ),
    )
    snapshots = {
        fund_return_distribution._CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-08-03",
            source_mode="official_nav",
            valid_count=13_000,
        ),
        fund_return_distribution._INTRADAY_CACHE_KEY: _distribution_cache_payload(
            as_of_date="2026-08-03",
            source_mode="intraday_estimate",
        ),
    }
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot",
        lambda key, **_kwargs: snapshots.get(key),
    )
    monkeypatch.setattr(
        fund_return_distribution,
        "get_spot_snapshot_any_age",
        lambda key: snapshots.get(key),
    )

    result = fund_return_distribution.build_fund_return_distribution()

    assert result["available"] is True
    assert result["source_mode"] == "official_nav"
    assert result["as_of_date"] == "2026-08-03"
