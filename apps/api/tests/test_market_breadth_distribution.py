from __future__ import annotations

from app.services import fund_return_distribution, market_breadth_signal


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
