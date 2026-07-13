"""荐基候选池默认使用全量横截面，并保留有界降级路径。"""

from __future__ import annotations

from unittest.mock import patch

from app.services import discovery_candidate_pool, dip_drop_scanner


def test_build_candidate_pool_uses_full_universe_before_rank_fallback():
    with (
        patch(
            "app.services.discovery_candidate_pool.fetch_discovery_fund_universe_cached",
            return_value=[
                {
                    "fund_code": "000001",
                    "fund_name": "测试半导体基金A",
                    "return_3m_percent": 3.0,
                    "return_6m_percent": 6.0,
                }
            ],
        ) as universe,
        patch(
            "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
            return_value=[],
        ) as cached,
        patch(
            "app.services.akshare_subprocess.fetch_open_fund_rank",
            return_value=[],
        ) as raw,
        patch(
            "app.services.discovery_candidate_pool.fetch_new_fund_offerings",
            return_value=[],
        ),
    ):
        discovery_candidate_pool.build_candidate_pool(target_sectors=["半导体"])

    assert universe.called
    assert not cached.called, "全量横截面可用时不应退回赢家榜"
    assert not raw.called, "不应直调 akshare_subprocess.fetch_open_fund_rank"


def test_build_dip_pool_for_sectors_uses_recent_loser_rank_by_default():
    """conftest._stub_market_data_fetches 把整个 build_dip_pool_for_sectors stub 掉了；
    本测试 reload 模块拿回真实实现，再 patch 内部 fetcher 验证默认走 fund_rank_cache。"""
    import importlib

    fresh = importlib.reload(dip_drop_scanner)

    with (
        patch(
            "app.services.dip_drop_scanner.fetch_open_fund_rank_worst_recent",
            return_value=[],
        ) as cached,
        patch(
            "app.services.akshare_subprocess.fetch_open_fund_rank",
            return_value=[],
        ) as raw,
    ):
        fresh.build_dip_pool_for_sectors(
            target_sectors=["半导体"],
            lookback_days=5,
            min_drop_percent=3.0,
        )

    assert cached.called, "大跌扫描应走近期跌幅榜，而不是年度冠军榜"
    assert not raw.called
