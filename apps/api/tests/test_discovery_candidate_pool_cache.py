"""F2 回归：荐基候选池默认 fetcher 接 fund_rank_cache（共享 1h 缓存）。"""

from __future__ import annotations

from unittest.mock import patch

from app.services import discovery_candidate_pool, dip_drop_scanner


def test_build_candidate_pool_uses_rank_cache_by_default():
    """默认不显式注入 fetcher 时，应调用 fund_rank_cache.fetch_open_fund_rank_cached，
    而不是直接走 akshare_subprocess.fetch_open_fund_rank。

    Python `from X import Y` 让 Y 成为消费模块的本地名，因此 patch 路径必须是
    消费模块的本地绑定（discovery_candidate_pool.fetch_open_fund_rank_cached），
    而不是定义源（fund_rank_cache.fetch_open_fund_rank_cached）。
    """
    with (
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

    assert cached.called, "应走 fund_rank_cache.fetch_open_fund_rank_cached"
    assert not raw.called, "不应直调 akshare_subprocess.fetch_open_fund_rank"


def test_build_dip_pool_for_sectors_uses_rank_cache_by_default():
    """conftest._stub_market_data_fetches 把整个 build_dip_pool_for_sectors stub 掉了；
    本测试 reload 模块拿回真实实现，再 patch 内部 fetcher 验证默认走 fund_rank_cache。"""
    import importlib

    fresh = importlib.reload(dip_drop_scanner)

    with (
        patch(
            "app.services.dip_drop_scanner.fetch_open_fund_rank_cached",
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

    assert cached.called, "build_dip_pool_for_sectors 应走 fund_rank_cache"
    assert not raw.called
