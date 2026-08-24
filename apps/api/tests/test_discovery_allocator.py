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
    _entry_maturity_tranche_ratio_cap,
    _sector_exposures,
    apply_deterministic_discovery_allocation,
)
from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.fund_tradeability import TRADEABILITY_GATE_SCHEMA_VERSION
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_TO_START,
)


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
    risk_context: dict | None = None,
    priority_inputs: dict | None = None,
    tranche_ratio_cap: float | None = None,
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
        risk_context=risk_context,
        priority_inputs=priority_inputs,
        current_tranche_ratio_cap=tranche_ratio_cap,
        amount_step_yuan=step,
    )


def _amounts(plan: dict) -> dict[str, float]:
    return {
        row["fund_code"]: row["suggested_amount_yuan"]
        for row in plan["allocations"]
    }


def _recommendation(sector: str) -> DiscoveryRecommendation:
    return DiscoveryRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        sector_name=sector,
        action="分批买入",
        confidence="中",
        points=["测试"],
    )


def test_entry_maturity_v3_scales_the_deterministic_first_tranche() -> None:
    facts = {
        "sector_opportunities": [
            {
                "sector_label": "半导体",
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "entry_state": ENTRY_READY_TO_START,
                "first_tranche_scale": 0.6,
            }
        ]
    }

    assert _entry_maturity_tranche_ratio_cap(
        facts, [_recommendation("半导体")]
    ) == 0.12


def test_entry_maturity_v2_keeps_the_twenty_percent_cap() -> None:
    facts = {
        "sector_opportunities": [
            {
                "sector_label": "半导体",
                "score_policy_version": ENTRY_POLICY_VERSION,
                "entry_state": ENTRY_READY_TO_START,
            }
        ]
    }

    assert _entry_maturity_tranche_ratio_cap(
        facts, [_recommendation("半导体")]
    ) == 0.20


def test_fund_recovery_position_override_keeps_v3_tranche_cap() -> None:
    facts = {
        "candidate_pool": [
            {
                "fund_code": "000001",
                "fund_entry_signal": {"entry_ready": True},
            }
        ],
        "sector_opportunities": [
            {
                "sector_label": "半导体",
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "entry_state": "ready_on_pullback",
                "trend_strength_score": 75.0,
                "participation_score": 42.0,
                "position_risk_score": 20.0,
                "entry_gate_inputs": {"mainline_status": "confirmed"},
                "first_tranche_scale": 0.6,
            }
        ],
    }

    assert _entry_maturity_tranche_ratio_cap(
        facts, [_recommendation("半导体")]
    ) == 0.12


def test_improving_flow_probe_uses_reduced_sector_and_fund_scale() -> None:
    facts = {
        "candidate_pool": [
            {
                "fund_code": "000001",
                "fund_entry_signal": {
                    "entry_ready": True,
                    "entry_path": "benign_pullback",
                    "first_tranche_scale": 0.5,
                },
            }
        ],
        "sector_opportunities": [
            {
                "sector_label": "中药",
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "entry_state": "ready_on_pullback",
                "flow_improving_probe_eligible": True,
                "first_tranche_scale": 0.4,
            }
        ],
    }

    assert _entry_maturity_tranche_ratio_cap(
        facts, [_recommendation("中药")]
    ) == 0.08


def test_probability_early_probe_uses_probability_sized_tranche() -> None:
    facts = {
        "candidate_pool": [
            {
                "fund_code": "000001",
                "fund_entry_signal": {
                    "entry_ready": False,
                    "early_probe_ready": True,
                    "first_tranche_scale": 0.4,
                },
            }
        ],
        "sector_opportunities": [
            {
                "sector_label": "云计算",
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "entry_state": "forming",
                "trend_formation_probability": 61.0,
                "probability_early_probe_eligible": True,
                "first_tranche_scale": 0.25,
            }
        ],
    }

    assert _entry_maturity_tranche_ratio_cap(
        facts, [_recommendation("云计算")]
    ) == 0.05


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
    with_dca = _allocate([candidate])
    without_dca = _allocate([candidate], prefer_dca=False)

    assert with_dca["policy"]["applied_current_tranche_ratio"] == 0.25
    assert with_dca["budget"]["current_tranche_cap_yuan"] == 2_500
    assert without_dca["policy"]["applied_current_tranche_ratio"] == 0.35
    assert without_dca["budget"]["current_tranche_cap_yuan"] == 3_500


def test_entry_maturity_can_cap_initial_tranche_without_changing_profile_policy() -> None:
    plan = _allocate(
        [_candidate("000001", "科技")],
        prefer_dca=False,
        tranche_ratio_cap=0.20,
    )

    assert plan["policy"]["nominal_current_tranche_ratio"] == 0.35
    assert plan["policy"]["current_tranche_ratio_cap"] == 0.20
    assert plan["policy"]["applied_current_tranche_ratio"] == 0.20
    assert plan["budget"]["current_tranche_cap_yuan"] == 2_000


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


def test_current_allocation_does_not_precommit_followup_orders() -> None:
    plan = _allocate([_candidate("000001", "科技")])

    allocation = plan["allocations"][0]
    assert allocation["suggested_amount_yuan"] > 0
    assert allocation["revalidation_required"] is True
    assert "future_tranches" not in allocation
    assert "deferred_future_tranches_yuan" not in plan["unallocated_budget"]


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


def test_unclassified_exposure_keys_are_skipped_not_blocking() -> None:
    plan = _allocate(
        [_candidate("000001", "科技")],
        exposures={"未分类": 4_494.37, "科技": 1_000},
        budget=10_000,
        cash=10_000,
        denominator=30_000,
        concentration=35,
    )

    assert plan["status"] != "blocked"
    assert plan["allocations"]
    assert plan["allocations"][0]["suggested_amount_yuan"] > 0


def test_sector_exposures_skip_unclassified_holdings() -> None:
    exposures = _sector_exposures(
        [
            {
                "fund_code": "012200",
                "sector_name": None,
                "holding_amount": 2_227.19,
            },
            {
                "fund_code": "017787",
                "sector_name": "",
                "holding_amount": 2_267.18,
            },
            {
                "fund_code": "002610",
                "sector_name": "黄金",
                "holding_amount": 2_105.25,
            },
            {
                "fund_code": "021959",
                "sector_name": "未分类",
                "holding_amount": 1_660.24,
            },
        ]
    )

    assert exposures == {"黄金": 2_105.25}


def test_sector_exposures_still_fail_closed_on_classified_missing_amount() -> None:
    assert (
        _sector_exposures(
            [
                {
                    "fund_code": "002610",
                    "sector_name": "黄金",
                    "holding_amount": None,
                }
            ]
        )
        is None
    )


def test_allocation_keeps_other_sectors_when_holdings_are_unclassified() -> None:
    recommendations = [
        DiscoveryRecommendation(
            fund_code="021362",
            fund_name="易方达黄金股指数发起式A",
            sector_name="黄金股",
            action="分批买入",
            suggested_amount_yuan=100,
            points=["候选质量通过"],
            risks=["波动风险"],
        )
    ]
    projected, plan, _, caveats = apply_deterministic_discovery_allocation(
        recommendations,
        candidate_pool=[
            {
                "fund_code": "021362",
                "fund_name": "易方达黄金股指数发起式A",
                "sector_name": "黄金股",
                "quality_action": "eligible",
                "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            }
        ],
        discovery_facts={
            "portfolio_gap": {
                "weight_denominator_yuan": 30_000,
                "holdings_slim": [
                    {
                        "fund_code": "012200",
                        "fund_name": "新华鑫科技3个月滚动持有灵活配置混合A",
                        "sector_name": None,
                        "holding_amount": 2_227.19,
                    },
                    {
                        "fund_code": "017787",
                        "fund_name": "万家宏观择时多策略混合C",
                        "sector_name": "",
                        "holding_amount": 2_267.18,
                    },
                    {
                        "fund_code": "002610",
                        "fund_name": "博时黄金ETF联接A",
                        "sector_name": "黄金",
                        "holding_amount": 2_105.25,
                    },
                ],
            }
        },
        profile=InvestorProfile(
            avoid_chasing=False,
            prefer_dca=True,
            concentration_limit_percent=35,
            expected_investment_amount=30_000,
        ),
        budget_yuan=10_000,
        decision_at=None,
    )

    assert plan["status"] != "blocked"
    assert "sector_exposure_unavailable" not in (
        (plan.get("unallocated_budget") or {}).get("reason_codes") or []
    )
    assert projected[0].action == "分批买入"
    assert (projected[0].suggested_amount_yuan or 0) > 0
    assert not any("清除全部买入金额" in item for item in caveats)
