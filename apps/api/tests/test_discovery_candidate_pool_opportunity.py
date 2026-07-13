from __future__ import annotations

import pytest

from app.services.discovery_candidate_pool import build_candidate_pool


@pytest.fixture(autouse=True)
def _disable_live_full_universe(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_discovery_fund_universe_cached",
        lambda limit=20_000: [],
    )


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
    assert "fund_quality_score" in pool[0]


def test_candidate_pool_ranks_primary_rows_by_quality_score(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
        lambda limit=300: [
            {
                "fund_code": "000001",
                "fund_name": "低质半导体指数A",
                "fund_scale_yi": 2,
                "return_3m_percent": -3,
                "return_6m_percent": -4,
                "return_1y_percent": 90,
                "max_drawdown_1y_percent": -45,
            },
            {
                "fund_code": "000002",
                "fund_name": "优质半导体指数A",
                "fund_scale_yi": 35,
                "return_3m_percent": 12,
                "return_6m_percent": 22,
                "return_1y_percent": 38,
                "max_drawdown_1y_percent": -18,
            },
            {
                "fund_code": "000003",
                "fund_name": "稳健半导体指数A",
                "fund_scale_yi": 18,
                "return_3m_percent": 6,
                "return_6m_percent": 14,
                "return_1y_percent": 24,
                "max_drawdown_1y_percent": -16,
            },
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {"fund_code": "000001", "sector_name": "半导体", "fund_name": "低质半导体指数A", "confidence": 0.95},
            {"fund_code": "000002", "sector_name": "半导体", "fund_name": "优质半导体指数A", "confidence": 0.75},
            {"fund_code": "000003", "sector_name": "半导体", "fund_name": "稳健半导体指数A", "confidence": 0.7},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(target_sectors=["半导体"], per_sector=2, pool_cap=2)

    assert [item["fund_code"] for item in pool] == ["000002", "000003"]
    assert pool[0]["fund_quality_score"] > pool[1]["fund_quality_score"]
    assert "低质半导体指数A" not in [item["fund_name"] for item in pool]


def test_candidate_pool_applies_type_preference_to_primary_rows(monkeypatch):
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_open_fund_rank_cached", lambda limit=300: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {"fund_code": "020640", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接C", "confidence": 0.9},
            {"fund_code": "020639", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接A", "confidence": 0.85},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(target_sectors=["半导体"], fund_type_preference="no_c_class")

    assert [item["fund_code"] for item in pool] == ["020639"]


def test_candidate_pool_allocates_extra_slot_to_stronger_opportunities(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
        lambda limit=300: [
            {
                "fund_code": f"10{i:04d}",
                "fund_name": f"半导体指数{i}A",
                "fund_scale_yi": 20 + i,
                "return_3m_percent": 10 - i,
                "return_6m_percent": 18 - i,
                "return_1y_percent": 25 + i,
                "max_drawdown_1y_percent": -15,
            }
            for i in range(5)
        ]
        + [
            {
                "fund_code": f"20{i:04d}",
                "fund_name": f"白酒指数{i}A",
                "fund_scale_yi": 20 + i,
                "return_3m_percent": 6 - i,
                "return_6m_percent": 10 - i,
                "return_1y_percent": 18 + i,
                "max_drawdown_1y_percent": -14,
            }
            for i in range(5)
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(
        target_sectors=["半导体", "白酒"],
        per_sector=3,
        pool_cap=8,
        sector_opportunities=[
            {"sector_label": "半导体", "track": "momentum", "score": 95},
            {"sector_label": "白酒", "track": "setup", "score": 45},
        ],
    )

    counts = {label: len([item for item in pool if item["sector_label"] == label]) for label in {"半导体", "白酒"}}
    assert counts["半导体"] == 4
    assert counts["白酒"] == 3
    assert len(pool) == 7


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
