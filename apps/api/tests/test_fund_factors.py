"""基金横截面因子打分纯函数测试。

设计文档：docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md 第 9 章。

覆盖：
- 去极值 / z-score / 百分位 / 动量合成 / Calmar / 合成归一 的已知答案
- 池子不足、零方差因子、缺字段、持仓不在池、空持仓 等边界
- hypothesis 不变量：z ∈ [-3, 3]、percentile ∈ [0, 100]
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.fund_factors import (
    FACTOR_WEIGHTS,
    MIN_UNIVERSE_SIZE,
    Z_CLIP,
    FundFactorInput,
    _blend_momentum,
    _calmar,
    _composite_z,
    _factor_stats,
    _FactorStats,
    _grade,
    _percentile_rank,
    _winsorize,
    _zscore,
    compute_factor_scores,
)


# ---------------------------------------------------------------------------
# 已知答案：横截面统计工具
# ---------------------------------------------------------------------------


def test_winsorize_caps_outlier():
    out = _winsorize([1, 2, 3, 4, 1000])
    assert max(out) < 1000  # 极端值被压回分位线


def test_zscore_basic_known_value():
    # 直接构造统计量隔离 z-score 逻辑（不经去极值）
    stats = _FactorStats(mean=10.0, std=10.0)
    assert round(_zscore(20, stats), 2) == 1.0
    assert round(_zscore(0, stats), 2) == -1.0


def test_zscore_clips_extreme():
    stats = _FactorStats(mean=0.0, std=1.0)
    assert _zscore(100.0, stats) == Z_CLIP
    assert _zscore(-100.0, stats) == -Z_CLIP


def test_zscore_zero_variance_degrades_to_zero():
    stats = _factor_stats([5, 5, 5, 5])  # 全相同 → std=0
    assert _zscore(5, stats) == 0.0


def test_percentile_rank_top_and_bottom():
    assert _percentile_rank(9.0, [1, 2, 3, 9]) == 100.0
    assert _percentile_rank(1.0, [1, 2, 3, 9]) == 25.0


# ---------------------------------------------------------------------------
# 已知答案：原始值提取
# ---------------------------------------------------------------------------


def test_blend_momentum_full_windows():
    row = FundFactorInput(
        "x", return_6m_percent=10, return_3m_percent=20, return_1y_percent=5
    )
    # 0.5*10 + 0.3*20 + 0.2*5 = 12
    assert _blend_momentum(row) == 12.0


def test_blend_momentum_handles_missing_window():
    row = FundFactorInput("x", return_6m_percent=10)
    assert _blend_momentum(row) == 10.0  # 仅 6 月时按剩余权重归一回该值


def test_blend_momentum_all_missing_returns_none():
    assert _blend_momentum(FundFactorInput("x")) is None


def test_calmar_uses_abs_drawdown():
    row = FundFactorInput("x", return_1y_percent=20, max_drawdown_1y_percent=-10)
    assert _calmar(row) == 2.0


def test_calmar_zero_drawdown_returns_none():
    row = FundFactorInput("x", return_1y_percent=20, max_drawdown_1y_percent=0.0)
    assert _calmar(row) is None


def test_composite_renormalizes_missing_factor():
    # 缺规模因子时按剩余权重归一，不当作 0
    z = {"momentum": 1.0, "risk_adjusted": 1.0, "drawdown": 1.0, "size": None}
    result = _composite_z(z)
    assert result is not None
    assert round(result, 6) == 1.0  # 全为 1，归一后仍 1


def test_composite_all_none_returns_none():
    assert _composite_z({k: None for k in FACTOR_WEIGHTS}) is None


def test_grade_thresholds():
    assert _grade(80) == "A"
    assert _grade(60) == "B"
    assert _grade(30) == "C"
    assert _grade(10) == "D"
    assert _grade(None) is None


# ---------------------------------------------------------------------------
# 主函数 compute_factor_scores
# ---------------------------------------------------------------------------


def _make_universe(n: int) -> list[FundFactorInput]:
    """构造 n 只有梯度的基金池，保证各因子有方差。"""
    rows = []
    for i in range(n):
        rows.append(
            FundFactorInput(
                fund_code=f"{100000 + i:06d}",
                fund_name=f"基金{i}",
                return_3m_percent=float(i % 13) - 6,
                return_6m_percent=float(i % 17) - 8,
                return_1y_percent=float(i % 23) - 10,
                max_drawdown_1y_percent=-float(i % 19 + 1),
                fund_scale_yi=float(i % 50 + 1),
            )
        )
    return rows


def test_small_universe_returns_unavailable():
    universe = _make_universe(10)  # < MIN_UNIVERSE_SIZE
    result = compute_factor_scores(universe=universe, targets=universe[:1])
    assert result.available is False
    assert result.universe_size == 10
    assert result.message is not None


def test_scores_target_in_universe():
    universe = _make_universe(MIN_UNIVERSE_SIZE + 20)
    target = universe[5]
    result = compute_factor_scores(universe=universe, targets=[target])
    assert result.available is True
    assert len(result.funds) == 1
    fund = result.funds[0]
    assert fund.in_universe is True
    assert fund.composite_score is not None
    assert 0.0 <= fund.composite_score <= 100.0
    assert fund.composite_grade in {"A", "B", "C", "D"}
    assert set(fund.factors.keys()) == set(FACTOR_WEIGHTS.keys())


def test_target_not_in_universe_flagged():
    universe = _make_universe(MIN_UNIVERSE_SIZE + 20)
    outsider = FundFactorInput(
        fund_code="999999",
        fund_name="池外基金",
        return_3m_percent=5,
        return_6m_percent=6,
        return_1y_percent=7,
        max_drawdown_1y_percent=-8,
        fund_scale_yi=20,
    )
    result = compute_factor_scores(universe=universe, targets=[outsider])
    assert result.available is True
    assert result.funds[0].in_universe is False
    assert result.funds[0].composite_score is not None


def test_empty_targets_no_crash():
    universe = _make_universe(MIN_UNIVERSE_SIZE + 5)
    result = compute_factor_scores(universe=universe, targets=[])
    assert result.available is True
    assert result.funds == []


def test_target_all_none_fields_does_not_crash():
    universe = _make_universe(MIN_UNIVERSE_SIZE + 5)
    blank = FundFactorInput(fund_code="888888", fund_name="无数据")
    result = compute_factor_scores(universe=universe, targets=[blank])
    assert result.available is True
    fund = result.funds[0]
    # 各因子原始值为 None → z 为 None → 综合分 None
    assert fund.composite_score is None
    assert fund.composite_grade is None


# ---------------------------------------------------------------------------
# Hypothesis 不变量
# ---------------------------------------------------------------------------

_factor_value = st.floats(
    min_value=-100.0, max_value=300.0, allow_nan=False, allow_infinity=False
)


@given(st.lists(_factor_value, min_size=2, max_size=300))
@settings(max_examples=200)
def test_zscore_within_clip(values):
    stats = _factor_stats(values)
    for v in values:
        z = _zscore(v, stats)
        if z is not None:
            assert -Z_CLIP - 1e-9 <= z <= Z_CLIP + 1e-9


@given(st.lists(_factor_value, min_size=1, max_size=200), _factor_value)
@settings(max_examples=200)
def test_percentile_rank_within_bounds(population, value):
    pct = _percentile_rank(value, population)
    assert pct is None or (0.0 <= pct <= 100.0)


# ---------------------------------------------------------------------------
# 装配层 build_factor_scores_payload（离线，注入 fetch_rank / fetch_nav）
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from app.models import Holding
from app.services.portfolio_snapshot import build_factor_scores_payload


def _fake_rank_rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "fund_code": f"{100000 + i:06d}",
                "fund_name": f"排行基金{i}",
                "return_3m_percent": float(i % 13) - 6,
                "return_6m_percent": float(i % 17) - 8,
                "return_1y_percent": float(i % 23) - 10,
                "max_drawdown_1y_percent": -float(i % 19 + 1),
                "fund_scale_yi": float(i % 50 + 1),
            }
        )
    return rows


def _fake_nav_points(start: float, step: float, days: int) -> list:
    return [
        SimpleNamespace(date=f"2026-01-{d + 1:02d}", nav=round(start + step * d, 4))
        for d in range(days)
    ]


def test_assembly_scores_holdings_offline():
    holdings = [
        Holding(fund_code="100005", fund_name="持仓在榜", holding_amount=1000.0),
        Holding(fund_code="999999", fund_name="持仓不在榜", holding_amount=500.0),
    ]
    payload = build_factor_scores_payload(
        holdings,
        fetch_rank=lambda: _fake_rank_rows(40),
        fetch_nav=lambda code, name, trading_days: _fake_nav_points(1.0, 0.01, 60),
    )
    assert payload["available"] is True
    assert payload["universe_size"] == 40
    assert len(payload["funds"]) == 2
    by_code = {f["fund_code"]: f for f in payload["funds"]}
    assert by_code["100005"]["in_universe"] is True
    assert by_code["999999"]["in_universe"] is False
    # 不在榜的持仓走净值兜底算动量（上升序列 → 动量原始值 > 0）
    assert by_code["999999"]["factors"]["momentum"]["raw"] is not None
    assert by_code["999999"]["factors"]["momentum"]["raw"] > 0


def test_assembly_empty_universe_unavailable():
    holdings = [Holding(fund_code="100005", fund_name="x", holding_amount=1000.0)]
    payload = build_factor_scores_payload(
        holdings,
        fetch_rank=lambda: [],
        fetch_nav=lambda code, name, trading_days: [],
    )
    assert payload["available"] is False
    assert payload["message"] is not None
