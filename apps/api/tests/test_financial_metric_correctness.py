from __future__ import annotations

import math
import statistics

import pytest

from app.models import AnalysisRequest, FundNavHistory, FundNavPoint, Holding, InvestorProfile
from app.services.nav_trend_summary import summarize_nav_history
from app.services.outcome_path_metrics import (
    build_path_metrics,
    evaluate_no_action_counterfactual,
)
from app.services.portfolio_risk_metrics import (
    MIN_ANNUALIZATION_SAMPLE_DAYS,
    TRADING_DAYS_PER_YEAR,
    _max_drawdown,
    _sharpe,
    _sortino,
    compute_portfolio_metrics,
)
from app.services.recommendations import build_offline_fund_recommendations
from app.services.risk import (
    evaluate_portfolio_risk,
    holding_weight_percent,
    resolve_weight_denominator,
)
from app.services.selection_baseline_evaluation import (
    evaluate_candidate_baselines,
    freeze_candidate_baselines,
)


def test_total_return_summary_does_not_treat_distribution_as_loss() -> None:
    history = FundNavHistory(
        fund_code="000001",
        fund_name="测试基金",
        source="test",
        points=[
            FundNavPoint(date="2026-01-01", nav=1.0),
            # 单位净值因分红下降 10%，但官方日增长率为 0%，总收益没有损失。
            FundNavPoint(
                date="2026-01-02",
                nav=0.9,
                daily_return_percent=0.0,
            ),
            FundNavPoint(
                date="2026-01-05",
                nav=0.909,
                daily_return_percent=1.0,
            ),
        ],
    )

    summary = summarize_nav_history(history, window_days=None)

    assert summary is not None
    assert summary["period_change_percent"] == pytest.approx(1.0)
    assert summary["recent_5d_daily_change_percent"] == [0.0, 1.0]
    assert summary["return_series_basis"] == "total_return_daily_growth_first"
    # 单位净值仍原样保留用于展示，不伪装成复权净值。
    assert summary["latest_nav"] == pytest.approx(0.909)


def test_max_drawdown_includes_a_negative_first_day() -> None:
    assert _max_drawdown([-0.10, 0.05]) == pytest.approx(-0.10)


def test_sharpe_and_sortino_use_daily_excess_return_contract() -> None:
    returns = [0.01, -0.005, 0.004, -0.002, 0.003]
    risk_free = 0.02
    daily_rf = (1.0 + risk_free) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    excess = [value - daily_rf for value in returns]
    expected_sharpe = (
        statistics.mean(excess)
        / statistics.stdev(excess)
        * math.sqrt(TRADING_DAYS_PER_YEAR)
    )
    downside = math.sqrt(
        sum(min(value - daily_rf, 0.0) ** 2 for value in returns) / len(returns)
    ) * math.sqrt(TRADING_DAYS_PER_YEAR)
    expected_sortino = (
        (statistics.mean(returns) - daily_rf) * TRADING_DAYS_PER_YEAR / downside
    )

    assert _sharpe(returns, risk_free) == pytest.approx(expected_sharpe)
    assert _sortino(returns, risk_free) == pytest.approx(expected_sortino)


def test_short_window_risk_metrics_are_explicitly_low_confidence() -> None:
    sample = [0.2, -0.1] * 10
    metrics = compute_portfolio_metrics(
        portfolio_daily_returns=sample,
        index_daily_returns=sample,
        holding_amounts=[60, 40],
    )

    assert metrics.available is True
    assert metrics.sample_days == 20
    assert metrics.sample_quality == "short_window"
    assert metrics.annualization_reliable is False
    assert str(MIN_ANNUALIZATION_SAMPLE_DAYS) in (metrics.message or "")


def test_concentration_uses_expected_investment_plan_for_guard_decisions() -> None:
    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=6_795.33,
            return_percent=3.92,
        ),
        Holding(
            fund_code="000002",
            fund_name="基金B",
            holding_amount=2_340.78,
            return_percent=0,
        ),
    ]
    profile = InvestorProfile(
        expected_investment_amount=40_000,
        concentration_limit_percent=40,
        max_drawdown_percent=8,
    )

    assert resolve_weight_denominator(holdings, profile) == 40_000
    assert holding_weight_percent(holdings[0], holdings, profile) == pytest.approx(16.988325)
    result = evaluate_portfolio_risk(holdings, profile)
    codes = [alert.code for alert in result.alerts]
    assert "CONCENTRATION" not in codes
    assert "PORTFOLIO_COST_BASIS_LOSS" not in codes

    recommendations = build_offline_fund_recommendations(
        AnalysisRequest(holdings=holdings, profile=profile)
    )
    assert recommendations[0].action != "减仓评估"
    assert all("超过集中度上限" not in point for point in recommendations[0].points)


def test_concentration_falls_back_to_actual_value_without_an_investment_plan() -> None:
    holdings = [
        Holding(fund_code="000001", fund_name="基金A", holding_amount=60),
        Holding(fund_code="000002", fund_name="基金B", holding_amount=40),
    ]
    profile = InvestorProfile(
        expected_investment_amount=None,
        concentration_limit_percent=35,
    )

    assert resolve_weight_denominator(holdings, profile) == 100
    assert "CONCENTRATION" in {
        alert.code for alert in evaluate_portfolio_risk(holdings, profile).alerts
    }


def test_cost_basis_loss_alert_is_not_labeled_as_max_drawdown() -> None:
    holdings = [
        Holding(
            fund_code="000001",
            fund_name="基金A",
            holding_amount=100,
            return_percent=-12,
        )
    ]
    result = evaluate_portfolio_risk(
        holdings,
        InvestorProfile(max_drawdown_percent=8, concentration_limit_percent=100),
    )
    codes = [alert.code for alert in result.alerts]

    assert "PORTFOLIO_COST_BASIS_LOSS" in codes
    assert "HOLDING_COST_BASIS_LOSS" in codes
    assert "MAX_DRAWDOWN" not in codes
    assert "HOLDING_DRAWDOWN" not in codes


def test_path_metrics_include_mae_mfe_drawdown_and_cvar_coverage() -> None:
    points = [("2026-01-01", 1.0)]
    value = 1.0
    daily_returns = [-0.10] + [0.01] * 19
    for index, daily_return in enumerate(daily_returns, start=2):
        value *= 1.0 + daily_return
        points.append((f"2026-01-{index:02d}", value))

    metrics = build_path_metrics(points, baseline_index=0, target_index=20)

    assert metrics["available"] is True
    assert metrics["sample_days"] == 20
    assert metrics["max_adverse_excursion_percent"] == pytest.approx(-10.0)
    assert metrics["max_drawdown_percent"] == pytest.approx(-10.0)
    assert metrics["daily_cvar_95"]["available"] is True
    assert metrics["daily_cvar_95"]["value_percent"] == pytest.approx(-10.0)


def test_no_action_counterfactual_uses_direction_and_frozen_fee() -> None:
    fee_policy = {
        "status": "available",
        "fee_source": "user_assumption",
        "round_trip_fee_percent": 1.0,
        "fee_calculation": "initial_principal_haircut",
    }
    buy = evaluate_no_action_counterfactual(
        gross_return_percent=5.0,
        evaluation_class="bullish",
        recommendation={
            "suggested_position_change_percent": 20,
            "suggested_position_change_basis": "相对当前持仓",
        },
        fee_policy=fee_policy,
    )
    reduce = evaluate_no_action_counterfactual(
        gross_return_percent=-5.0,
        evaluation_class="bearish",
        recommendation={"suggested_position_change_percent": -20},
        fee_policy=fee_policy,
    )

    assert buy["incremental_value_add_percent"] == pytest.approx(4.0)
    assert reduce["incremental_value_add_percent"] == pytest.approx(4.0)
    assert buy["hit"] is True
    assert reduce["hit"] is True


def test_candidate_baselines_are_frozen_and_evaluated_deterministically() -> None:
    report = {
        "id": "discovery-1",
        "created_at": "2026-01-01T08:00:00+00:00",
        "candidate_pool": [
            {
                "fund_code": "000002",
                "fund_name": "高质量",
                "sector_label": "半导体",
                "fund_quality_score": 90,
                "quality_gate": {"status": "eligible"},
                "estimated_total_cost_upper_bound_percent": 1.2,
            },
            {
                "fund_code": "000003",
                "fund_name": "低费用",
                "sector_label": "半导体",
                "fund_quality_score": 80,
                "quality_gate": {"status": "eligible"},
                "estimated_total_cost_upper_bound_percent": 0.2,
            },
        ],
    }
    specs = freeze_candidate_baselines(
        report=report,
        facts={},
        recommendation={
            "fund_code": "000001",
            "sector_name": "半导体",
        },
    )
    assert specs["quality_only_peer"]["fund_code"] == "000002"
    assert specs["low_fee_peer"]["fund_code"] == "000003"

    def fetch_nav(code: str, *, trading_days: int):
        _ = trading_days
        multiplier = 1.02 if code == "000002" else 1.01
        rows = [{"date": "2026-01-01", "nav": 1.0}]
        for index in range(1, 6):
            rows.append(
                {
                    "date": f"2026-01-{index + 1:02d}",
                    "nav": multiplier**index,
                    "daily_growth": (multiplier - 1.0) * 100.0,
                }
            )
        return {"data": rows}

    results = evaluate_candidate_baselines(
        specs,
        execution_date="2026-01-01",
        horizon=5,
        target_net_return_percent=8.0,
        fetch_nav=fetch_nav,
        trading_days=90,
        fee_policy={
            "status": "available",
            "fee_source": "user_assumption",
            "round_trip_fee_percent": 1.0,
            "fee_calculation": "initial_principal_haircut",
        },
    )

    quality = results["comparators"]["quality_only_peer"]
    low_fee = results["comparators"]["low_fee_peer"]
    assert quality["status"] == "mature"
    assert low_fee["status"] == "mature"
    assert quality["net_total_return_percent"] > low_fee["net_total_return_percent"]
