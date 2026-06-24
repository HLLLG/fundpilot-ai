"""分层抽样基金池：在按业绩排序的榜单里等距抽样，横跨赢家→输家。

设计文档：docs/superpowers/specs/2026-06-24-factor-style-and-universe-design.md（3D）。

纯函数。把「取前 N 名」（偏强样本）换成「跨业绩段等距抽样」，让横截面更中性。
诚实边界：榜单本身仍有幸存者偏差（清盘基金不在榜），彻底去偏需 point-in-time 库。
"""
from __future__ import annotations


def sample_universe(rank_rows: list[dict], sample_size: int) -> list[dict]:
    """在按业绩排序的榜单里等距分层抽样。

    rank_rows 数 <= sample_size 或 sample_size <= 0 时原样返回。
    否则以 step = n / sample_size 等距取样，覆盖从榜首到榜尾各业绩段。
    """
    n = len(rank_rows)
    if sample_size <= 0 or n <= sample_size:
        return list(rank_rows)
    step = n / sample_size
    return [rank_rows[int(i * step)] for i in range(sample_size)]
