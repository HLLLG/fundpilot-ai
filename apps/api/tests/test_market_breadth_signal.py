"""大盘情绪温度计（M1.1）单测。

覆盖：主信号（创新高低家数百分位自校准）、辅助快照（涨跌停/炸板，向前回退查找有效日）、
两融环比、disabled 配置、best-effort 降级（历史不足/接口失败）、缓存命中与 stale 回退。
"""

from __future__ import annotations

from datetime import datetime

from app.config import refresh_settings
from app.services import market_breadth_signal as service
from app.services.market_breadth_signal import (
    SENTIMENT_LEVELS,
    _compute_sentiment,
    _percentile_rank,
    _sentiment_level_from_percentile,
    build_market_breadth_signal,
)


def _breadth_rows(values: list[float], *, start_date: str = "2024-01-01") -> list[dict]:
    """构造 high20-low20=values[i] 的连续交易日行（简化：日期仅用于排序展示）。"""
    import datetime

    base = datetime.date.fromisoformat(start_date)
    rows = []
    for index, value in enumerate(values):
        day = base + datetime.timedelta(days=index)
        # high20/low20 具体拆分不重要，只要差值符合预期即可
        high20 = max(0.0, 250 + value / 2)
        low20 = max(0.0, 250 - value / 2)
        rows.append({"date": day.isoformat(), "high20": high20, "low20": low20})
    return rows


def test_percentile_rank_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile_rank(values, 3.0) == 60.0
    assert _percentile_rank(values, 5.0) == 100.0
    assert _percentile_rank(values, 0.5) == 0.0


def test_sentiment_level_thresholds_cover_full_range():
    assert _sentiment_level_from_percentile(0) == "冰点"
    assert _sentiment_level_from_percentile(10) == "冰点"
    assert _sentiment_level_from_percentile(11) == "低迷"
    assert _sentiment_level_from_percentile(50) == "中性"
    assert _sentiment_level_from_percentile(90) == "偏热"
    assert _sentiment_level_from_percentile(91) == "亢奋"
    assert _sentiment_level_from_percentile(100) == "亢奋"


def test_compute_sentiment_insufficient_sample_returns_none():
    rows = _breadth_rows([10.0] * 30)  # < _MIN_BREADTH_SAMPLE_DAYS(60)
    assert _compute_sentiment(rows) is None


def test_compute_sentiment_extreme_cold_percentile():
    # 前 99 天中性偏高，最后一天极端冷（净新低远超以往），应落入低百分位档位。
    values = [0.0] * 99 + [-500.0]
    rows = _breadth_rows(values)
    sentiment = _compute_sentiment(rows)
    assert sentiment is not None
    assert sentiment["sentiment_level"] == "冰点"
    assert sentiment["breadth_percentile"] <= 10
    assert sentiment["sample_days"] == 100


def test_compute_sentiment_level_change_detects_cooling():
    # 倒数第二天中性，最后一天骤冷，level_change 应为负（降档）。
    values = [0.0] * 98 + [0.0, -500.0]
    rows = _breadth_rows(values)
    sentiment = _compute_sentiment(rows)
    assert sentiment is not None
    assert sentiment["sentiment_level_change"] is not None
    assert sentiment["sentiment_level_change"] < 0


def test_sentiment_levels_ordered_cold_to_hot():
    assert SENTIMENT_LEVELS == ("冰点", "低迷", "中性", "偏热", "亢奋")


def test_build_market_breadth_signal_disabled(monkeypatch):
    monkeypatch.setenv("FUND_AI_MARKET_BREADTH_ENABLED", "false")
    refresh_settings()
    try:
        result = build_market_breadth_signal(trade_date="2026-07-02")
        assert result["available"] is False
        assert result["reason"] == "disabled"
    finally:
        monkeypatch.delenv("FUND_AI_MARKET_BREADTH_ENABLED", raising=False)
        refresh_settings()


def test_build_market_breadth_signal_breadth_unavailable_is_best_effort(monkeypatch):
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_high_low_breadth_history", lambda **_kwargs: None)

    result = build_market_breadth_signal(trade_date="2026-07-02")
    assert result["available"] is False
    assert result["reason"] == "breadth_history_unavailable"


def test_build_market_breadth_signal_success_end_to_end(monkeypatch):
    rows = _breadth_rows([0.0] * 90 + [200.0])  # 末日明显偏热

    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_high_low_breadth_history", lambda **_kwargs: rows)
    monkeypatch.setattr(
        service,
        "_fetch_limit_pool_snapshot",
        lambda *_a, **_k: {
            "limit_up_count": 80,
            "limit_down_count": 10,
            "limit_up_broken_ratio_percent": 12.5,
            "max_consecutive_boards": 5,
            "as_of_date": "2026-07-02",
        },
    )
    monkeypatch.setattr(
        service,
        "_fetch_margin_balance_change",
        lambda *_a, **_k: {
            "as_of_date": "2026-06-30",
            "margin_balance_change_yi": -12.4,
            "margin_scope": "sse_only",
        },
    )

    result = build_market_breadth_signal(trade_date="2026-07-02")
    assert result["available"] is True
    assert result["sentiment_level"] in SENTIMENT_LEVELS
    assert result["limit_up_count"] == 80
    assert result["limit_down_count"] == 10
    assert result["margin_balance_change_yi"] == -12.4
    assert result["margin_scope"] == "sse_only"
    assert "情绪" in result["interpretation"]
    assert "非历史回测校准" in result["basis"] or "自校准" in result["basis"]


def test_build_market_breadth_signal_limit_pool_and_margin_failure_still_available(monkeypatch):
    """涨跌停快照/两融任一失败都不应拖垮主信号（best-effort 子字段降级）。"""
    rows = _breadth_rows([0.0] * 90 + [5.0])

    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_high_low_breadth_history", lambda **_kwargs: rows)
    monkeypatch.setattr(service, "_fetch_limit_pool_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_margin_balance_change", lambda *_a, **_k: None)

    result = build_market_breadth_signal(trade_date="2026-07-02")
    assert result["available"] is True
    assert result["limit_pool_available"] is False
    assert result["margin_available"] is False
    assert result["limit_up_count"] is None
    assert result["margin_balance_change_yi"] is None


def test_build_market_breadth_signal_uses_cache_hit(monkeypatch):
    cached_payload = {"available": True, "trade_date": "2026-07-01", "sentiment_level": "中性"}
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: cached_payload)

    def _boom(**_kwargs):
        raise AssertionError("should not recompute on cache hit")

    monkeypatch.setattr(service, "_fetch_high_low_breadth_history", _boom)

    result = build_market_breadth_signal(trade_date="2026-07-02")
    assert result == cached_payload


def test_build_market_breadth_signal_falls_back_to_stale_cache_on_failure(monkeypatch):
    stale_payload = {"available": True, "trade_date": "2026-06-30", "sentiment_level": "低迷"}
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: stale_payload)
    monkeypatch.setattr(service, "_fetch_high_low_breadth_history", lambda **_kwargs: None)

    result = build_market_breadth_signal(trade_date="2026-07-02")
    assert result["available"] is True
    assert result["stale"] is True
    assert result["trade_date"] == "2026-06-30"


def test_fetch_limit_pool_snapshot_looks_back_over_empty_days(monkeypatch):
    """周末/假日当天返回全 0 快照时，向前回退查找最近一个有交易的日期。"""
    calls: list[str] = []

    def _fake_fetch(query_date: str, *, timeout: float):
        calls.append(query_date)
        if len(calls) < 3:
            return None  # 模拟空数据日
        return {
            "limit_up_count": 42,
            "limit_down_count": 3,
            "limit_up_broken_ratio_percent": 5.0,
            "max_consecutive_boards": 2,
        }

    monkeypatch.setattr(service, "_fetch_limit_pool_for_date", _fake_fetch)

    result = service._fetch_limit_pool_snapshot("2026-07-02", timeout=5.0)
    assert result is not None
    assert result["limit_up_count"] == 42
    assert len(calls) == 3


def test_fetch_limit_pool_snapshot_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(service, "_fetch_limit_pool_for_date", lambda *_a, **_k: None)
    result = service._fetch_limit_pool_snapshot("2026-07-02", timeout=5.0)
    assert result is None


def test_fetch_limit_pool_for_date_computes_broken_ratio(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "limit_up_count": 90,
            "limit_down_count": 5,
            "broken_count": 10,
            "max_consecutive_boards": 4,
        },
    )
    result = service._fetch_limit_pool_for_date("20260702", timeout=5.0)
    assert result is not None
    # broken_ratio = 10 / (90 + 10) * 100 = 10.0
    assert result["limit_up_broken_ratio_percent"] == 10.0


def test_fetch_limit_pool_for_date_treats_all_zero_as_empty(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "limit_up_count": 0,
            "limit_down_count": 0,
            "broken_count": 0,
            "max_consecutive_boards": 0,
        },
    )
    result = service._fetch_limit_pool_for_date("20260704", timeout=5.0)
    assert result is None


def test_fetch_margin_balance_change_computes_delta_in_yi(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "data": [
                {"date": "2026-06-29", "balance_yuan": 1_000_000_000_000.0},
                {"date": "2026-06-30", "balance_yuan": 998_760_000_000.0},
            ]
        },
    )
    result = service._fetch_margin_balance_change("2026-07-02", timeout=5.0)
    assert result is not None
    assert result["margin_scope"] == "sse_only"
    assert result["margin_balance_change_yi"] == -12.4
    assert result["as_of_date"] == "2026-06-30"


def test_fetch_margin_balance_change_handles_insufficient_rows(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {"data": [{"date": "2026-06-30", "balance_yuan": 1.0}]},
    )
    assert service._fetch_margin_balance_change("2026-07-02", timeout=5.0) is None


def test_fetch_high_low_breadth_history_dedupes_by_date(monkeypatch):
    """AkShare 文档示例里出现过同日期重复行（见接口示例数据），须按日期去重取最后一条。"""
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "data": [
                {"date": "2026-06-30", "high20": 100.0, "low20": 50.0},
                {"date": "2026-06-30", "high20": 120.0, "low20": 40.0},
                {"date": "2026-07-01", "high20": 90.0, "low20": 60.0},
            ]
        },
    )
    rows = service._fetch_high_low_breadth_history(timeout=5.0)
    assert rows is not None
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-06-30"
    assert rows[0]["high20"] == 120.0  # 保留同日最后一条
    assert rows[1]["date"] == "2026-07-01"


def _session(kind: str, *, calendar_date: str = "2026-07-13", effective: str | None = None) -> dict:
    return {
        "session_kind": kind,
        "calendar_date": calendar_date,
        "effective_trade_date": effective or calendar_date,
    }


def _live_activity(as_of: str = "2026-07-13 10:00:00") -> dict:
    return {
        "available": True,
        "advance_count": 1000,
        "decline_count": 4000,
        "flat_count": 50,
        "activity_percent": 19.8,
        "advance_ratio_percent": 19.8,
        "limit_up_count": 30,
        "limit_down_count": 12,
        "real_limit_up_count": 20,
        "real_limit_down_count": 8,
        "as_of_datetime": as_of,
    }


def test_intraday_uses_current_trade_date_and_is_guard_eligible_when_fresh(monkeypatch):
    closing = {
        "available": True,
        "trade_date": "2026-07-10",
        "sentiment_level": "中性",
        "breadth_percentile": 50.0,
        "decision_eligible": True,
        "freshness_status": "fresh",
        "stale": False,
    }
    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_intraday"))
    monkeypatch.setattr(service, "_now_cn", lambda: datetime(2026, 7, 13, 10, 1, tzinfo=service._CN_TZ))
    monkeypatch.setattr(service, "get_previous_trade_date", lambda *_a: "2026-07-10")
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "get_spot_snapshot_any_age",
        lambda key: closing if ":closing:" in key else None,
    )
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_intraday_market_activity", lambda **_k: _live_activity())

    result = build_market_breadth_signal("2026-07-13")

    assert result["trade_date"] == "2026-07-13"
    assert result["signal_mode"] == "intraday"
    assert result["source_mode"] == "intraday_live"
    assert result["advance_count"] == 1000
    assert result["closing_trade_date"] == "2026-07-10"
    assert result["sentiment_level_change"] is None
    assert result["decision_eligible"] is True
    assert result["freshness_status"] == "live"


def test_intraday_opening_observation_is_not_guard_eligible(monkeypatch):
    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_intraday"))
    monkeypatch.setattr(service, "_now_cn", lambda: datetime(2026, 7, 13, 9, 32, tzinfo=service._CN_TZ))
    monkeypatch.setattr(service, "get_previous_trade_date", lambda *_a: "2026-07-10")
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "_fetch_intraday_market_activity",
        lambda **_k: _live_activity("2026-07-13 09:32:00"),
    )

    result = build_market_breadth_signal("2026-07-13")

    assert result["stale"] is False
    assert result["decision_eligible"] is False
    assert result["decision_status"] == "opening_observation"


def test_intraday_source_failure_returns_stale_display_only_snapshot(monkeypatch):
    cached = {
        **_live_activity("2026-07-13 09:40:00"),
        "signal_mode": "intraday",
        "source_mode": "intraday_live",
        "sentiment_level": "冰点",
        "sentiment_level_change": -2,
        "decision_eligible": True,
    }
    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_intraday"))
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: cached)
    monkeypatch.setattr(service, "_fetch_intraday_market_activity", lambda **_k: None)

    result = build_market_breadth_signal("2026-07-13", force_refresh=True)

    assert result["available"] is True
    assert result["stale"] is True
    assert result["decision_eligible"] is False
    assert result["decision_status"] == "ineligible_source_fallback"


def test_after_close_freezes_last_confirmed_intraday_snapshot(monkeypatch):
    cached = {
        **_live_activity("2026-07-13 14:59:00"),
        "trade_date": "2026-07-13",
        "sentiment_level": "低迷",
        "sentiment_level_change": -1,
    }
    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_after_close"))
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: cached)

    result = build_market_breadth_signal("2026-07-13")

    assert result["source_mode"] == "intraday_final"
    assert result["freshness_status"] == "fresh"
    assert result["decision_eligible"] is True


def test_after_close_does_not_freeze_1455_snapshot_as_final(monkeypatch):
    cached = {
        **_live_activity("2026-07-13 14:55:00"),
        "trade_date": "2026-07-13",
        "sentiment_level": "低迷",
    }
    closing_fallback = {
        "available": True,
        "trade_date": "2026-07-13",
        "signal_mode": "closing",
        "source_mode": "closing",
        "decision_eligible": False,
        "stale": True,
    }
    fetch_calls: list[bool] = []
    monkeypatch.setattr(service, "build_trading_session", lambda: _session("trading_day_after_close"))
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: cached)
    monkeypatch.setattr(
        service,
        "_fetch_intraday_market_activity",
        lambda **_k: fetch_calls.append(True) or None,
    )
    monkeypatch.setattr(service, "_build_closing_signal", lambda *_a, **_k: closing_fallback)

    result = build_market_breadth_signal("2026-07-13")

    assert fetch_calls == [True]
    assert result["source_mode"] == "closing"
    assert result["decision_eligible"] is False


def test_fetch_intraday_market_activity_normalizes_legu_payload(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "advance_count": 972.0,
            "decline_count": 4167.0,
            "flat_count": 58.0,
            "limit_up_count": 22.0,
            "limit_down_count": 17.0,
            "real_limit_up_count": 15.0,
            "real_limit_down_count": 12.0,
            "activity_percent": 18.68,
            "as_of_datetime": "2026-07-13 09:55:00",
        },
    )

    result = service._fetch_intraday_market_activity(timeout=4.0)

    assert result is not None
    assert result["advance_count"] == 972
    assert result["activity_percent"] == 18.68
    assert result["as_of_datetime"] == "2026-07-13T09:55:00+08:00"


def test_intraday_never_compares_activity_level_with_closing_percentile_level(monkeypatch):
    closing = {
        "available": True,
        "trade_date": "2026-07-10",
        "sentiment_level": "亢奋",
        "decision_eligible": True,
        "freshness_status": "fresh",
        "stale": False,
    }
    result = service._compose_intraday_signal(
        _live_activity(),
        closing=closing,
        anchor="2026-07-13",
        session=_session("trading_day_intraday"),
    )

    assert result["sentiment_level"] == "低迷"
    assert result["sentiment_level_change"] is None


def test_fetch_intraday_market_activity_rejects_incomplete_market_sample(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_akshare_json_script",
        lambda *_a, **_k: {
            "advance_count": 20,
            "decline_count": 30,
            "flat_count": 0,
            "activity_percent": 40.0,
            "as_of_datetime": "2026-07-13 10:00:00",
        },
    )

    assert service._fetch_intraday_market_activity(timeout=4.0) is None
