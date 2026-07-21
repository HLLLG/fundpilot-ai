from __future__ import annotations

from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    RiskAssessment,
)
from app.services.analysis_payload import prepare_analysis_bundle
from app.services.daily_tradeability import (
    assess_holding_add_amount,
    build_holding_transaction_execution,
)
from app.services.decision_data_evidence import decision_evidence_allows_action
from app.services.recommendation_guard import (
    apply_recommendation_guards,
    normalize_action_text,
)


def _tradeability(**updates) -> dict:
    value = {
        "schema_version": "fund_tradeability.v1",
        "fund_code": "000001",
        "data_status": "complete",
        "freshness": "fresh",
        "purchase_state": "open",
        "purchase_status": "开放申购",
        "redemption_state": "open",
        "redemption_status": "开放赎回",
        "currency": "CNY",
        "minimum_initial_purchase_yuan": 10.0,
        "minimum_additional_purchase_yuan": 100.0,
        "daily_purchase_limit_yuan": 500.0,
        "daily_purchase_limit_unlimited": False,
        "standard_purchase_fee_tiers": [],
        "redemption_fee_tiers": [
            {"condition": "大于等于7天", "min_days": 7, "fee_percent": 0.0}
        ],
        "fee_freshness": "fresh",
        "source_conflict": False,
        "source_ids": ["pytest.tradeability"],
        "checked_at": "2026-07-14T10:00:00+08:00",
    }
    value.update(updates)
    return value


def _holding() -> Holding:
    return Holding(
        fund_code="000001",
        fund_name="测试基金",
        holding_amount=10_000,
        sector_name="半导体",
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[_holding()],
        profile=InvestorProfile(
            decision_style="aggressive",
            avoid_chasing=False,
            concentration_limit_percent=100,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _guard_facts(tradeability: dict) -> dict:
    holding = _holding()
    return {
        "holdings": [
            {
                "fund_code": holding.fund_code,
                "sector_opportunity": {
                    "score": 85,
                    "confidence": "高",
                    "opportunity_available": True,
                    "pattern_label": "price_flow_aligned_up",
                },
                "evidence": {"composite": {"level": "高", "score": 3.0}},
                "tradeability": tradeability,
                "transaction_execution": build_holding_transaction_execution(
                    tradeability,
                    holding_amount_yuan=holding.holding_amount,
                ),
            }
        ]
    }


def test_existing_holding_gate_never_substitutes_initial_for_unknown_additional() -> None:
    tradeability = _tradeability(minimum_additional_purchase_yuan=None)
    execution = build_holding_transaction_execution(
        tradeability,
        holding_amount_yuan=10_000,
    )

    assert execution["purchase_minimum_basis"] == "existing_holding_additional_purchase"
    assert execution["add_status"] == "watch_only"
    assert "additional_minimum_unknown" in execution["add_block_reasons"]


def test_existing_holding_add_amount_is_capped_by_verified_daily_limit() -> None:
    assessment = assess_holding_add_amount(
        _tradeability(),
        holding_amount_yuan=10_000,
        amount_yuan=800,
    )

    assert assessment["executable"] is True
    assert assessment["approved_amount_yuan"] == 500
    assert assessment["amount_capped_by_daily_limit"] is True


def test_existing_holding_add_below_additional_minimum_fails_closed() -> None:
    assessment = assess_holding_add_amount(
        _tradeability(),
        holding_amount_yuan=10_000,
        amount_yuan=50,
    )

    assert assessment["executable"] is False
    assert assessment["approved_amount_yuan"] is None
    assert "below_additional_minimum" in assessment["block_reasons"]


def test_fresh_label_without_timestamp_or_source_is_not_usable() -> None:
    execution = build_holding_transaction_execution(
        _tradeability(checked_at=None, source_ids=[]),
        holding_amount_yuan=10_000,
    )

    assert execution["add_status"] == "watch_only"
    assert execution["redemption_status"] == "watch_only"
    assert "tradeability_checked_at_missing" in execution["add_block_reasons"]
    assert "tradeability_source_missing" in execution["add_block_reasons"]


def test_limited_purchase_needs_a_finite_positive_limit() -> None:
    execution = build_holding_transaction_execution(
        _tradeability(
            purchase_state="limited",
            daily_purchase_limit_yuan=None,
            daily_purchase_limit_unlimited=True,
        ),
        holding_amount_yuan=10_000,
    )

    assert execution["add_status"] == "watch_only"
    assert (
        "limited_purchase_requires_finite_positive_limit"
        in execution["add_block_reasons"]
    )


def test_stop_profit_and_loss_are_reduction_reviews_but_negations_are_not() -> None:
    assert normalize_action_text("分批止盈") == "减仓评估"
    assert normalize_action_text("触发止损") == "减仓评估"
    assert normalize_action_text("暂不止损") == "观察"


def test_daily_guard_caps_add_and_attaches_server_tradeability() -> None:
    recommendation = FundRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        action="分批加仓",
        amount_yuan=800,
        tradeability={"purchase_state": "suspended"},
    )

    _, guarded = apply_recommendation_guards(
        [recommendation],
        [],
        _request(),
        _risk(),
        facts=_guard_facts(_tradeability()),
    )

    assert guarded[0].action == "分批加仓"
    assert guarded[0].amount_yuan is None
    assert guarded[0].suggested_position_change_percent == 5
    assert guarded[0].estimated_position_change_amount_yuan == 500
    assert "单日申购限额" in guarded[0].suggested_position_change_basis
    assert guarded[0].tradeability["purchase_state"] == "open"
    assert guarded[0].transaction_execution["amount_assessment"]["executable"] is True
    assert "单日申购限额" in guarded[0].points[0]


def test_daily_guard_blocks_add_when_additional_minimum_is_unknown() -> None:
    recommendation = FundRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        action="分批加仓",
        amount_yuan=300,
    )

    _, guarded = apply_recommendation_guards(
        [recommendation],
        [],
        _request(),
        _risk(),
        facts=_guard_facts(_tradeability(minimum_additional_purchase_yuan=None)),
    )

    assert guarded[0].action == "观察"
    assert guarded[0].amount_yuan is None
    assert guarded[0].confidence == "低"


def test_daily_guard_keeps_reduction_percentage_while_requiring_fee_review() -> None:
    recommendation = FundRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        action="止盈",
        amount_yuan=1_000,
        suggested_position_change_percent=-20,
    )

    _, guarded = apply_recommendation_guards(
        [recommendation],
        [],
        _request(),
        _risk(),
        facts=_guard_facts(_tradeability()),
    )

    assert guarded[0].action == "减仓评估"
    assert guarded[0].amount_yuan is None
    assert guarded[0].suggested_position_change_percent == -25
    assert guarded[0].estimated_position_change_amount_yuan == 2500
    assert "相对当前估算持仓" in guarded[0].suggested_position_change_basis
    assert guarded[0].transaction_execution["acquisition_lot_status"] == "unverified"
    assert any("逐笔" in point for point in guarded[0].points)


def test_daily_guard_downgrades_reduction_when_redemption_is_not_open() -> None:
    recommendation = FundRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        action="止损",
        amount_yuan=1_000,
    )

    _, guarded = apply_recommendation_guards(
        [recommendation],
        [],
        _request(),
        _risk(),
        facts=_guard_facts(_tradeability(redemption_state="suspended")),
    )

    assert guarded[0].action == "风控复核"
    assert guarded[0].amount_yuan is None
    assert guarded[0].confidence == "低"


def test_analysis_evidence_requires_directional_tradeability_only_for_new_contract() -> None:
    base_items = [
        {
            "fact_id": "holdings.000001.holding_amount",
            "freshness": "fresh",
            "confidence": "high",
        },
        {
            "fact_id": "holdings.000001.daily_return_percent",
            "freshness": "fresh",
            "confidence": "medium",
        },
    ]
    legacy_facts = {
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": base_items,
        }
    }
    allowed, reasons = decision_evidence_allows_action(
        legacy_facts,
        scope="analysis",
        fund_code="000001",
        direction="add",
    )
    assert allowed is True
    assert reasons == []

    new_facts = {
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": [
                *base_items,
                {
                    "fact_id": "holdings.000001.tradeability",
                    "freshness": "fresh",
                    "confidence": "high",
                },
                {
                    "fact_id": "holdings.000001.purchase_execution",
                    "freshness": "unavailable",
                    "confidence": "none",
                },
            ],
        }
    }
    allowed, reasons = decision_evidence_allows_action(
        new_facts,
        scope="analysis",
        fund_code="000001",
        direction="add",
    )
    assert allowed is False
    assert reasons == ["holding_purchase_execution_not_point_in_time_usable"]


def test_prepare_bundle_resolves_tradeability_in_parallel_and_builds_evidence(
    monkeypatch,
) -> None:
    resolver_started = Event()
    context_released = Event()

    def resolver(codes, *, decision_at=None):
        assert codes == ["000001"]
        assert decision_at is not None
        resolver_started.set()
        assert context_released.wait(1)
        return {"000001": _tradeability()}

    def context(*_args, **_kwargs):
        assert resolver_started.wait(1)
        context_released.set()
        return (
            {
                "calendar_date": "2026-07-14",
                "effective_trade_date": "2026-07-14",
            },
            None,
            None,
            None,
        )

    monkeypatch.setattr("app.services.analysis_payload._compute_analysis_context", context)
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_matched_profiles",
        lambda holdings, **_kwargs: [None for _holding in holdings],
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context", lambda *_args: {}
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy", lambda *_args: {}
    )
    monkeypatch.setattr(
        "app.services.analysis_facts._build_sector_intraday_map", lambda *_args: {}
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": False,
            "held": {},
            "market_top": [],
            "sector_flow_by_label": {},
        },
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_breadth_signal",
        lambda *_args, **_kwargs: {"available": False},
    )

    bundle = prepare_analysis_bundle(
        _request(),
        _risk(),
        [],
        [],
        decision_at=datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        tradeability_resolver=resolver,
    )

    row = bundle.facts["holdings"][0]
    assert row["tradeability"]["purchase_state"] == "open"
    assert row["transaction_execution"]["add_status"] == "eligible"
    evidence = {
        item["fact_id"]: item for item in bundle.facts["data_evidence"]["items"]
    }
    assert evidence["holdings.000001.tradeability"]["confidence"] == "high"
    assert evidence["holdings.000001.purchase_execution"]["confidence"] == "high"
    assert evidence["holdings.000001.redemption_execution"]["confidence"] == "high"
    assert evidence["holdings.000001.redemption_lot_cost"]["confidence"] == "none"
