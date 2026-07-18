from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.routes import portfolio_risk
from app.services.portfolio_fee_evidence import build_portfolio_fee_evidence
from app.services.portfolio_stress_test import (
    build_portfolio_stress_test,
    validate_portfolio_stress_test,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def _history(code: str, returns: list[float]) -> dict:
    nav = 100.0
    points = [{"date": date(2026, 1, 1).isoformat(), "nav": nav}]
    for index, value in enumerate(returns, start=1):
        nav *= 1.0 + value
        points.append(
            {
                "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "nav": nav,
            }
        )
    return {
        "fund_code": code,
        "source": "pytest.frozen_nav",
        "points": points,
    }


def test_current_weight_historical_replay_is_deterministic_and_non_executing() -> None:
    left = [0.0] * 100
    right = [0.0] * 100
    left[20] = -0.10
    right[20] = -0.20
    histories = {
        "000001": _history("000001", left),
        "000002": _history("000002", right),
    }
    holdings = [
        {"fund_code": "000001", "fund_name": "甲", "holding_amount": 7_500},
        {"fund_code": "000002", "fund_name": "乙", "holding_amount": 2_500},
    ]

    result = build_portfolio_stress_test(
        holdings,
        fetch_history=lambda code, _name, _days: histories[code],
        now=NOW,
    )

    assert result["available"] is True
    assert result["automatic_action_allowed"] is False
    assert result["forecast"] is False
    assert result["validation"] == {"status": "valid", "error_codes": []}
    assert result["sample"]["common_return_days"] == 100
    assert [row["current_weight_percent"] for row in result["holdings"]] == [75.0, 25.0]
    scenarios = {row["scenario_id"]: row for row in result["scenarios"]}
    assert scenarios["worst_observed_1d"]["return_percent"] == -12.5
    assert scenarios["worst_observed_1d"]["estimated_loss_yuan"] == 1_250.0
    assert scenarios["historical_expected_shortfall_95_1d"]["tail_observation_count"] == 5

    repeated = build_portfolio_stress_test(
        holdings,
        fetch_history=lambda code, _name, _days: histories[code],
        now=NOW,
    )
    assert repeated == result


def test_missing_one_holding_history_fails_closed_without_reweighting() -> None:
    histories = {
        "000001": _history("000001", [0.001] * 100),
        "000002": {"source": "error", "points": []},
    }
    result = build_portfolio_stress_test(
        [
            {"fund_code": "000001", "fund_name": "甲", "holding_amount": 7_500},
            {"fund_code": "000002", "fund_name": "乙", "holding_amount": 2_500},
        ],
        fetch_history=lambda code, _name, _days: histories[code],
        now=NOW,
    )

    assert result["available"] is False
    assert result["scenarios"] == []
    assert result["reason_codes"] == ["holding_nav_history_incomplete"]
    assert result["missing_fund_codes"] == ["000002"]
    assert result["validation"] == {"status": "valid", "error_codes": []}


def test_nav_gaps_are_aligned_before_returns_are_combined() -> None:
    returns = [0.0] * 70
    returns[29] = -0.10
    complete = _history("000001", returns)
    with_gap = _history("000002", returns)
    del with_gap["points"][30]
    histories = {"000001": complete, "000002": with_gap}

    result = build_portfolio_stress_test(
        [
            {"fund_code": "000001", "fund_name": "甲", "holding_amount": 5_000},
            {"fund_code": "000002", "fund_name": "乙", "holding_amount": 5_000},
        ],
        fetch_history=lambda code, _name, _days: histories[code],
        now=NOW,
    )

    assert result["available"] is True
    assert result["sample"]["common_return_days"] == 69
    scenarios = {row["scenario_id"]: row for row in result["scenarios"]}
    assert scenarios["worst_observed_1d"]["return_percent"] == -10.0


def test_future_nav_point_invalidates_the_whole_stress_artifact() -> None:
    history = _history("000001", [0.001] * 100)
    history["points"].append({"date": "2026-07-19", "nav": 120})

    result = build_portfolio_stress_test(
        [{"fund_code": "000001", "fund_name": "甲", "holding_amount": 10_000}],
        fetch_history=lambda *_args: history,
        now=NOW,
    )

    assert result["available"] is False
    assert result["reason_codes"] == ["holding_nav_history_incomplete"]
    assert result["scenarios"] == []


def test_stress_snapshot_tampering_is_detected() -> None:
    history = _history("000001", [0.001] * 100)
    result = build_portfolio_stress_test(
        [{"fund_code": "000001", "fund_name": "甲", "holding_amount": 10_000}],
        fetch_history=lambda *_args: history,
        now=NOW,
    )
    tampered = deepcopy(result)
    tampered["scenarios"][0]["estimated_loss_yuan"] = 99_999

    validation = validate_portfolio_stress_test(tampered)

    assert validation["status"] == "invalid"
    assert "snapshot_hash_invalid" in validation["error_codes"]


def test_realized_fee_evidence_keeps_unknown_distinct_from_known_zero() -> None:
    result = build_portfolio_fee_evidence(
        [
            {
                "status": "confirmed",
                "fund_code": "000001",
                "fund_name": "甲",
                "direction": "buy",
                "amount_yuan": 1_000,
                "fee_yuan": 0,
            },
            {
                "status": "confirmed",
                "fund_code": "000002",
                "fund_name": "乙",
                "direction": "sell",
                "amount_yuan": 2_000,
                "fee_yuan": None,
            },
            {
                "status": "superseded",
                "fund_code": "000003",
                "fund_name": "丙",
                "direction": "buy",
                "amount_yuan": 3_000,
                "fee_yuan": 30,
            },
        ]
    )

    assert result["status"] == "collecting"
    assert result["confirmed_transaction_count"] == 2
    assert result["known_fee_transaction_count"] == 1
    assert result["unknown_fee_transaction_count"] == 1
    assert result["known_fee_coverage_percent"] == 50.0
    assert result["total_recorded_fee_yuan"] == 0.0
    assert result["weighted_recorded_fee_percent"] == 0.0
    assert result["candidate_cost_model_eligible"] is False


def test_portfolio_risk_routes_are_bounded_and_current_user_scoped(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(portfolio_risk, "load_persisted_holdings", lambda: ([], None, None))
    monkeypatch.setattr(portfolio_risk, "list_fund_transactions", lambda: [])
    monkeypatch.setattr(
        portfolio_risk,
        "build_portfolio_stress_test",
        lambda holdings, *, lookback_days: {
            "holding_count": len(holdings),
            "lookback_days": lookback_days,
        },
    )

    stress = client.get("/api/portfolio/stress-test?lookback_days=999")
    fees = client.get("/api/portfolio/fee-evidence")

    assert stress.status_code == 200
    assert stress.json() == {"holding_count": 0, "lookback_days": 400}
    assert stress.headers["cache-control"] == "no-store"
    assert fees.status_code == 200
    assert fees.json()["status"] == "not_started"
    assert fees.headers["cache-control"] == "no-store"


def test_portfolio_stress_domain_stays_out_of_the_composition_root() -> None:
    main_source = (
        Path(__file__).parents[1] / "app" / "main.py"
    ).read_text(encoding="utf-8")

    assert "from app.routes.portfolio_risk import router as portfolio_risk_router" in main_source
    assert "app.include_router(portfolio_risk_router)" in main_source
    assert "app.services.portfolio_stress_test" not in main_source
    assert "app.services.portfolio_fee_evidence" not in main_source
