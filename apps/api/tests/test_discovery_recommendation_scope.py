from __future__ import annotations

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_payload import build_user_payload
from app.services.discovery_recommendation_scope import (
    build_recommendation_candidate_scope,
    candidates_in_recommendation_scope,
    project_candidate_decisions_for_report,
    reconcile_recommendations_with_scope,
)
from app.services.sector_opportunity_scoring import ENTRY_POLICY_VERSION_V3


def _candidate(
    code: str,
    sector: str,
    *,
    opportunity_score: float = 80.0,
    entry_ready: bool = True,
    early_probe_ready: bool = True,
    quality_status: str = "eligible",
    vehicle_status: str = "eligible",
    sector_fit: float = 30.0,
    sector_match_kind: str = "primary",
) -> dict:
    return {
        "fund_code": code,
        "fund_name": f"{sector}测试基金{code}",
        "sector_label": sector,
        "quality_gate": {
            "status": quality_status,
            "eligible": quality_status == "eligible",
        },
        "vehicle_quality_status": vehicle_status,
        "vehicle_quality_score": 72.0,
        "fund_quality_score": 68.0,
        "sector_fit_score": sector_fit,
        "sector_match_kind": sector_match_kind,
        "sector_identity_status": (
            "verified"
            if sector_match_kind in {"primary", "tracking_exact"}
            else "pending"
        ),
        "sector_identity_eligible": sector_match_kind in {"primary", "tracking_exact"},
        "opportunity_score_20_60d": opportunity_score,
        "nav_trend": {"annualized_volatility_20d_percent": 32.0},
        "fund_entry_signal": {
            "entry_ready": entry_ready,
            "early_probe_ready": early_probe_ready,
        },
    }


def _opportunity(
    sector: str,
    state: str,
    *,
    priority: float,
    probability_probe: bool = False,
) -> dict:
    return {
        "sector_label": sector,
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "entry_state": state,
        "selection_priority_score": priority,
        "opportunity_available": True,
        "probability_early_probe_eligible": probability_probe,
    }


def test_scope_excludes_stronger_funds_from_waiting_directions() -> None:
    media = _candidate("000001", "传媒", opportunity_score=72)
    medicine = _candidate("000002", "中药", opportunity_score=160)
    scope = build_recommendation_candidate_scope(
        [medicine, media],
        [
            _opportunity("传媒", "ready_to_start", priority=90),
            _opportunity("中药", "forming", priority=65),
        ],
    )

    assert scope["policy_enforced"] is True
    assert scope["ordered_eligible_fund_codes"] == ["000001"]
    assert scope["eligible_sector_labels"] == ["传媒"]
    assert scope["research_sector_labels"] == ["中药"]
    assert {
        item["fund_code"]: (item["status"], item["reason_codes"])
        for item in scope["candidate_decisions"]
    } == {
        "000001": ("actionable", []),
        "000002": ("conditional_wait", ["direction_entry_not_open"]),
    }
    assert scope["conditional_wait_fund_codes"] == ["000002"]
    assert scope["watch_only_fund_codes"] == []
    assert [row["fund_code"] for row in candidates_in_recommendation_scope(
        [medicine, media], scope
    )] == ["000001"]


def test_reconciliation_drops_waiting_pick_and_backfills_actionable_direction() -> None:
    media = _candidate("000001", "传媒")
    medicine = _candidate("000002", "中药", opportunity_score=180)
    facts = {
        "sector_opportunities": [
            _opportunity("传媒", "ready_to_start", priority=90),
            _opportunity("中药", "forming", priority=60),
        ]
    }
    model_pick = DiscoveryRecommendation(
        fund_code="000002",
        fund_name=medicine["fund_name"],
        sector_name="中药",
        action="建议关注",
        risks=["测试风险"],
    )

    reconciled, caveats = reconcile_recommendations_with_scope(
        [model_pick],
        candidate_pool=[media, medicine],
        discovery_facts=facts,
    )

    assert [item.fund_code for item in reconciled] == ["000001"]
    assert reconciled[0].sector_name == "传媒"
    assert facts["recommendation_scope_reconciliation"] == {
        "schema_version": "discovery_recommendation_scope_reconciliation.v3",
        "model_fund_codes": ["000002"],
        "dropped_fund_codes": ["000002"],
        "backfilled_fund_codes": ["000001"],
        "final_fund_codes": ["000001"],
        "cross_direction_fallback_allowed": False,
        "maximum_recommendations_per_sector": 2,
    }
    assert caveats


def test_probability_direction_requires_the_fund_early_repair_signal() -> None:
    ready = _candidate("000011", "云计算", early_probe_ready=True, entry_ready=False)
    unready = _candidate("000012", "云计算", early_probe_ready=False, entry_ready=False)
    scope = build_recommendation_candidate_scope(
        [unready, ready],
        [
            _opportunity(
                "云计算",
                "forming",
                priority=80,
                probability_probe=True,
            )
        ],
    )

    assert scope["ordered_eligible_fund_codes"] == ["000011"]
    funnel = scope["sector_funnel"][0]
    assert funnel["eligible_count"] == 1
    assert funnel["rejected_reason_counts"]["direction_entry_not_open"] == 1


def test_scope_keeps_two_best_quality_vehicles_per_sector() -> None:
    preferred = _candidate("000013", "煤炭", opportunity_score=40)
    second = _candidate("000014", "煤炭", opportunity_score=60)
    hotter_but_lower_quality = _candidate("000015", "煤炭", opportunity_score=200)
    preferred.update(fund_quality_score=90.0, vehicle_quality_score=92.0)
    second.update(fund_quality_score=82.0, vehicle_quality_score=84.0)
    hotter_but_lower_quality.update(
        fund_quality_score=65.0,
        vehicle_quality_score=68.0,
    )

    scope = build_recommendation_candidate_scope(
        [hotter_but_lower_quality, second, preferred],
        [_opportunity("煤炭", "ready_to_start", priority=90)],
    )

    assert scope["ordered_eligible_fund_codes"] == ["000013", "000014"]
    assert scope["alternate_eligible_fund_codes"] == ["000015"]
    assert scope["maximum_recommendations_per_sector"] == 2
    assert {
        item["fund_code"]: item["status"] for item in scope["candidate_decisions"]
    } == {
        "000015": "actionable",
        "000014": "actionable",
        "000013": "actionable",
    }


def test_scope_caps_global_recommendation_whitelist_at_six() -> None:
    sectors = ["sector-a", "sector-b", "sector-c", "sector-d"]
    candidates = []
    opportunities = []
    for sector_index, sector in enumerate(sectors):
        opportunities.append(
            _opportunity(
                sector,
                "ready_to_start",
                priority=100 - sector_index * 10,
            )
        )
        for rank in range(2):
            code = str(100 + sector_index * 10 + rank).zfill(6)
            candidate = _candidate(code, sector)
            candidate.update(
                fund_quality_score=90.0 - rank,
                vehicle_quality_score=90.0 - rank,
            )
            candidates.append(candidate)

    scope = build_recommendation_candidate_scope(candidates, opportunities)

    assert scope["max_recommendations"] == 6
    assert scope["maximum_recommendations_per_sector"] == 2
    assert scope["ordered_eligible_fund_codes"] == [
        "000100",
        "000110",
        "000120",
        "000130",
        "000101",
        "000111",
    ]
    assert scope["alternate_eligible_fund_codes"] == ["000121", "000131"]


def test_actionable_direction_without_verified_vehicle_is_reported_not_cross_filled() -> None:
    unverified_gold = _candidate(
        "000021",
        "贵金属",
        sector_fit=16,
        sector_match_kind="name",
    )
    waiting_fintech = _candidate("000022", "金融科技", opportunity_score=200)
    scope = build_recommendation_candidate_scope(
        [unverified_gold, waiting_fintech],
        [
            _opportunity("贵金属", "ready_to_start", priority=88),
            _opportunity("金融科技", "forming", priority=70),
        ],
    )

    assert scope["ordered_eligible_fund_codes"] == []
    assert scope["unmatched_actionable_sector_labels"] == ["贵金属"]
    assert scope["sector_funnel"][0]["rejected_reason_counts"] == {
        "sector_identity_not_verified": 1
    }
    decisions = {
        item["fund_code"]: item for item in scope["candidate_decisions"]
    }
    assert decisions["000021"] == {
        "fund_code": "000021",
        "fund_name": unverified_gold["fund_name"],
        "sector_label": "贵金属",
        "status": "watch_only",
        "entry_path": "confirmed_entry",
        "fund_gates_passed": False,
        "direction_gate_passed": True,
        "reason_codes": ["sector_identity_not_verified"],
    }
    assert decisions["000022"]["status"] == "conditional_wait"
    assert scope["conditional_wait_fund_codes"] == ["000022"]
    assert scope["watch_only_fund_codes"] == ["000021"]


def test_name_and_new_issue_recall_never_open_execution_by_score() -> None:
    name_match = _candidate(
        "000024",
        "云计算",
        sector_fit=99,
        sector_match_kind="name",
    )
    new_issue = _candidate(
        "000025",
        "云计算",
        sector_fit=18,
        sector_match_kind="new_issue",
    )

    scope = build_recommendation_candidate_scope(
        [name_match, new_issue],
        [_opportunity("云计算", "ready_to_start", priority=90)],
    )

    assert scope["ordered_eligible_fund_codes"] == []
    assert scope["watch_only_fund_codes"] == ["000024", "000025"]
    assert scope["sector_funnel"][0]["rejected_reason_counts"] == {
        "sector_identity_not_verified": 2
    }


def test_scope_keeps_candidates_without_direction_evidence_visible_and_fail_closed() -> None:
    candidate = _candidate("000023", "未记录方向")

    scope = build_recommendation_candidate_scope(
        [candidate],
        [_opportunity("贵金属", "ready_to_start", priority=88)],
    )

    assert scope["ordered_eligible_fund_codes"] == []
    assert scope["candidate_decisions"] == [
        {
            "fund_code": "000023",
            "fund_name": candidate["fund_name"],
            "sector_label": "未记录方向",
            "status": "conditional_wait",
            "entry_path": None,
            "fund_gates_passed": True,
            "direction_gate_passed": False,
            "reason_codes": ["direction_evidence_unavailable"],
        }
    ]


def test_legacy_reports_keep_their_existing_candidate_behavior() -> None:
    candidate = _candidate("000031", "综合")
    recommendation = DiscoveryRecommendation(
        fund_code="000031",
        fund_name=candidate["fund_name"],
        sector_name="综合",
        action="建议关注",
        risks=["测试风险"],
    )
    facts: dict = {"sector_opportunities": []}

    reconciled, caveats = reconcile_recommendations_with_scope(
        [recommendation],
        candidate_pool=[candidate],
        discovery_facts=facts,
    )

    assert reconciled == [recommendation]
    assert caveats == []
    assert facts["recommendation_candidate_scope"]["policy_enforced"] is False


def test_v1_report_gets_display_decisions_without_rewriting_its_whitelist() -> None:
    ready = _candidate("000033", "传媒")
    waiting = _candidate("000034", "中药")
    report = {
        "candidate_pool": [ready, waiting],
        "discovery_facts": {
            "mainline_snapshot": {"entry_policy_version": ENTRY_POLICY_VERSION_V3},
            "sector_opportunities": [
                _opportunity("传媒", "ready_to_start", priority=90),
                _opportunity("中药", "forming", priority=60),
            ],
            "recommendation_candidate_scope": {
                "schema_version": "discovery_recommendation_scope.2026-08.v1",
                "policy_enforced": True,
                "ordered_eligible_fund_codes": ["000033"],
            },
        },
    }

    projected = project_candidate_decisions_for_report(report)
    scope = projected["discovery_facts"]["recommendation_candidate_scope"]

    assert scope["schema_version"] == "discovery_recommendation_scope.2026-08.v1"
    assert scope["ordered_eligible_fund_codes"] == ["000033"]
    assert {
        item["fund_code"]: item["status"] for item in scope["candidate_decisions"]
    } == {"000033": "actionable", "000034": "conditional_wait"}
    assert scope["candidate_decision_projection"] == "read_time_compatibility_v1"


def test_llm_payload_contains_only_the_direction_fund_whitelist() -> None:
    media = _candidate("000041", "传媒", opportunity_score=70)
    medicine = _candidate("000042", "中药", opportunity_score=190)
    facts = {
        "candidate_pool": [medicine, media],
        "sector_opportunities": [
            _opportunity("传媒", "ready_to_start", priority=91),
            _opportunity("中药", "forming", priority=64),
        ],
        "session": {"calendar_date": "2026-08-04"},
        "portfolio_gap": {"target_sectors": ["传媒", "中药"], "holdings_slim": []},
        "sector_heat": [],
    }

    payload = build_user_payload(
        discovery_facts=facts,
        profile=InvestorProfile(),
        focus_sectors=[],
    )

    llm_facts = payload["discovery_facts"]
    assert [row["fund_code"] for row in llm_facts["candidate_pool"]] == ["000041"]
    assert llm_facts["recommendation_candidate_scope"][
        "ordered_eligible_fund_codes"
    ] == ["000041"]


def test_llm_payload_compacts_factor_scope_and_candidate_evidence() -> None:
    media = _candidate("000051", "传媒", opportunity_score=70)
    medicine = _candidate("000052", "中药", opportunity_score=190)
    facts = {
        "candidate_pool": [medicine, media],
        "sector_opportunities": [
            _opportunity("传媒", "ready_to_start", priority=91),
            _opportunity("中药", "forming", priority=64),
        ],
        "session": {"calendar_date": "2026-08-04"},
        "portfolio_gap": {"target_sectors": ["传媒", "中药"], "holdings_slim": []},
        "sector_heat": [],
        "candidate_factor_scores": {
            "available": True,
            "ic_status": {
                "state": "available",
                "available": True,
                "segments": {"large_internal_payload": "must_not_reach_model"},
            },
            "selected_fund_codes": ["000051", "000052"],
            "execution_qualified_fund_codes": ["000051", "000052"],
            "holdings": [
                {"fund_code": "000051", "composite_score": 60},
                {
                    "fund_code": "000052",
                    "composite_score": 99,
                    "private_payload": "must_not_reach_model",
                },
            ],
        },
        "data_evidence": {
            "schema_version": "1.0",
            "decision_ready": True,
            "blocking_reasons": [],
            "items": [
                {"fact_id": "portfolio.holdings", "freshness": "fresh"},
                {
                    "fact_id": "candidates.000051.candidate_metrics",
                    "freshness": "fresh",
                },
                {
                    "fact_id": "candidates.000052.candidate_metrics",
                    "freshness": "fresh",
                },
            ],
        },
    }

    payload = build_user_payload(
        discovery_facts=facts,
        profile=InvestorProfile(),
        focus_sectors=[],
    )
    llm_facts = payload["discovery_facts"]

    assert [row["fund_code"] for row in llm_facts["candidate_factor_scores"]["holdings"]] == [
        "000051"
    ]
    assert llm_facts["candidate_factor_scores"][
        "execution_qualified_fund_codes"
    ] == ["000051"]
    assert "segments" not in llm_facts["candidate_factor_scores"]["ic_status"]
    fact_ids = {row["fact_id"] for row in llm_facts["data_evidence"]["items"]}
    assert fact_ids == {
        "portfolio.holdings",
        "candidates.000051.candidate_metrics",
    }
    assert {
        row["fund_code"]
        for row in llm_facts["recommendation_candidate_scope"]["candidate_decisions"]
    } == {"000051"}
