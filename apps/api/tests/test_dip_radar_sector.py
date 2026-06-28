"""大跌雷达关联板块解析。"""

from __future__ import annotations

from app.database import save_fund_primary_sector_global
from app.services.dip_drop_scanner import build_dip_radar_pool_fast
from app.services.fund_primary_sector_service import resolve_sector_labels_for_radar


def _rank_row(code: str, name: str, *, r1w: float = -8.0) -> dict:
    return {
        "fund_code": code,
        "fund_name": name,
        "return_1w_percent": r1w,
        "return_1y_percent": 10.0,
        "fund_scale_yi": 5.0,
    }


def test_resolve_sector_labels_for_radar_uses_global_cache():
    save_fund_primary_sector_global(
        fund_code="017115",
        sector_name="医药",
        source="precompute_benchmark",
        confidence=0.82,
    )
    labels = resolve_sector_labels_for_radar({"017115": "浦银安盛景气优选混合C"})
    assert labels["017115"] == "医药"


def test_resolve_sector_labels_for_radar_name_keywords_fallback():
    labels = resolve_sector_labels_for_radar({"161024": "富国中证军工指数A"})
    assert labels["161024"] in ("军工", "国防军工")


def test_build_dip_radar_pool_fast_applies_global_sector():
    save_fund_primary_sector_global(
        fund_code="017115",
        sector_name="食品饮料",
        source="precompute_benchmark",
        confidence=0.82,
    )

    def _fetch_rank(limit=150):
        return [_rank_row("017115", "浦银安盛景气优选混合C")]

    pool = build_dip_radar_pool_fast(
        pool_cap=5,
        budget_seconds=1.0,
        fetch_rank=_fetch_rank,
    )
    assert len(pool) == 1
    assert pool[0]["sector_label"] == "食品饮料"
