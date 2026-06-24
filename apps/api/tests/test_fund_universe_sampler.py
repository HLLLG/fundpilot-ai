"""分层抽样基金池测试（3D）。"""
from __future__ import annotations

from app.services.fund_universe_sampler import sample_universe


def _rows(n: int) -> list[dict]:
    return [{"fund_code": f"{i:06d}", "rank": i} for i in range(n)]


def test_returns_exact_sample_size():
    out = sample_universe(_rows(500), 50)
    assert len(out) == 50


def test_spans_top_and_bottom():
    rows = _rows(500)
    out = sample_universe(rows, 50)
    ranks = [r["rank"] for r in out]
    assert ranks[0] == 0  # 覆盖榜首
    assert ranks[-1] >= 450  # 覆盖榜尾段
    assert ranks == sorted(ranks)  # 保序


def test_small_pool_returned_as_is():
    rows = _rows(10)
    assert sample_universe(rows, 50) == rows


def test_non_positive_size_returned_as_is():
    rows = _rows(10)
    assert sample_universe(rows, 0) == rows


def test_no_duplicates():
    out = sample_universe(_rows(500), 100)
    codes = [r["fund_code"] for r in out]
    assert len(set(codes)) == len(codes)
