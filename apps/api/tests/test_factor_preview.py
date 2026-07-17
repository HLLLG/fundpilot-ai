from __future__ import annotations

from app.services.factor_preview import (
    apply_factor_preview_amount,
    build_factor_preview,
    reconcile_factor_preview,
)


def _scores(*, stale: bool = False, model_version: str = "factor_ic.v2") -> dict:
    return {
        "available": True,
        "model_version": model_version,
        "selected_fund_codes": ["020356", "021627"],
        "ic_status": {
            "state": "stale" if stale else "available",
            "available": True,
            "stale": stale,
            "cohort_mode": "current_survivors",
            "snapshot_id": "factor-2026-07-16",
            "run_date": "2026-07-16",
        },
        "holdings": [
            {
                "fund_code": "020356",
                "descriptive_applicable": True,
                "peer_group": "hh",
                "peer_count": 120,
                "feature_count": 3,
                "feature_completeness": 0.75,
                "target_feature_freshness": "fresh",
                "target_return_coverage": 0.98,
                "factor_percentiles": {
                    "momentum": 92,
                    "risk_adjusted": 88,
                    "drawdown": 30,
                },
                "factor_reliability": {
                    "momentum": {"level": "中"},
                    "risk_adjusted": {"level": "中"},
                    "drawdown": {"level": "低"},
                },
            },
            {
                "fund_code": "021627",
                "descriptive_applicable": True,
                "peer_group": "hh",
                "peer_count": 120,
                "feature_count": 3,
                "feature_completeness": 0.75,
                "target_feature_freshness": "fresh",
                "target_return_coverage": 0.98,
                "factor_percentiles": {"momentum": 75, "risk_adjusted": 65},
                "factor_reliability": {
                    "momentum": {"level": "中"},
                    "risk_adjusted": {"level": "中"},
                },
            },
        ],
    }


def test_preview_is_auditable_and_ranks_only_current_sector_candidates() -> None:
    preview = build_factor_preview(
        _scores(),
        "020356",
        mode="shadow",
        max_adjustment_percent=10,
        sector_name="半导体",
        candidate_pool=[
            {"fund_code": "020356", "sector_label": "半导体"},
            {"fund_code": "021627", "sector_label": "半导体"},
        ],
    )

    assert preview is not None
    assert preview["status"] == "eligible"
    assert preview["preview_score"] == 90
    assert preview["proposed_adjustment_percent"] == 10
    assert preview["sector_rank"] == 1
    assert preview["sector_sample_size"] == 2
    assert preview["snapshot_id"] == "factor-2026-07-16"
    assert preview["survivorship_bias"] is True


def test_shadow_mode_records_counterfactual_but_does_not_change_amount() -> None:
    preview = build_factor_preview(
        _scores(),
        "020356",
        mode="shadow",
        max_adjustment_percent=10,
    )

    amount, projected = apply_factor_preview_amount(
        preview,
        amount_yuan=1000,
        hard_cap_yuan=1050,
    )

    assert amount == 1000
    assert projected is not None
    assert projected["projected_amount_yuan"] == 1050
    assert projected["adjusted_amount_yuan"] == 1000
    assert projected["application_status"] == "shadow_only"
    assert projected["applied_adjustment_percent"] == 0


def test_enforced_mode_applies_bounded_amount_only_and_reconciles_failed_action() -> None:
    preview = build_factor_preview(
        _scores(),
        "020356",
        mode="enforced",
        max_adjustment_percent=10,
    )

    amount, projected = apply_factor_preview_amount(
        preview,
        amount_yuan=1000,
        hard_cap_yuan=10_000,
    )

    assert amount == 1100
    assert projected is not None
    assert projected["application_status"] == "applied"
    assert projected["applied_adjustment_percent"] == 10

    reconciled = reconcile_factor_preview(
        projected,
        action="建议关注",
        final_amount_yuan=None,
    )
    assert reconciled is not None
    assert reconciled["application_status"] == "not_applied"
    assert reconciled["applied_adjustment_percent"] == 0
    assert reconciled["adjusted_amount_yuan"] is None


def test_enforced_mode_does_not_claim_an_adjustment_when_hard_cap_blocks_it() -> None:
    preview = build_factor_preview(
        _scores(),
        "020356",
        mode="enforced",
        max_adjustment_percent=10,
    )

    amount, projected = apply_factor_preview_amount(
        preview,
        amount_yuan=1000,
        hard_cap_yuan=1000,
    )

    assert amount == 1000
    assert projected is not None
    assert projected["application_status"] == "not_applied"
    assert projected["applied_adjustment_percent"] == 0


def test_preview_fails_closed_for_stale_or_formal_v3_snapshot() -> None:
    stale = build_factor_preview(
        _scores(stale=True),
        "020356",
        mode="enforced",
        max_adjustment_percent=10,
    )
    formal_v3 = build_factor_preview(
        _scores(model_version="factor_ic.v3"),
        "020356",
        mode="enforced",
        max_adjustment_percent=10,
    )

    assert stale is not None and stale["status"] == "ineligible"
    assert formal_v3 is not None and formal_v3["status"] == "ineligible"
    assert "正式 PIT v3" in formal_v3["reasons"][0]
