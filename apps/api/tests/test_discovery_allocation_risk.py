from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import FundNavHistory, FundNavPoint
from app.services.discovery_allocation_risk import (
    RISK_CONTEXT_SCHEMA_VERSION,
    build_discovery_risk_context,
)
from app.services.discovery_allocator import allocate_discovery_candidates
from app.services.fund_tradeability import TRADEABILITY_GATE_SCHEMA_VERSION


DECISION_AT = datetime(2026, 7, 14, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
EFFECTIVE_DATE = date(2026, 7, 14)


def _rows() -> list[dict]:
    return [
        {"fund_code": "000001", "fund_name": "候选甲"},
        {"fund_code": "000002", "fund_name": "候选乙"},
    ]


def _holdings() -> list[dict]:
    return [
        {"fund_code": "100001", "fund_name": "持仓甲", "holding_amount": 800},
        {"fund_code": "100002", "fund_name": "持仓乙", "holding_amount": 200},
    ]


def _points(
    code: str,
    *,
    end: date = EFFECTIVE_DATE,
    count: int = 96,
) -> list[dict]:
    phase = int(code[-2:]) / 7.0
    nav = 1.0 + int(code[-1]) * 0.01
    output: list[dict] = []
    for index in range(count):
        day = end - timedelta(days=count - 1 - index)
        daily_return = (
            0.0004
            + 0.0035 * math.sin(index * 0.31 + phase)
            + 0.0018 * math.cos(index * 0.13 * (1.0 + phase / 10.0))
        )
        nav *= 1.0 + daily_return
        output.append({"date": day.isoformat(), "nav": nav})
    return output


def _fetch(code: str, _name: str, _days: int):
    return _points(code)


def _build(
    *,
    candidates: list[dict] | None = None,
    holdings: list[dict] | None = None,
    fetch_nav=_fetch,
    decision_at: datetime = DECISION_AT,
) -> dict:
    return build_discovery_risk_context(
        _rows() if candidates is None else candidates,
        _holdings() if holdings is None else holdings,
        decision_at=decision_at,
        fetch_nav=fetch_nav,
    )


def test_builds_qualified_symmetric_psd_context_with_audit_fields() -> None:
    result = _build()

    assert result["schema_version"] == RISK_CONTEXT_SCHEMA_VERSION
    assert result["status"] == "qualified"
    assert result["qualified"] is True
    assert result["candidate_common_return_sample_days"] >= 60
    assert result["current_holdings_nav_amount_coverage_ratio"] == 1.0
    assert len(result["snapshot_hash"]) == 64

    covariance = result["covariance_by_code"]
    codes = sorted(covariance)
    for left in codes:
        assert covariance[left][left] > 0
        for right in codes:
            assert covariance[left][right] == covariance[right][left]

    # For the 2x2 case, non-negative determinant is exactly the PSD condition.
    determinant = (
        covariance[codes[0]][codes[0]] * covariance[codes[1]][codes[1]]
        - covariance[codes[0]][codes[1]] ** 2
    )
    assert determinant >= -1e-15
    assert set(result["max_drawdown_percent_by_code"]) == set(codes)
    assert set(
        result["positive_correlation_penalty_to_current_holdings_by_code"]
    ) == set(codes)
    assert all(
        0 <= value <= 1
        for value in result[
            "positive_correlation_penalty_to_current_holdings_by_code"
        ].values()
    )
    assert result["scenario_drawdown"][
        "equal_weight_candidate_basket_return_sample_days"
    ] >= 60
    assert result["series_by_code"]["000001"]["source"] == "injected_points"


def test_qualified_context_is_directly_consumable_by_deterministic_allocator() -> None:
    risk_context = _build(holdings=[])
    candidates = []
    for index, row in enumerate(_rows()):
        candidates.append(
            {
                **row,
                "sector_name": f"板块{index}",
                "quality_action": "eligible",
                "quality_gate": {"status": "eligible", "eligible": True},
                "tradeability_gate": {
                    "schema_version": TRADEABILITY_GATE_SCHEMA_VERSION,
                    "status": "eligible",
                    "effective_initial_min_purchase_yuan": 100,
                    "effective_additional_min_purchase_yuan": 10,
                    "effective_min_purchase_yuan": 100,
                    "max_purchase_yuan": None,
                    "max_purchase_unlimited": True,
                    "max_period": "day",
                    "max_scope": "provider_channel_unknown_remaining",
                    "revalidation_required": True,
                    "reason_codes": [],
                },
            }
        )

    plan = allocate_discovery_candidates(
        candidates,
        requested_budget_yuan=10_000,
        confirmed_cash_yuan=10_000,
        existing_sector_exposure_yuan={},
        concentration_denominator_yuan=20_000,
        concentration_limit_percent=35,
        prefer_dca=True,
        decision_style="conservative",
        risk_context=risk_context,
    )

    assert plan["risk_context"]["status"] == "qualified"
    assert plan["allocations"]


def test_pit_filters_future_points_before_every_statistic() -> None:
    historical_decision = datetime(
        2026, 7, 10, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )

    def future_fetch(future_nav: float):
        def fetch(code: str, _name: str, _days: int):
            points = _points(code)
            for point in points:
                if point["date"] > "2026-07-10":
                    point["nav"] = future_nav
            return points

        return fetch

    low_future = _build(
        holdings=[],
        fetch_nav=future_fetch(0.01),
        decision_at=historical_decision,
    )
    high_future = _build(
        holdings=[],
        fetch_nav=future_fetch(1000.0),
        decision_at=historical_decision,
    )

    assert low_future["status"] == high_future["status"] == "qualified"
    assert (
        low_future["max_drawdown_percent_by_code"]
        == high_future["max_drawdown_percent_by_code"]
    )
    assert low_future["covariance_by_code"] == high_future["covariance_by_code"]
    assert low_future["scenario_drawdown"] == high_future["scenario_drawdown"]
    assert low_future["effective_trade_date"] == "2026-07-10"
    assert low_future["series_by_code"]["000001"]["latest_nav_date"] == "2026-07-10"
    assert low_future["series_by_code"]["000001"]["future_points_dropped"] == 4


def test_candidate_common_sample_below_60_fails_closed() -> None:
    def short_fetch(code: str, _name: str, _days: int):
        return _points(code, count=60)  # 59 return observations

    result = _build(holdings=[], fetch_nav=short_fetch)

    assert result["status"] == "unqualified"
    assert "candidate_common_return_sample_insufficient" in result["reason_codes"]
    assert result["covariance_by_code"] == {}


def test_stale_candidate_nav_fails_closed() -> None:
    def stale_fetch(code: str, _name: str, _days: int):
        return _points(code, end=EFFECTIVE_DATE - timedelta(days=8))

    result = _build(holdings=[], fetch_nav=stale_fetch)

    assert result["status"] == "unqualified"
    assert "candidate_nav_stale" in result["reason_codes"]


def test_holding_nav_coverage_allows_exactly_80_percent_and_penalizes_unknown() -> None:
    def partial_fetch(code: str, _name: str, _days: int):
        if code == "100002":
            return []
        return _points(code)

    result = _build(fetch_nav=partial_fetch)

    assert result["status"] == "qualified"
    assert result["current_holdings_nav_amount_coverage_ratio"] == 0.8
    assert result["current_holdings_covered_amount_yuan"] == 800
    assert all(
        value >= 0.2
        for value in result[
            "positive_correlation_penalty_to_current_holdings_by_code"
        ].values()
    )


def test_holding_nav_coverage_below_80_percent_fails_closed() -> None:
    holdings = [
        {"fund_code": "100001", "fund_name": "持仓甲", "holding_amount": 790},
        {"fund_code": "100002", "fund_name": "持仓乙", "holding_amount": 210},
    ]

    def partial_fetch(code: str, _name: str, _days: int):
        return [] if code == "100002" else _points(code)

    result = _build(holdings=holdings, fetch_nav=partial_fetch)

    assert result["status"] == "unqualified"
    assert result["current_holdings_nav_amount_coverage_ratio"] == 0.79
    assert (
        "current_holdings_nav_amount_coverage_insufficient"
        in result["reason_codes"]
    )
    assert result["max_drawdown_percent_by_code"] == {}


def test_no_holdings_is_100_percent_covered_cash_baseline() -> None:
    result = _build(holdings=[])

    assert result["status"] == "qualified"
    assert result["current_holdings_nav_amount_coverage_ratio"] == 1.0
    assert result["current_holdings_nav_amount_coverage_percent"] == 100.0
    assert result["scenario_drawdown"][
        "current_portfolio_max_drawdown_percent"
    ] == 0.0
    assert result["scenario_drawdown"]["current_portfolio_basis"] == (
        "no_holdings_cash_baseline"
    )
    assert set(
        result["positive_correlation_penalty_to_current_holdings_by_code"].values()
    ) == {0.0}


def test_snapshot_hash_is_stable_across_input_and_point_order() -> None:
    def reversed_fetch(code: str, _name: str, _days: int):
        return list(reversed(_points(code)))

    first = _build()
    second = _build(
        candidates=list(reversed(_rows())),
        holdings=list(reversed(_holdings())),
        fetch_nav=reversed_fetch,
    )

    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first == second


def test_fetch_nav_accepts_fund_nav_history() -> None:
    def history_fetch(code: str, name: str, _days: int):
        points = [FundNavPoint(**point) for point in _points(code)]
        return FundNavHistory(
            fund_code=code,
            fund_name=name,
            source="unit-test-provider",
            points=points,
            latest_nav=points[-1].nav,
            latest_date=points[-1].date,
        )

    result = _build(holdings=[], fetch_nav=history_fetch)

    assert result["status"] == "qualified"
    assert result["series_by_code"]["000001"]["source"] == "unit-test-provider"


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda points: points.__setitem__(20, {"date": "not-a-date", "nav": 1.0}),
            "nav_point_date_invalid",
        ),
        (
            lambda points: points.__setitem__(20, {"date": points[20]["date"], "nav": math.nan}),
            "nav_point_value_invalid",
        ),
        (
            lambda points: points.__setitem__(20, {"date": points[20]["date"], "nav": math.inf}),
            "nav_point_value_invalid",
        ),
    ],
)
def test_malformed_nav_values_fail_closed(mutator, reason: str) -> None:
    def malformed_fetch(code: str, _name: str, _days: int):
        points = _points(code)
        if code == "000001":
            mutator(points)
        return points

    result = _build(holdings=[], fetch_nav=malformed_fetch)

    assert result["status"] == "unqualified"
    assert reason in result["reason_codes"]
    assert result["covariance_by_code"] == {}


@pytest.mark.parametrize(
    ("candidates", "holdings", "reason"),
    [
        (
            [
                {"fund_code": "000001", "fund_name": "甲"},
                {"fund_code": "000001", "fund_name": "甲重复"},
            ],
            [],
            "candidate_fund_code_duplicated",
        ),
        (
            [{"fund_code": "000000", "fund_name": "未知"}],
            [],
            "candidate_fund_code_missing_or_unknown",
        ),
        (
            _rows(),
            [
                {"fund_code": "100001", "fund_name": "持仓", "holding_amount": 10},
                {"fund_code": "100001", "fund_name": "重复", "holding_amount": 20},
            ],
            "holding_fund_code_duplicated",
        ),
    ],
)
def test_missing_unknown_or_duplicate_codes_fail_closed(
    candidates: list[dict], holdings: list[dict], reason: str
) -> None:
    result = _build(candidates=candidates, holdings=holdings)

    assert result["status"] == "unqualified"
    assert reason in result["reason_codes"]
    assert result["max_drawdown_percent_by_code"] == {}
