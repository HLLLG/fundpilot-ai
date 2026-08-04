from __future__ import annotations

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_payload import build_user_payload
from app.services.discovery_recommendation_scope import (
    build_recommendation_candidate_scope,
    candidates_in_recommendation_scope,
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
        "schema_version": "discovery_recommendation_scope_reconciliation.v1",
        "model_fund_codes": ["000002"],
        "dropped_fund_codes": ["000002"],
        "backfilled_fund_codes": ["000001"],
        "final_fund_codes": ["000001"],
        "cross_direction_fallback_allowed": False,
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


def test_actionable_direction_without_verified_vehicle_is_reported_not_cross_filled() -> None:
    unverified_gold = _candidate(
        "000021",
        "贵金属",
        sector_fit=16,
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
