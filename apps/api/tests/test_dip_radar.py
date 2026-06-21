from __future__ import annotations


def test_get_dip_radar_returns_items_sorted_by_drop(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.get_dip_radar_snapshot",
        lambda **kw: {
            "refreshed_at": "2026-06-21T12:00:00Z",
            "trade_date": "2026-06-21",
            "lookback_days": 5,
            "fee_break_even_percent": 2.5,
            "items": [
                {
                    "fund_code": "000001",
                    "fund_name": "A",
                    "sector_label": "半导体",
                    "dip_drop_percent": -7.0,
                    "rebound_score": 80,
                    "rebound_signals": [],
                    "rank": 1,
                },
                {
                    "fund_code": "000002",
                    "fund_name": "B",
                    "sector_label": "银行",
                    "dip_drop_percent": -4.0,
                    "rebound_score": 60,
                    "rebound_signals": [],
                    "rank": 2,
                },
            ],
            "sector_dip_leaders": [],
            "available": True,
            "from_cache": False,
            "stale": False,
            "session_kind": "trading_day_intraday",
            "message": None,
        },
    )

    resp = client.get("/api/market/dip-radar?lookback_days=5&limit=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["dip_drop_percent"] == -7.0
    assert body["lookback_days"] == 5


def test_get_dip_radar_sector_filter(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.get_dip_radar_snapshot",
        lambda **kw: {
            "refreshed_at": "2026-06-21T12:00:00Z",
            "trade_date": "2026-06-21",
            "lookback_days": 5,
            "fee_break_even_percent": 2.5,
            "items": [
                {
                    "fund_code": "519674",
                    "fund_name": "银河创新成长",
                    "sector_label": "半导体",
                    "dip_drop_percent": -5.2,
                    "rebound_score": 72.0,
                    "rebound_signals": [],
                    "rank": 1,
                }
            ],
            "sector_dip_leaders": [],
            "available": True,
            "from_cache": True,
            "stale": False,
            "session_kind": "trading_day_intraday",
            "message": None,
        },
    )

    resp = client.get("/api/market/dip-radar?sector=半导体&limit=10")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["sector_label"] == "半导体"


def test_get_dip_radar_rejects_invalid_lookback(client):
    resp = client.get("/api/market/dip-radar?lookback_days=7")
    assert resp.status_code == 400


def test_get_dip_radar_snapshot_sector_filter_empty_keeps_total_matches(monkeypatch):
    from app.services.dip_radar_snapshot import get_dip_radar_snapshot

    cached = {
        "refreshed_at": "2026-06-21T12:00:00Z",
        "trade_date": "2026-06-21",
        "lookback_days": 5,
        "fee_break_even_percent": 2.5,
        "items": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "sector_label": "半导体",
                "dip_drop_percent": -5.2,
                "rebound_score": 72.0,
                "rebound_signals": [],
                "rank": 1,
            }
        ],
        "sector_dip_leaders": [
            {
                "sector_label": "半导体",
                "avg_dip_drop_percent": -5.2,
                "fund_count": 1,
                "min_dip_drop_percent": -5.2,
            }
        ],
        "scan_stats": {
            "rank_shortlist": 150,
            "dip_threshold_percent": 2.0,
            "matches": 1,
        },
        "available": True,
        "from_cache": True,
        "stale": False,
        "session_kind": "trading_day_intraday",
        "message": None,
    }

    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: cached,
    )
    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.build_trading_session",
        lambda: {"effective_trade_date": "2026-06-21", "session_kind": "closed"},
    )

    result = get_dip_radar_snapshot(lookback_days=5, sector="银行", force_refresh=False)

    assert result["items"] == []
    assert result["available"] is False
    assert result["scan_stats"]["total_matches"] == 1
    assert result["scan_stats"]["matches"] == 0
    assert result["scan_stats"]["sector_filter"] == "银行"
    assert "全市场已扫描 1 只" in (result["message"] or "")
    assert len(result["sector_dip_leaders"]) == 1
