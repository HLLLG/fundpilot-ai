from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    AnalysisRequest,
    DiscoveryRecommendation,
    FundRecommendation,
    Holding,
    InvestorProfile,
    RiskAssessment,
)
from app.services.decision_data_evidence import attach_discovery_data_evidence


def _verified_tradeability() -> dict:
    return {
        "data_status": "complete",
        "freshness": "fresh",
        "purchase_state": "open",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
        "standard_purchase_fee_tiers": [
            {
                "condition": "全部",
                "fee_type": "percent",
                "fee_percent": 0.0,
                "flat_fee_yuan": None,
                "min_amount_yuan": None,
                "max_amount_yuan": None,
                "source_rate": "standard_undiscounted",
            }
        ],
        "redemption_fee_tiers": [
            {
                "condition": "大于等于0天",
                "min_days": 0,
                "max_days": None,
                "fee_percent": 0.0,
            }
        ],
        "sales_service_fee_annual_percent": 0.0,
        "sales_service_fee_status": "known_zero",
        "fee_freshness": "fresh",
        "conflicts": [],
        "source_ids": ["pytest.tradeability"],
    }


def test_discovery_benchmark_metrics_get_field_level_evidence() -> None:
    facts = {
        "session": {"effective_trade_date": "2026-07-14"},
        "candidate_pool": [],
    }
    candidate = {
        "fund_code": "000001",
        "benchmark_metrics": {
            "schema_version": "fund_benchmark_research.v1",
            "status": "qualified",
            "qualified": True,
            "effective_trade_date": "2026-07-14",
            "alignment": {"last_common_date": "2026-07-14"},
            "fund_series": {
                "source": "fund_nav_provider",
                "available_at": "2026-07-14T15:55:00+08:00",
            },
            "components": [
                {
                    "source": "benchmark_component_provider",
                    "available_at": "2026-07-14T15:56:00+08:00",
                }
            ],
        },
    }

    enriched = attach_discovery_data_evidence(
        facts,
        holdings=[],
        candidate_pool=[candidate],
        portfolio_context=None,
    )
    evidence = {
        row["fact_id"]: row for row in enriched["data_evidence"]["items"]
    }
    row = evidence["candidates.000001.benchmark_metrics"]

    assert row["source"] == "fund_nav_provider+benchmark_component_provider"
    assert row["source_type"] == "derived"
    assert row["as_of_date"] == "2026-07-14"
    assert row["freshness"] == "fresh"
    assert row["confidence"] == "medium"
    assert row["is_estimate"] is False


def test_future_candidate_nav_is_unavailable_and_cannot_authorize_action() -> None:
    from app.services.decision_data_evidence import decision_evidence_allows_action

    candidate = {
        "fund_code": "000001",
        "nav_date": "2099-01-01",
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
        "tradeability": _verified_tradeability(),
    }
    enriched = attach_discovery_data_evidence(
        {
            "session": {"effective_trade_date": "2026-07-14"},
            "candidate_pool": [candidate],
        },
        holdings=[],
        candidate_pool=[candidate],
        portfolio_context=None,
    )
    evidence = {
        row["fact_id"]: row for row in enriched["data_evidence"]["items"]
    }

    metric = evidence["candidates.000001.candidate_metrics"]
    assert metric["as_of_date"] == "2099-01-01"
    assert metric["source"] == "unavailable"
    assert metric["freshness"] == "unavailable"
    assert metric["confidence"] == "none"
    assert decision_evidence_allows_action(
        enriched,
        scope="discovery",
        fund_code="000001",
    ) == (False, ["candidate_metrics_not_point_in_time_usable"])


def _holding(
    amount: float,
    *,
    source: str = "official_nav",
    fund_code: str = "000001",
) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name="测试基金",
        holding_amount=amount,
        holding_return_percent=3.2,
        daily_return_percent=0.5,
        daily_return_percent_source=source,
        sector_name="半导体",
        sector_return_percent=0.7,
        sector_return_percent_source="realtime",
    )


def test_compact_position_truth_preserves_fail_closed_ledger_flags():
    from app.services.decision_data_evidence import compact_portfolio_position_truth

    compact = compact_portfolio_position_truth(
        {
            "position_snapshot": {
                "snapshot_id": "snapshot-1",
                "ledger_version": "pl1:10000:abc",
                "position_complete": False,
                "ledger_truncated": True,
                "known_unsettled_transaction_count": 2,
                "completeness": {"conflict_count": 1, "ledger_truncated": True},
                "cash": {"known": False, "balance_cny": None},
                "positions": [],
            }
        }
    )

    assert compact is not None
    assert compact["ledger_truncated"] is True
    assert compact["position_complete"] is False
    assert compact["known_unsettled_transaction_count"] == 2
    assert compact["conflict_count"] == 1
    assert compact["cash"]["balance_yuan"] is None


def test_portfolio_preflight_prefers_fresh_server_snapshot_and_audits_mismatch(monkeypatch):
    from app.services import decision_data_evidence as service

    captured_at = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        service,
        "load_persisted_holdings",
        lambda **_kwargs: ([_holding(1000)], "snapshot", "2026-07-10", captured_at),
    )
    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda *_args: {"effective_trade_date": "2026-07-10"},
    )

    result = service.resolve_portfolio_preflight([_holding(999, fund_code="000002")])

    assert result.holdings[0].holding_amount == 1000
    assert result.context["authoritative"] is True
    assert result.context["client_snapshot_mismatch"] is True
    assert result.context["freshness"] == "fresh"
    evidence = result.context["evidence"]
    assert evidence == {
        "fact_id": "portfolio.holdings",
        "source": "portfolio_daily_snapshots",
        "source_type": "first_party",
        "as_of_date": "2026-07-10",
        "available_at": "2026-07-10T06:00:00Z",
        "fetched_at": evidence["fetched_at"],
        "freshness": "fresh",
        "confidence": "high",
        "is_estimate": False,
    }


def test_portfolio_preflight_blocks_stale_server_snapshot_by_default(monkeypatch):
    from app.services import decision_data_evidence as service

    monkeypatch.setattr(
        service,
        "load_persisted_holdings",
        lambda **_kwargs: (
            [_holding(1000)],
            "snapshot",
            "2026-07-09",
            datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda *_args: {"effective_trade_date": "2026-07-10"},
    )

    with pytest.raises(service.StalePortfolioSnapshotError, match="2026-07-09"):
        service.resolve_portfolio_preflight([_holding(1000)])


def test_portfolio_preflight_allows_only_explicit_stale_degradation(monkeypatch):
    from app.services import decision_data_evidence as service

    monkeypatch.setattr(
        service,
        "load_persisted_holdings",
        lambda **_kwargs: (
            [_holding(1000)],
            "snapshot",
            "2026-07-09",
            datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda *_args: {"effective_trade_date": "2026-07-10"},
    )

    result = service.resolve_portfolio_preflight(
        [_holding(1000)],
        allow_stale=True,
    )

    assert result.context["stale"] is True
    assert result.context["degraded"] is True
    assert result.context["freshness"] == "stale"
    assert result.context["evidence"]["confidence"] == "low"


def test_portfolio_preflight_marks_first_run_client_input_as_non_authoritative(monkeypatch):
    from app.services import decision_data_evidence as service

    monkeypatch.setattr(
        service,
        "load_persisted_holdings",
        lambda **_kwargs: ([], "empty", None, None),
    )
    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda *_args: {"effective_trade_date": "2026-07-10"},
    )

    result = service.resolve_portfolio_preflight([_holding(1000)])

    assert result.context["authoritative"] is False
    assert result.context["source"] == "client_request"
    assert result.context["freshness"] == "fresh"
    assert result.context["evidence"]["source_type"] == "user_input"
    assert result.context["evidence"]["confidence"] == "medium"


def test_analysis_evidence_is_field_level_and_estimates_are_explicit():
    from app.services.decision_data_evidence import build_analysis_data_evidence

    holding = _holding(1000, source="sector_estimate")
    payload = build_analysis_data_evidence(
        [holding],
        snapshots=[],
        facts={"session": {"effective_trade_date": "2026-07-10"}},
        portfolio_context=None,
    )

    by_id = {item["fact_id"]: item for item in payload["items"]}
    assert by_id["holdings.000001.holding_amount"]["source_type"] == "user_input"
    daily = by_id["holdings.000001.daily_return_percent"]
    assert daily["source"] == "sector_estimate"
    assert daily["is_estimate"] is True
    assert daily["confidence"] == "low"
    assert payload["schema_version"] == "1.0"


def test_market_breadth_explicit_stale_status_overrides_same_trade_date():
    from app.services.decision_data_evidence import build_analysis_data_evidence

    payload = build_analysis_data_evidence(
        [_holding(1000)],
        snapshots=[],
        facts={
            "session": {"effective_trade_date": "2026-07-13"},
            "market_breadth": {
                "available": True,
                "trade_date": "2026-07-13",
                "stale": True,
                "freshness_status": "stale",
                "decision_eligible": False,
            },
        },
        portfolio_context=None,
    )

    by_id = {item["fact_id"]: item for item in payload["items"]}
    assert by_id["market.market_breadth"]["freshness"] == "stale"


def test_lookthrough_evidence_uses_disclosure_vintage_and_hash_refs() -> None:
    from app.services.decision_data_evidence import build_analysis_data_evidence

    payload = build_analysis_data_evidence(
        [_holding(1000)],
        snapshots=[],
        facts={
            "session": {"effective_trade_date": "2026-07-14"},
            "fund_lookthrough": {
                "status": "partial",
                "decision_at": "2026-07-14T10:00:00+08:00",
                "portfolio": {
                    "scope": "fund_holdings_only",
                    "identity_known_security_mass_lower_bound_percent": 10.0,
                },
                "existing_funds": [
                    {
                        "status": "qualified",
                        "snapshot": {
                            "as_of_date": "2026-03-31",
                            "current_freshness_label": "fresh",
                            "disclosed_overlap_lower_bound_eligible": True,
                            "available_at": "2026-04-20T09:00:00+08:00",
                            "first_observed_at": "2026-04-22T09:00:00+08:00",
                        },
                        "lookthrough": {
                            "identity_known_disclosed_mass_percent": 20.0,
                        },
                    },
                    {
                        "status": "qualified",
                        "snapshot": {
                            "as_of_date": "2026-06-30",
                            "current_freshness_label": "aging",
                            "disclosed_overlap_lower_bound_eligible": True,
                            "available_at": "2026-07-20T09:00:00+08:00",
                            "first_observed_at": "2026-07-21T09:00:00+08:00",
                        },
                        "lookthrough": {
                            "identity_known_disclosed_mass_percent": 20.0,
                        },
                    },
                ],
                "candidates": [
                    {
                        "fund_code": "000002",
                        "status": "qualified",
                        "snapshot": {
                            "as_of_date": "2026-06-30",
                            "current_freshness_label": "fresh",
                            "available_at": "2026-07-20T09:00:00+08:00",
                            "first_observed_at": "2026-07-22T09:00:00+08:00",
                        },
                        "capabilities": {"research_eligible": True},
                    }
                ],
                "resolution_audit": {
                    "rows": [
                        {
                            "fund_code": "000001",
                            "qualified": True,
                            "snapshot_ref": "abcdef123456",
                            "freshness": "fresh",
                            "as_of_date": "2026-03-31",
                            "first_observed_at": "2026-04-22T09:00:00+08:00",
                        }
                    ]
                },
            },
        },
        portfolio_context=None,
    )

    by_id = {item["fact_id"]: item for item in payload["items"]}
    portfolio = by_id["fund_lookthrough:portfolio"]
    assert portfolio["as_of_date"] is None
    assert portfolio["freshness"] == "unknown"
    assert portfolio["available_at"] == "2026-07-21T01:00:00Z"
    snapshot = by_id["holdings_snapshot:000001:abcdef123456"]
    assert snapshot["as_of_date"] == "2026-03-31"
    assert snapshot["freshness"] == "aging"
    candidate = by_id["fund_lookthrough:candidate:000002"]
    assert candidate["as_of_date"] == "2026-06-30"
    assert candidate["freshness"] == "aging"
    assert candidate["available_at"] == "2026-07-22T01:00:00Z"


def test_missing_lookthrough_evidence_never_improves_decision_readiness() -> None:
    from app.services.decision_data_evidence import build_analysis_data_evidence

    base_facts = {"session": {"effective_trade_date": "2026-07-14"}}
    baseline = build_analysis_data_evidence(
        [_holding(1000)],
        snapshots=[],
        facts=base_facts,
        portfolio_context=None,
    )
    missing = build_analysis_data_evidence(
        [_holding(1000)],
        snapshots=[],
        facts={
            **base_facts,
            "fund_lookthrough": {
                "status": "unavailable",
                "decision_at": "2026-07-14T10:00:00+08:00",
                "portfolio": None,
                "existing_funds": [],
                "candidates": [],
                "resolution_audit": {"rows": []},
            },
        },
        portfolio_context=None,
    )

    assert missing["decision_ready"] is baseline["decision_ready"]
    assert missing["blocking_reasons"] == baseline["blocking_reasons"]
    by_id = {item["fact_id"]: item for item in missing["items"]}
    assert by_id["fund_lookthrough:portfolio"]["source"] == "unavailable"
    assert by_id["fund_lookthrough:portfolio"]["freshness"] == "unavailable"
    assert by_id["fund_lookthrough:portfolio"]["confidence"] == "none"


def test_lookthrough_availability_requires_qualified_research_dependencies() -> None:
    from app.services.decision_data_evidence import build_analysis_data_evidence

    payload = build_analysis_data_evidence(
        [_holding(1000)],
        snapshots=[],
        facts={
            "session": {"effective_trade_date": "2026-07-14"},
            "fund_lookthrough": {
                "status": "qualified",
                "portfolio": {
                    "identity_known_security_mass_lower_bound_percent": 20.0,
                },
                "existing_funds": [
                    {
                        "status": "unavailable",
                        "snapshot": {
                            "as_of_date": "2026-06-30",
                            "disclosed_overlap_lower_bound_eligible": False,
                            "first_observed_at": "2026-07-10T09:00:00+08:00",
                        },
                        "lookthrough": None,
                    }
                ],
                "candidates": [
                    {
                        "fund_code": "000002",
                        "status": "qualified",
                        "capabilities": {"research_eligible": False},
                        "snapshot": {
                            "as_of_date": "2026-06-30",
                            "first_observed_at": "2026-07-10T09:00:00+08:00",
                        },
                    }
                ],
                "resolution_audit": {"rows": []},
            },
        },
        portfolio_context=None,
    )

    by_id = {item["fact_id"]: item for item in payload["items"]}
    assert by_id["fund_lookthrough:portfolio"]["source"] == "unavailable"
    assert by_id["fund_lookthrough:candidate:000002"]["source"] == "unavailable"


def test_empty_server_snapshot_cannot_be_overridden_by_stale_client_holdings(monkeypatch):
    from app.services import decision_data_evidence as service

    monkeypatch.setattr(
        service,
        "load_persisted_holdings",
        lambda **_kwargs: (
            [],
            "snapshot",
            "2026-07-10",
            datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda *_args: {"effective_trade_date": "2026-07-10"},
    )

    result = service.resolve_portfolio_preflight([_holding(1000)])

    assert result.holdings == []
    assert result.context["authoritative"] is True
    assert result.context["client_snapshot_mismatch"] is True


def test_snapshot_date_key_uses_shanghai_calendar_date():
    from app.services.portfolio_snapshot import snapshot_date_key

    assert snapshot_date_key(datetime(2026, 7, 9, 16, 30, tzinfo=timezone.utc)) == "2026-07-10"


def test_valuation_changes_do_not_create_a_position_fingerprint_mismatch():
    from app.services.decision_data_evidence import holdings_fingerprint

    before = _holding(1000).model_copy(
        update={"holding_profit": 10, "holding_return_percent": 1.0, "fund_name": "旧展示名"}
    )
    after = _holding(1200).model_copy(
        update={"holding_profit": 80, "holding_return_percent": 8.0, "fund_name": "新展示名"}
    )

    assert holdings_fingerprint([before]) == holdings_fingerprint([after])


def test_stale_root_evidence_is_not_relabelled_fresh_on_derived_holding_fields():
    from app.services.decision_data_evidence import build_analysis_data_evidence

    payload = build_analysis_data_evidence(
        [_holding(1000, source="sector_estimate")],
        snapshots=[],
        facts={"session": {"effective_trade_date": "2026-07-10"}},
        portfolio_context={
            "stale": True,
            "authoritative": True,
            "effective_trade_date": "2026-07-10",
            "evidence": {
                "fact_id": "portfolio.holdings",
                "source": "portfolio_daily_snapshots",
                "source_type": "first_party",
                "as_of_date": "2026-07-09",
                "available_at": "2026-07-09T06:00:00Z",
                "fetched_at": "2026-07-10T06:00:00Z",
                "freshness": "stale",
                "confidence": "low",
                "is_estimate": False,
            },
        },
    )

    by_id = {item["fact_id"]: item for item in payload["items"]}
    assert by_id["holdings.000001.holding_amount"]["freshness"] == "stale"
    assert by_id["holdings.000001.daily_return_percent"]["freshness"] == "stale"
    assert by_id["holdings.000001.sector_return_percent"]["freshness"] == "stale"
    assert payload["decision_ready"] is False


def test_discovery_llm_payload_keeps_snapshot_and_field_evidence():
    from app.services.discovery_payload import build_user_payload

    evidence = {
        "schema_version": "1.0",
        "decision_ready": False,
        "items": [],
        "raw_registry": {"secret": "EVIDENCE_LEAK_SENTINEL"},
    }
    snapshot = {
        "snapshot_id": "snap-1",
        "stale": True,
        "authoritative": True,
        "position_snapshot": {
            "positions": [{"fund_code": "000001", "secret": "LEDGER_LEAK_SENTINEL"}]
        },
    }
    payload = build_user_payload(
        discovery_facts={
            "candidate_pool": [
                {
                    "fund_code": "000001",
                    "fund_name": "测试基金",
                    "custom_payload": {"secret": "CANDIDATE_LEAK_SENTINEL"},
                }
            ],
            "portfolio_gap": {},
            "sector_heat": [],
            "portfolio_snapshot": snapshot,
            "portfolio_position_truth": {
                "schema_version": "portfolio_position_truth.compact.v1",
                "position_complete": True,
                "positions": [
                    {
                        "fund_code": "000001",
                        "settled_shares": "100.00",
                        "raw_events": ["POSITION_TRUTH_LEAK_SENTINEL"],
                    }
                ],
            },
            "data_evidence": evidence,
        },
        profile=InvestorProfile(),
        focus_sectors=[],
    )

    projected_snapshot = payload["discovery_facts"]["portfolio_snapshot"]
    projected_evidence = payload["discovery_facts"]["data_evidence"]
    assert projected_snapshot["snapshot_id"] == "snap-1"
    assert projected_snapshot["stale"] is True
    assert projected_snapshot["authoritative"] is True
    assert "position_snapshot" not in projected_snapshot
    assert projected_evidence["schema_version"] == "1.0"
    assert projected_evidence["decision_ready"] is False
    assert "raw_registry" not in projected_evidence
    projected_position = payload["discovery_facts"]["portfolio_position_truth"]
    assert projected_position["positions"][0]["fund_code"] == "000001"
    assert projected_position["positions"][0]["settled_shares"] == "100.00"
    assert "raw_events" not in projected_position["positions"][0]
    assert "custom_payload" not in payload["discovery_facts"]["candidate_pool"][0]
    serialized = str(payload)
    assert "LEDGER_LEAK_SENTINEL" not in serialized
    assert "EVIDENCE_LEAK_SENTINEL" not in serialized
    assert "CANDIDATE_LEAK_SENTINEL" not in serialized
    assert "POSITION_TRUTH_LEAK_SENTINEL" not in serialized


def test_degraded_daily_snapshot_guard_removes_add_action_and_amount():
    from app.services.recommendation_guard import apply_recommendation_guards

    holding = _holding(1000)
    request = AnalysisRequest(holdings=[holding], profile=InvestorProfile())
    recommendation = FundRecommendation(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        action="分批加仓",
        amount_yuan=500,
        points=["测试"],
    )
    risk = RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=1.0,
        alerts=[],
    )

    portfolio, guarded = apply_recommendation_guards(
        [recommendation],
        ["建议加仓 500 元，并把仓位提高到 20%。"],
        request,
        risk,
        facts={
            "portfolio_snapshot": {"stale": True, "authoritative": True},
            "holdings": [{"fund_code": holding.fund_code}],
        },
    )

    assert guarded[0].action == "观察"
    assert guarded[0].amount_yuan is None
    assert guarded[0].confidence == "低"
    assert all("500 元" not in point for point in guarded[0].points)
    assert all("加仓" not in line for line in portfolio)


def test_degraded_discovery_snapshot_guard_removes_buy_action_and_amount():
    from app.services.discovery_guard import apply_discovery_guards

    recommendation = DiscoveryRecommendation(
        fund_code="000001",
        fund_name="测试基金",
        sector_name="半导体",
        action="分批买入",
        suggested_amount_yuan=500,
        points=["测试"],
        risks=["波动风险"],
    )
    guarded, caveats, _ = apply_discovery_guards(
        [recommendation],
        candidate_pool=[
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "sector_label": "半导体",
                "fund_quality_score": 80,
                "sector_fit_score": 80,
                "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
                "tradeability": _verified_tradeability(),
            }
        ],
        held_codes=set(),
        profile=InvestorProfile(avoid_chasing=False),
        budget_yuan=1000,
        sector_heat=[],
        discovery_facts={
            "portfolio_snapshot": {"stale": True, "authoritative": True},
            "sector_opportunities": [],
        },
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert guarded[0].confidence == "低"
    assert any("禁止买入动作" in caveat for caveat in caveats)


@pytest.mark.parametrize(
    ("cash_known", "cash_balance", "expected_amount"),
    [
        (True, "300", 300),
        (True, "0", None),
        (False, None, None),
    ],
)
def test_discovery_guard_caps_by_known_cash_and_blocks_unknown_cash(
    cash_known: bool,
    cash_balance: str | None,
    expected_amount: float | None,
):
    from app.services.discovery_guard import apply_discovery_guards

    facts = {
        "portfolio_snapshot": {
            "stale": False,
            "authoritative": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": {
            "position_complete": True,
            "cash": {"known": cash_known, "balance_yuan": cash_balance},
            "positions": [],
        },
        "portfolio_gap": {"holdings_slim": []},
        "sector_opportunities": [],
    }
    guarded, caveats, _ = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="000001",
                fund_name="测试基金",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=500,
                points=["测试"],
                risks=["波动风险"],
            )
        ],
        candidate_pool=[
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "sector_label": "半导体",
                "fund_quality_score": 80,
                "sector_fit_score": 80,
                "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
                "tradeability": _verified_tradeability(),
            }
        ],
        held_codes=set(),
        profile=InvestorProfile(
            avoid_chasing=False,
            concentration_limit_percent=100,
        ),
        budget_yuan=1000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert guarded[0].suggested_amount_yuan == expected_amount
    if cash_known and cash_balance == "0":
        assert caveats
        assert guarded[0].amount_note


def test_unknown_directional_evidence_is_consumed_by_daily_final_guard():
    from app.services.recommendation_guard import apply_recommendation_guards

    holding = _holding(1000)
    request = AnalysisRequest(holdings=[holding], profile=InvestorProfile())
    recommendation = FundRecommendation(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        action="分批加仓",
        amount_yuan=300,
        points=["建议加仓 300 元"],
    )
    risk = RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=1.0,
        alerts=[],
    )
    facts = {
        "portfolio_snapshot": {"stale": False, "authoritative": True},
        "holdings": [{"fund_code": holding.fund_code}],
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": [
                {
                    "fact_id": "holdings.000001.holding_amount",
                    "freshness": "fresh",
                    "confidence": "high",
                },
                {
                    "fact_id": "holdings.000001.daily_return_percent",
                    "freshness": "unknown",
                    "confidence": "medium",
                },
                {
                    "fact_id": "holdings.000001.sector_return_percent",
                    "freshness": "unknown",
                    "confidence": "medium",
                },
            ],
        },
    }

    _, guarded = apply_recommendation_guards(
        [recommendation], [], request, risk, facts=facts
    )

    assert guarded[0].action == "观察"
    assert guarded[0].amount_yuan is None
    assert facts["data_evidence_guard"]["execution_blocked"] is True


def test_unknown_candidate_evidence_is_consumed_by_discovery_final_guard():
    from app.services.discovery_guard import apply_discovery_guards

    facts = {
        "portfolio_snapshot": {"stale": False, "authoritative": True},
        "sector_opportunities": [],
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": [
                {
                    "fact_id": "candidates.000001.candidate_metrics",
                    "freshness": "unknown",
                    "confidence": "medium",
                }
            ],
        },
    }
    guarded, _, _ = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="000001",
                fund_name="测试基金",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=500,
                points=["建议买入 500 元"],
                risks=["波动风险"],
            )
        ],
        candidate_pool=[
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "sector_label": "半导体",
                "fund_quality_score": 80,
                "sector_fit_score": 80,
                "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
                "tradeability": _verified_tradeability(),
            }
        ],
        held_codes=set(),
        profile=InvestorProfile(avoid_chasing=False),
        budget_yuan=1000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert all("500 元" not in point for point in guarded[0].points)
    assert facts["data_evidence_guard"]["execution_blocked"] is True


def test_blocked_report_chat_prompts_forbid_recreating_executable_advice():
    from app.services.discovery_chat import _discovery_chat_system_prompt
    from app.services.report_chat import _report_chat_system_prompt

    report_prompt = _report_chat_system_prompt(
        "日报",
        news_tool_enabled=False,
        execution_blocked=True,
    )
    discovery_prompt = _discovery_chat_system_prompt(
        "荐基报告",
        {
            "candidate_pool": [],
            "discovery_facts": {"data_evidence_guard": {"execution_blocked": True}},
        },
    )

    assert "不得给出买入、加仓" in report_prompt
    assert "不得给出买入、加仓" in discovery_prompt


def test_async_analysis_keeps_original_client_holdings_for_worker_mismatch_audit(monkeypatch):
    from app import main
    from app.services.decision_data_evidence import PortfolioPreflightResult

    client_holding = _holding(999, fund_code="000002")
    server_holding = _holding(1000, fund_code="000001")
    captured: dict[str, AnalysisRequest] = {}
    monkeypatch.setattr(
        main,
        "resolve_portfolio_preflight",
        lambda *_args, **_kwargs: PortfolioPreflightResult(
            holdings=[server_holding],
            context={"client_snapshot_mismatch": True},
        ),
    )
    monkeypatch.setattr(
        main,
        "create_analysis_job",
        lambda request: captured.setdefault("request", request) and "job-1",
    )

    response = main.analyze_async(AnalysisRequest(holdings=[client_holding]))

    assert response["job_id"] == "job-1"
    assert captured["request"].holdings[0].fund_code == "000002"
