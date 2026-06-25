from __future__ import annotations

from app.services.portfolio_risk_metrics import (
    MIN_CORRELATION_SAMPLE_DAYS,
    MIN_SAMPLE_DAYS,
    _equity_curve,
    _hhi,
    _max_drawdown,
    compute_correlation_matrix,
    compute_portfolio_metrics,
)


def test_equity_curve_and_drawdown_formulas():
    assert round(_equity_curve([0.03, -0.03])[-1] - 1.0, 4) == -0.0009
    returns = [0.10, -0.0454545, -0.0952381, 0.0736842]
    assert round(_max_drawdown(returns), 3) == -0.136
    assert round(_hhi([0.5, 0.3, 0.2]), 2) == 0.38


def test_insufficient_sample_returns_unavailable():
    m = compute_portfolio_metrics(
        portfolio_daily_returns=[1.0] * 5,
        index_daily_returns=[0.5] * 5,
        holding_amounts=[100.0],
    )
    assert m.available is False
    assert m.sample_days == 5


def test_zero_volatility_returns_none_sharpe():
    m = compute_portfolio_metrics(
        portfolio_daily_returns=[0.5] * MIN_SAMPLE_DAYS,
        index_daily_returns=[0.5] * MIN_SAMPLE_DAYS,
        holding_amounts=[100.0],
    )
    assert m.available is True
    assert m.sharpe_ratio is None
    assert m.sortino_ratio is None


def test_full_payload_has_core_fields():
    returns = [1.2, -0.8] * MIN_SAMPLE_DAYS
    index = [0.6, -0.4] * MIN_SAMPLE_DAYS
    m = compute_portfolio_metrics(
        portfolio_daily_returns=returns,
        index_daily_returns=index,
        holding_amounts=[600.0, 300.0, 100.0],
    )
    assert m.available is True
    assert m.sharpe_ratio is not None
    assert m.max_drawdown_percent is not None
    assert m.hhi == round(0.6**2 + 0.3**2 + 0.1**2, 3)


def test_correlation_matrix_known_values():
    days = [f"2026-01-{i + 1:02d}" for i in range(MIN_CORRELATION_SAMPLE_DAYS)]
    base = [(i % 5) - 2 + 0.1 * i for i in range(MIN_CORRELATION_SAMPLE_DAYS)]
    a = {day: base[i] for i, day in enumerate(days)}
    b = {day: 2 * base[i] for i, day in enumerate(days)}
    c = {day: -base[i] for i, day in enumerate(days)}
    result = compute_correlation_matrix(
        returns_by_code={"A": a, "B": b, "C": c},
        names_by_code={"A": "基金A", "B": "基金B", "C": "基金C"},
    )
    assert result.available is True
    assert result.matrix[0][1] == 1.0
    assert result.matrix[0][2] == -1.0
