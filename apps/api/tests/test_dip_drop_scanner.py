from app.services.dip_drop_scanner import normalize_rebound_score, prescreen_dip_candidates


def test_prescreen_prefers_deeper_dip():
    rank_rows = [
        {"fund_code": "000001", "fund_name": "深跌半导体A", "fund_scale_yi": 5.0, "return_1y_percent": 20.0},
        {"fund_code": "000002", "fund_name": "浅跌半导体B", "fund_scale_yi": 5.0, "return_1y_percent": 20.0},
    ]
    nav_by_code = {
        "000001": {"recent_5d_change_percent": -6.0, "distance_from_high_percent": -10.0},
        "000002": {"recent_5d_change_percent": -2.0, "distance_from_high_percent": -3.0},
    }
    rows = prescreen_dip_candidates(
        sector_label="半导体",
        rank_rows=rank_rows,
        nav_by_code=nav_by_code,
        lookback_days=5,
        min_drop_percent=3.0,
        keywords=("半导体",),
    )
    assert rows[0]["fund_code"] == "000001"
    assert rows[0]["dip_drop_percent"] <= -5.0
    assert 0 <= rows[0]["rebound_score"] <= 100


def test_prescreen_skips_shallow_dip():
    rank_rows = [
        {"fund_code": "000002", "fund_name": "浅跌B", "fund_scale_yi": 5.0, "return_1y_percent": 20.0},
    ]
    nav_by_code = {
        "000002": {"recent_5d_change_percent": -2.0, "distance_from_high_percent": -3.0},
    }
    rows = prescreen_dip_candidates(
        sector_label="半导体",
        rank_rows=rank_rows,
        nav_by_code=nav_by_code,
        lookback_days=5,
        min_drop_percent=3.0,
        keywords=("半导体",),
    )
    assert rows == []


def test_prescreen_emits_rebound_signals_for_reversal():
    rank_rows = [
        {"fund_code": "000001", "fund_name": "半导体A", "fund_scale_yi": 5.0, "return_1y_percent": 25.0},
    ]
    nav_by_code = {
        "000001": {
            "recent_5d_change_percent": -5.0,
            "distance_from_high_percent": -8.0,
            "recent_5d_daily_change_percent": [-2.0, 1.2],
        },
    }
    rows = prescreen_dip_candidates(
        sector_label="半导体",
        rank_rows=rank_rows,
        nav_by_code=nav_by_code,
        lookback_days=5,
        min_drop_percent=3.0,
        keywords=("半导体",),
    )
    assert rows
    signal_ids = {item["id"] for item in rows[0]["rebound_signals"]}
    assert "two_day_reversal_up" in signal_ids


def test_normalize_rebound_score_bounded():
    score = normalize_rebound_score(
        {
            "return_1y_percent": 30.0,
            "nav_trend": {"recent_5d_change_percent": -8.0, "distance_from_high_percent": -15.0},
        }
    )
    assert 0 <= score <= 100


def test_build_dip_radar_pool_fast_sorts_by_drop(monkeypatch):
    from app.services.dip_drop_scanner import build_dip_radar_pool_fast

    rank_rows = [
        {"fund_code": "000001", "fund_name": "深跌半导体A", "fund_scale_yi": 5.0, "return_1y_percent": 20.0, "return_1w_percent": -5.0},
        {"fund_code": "000002", "fund_name": "浅跌半导体B", "fund_scale_yi": 5.0, "return_1y_percent": 20.0, "return_1w_percent": -1.0},
    ]

    def fake_rank(limit=300):
        return rank_rows[:limit]

    monkeypatch.setattr("app.services.dip_drop_scanner.fetch_open_fund_rank", fake_rank)
    monkeypatch.setattr("app.services.dip_drop_scanner.list_fund_primary_sectors", lambda: [])

    rows = build_dip_radar_pool_fast(
        lookback_days=5,
        min_drop_percent=2.0,
        pool_cap=10,
        fetch_rank=fake_rank,
        budget_seconds=0.1,
    )
    assert len(rows) == 1
    assert rows[0]["fund_code"] == "000001"
    assert rows[0]["dip_drop_percent"] <= -5.0
