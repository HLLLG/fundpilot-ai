"""因子有效性回测（Rank IC）引擎测试。

设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md 第 7 章。

覆盖：
- 秩相关工具（并列均值秩、完美正/负、单调非线性、零方差）
- 单期 Rank IC 对齐与横截面下限
- 主引擎：植入真信号 → IC≈1、噪声 → 不显著、前视偏差守卫、边界
- hypothesis：IC∈[-1,1]、positive_ratio∈[0,1]
"""
from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.factor_ic_backtest import (
    MIN_PERIODS,
    NavPoint,
    _rank_ic_for_period,
    _rankdata,
    _spearman,
    compute_factor_ic,
)


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------


def test_rankdata_handles_ties():
    assert _rankdata([10, 10, 20]) == [1.5, 1.5, 3.0]


def test_spearman_perfect_positive():
    assert _spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0


def test_spearman_perfect_negative():
    assert _spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0


def test_spearman_monotonic_nonlinear_is_one():
    assert _spearman([1, 2, 3, 4], [1, 4, 9, 16]) == 1.0


def test_spearman_zero_variance_none():
    assert _spearman([1, 1, 1], [1, 2, 3]) is None


def test_rank_ic_insufficient_cross_section():
    fv = {f"{i}": float(i) for i in range(5)}
    fwd = {f"{i}": float(i) for i in range(5)}
    assert _rank_ic_for_period(fv, fwd, min_cross_section=10) is None


def test_rank_ic_aligns_on_common_codes():
    fv = {"a": 1.0, "b": 2.0, "c": 3.0, "d": None}
    fwd = {"a": 1.0, "b": 2.0, "c": 3.0}
    assert _rank_ic_for_period(fv, fwd, min_cross_section=3) == 1.0


# ---------------------------------------------------------------------------
# 主引擎
# ---------------------------------------------------------------------------


def test_planted_momentum_signal_detected():
    # 20 只基金，过去涨速越快、未来也越快（同序）+ 轻微噪声 → 动量 IC 高且显著
    rng = random.Random(42)
    n_days = 600
    cal = [f"D{i:04d}" for i in range(n_days)]
    panel = {}
    for k in range(20):
        slope = 0.0005 * (k + 1)
        nav = 1.0
        pts = []
        for i in range(n_days):
            nav *= (1.0 + slope) * (1.0 + rng.uniform(-0.003, 0.003))
            pts.append(NavPoint(cal[i], nav))
        panel[f"{k:06d}"] = pts
    res = compute_factor_ic(
        nav_panel=panel,
        calendar=cal,
        rebalance_step=21,
        forward_days=20,
        factor_lookback=250,
        min_cross_section=10,
    )
    assert res.available is True
    mom = next(f for f in res.factors if f.factor == "momentum")
    assert mom.mean_ic is not None and mom.mean_ic > 0.7
    assert mom.significant is True


def test_noise_panel_not_significant():
    random.seed(1)
    n_days = 600
    cal = [f"D{i:04d}" for i in range(n_days)]
    panel = {}
    for k in range(20):
        nav = 1.0
        pts = []
        for i in range(n_days):
            nav *= 1.0 + random.uniform(-0.01, 0.01)
            pts.append(NavPoint(cal[i], nav))
        panel[f"{k:06d}"] = pts
    res = compute_factor_ic(nav_panel=panel, calendar=cal, min_cross_section=10)
    mom = next(f for f in res.factors if f.factor == "momentum")
    assert mom.significant is False


def test_lookahead_guard_ignores_future():
    # 面板B 把每只基金最后 30 天抬高 50%（未来突变）；早期再平衡期因子值不该受影响
    n_days = 400
    cal = [f"D{i:04d}" for i in range(n_days)]
    base = {}
    for k in range(15):
        slope = 0.0004 * (k + 1)
        base[f"{k:06d}"] = [(cal[i], (1.0 + slope) ** i) for i in range(n_days)]
    panelA = {c: [NavPoint(d, v) for d, v in s] for c, s in base.items()}
    panelB = {}
    for c, s in base.items():
        pts = [NavPoint(d, v) for d, v in s]
        for j in range(len(pts) - 30, len(pts)):
            pts[j] = NavPoint(pts[j].date, pts[j].nav * 1.5)
        panelB[c] = pts
    resA = compute_factor_ic(nav_panel=panelA, calendar=cal, min_cross_section=10)
    resB = compute_factor_ic(nav_panel=panelB, calendar=cal, min_cross_section=10)
    momA = next(f for f in resA.factors if f.factor == "momentum")
    momB = next(f for f in resB.factors if f.factor == "momentum")
    k = min(len(momA.ic_series), len(momB.ic_series)) - 2
    assert k > 0
    assert momA.ic_series[:k] == momB.ic_series[:k]


def test_small_universe_unavailable():
    cal = [f"D{i:04d}" for i in range(300)]
    panel = {
        f"{k:06d}": [NavPoint(cal[i], 1.0 + 0.001 * i) for i in range(300)]
        for k in range(5)
    }
    res = compute_factor_ic(nav_panel=panel, calendar=cal, min_cross_section=10)
    assert res.available is False
    assert res.message is not None


def test_few_periods_not_significant():
    cal = [f"D{i:04d}" for i in range(120)]
    panel = {
        f"{k:06d}": [NavPoint(cal[i], (1.0 + 0.0003 * (k + 1)) ** i) for i in range(120)]
        for k in range(15)
    }
    res = compute_factor_ic(
        nav_panel=panel,
        calendar=cal,
        rebalance_step=21,
        forward_days=20,
        min_cross_section=10,
    )
    assert res.available is True
    for f in res.factors:
        assert f.significant is False


# ---------------------------------------------------------------------------
# hypothesis 不变量
# ---------------------------------------------------------------------------


def test_runner_offline_writes_summary(tmp_path):
    import json

    from scripts.run_factor_ic import build_ic_report

    cal = [f"D{i:04d}" for i in range(400)]

    def fetch_rank(limit):
        return [{"fund_code": f"{k:06d}", "fund_name": f"基金{k}"} for k in range(15)]

    def fetch_nav(code, name, trading_days):
        k = int(code)
        return [NavPoint(cal[i], (1.0 + 0.0003 * (k + 1)) ** i) for i in range(400)]

    out = build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_size=15,
        nav_days=400,
    )
    assert out["available"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "report.txt").exists()
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert "factors" in data and isinstance(data["factors"], list)
    assert data["universe_size"] == 15


def test_runner_sampled_mode_stratifies_pool(tmp_path):
    import json

    from scripts.run_factor_ic import build_ic_report

    cal = [f"D{i:04d}" for i in range(400)]
    seen_limits: list[int] = []

    def fetch_rank(limit):
        seen_limits.append(limit)
        return [{"fund_code": f"{k:06d}", "fund_name": f"基金{k}"} for k in range(60)]

    def fetch_nav(code, name, trading_days):
        k = int(code)
        return [NavPoint(cal[i], (1.0 + 0.0003 * (k + 1)) ** i) for i in range(400)]

    out = build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_size=12,
        universe_mode="sampled",
        sample_pool_size=60,
        nav_days=400,
    )
    assert out["available"] is True
    assert out["params"]["universe_mode"] == "sampled"
    assert seen_limits == [60]  # 用大池而非 universe_size 取数
    assert out["universe_size"] == 12  # 抽样后只剩 12 只


@given(st.integers(min_value=0, max_value=10_000))
@settings(max_examples=30, deadline=None)
def test_ic_series_within_bounds(seed):
    rng = random.Random(seed)
    n_days = 350
    cal = [f"D{i:04d}" for i in range(n_days)]
    panel = {}
    for k in range(15):
        nav = 1.0
        pts = []
        for i in range(n_days):
            nav *= 1.0 + rng.uniform(-0.02, 0.02)
            pts.append(NavPoint(cal[i], nav))
        panel[f"{k:06d}"] = pts
    res = compute_factor_ic(nav_panel=panel, calendar=cal, min_cross_section=10)
    for f in res.factors:
        for ic in f.ic_series:
            assert -1.0 - 1e-9 <= ic <= 1.0 + 1e-9
        if f.positive_ratio is not None:
            assert 0.0 <= f.positive_ratio <= 1.0
