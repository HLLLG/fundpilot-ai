"""荐基候选池默认使用全量横截面，并保留有界降级路径。"""

from __future__ import annotations

from unittest.mock import patch

from app.services import discovery_candidate_pool


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
