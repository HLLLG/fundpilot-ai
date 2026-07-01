from __future__ import annotations

"""荐基板块方向机会打分：薄封装，实际实现已迁到 sector_opportunity_scoring.py。

2026-07 日报升级时抽取为共享模块，供日报（report_sector_opportunity.py）复用同一套
打分口径。此文件保留原路径/原名字导出，避免破坏既有荐基调用方与测试。
"""

from app.services.sector_opportunity_scoring import (
    MOMENTUM_TRACK,
    SETUP_TRACK,
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
    select_sector_opportunities,
)

__all__ = [
    "MOMENTUM_TRACK",
    "SETUP_TRACK",
    "build_sector_flow_map_for_opportunities",
    "describe_sector_opportunity",
    "select_sector_opportunities",
]
