from __future__ import annotations

from app.services.discovery_candidate_pool import build_candidate_pool


def test_candidate_pool_uses_sector_primary_rows_before_name_matching(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
        lambda limit=300: [
            {"fund_code": "111111", "fund_name": "泛科技基金", "fund_scale_yi": 10, "return_3m_percent": 2},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {
                "fund_code": "020640",
                "sector_name": "半导体",
                "source": "precompute_benchmark",
                "confidence": 0.8,
                "fund_name": "广发半导体设备ETF联接C",
            }
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(
        target_sectors=["半导体"],
        sector_opportunities=[
            {"sector_label": "半导体", "track": "momentum", "score": 80, "entry_hint": "可分批关注"}
        ],
    )

    assert pool[0]["fund_code"] == "020640"
    assert pool[0]["selection_reason"] == "板块机会映射"
    assert pool[0]["opportunity_track"] == "momentum"
    assert pool[0]["entry_hint"] == "可分批关注"


def test_candidate_pool_dedupes_same_fund_family(monkeypatch):
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_open_fund_rank_cached", lambda limit=300: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {"fund_code": "020639", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接A"},
            {"fund_code": "020640", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接C"},
            {"fund_code": "021533", "sector_name": "半导体", "fund_name": "天弘半导体设备指数C"},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(target_sectors=["半导体"])

    codes = [item["fund_code"] for item in pool]
    assert len({"020639", "020640"} & set(codes)) == 1
    assert "021533" in codes


def test_candidate_pool_dedupes_ranked_family_entries(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
        lambda limit=300: [
            {"fund_code": "020639", "fund_name": "广发半导体设备ETF联接A", "fund_scale_yi": 20},
            {"fund_code": "020640", "fund_name": "广发半导体设备ETF联接C", "fund_scale_yi": 20},
            {"fund_code": "021533", "fund_name": "天弘半导体设备指数C", "fund_scale_yi": 12},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(target_sectors=["半导体"])

    codes = [item["fund_code"] for item in pool]
    assert len({"020639", "020640"} & set(codes)) == 1
    assert "021533" in codes
