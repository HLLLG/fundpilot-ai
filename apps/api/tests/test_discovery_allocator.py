from __future__ import annotations

from itertools import permutations

import pytest

from app.services.discovery_allocator import (
    ALLOCATION_PLAN_SCHEMA_VERSION,
    CURRENT_AMOUNT_SEMANTICS,
    PEER_RANK_SCHEMA_VERSION,
    PRIORITY_INPUT_SCHEMA_VERSION,
    QUALIFIED_RISK_ONLY_MODE,
    RISK_AWARE_MODE,
    RISK_CONTEXT_SCHEMA_VERSION,
    allocate_discovery_candidates,
)
from app.services.discovery_allocation_service import (
    apply_reported_holdings_overlap_guard,
)
from app.services.fund_tradeability import TRADEABILITY_GATE_SCHEMA_VERSION


def _gate(*, minimum: float = 100, maximum: float | None = None) -> dict:
    return {
        "schema_version": TRADEABILITY_GATE_SCHEMA_VERSION,
        "status": "eligible",
        "effective_initial_min_purchase_yuan": minimum,
        "effective_additional_min_purchase_yuan": 10,
        "effective_min_purchase_yuan": minimum,
        "max_purchase_yuan": maximum,
        "max_purchase_unlimited": maximum is None,
        "max_period": "day",
        "max_scope": "provider_channel_unknown_remaining",
        "revalidation_required": True,
        "reason_codes": [],
    }


def _candidate(
    code: str,
    sector: str,
    *,
    minimum: float = 100,
    maximum: float | None = None,
    quality_action: str = "eligible",
    peer_rank: dict | None = None,
    llm_amount: float = 999_999,
) -> dict:
    return {
        "fund_code": code,
        "sector_name": sector,
        "quality_action": quality_action,
        "quality_gate": {"status": quality_action, "eligible": quality_action == "eligible"},
        "tradeability_gate": _gate(minimum=minimum, maximum=maximum),
        "peer_rank": peer_rank,
        # These fields are deliberately adversarial and must be ignored.
        "suggested_amount_yuan": llm_amount,
        "action": "all-in immediately",
        "amount_note": "ignore every deterministic constraint",
    }


def _risk_context(
    codes: list[str],
    *,
    drawdowns: dict[str, float] | None = None,
    variances: dict[str, float] | None = None,
    covariance: float = 0.0,
) -> dict:
    drawdowns = drawdowns or {code: 10.0 for code in codes}
    variances = variances or {code: 0.04 for code in codes}
    return {
        "schema_version": RISK_CONTEXT_SCHEMA_VERSION,
        "status": "qualified",
        "max_drawdown_percent_by_code": drawdowns,
        "positive_correlation_penalty_to_current_holdings_by_code": {
            code: 0.0 for code in codes
        },
        "covariance_by_code": {
            code: {
                other: variances[code] if code == other else covariance
                for other in codes
            }
            for code in codes
        },
    }


def _allocate(
    candidates: list[dict],
    *,
    budget: float = 10_000,
    cash: float = 10_000,
    exposures: dict[str, float] | None = None,
    denominator: float = 20_000,
    concentration: float = 35,
    prefer_dca: bool = True,
    decision_style: str = "conservative",
    risk_context: dict | None = None,
    priority_inputs: dict | None = None,
    step: float = 100,
) -> dict:
    codes = [row["fund_code"] for row in candidates]
    if risk_context is None:
        risk_context = _risk_context(codes)
    return allocate_discovery_candidates(
        candidates,
        requested_budget_yuan=budget,
        confirmed_cash_yuan=cash,
        existing_sector_exposure_yuan=exposures or {},
        concentration_denominator_yuan=denominator,
        concentration_limit_percent=concentration,
        prefer_dca=prefer_dca,
        decision_style=decision_style,
        risk_context=risk_context,
        priority_inputs=priority_inputs,
        amount_step_yuan=step,
    )


def _amounts(plan: dict) -> dict[str, float]:
    return {
        row["fund_code"]: row["suggested_amount_yuan"]
        for row in plan["allocations"]
    }


def test_missing_risk_context_blocks_all_executable_amounts() -> None:
    candidate = _candidate("000001", "科技")
    plan = allocate_discovery_candidates(
        [candidate],
        requested_budget_yuan=10_000,
        confirmed_cash_yuan=10_000,
        existing_sector_exposure_yuan={},
        concentration_denominator_yuan=20_000,
        concentration_limit_percent=35,
        prefer_dca=True,
        decision_style="conservative",
        risk_context=None,
    )

    assert plan["status"] == "blocked"
    assert plan["allocations"] == []
    assert plan["budget"]["allocated_current_tranche_yuan"] == 0
    assert plan["risk_context"]["status"] == "risk_context_unavailable"
    assert plan["risk_context"]["fallback_rule"] == (
        "no_executable_amount_without_qualified_risk_context"
    )


def test_qualified_risk_only_plan_allocates_current_verified_tranche() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "医药")]
    plan = _allocate(candidates, budget=10_000, cash=8_000)

    assert plan["schema_version"] == ALLOCATION_PLAN_SCHEMA_VERSION
    assert plan["status"] == "allocated"
    assert plan["allocation_mode"] == QUALIFIED_RISK_ONLY_MODE
    assert plan["amount_semantics"] == CURRENT_AMOUNT_SEMANTICS
    assert plan["budget"]["current_tranche_cap_yuan"] == 2_000
    assert plan["budget"]["allocated_current_tranche_yuan"] == 2_000
    assert sum(_amounts(plan).values()) == 2_000


def test_current_tranche_ratio_uses_profile_policy() -> None:
    candidate = _candidate("000001", "科技")
    conservative = _allocate([candidate], decision_style="conservative")
    aggressive = _allocate(
        [candidate], decision_style="aggressive", prefer_dca=False
    )

    assert conservative["policy"]["applied_current_tranche_ratio"] == 0.25
    assert conservative["budget"]["current_tranche_cap_yuan"] == 2_500
    assert aggressive["policy"]["applied_current_tranche_ratio"] == 0.5
    assert aggressive["budget"]["current_tranche_cap_yuan"] == 5_000


def test_confirmed_cash_caps_current_tranche() -> None:
    plan = _allocate([_candidate("000001", "科技")], budget=10_000, cash=2_000)

    assert plan["budget"]["spendable_yuan"] == 2_000
    assert plan["budget"]["current_tranche_cap_yuan"] == 500
    assert plan["budget"]["allocated_current_tranche_yuan"] == 500
    assert plan["unallocated_budget"]["unavailable_due_to_cash_yuan"] == 8_000


def test_daily_limit_minimum_and_amount_step_are_hard_constraints() -> None:
    candidate = _candidate("000001", "科技", minimum=150, maximum=950)
    plan = _allocate([candidate], budget=10_000, step=100)

    row = plan["allocations"][0]
    assert row["suggested_amount_yuan"] == 900
    assert row["suggested_amount_yuan"] >= 150
    assert row["suggested_amount_yuan"] % 100 == 0
    assert plan["unallocated_budget"]["current_tranche_unallocated_yuan"] == 1_600


def test_unlimited_daily_limit_still_obeys_budget_and_tranche() -> None:
    plan = _allocate([_candidate("000001", "科技", maximum=None)])

    assert _amounts(plan) == {"000001": 2_500}


def test_unused_capacity_is_redistributed_across_sectors() -> None:
    candidates = [
        _candidate("000001", "科技", maximum=100),
        _candidate("000002", "医药", maximum=None),
    ]
    plan = _allocate(candidates, budget=10_000)

    assert _amounts(plan) == {"000001": 100, "000002": 2_400}
    assert plan["budget"]["allocated_current_tranche_yuan"] == 2_500


def test_same_sector_cap_subtracts_existing_exposure() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "科技")]
    plan = _allocate(
        candidates,
        budget=10_000,
        denominator=10_000,
        concentration=30,
        exposures={"科技": 2_500},
    )

    assert sum(_amounts(plan).values()) == 500
    assert plan["budget"]["allocated_current_tranche_yuan"] == 500


def test_request_level_sector_cap_prevents_one_theme_from_taking_all() -> None:
    candidate = _candidate("000001", "科技")
    plan = _allocate(
        [candidate],
        budget=10_000,
        denominator=1_000_000,
        concentration=10,
        decision_style="aggressive",
        prefer_dca=False,
    )

    assert _amounts(plan) == {"000001": 1_000}


def test_candidate_below_rounded_minimum_is_excluded() -> None:
    candidate = _candidate("000001", "科技", minimum=150, maximum=199)
    plan = _allocate([candidate])

    assert plan["status"] == "blocked"
    assert plan["allocations"] == []
    assert plan["excluded_candidates"][0]["reason_codes"] == [
        "rounded_purchase_capacity_below_initial_minimum"
    ]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"schema_version": "legacy"}, "tradeability_gate_schema_invalid"),
        ({"status": "watch_only"}, "tradeability_gate_not_eligible"),
        ({"effective_initial_min_purchase_yuan": None}, "effective_initial_minimum_invalid"),
        (
            {"max_purchase_yuan": None, "max_purchase_unlimited": False},
            "maximum_purchase_unknown",
        ),
    ],
)
def test_invalid_b1_gate_fails_closed(mutation: dict, reason: str) -> None:
    candidate = _candidate("000001", "科技", maximum=1_000)
    candidate["tradeability_gate"].update(mutation)
    plan = _allocate([candidate])

    assert plan["allocations"] == []
    assert reason in plan["excluded_candidates"][0]["reason_codes"]


def test_quality_action_must_be_eligible_even_when_llm_says_buy() -> None:
    candidate = _candidate("000001", "科技", quality_action="watch_only")
    plan = _allocate([candidate])

    assert plan["allocations"] == []
    assert "quality_action_not_eligible" in plan["excluded_candidates"][0][
        "reason_codes"
    ]


def test_llm_amount_action_and_prose_do_not_change_plan() -> None:
    first = _candidate("000001", "科技", llm_amount=1)
    second = _candidate("000001", "科技", llm_amount=999_999_999)
    second["action"] = "建议关注"
    second["amount_note"] = "完全不同的文本"

    assert _allocate([first]) == _allocate([second])


def test_only_qualified_priority_input_can_tilt_allocation() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "医药")]
    invalid = {
        "000002": {
            "schema_version": PRIORITY_INPUT_SCHEMA_VERSION,
            "status": "unqualified",
            "score": 100,
        }
    }
    qualified = {
        "000002": {
            "schema_version": PRIORITY_INPUT_SCHEMA_VERSION,
            "status": "qualified",
            "score": 100,
        }
    }

    baseline = _amounts(_allocate(candidates, budget=20_000))
    assert _amounts(_allocate(candidates, budget=20_000, priority_inputs=invalid)) == baseline
    tilted = _amounts(_allocate(candidates, budget=20_000, priority_inputs=qualified))
    assert tilted["000002"] > tilted["000001"]


def test_peer_tilt_requires_qualified_peer_rank_v1() -> None:
    invalid_peer = {
        "schema_version": PEER_RANK_SCHEMA_VERSION,
        # Data comparability alone is descriptive and must not affect money.
        "qualified": True,
        "execution_tilt_eligible": False,
        "execution_tilt_gate": {"status": "blocked", "eligible": False},
        "execution_score_percentile": 100,
    }
    qualified_peer = {
        "schema_version": PEER_RANK_SCHEMA_VERSION,
        "qualified": True,
        "execution_tilt_eligible": True,
        "execution_tilt_gate": {"status": "qualified", "eligible": True},
        "execution_score_percentile": 100,
    }
    baseline_candidates = [
        _candidate("000001", "科技"),
        _candidate("000002", "医药", peer_rank=invalid_peer),
    ]
    qualified_candidates = [
        _candidate("000001", "科技"),
        _candidate("000002", "医药", peer_rank=qualified_peer),
    ]

    no_peer_candidates = [
        _candidate("000001", "科技"),
        _candidate("000002", "医药"),
    ]
    baseline = _amounts(_allocate(no_peer_candidates, budget=20_000))
    assert _amounts(_allocate(baseline_candidates, budget=20_000)) == baseline
    tilted_plan = _allocate(qualified_candidates, budget=20_000)
    tilted = _amounts(tilted_plan)
    assert tilted["000002"] > tilted["000001"]
    assert tilted_plan["allocation_mode"] == RISK_AWARE_MODE


def test_qualified_risk_context_allocates_less_to_higher_risk_candidate() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "医药")]
    risk = _risk_context(
        ["000001", "000002"],
        drawdowns={"000001": 5, "000002": 40},
        variances={"000001": 0.01, "000002": 0.09},
    )
    plan = _allocate(candidates, budget=20_000, risk_context=risk)

    amounts = _amounts(plan)
    assert amounts["000001"] > amounts["000002"]
    assert plan["allocation_mode"] == QUALIFIED_RISK_ONLY_MODE


def test_current_portfolio_positive_correlation_penalty_reduces_allocation() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "医药")]
    risk = _risk_context(["000001", "000002"])
    risk["positive_correlation_penalty_to_current_holdings_by_code"] = {
        "000001": 0.0,
        "000002": 1.0,
    }

    plan = _allocate(candidates, budget=20_000, risk_context=risk)

    amounts = _amounts(plan)
    assert amounts["000001"] > amounts["000002"]
    second = next(row for row in plan["allocations"] if row["fund_code"] == "000002")
    assert second["priority"]["current_portfolio_correlation_penalty"] == 1.0


def test_reported_holdings_overlap_guard_only_downweights_qualified_positive_overlap() -> None:
    candidates = [_candidate("000001", "绉戞妧"), _candidate("000002", "鍖昏嵂")]
    base_risk = _risk_context(["000001", "000002"])
    lookthrough = {
        "fund_lookthrough": {
            "research_hash": "a" * 64,
            "candidates": [
                {
                    "fund_code": "000002",
                    "status": "qualified",
                    "overlap_evidence_state": "positive_same_vintage_reported_overlap",
                    "reported_as_of_disclosed_overlap_percent": 90.0,
                    "decision_use": {
                        "concentration_risk_guard_eligible": True,
                        "allocation_authorization_eligible": False,
                    },
                }
            ],
        }
    }

    guarded_risk = apply_reported_holdings_overlap_guard(
        base_risk,
        discovery_facts=lookthrough,
        candidate_codes=["000001", "000002"],
    )
    baseline = _amounts(_allocate(candidates, budget=20_000, risk_context=base_risk))
    guarded = _amounts(_allocate(candidates, budget=20_000, risk_context=guarded_risk))

    assert guarded_risk[
        "positive_correlation_penalty_to_current_holdings_by_code"
    ] == {"000001": 0.0, "000002": 0.9}
    assert guarded_risk["reported_holdings_overlap_guard"][
        "allocation_authorization_eligible"
    ] is False
    assert len(guarded_risk["snapshot_hash"]) == 64
    assert guarded["000002"] < baseline["000002"]
    assert guarded["000001"] > guarded["000002"]


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        {
            "fund_code": "000002",
            "status": "qualified",
            "overlap_evidence_state": "positive_same_vintage_reported_overlap",
            "reported_as_of_disclosed_overlap_percent": None,
            "decision_use": {
                "concentration_risk_guard_eligible": True,
                "allocation_authorization_eligible": False,
            },
        },
        {
            "fund_code": "000002",
            "status": "qualified",
            "overlap_evidence_state": "no_common_same_vintage_disclosed_scope",
            "reported_as_of_disclosed_overlap_percent": 0.0,
            "decision_use": {
                "concentration_risk_guard_eligible": False,
                "allocation_authorization_eligible": False,
            },
        },
        {
            "fund_code": "000002",
            "status": "qualified",
            "overlap_evidence_state": "cross_vintage_descriptive_only",
            "reported_as_of_disclosed_overlap_percent": 90.0,
            "decision_use": {
                "concentration_risk_guard_eligible": False,
                "allocation_authorization_eligible": False,
            },
        },
        {
            "fund_code": "000002",
            "status": "qualified",
            "overlap_evidence_state": "positive_same_vintage_reported_overlap",
            "reported_as_of_disclosed_overlap_percent": 90.0,
            "decision_use": {
                "concentration_risk_guard_eligible": True,
                "allocation_authorization_eligible": True,
            },
        },
    ],
)
def test_missing_or_ineligible_reported_overlap_is_exact_risk_noop(
    candidate: dict | None,
) -> None:
    base_risk = _risk_context(["000001", "000002"])
    facts = {
        "fund_lookthrough": {
            "research_hash": "a" * 64,
            "candidates": [] if candidate is None else [candidate],
        }
    }

    guarded = apply_reported_holdings_overlap_guard(
        base_risk,
        discovery_facts=facts,
        candidate_codes=["000001", "000002"],
    )

    assert guarded == base_risk


@pytest.mark.parametrize(
    "risk_mutation",
    [
        {"status": "unqualified"},
        {"schema_version": "legacy"},
        {"max_drawdown_percent_by_code": {"000001": 10}},
        {
            "covariance_by_code": {
                "000001": {"000001": 0.04, "000002": 0.02},
                "000002": {"000001": 0.01, "000002": 0.04},
            }
        },
        {
            "covariance_by_code": {
                "000001": {"000001": 0.04, "000002": 0.05},
                "000002": {"000001": 0.05, "000002": 0.04},
            }
        },
    ],
)
def test_unqualified_or_incomplete_risk_context_blocks(risk_mutation: dict) -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000002", "医药")]
    risk = _risk_context(["000001", "000002"])
    risk.update(risk_mutation)
    plan = _allocate(candidates, risk_context=risk)

    assert plan["status"] == "blocked"
    assert plan["allocations"] == []
    assert plan["budget"]["allocated_current_tranche_yuan"] == 0


def test_future_tranche_has_no_precommitted_amount_and_requires_revalidation() -> None:
    plan = _allocate([_candidate("000001", "科技")])

    future = plan["allocations"][0]["future_tranches"][0]
    assert future["amount_yuan"] is None
    assert future["revalidation_required"] is True
    assert "tradeability_gate_recheck" in future["preconditions"]


def test_duplicate_fund_code_fails_closed_independent_of_payload_difference() -> None:
    candidates = [_candidate("000001", "科技"), _candidate("000001", "医药")]
    plan = _allocate(candidates)

    assert plan["status"] == "blocked"
    assert plan["allocations"] == []
    assert len(plan["excluded_candidates"]) == 2
    assert all(
        "duplicate_fund_code" in row["reason_codes"]
        for row in plan["excluded_candidates"]
    )


@pytest.mark.parametrize(
    "override",
    [
        {"confirmed_cash_yuan": None},
        {"existing_sector_exposure_yuan": None},
        {"concentration_denominator_yuan": 0},
        {"concentration_limit_percent": 101},
        {"decision_style": "unknown"},
    ],
)
def test_critical_global_input_missing_blocks(override: dict) -> None:
    kwargs = {
        "requested_budget_yuan": 10_000,
        "confirmed_cash_yuan": 10_000,
        "existing_sector_exposure_yuan": {},
        "concentration_denominator_yuan": 20_000,
        "concentration_limit_percent": 35,
        "prefer_dca": True,
        "decision_style": "conservative",
        "risk_context": _risk_context(["000001"]),
    }
    kwargs.update(override)
    plan = allocate_discovery_candidates([_candidate("000001", "科技")], **kwargs)

    assert plan["status"] == "blocked"
    assert plan["allocations"] == []


def test_stable_code_tie_break_when_only_one_minimum_can_be_funded() -> None:
    candidates = [
        _candidate("000002", "医药", minimum=300),
        _candidate("000001", "科技", minimum=300),
    ]
    plan = _allocate(candidates, budget=1_200, cash=1_200)

    assert _amounts(plan) == {"000001": 300}
    assert plan["allocations"][0]["fund_code"] == "000001"


def test_plan_is_exactly_permutation_invariant() -> None:
    candidates = [
        _candidate("000004", "消费", maximum=1_700),
        _candidate("000001", "科技", maximum=1_600),
        _candidate("000003", "科技", maximum=1_900),
        _candidate("000002", "医药", maximum=2_100),
    ]
    risk = _risk_context(
        ["000001", "000002", "000003", "000004"],
        drawdowns={"000001": 8, "000002": 12, "000003": 16, "000004": 20},
        variances={"000001": 0.02, "000002": 0.03, "000003": 0.04, "000004": 0.05},
        covariance=0.005,
    )
    priority = {
        "000003": {
            "schema_version": PRIORITY_INPUT_SCHEMA_VERSION,
            "status": "qualified",
            "score": 75,
        }
    }
    expected = _allocate(
        candidates,
        budget=30_000,
        risk_context=risk,
        priority_inputs=priority,
    )

    for reordered in permutations(candidates):
        actual = _allocate(
            list(reordered),
            budget=30_000,
            risk_context=risk,
            priority_inputs=priority,
        )
        assert actual == expected
