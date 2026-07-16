from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app.models import DiscoveryRequest, FundDiscoveryReport, InvestorProfile
from app.services.analysis_runtime import AnalysisRuntime
from app.services.candidate_selection_audit import (
    build_pipeline_candidate_selection_audit_v2,
)
from app.services.decision_data_evidence import PortfolioPreflightResult
from app.services.discovery_candidate_pool import (
    build_candidate_pool,
    finalize_candidate_pool,
)
from app.services.discovery_payload import build_user_payload


DECISION_AT = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
AVAILABLE_AT = "2026-07-14T07:30:00+00:00"


def _profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100_000,
    )


def _request() -> DiscoveryRequest:
    return DiscoveryRequest(holdings=[], profile=_profile(), focus_sectors=["科技"])


def _tradeability(code: str) -> dict:
    return {
        "schema_version": "fund_tradeability.v1",
        "fund_code": code,
        "data_status": "complete",
        "freshness": "fresh",
        "purchase_state": "open",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
        "minimum_initial_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
        "source_conflict": False,
        "source_ids": ["pytest.tradeability"],
        "checked_at": AVAILABLE_AT,
        "fee_checked_at": AVAILABLE_AT,
        "fee_freshness": "fresh",
    }


def _candidates() -> list[dict]:
    rows = []
    for code, name, score in (
        ("100001", "Alpha科技成长A", 95.0),
        ("100002", "Alpha科技成长C", 92.0),
        ("100003", "Beta科技成长A", 90.0),
    ):
        rows.append(
            {
                "fund_code": code,
                "fund_name": name,
                "fund_type": "混合型",
                "sector_label": "科技",
                "fund_quality_score": score,
                "sector_fit_score": 35.0,
                "quality_score_version": "fund_quality.v3",
                "quality_score_components": {"sector_fit": 35.0, "quality": score - 35.0},
                "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
                "tradeability": _tradeability(code),
                "candidate_universe_source": "pytest.frozen_universe",
                "candidate_universe_available_at": AVAILABLE_AT,
            }
        )
    return rows


def _recall_snapshot(rows: list[dict], *, complete: bool = True) -> dict:
    return {
        "schema_version": "discovery_candidate_recall.v1",
        "scope": {
            "definition": "unique scored candidates before sector/family/pool caps",
            "complete": complete,
            "candidate_count_total": len(rows) + (0 if complete else 1),
            "candidate_count_retained": len(rows),
            "retention_limit": 512,
            "truncated_reason": None if complete else "recall_audit_retention_limit",
            "catalogue_rows_embedded": False,
            "source_universe_size": 20_000,
        },
        "candidates": deepcopy(rows),
    }


def test_build_candidate_pool_captures_full_scored_recall_without_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank_rows = _candidates()
    for row in rank_rows:
        row.update(
            {
                "return_3m_percent": 8.0,
                "return_6m_percent": 12.0,
                "return_1y_percent": 20.0,
                "max_drawdown_1y_percent": -10.0,
                "fund_scale_yi": 10.0,
                "established_date": "2020-01-01",
                "nav_date": "2026-07-13",
                "source": "pytest.frozen_universe",
                "snapshot_available_at": AVAILABLE_AT,
            }
        )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda *_args, **_kwargs: [
            *[
                {
                    "fund_code": row["fund_code"],
                    "fund_name": row["fund_name"],
                    "sector_name": "科技",
                }
                for row in rank_rows
            ],
            {
                "fund_code": "100001",
                "fund_name": "Alpha科技成长A",
                "sector_name": "医药",
            },
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool._attach_descriptive_peer_research",
        lambda *_args, **_kwargs: None,
    )

    recall: dict = {}
    selected = build_candidate_pool(
        ["科技", "医药"],
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: deepcopy(rank_rows),
        fetch_new_funds=lambda limit: [],
        decision_at=DECISION_AT,
        recall_audit_sink=recall,
    )

    assert len(selected) == 1
    assert recall["scope"]["candidate_count_total"] == 3
    assert recall["scope"]["candidate_count_retained"] == 3
    assert recall["scope"]["complete"] is True
    assert recall["scope"]["retention_limit"] == 512
    assert recall["scope"]["source_universe_size"] == 3
    assert recall["scope"]["catalogue_rows_embedded"] is False
    assert {row["fund_code"] for row in recall["candidates"]} == {
        "100001",
        "100002",
        "100003",
    }
    first = next(row for row in recall["candidates"] if row["fund_code"] == "100001")
    assert first["recall_matched_sectors"] == ["科技", "医药"]
    assert "rows" not in recall
    assert len(json.dumps(recall, ensure_ascii=False)) < 64_000

    truncated: dict = {}
    build_candidate_pool(
        ["科技", "医药"],
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: deepcopy(rank_rows),
        fetch_new_funds=lambda limit: [],
        decision_at=DECISION_AT,
        recall_audit_sink=truncated,
        recall_audit_limit=2,
    )
    assert truncated["scope"]["candidate_count_total"] == 3
    assert truncated["scope"]["candidate_count_retained"] == 2
    assert truncated["scope"]["complete"] is False
    assert truncated["scope"]["truncated_reason"] == "recall_audit_retention_limit"


def test_finalize_v1_remains_and_pipeline_v2_is_full_funnel_fail_closed() -> None:
    rows = _candidates()
    excluded = deepcopy(rows[-1])
    excluded["fund_code"] = "100004"
    excluded["fund_name"] = "Gamma科技成长A"
    excluded["quality_gate"] = {
        "status": "excluded",
        "eligible": False,
        "reasons": ["hard_quality_gate"],
    }
    excluded["tradeability"] = _tradeability("100004")
    rows.append(excluded)
    legacy: dict = {}
    trace: dict = {}
    final = finalize_candidate_pool(
        deepcopy(rows),
        ["科技"],
        per_sector=3,
        pool_cap=3,
        audit_sink=legacy,
        stage_audit_sink=trace,
    )
    audit = build_pipeline_candidate_selection_audit_v2(
        decision_at=DECISION_AT,
        recall_snapshot=_recall_snapshot(rows),
        gate_candidates=trace["gate_candidates"],
        prescreen_candidates=trace["prescreen_candidates"],
        final_candidates=trace["final_candidates"],
    )

    assert legacy["schema_version"] == "discovery_candidate_selection_audit.v1"
    assert audit["schema_version"] == "discovery_candidate_selection_audit.v2"
    assert audit["stage_counts"] == {"recall": 4, "gate": 4, "prescreen": 2, "final": 2}
    assert audit["stages"]["recall"]["scope"]["source_universe_size"] == 20_000
    assert audit["validation"]["status"] == "valid"
    assert [row["fund_code"] for row in final] == ["100001", "100003"]
    by_code = {row["fund_code"]: row for row in audit["rows"]}
    assert by_code["100002"]["stage_records"]["gate"]["reason_codes"] == [
        "share_class_not_selected_after_tradeability_and_cost"
    ]
    assert by_code["100002"]["stage_records"]["prescreen"]["present"] is False
    assert by_code["100004"]["stage_records"]["gate"]["reason_codes"] == [
        "quality_or_tradeability_gate_excluded"
    ]
    assert by_code["100004"]["stage_records"]["prescreen"]["present"] is False

    missing = deepcopy(rows)
    for row in missing:
        row.pop("candidate_universe_source")
        row.pop("candidate_universe_available_at")
    invalid = build_pipeline_candidate_selection_audit_v2(
        decision_at=DECISION_AT,
        recall_snapshot=_recall_snapshot(missing, complete=False),
        gate_candidates=missing,
        prescreen_candidates=missing,
        final_candidates=missing[:1],
    )
    codes = {item["code"] for item in invalid["validation"]["errors"]}
    assert "recall_scope_incomplete" in codes
    assert "stage_evidence_incomplete" in codes
    assert "source_refs_missing" in codes
    assert invalid["validation"]["decision_eligible"] is False


def _patch_path(monkeypatch: pytest.MonkeyPatch, module, captured: dict) -> None:
    rows = _candidates()
    runtime = AnalysisRuntime(
        mode="fast", model="offline-test", news_enabled=False, news_max_topics=0, news_tool_max_rounds=0
    )
    monkeypatch.setattr(module, "capture_decision_clock", lambda: SimpleNamespace(decision_at=DECISION_AT))
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(deepseek_configured=False))
    monkeypatch.setattr(module, "resolve_analysis_runtime", lambda *_args: runtime)
    monkeypatch.setattr(
        module,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: PortfolioPreflightResult(holdings=list(holdings), context={}),
    )
    monkeypatch.setattr(module, "build_sector_heat_ranking", lambda **_kwargs: [])
    monkeypatch.setattr(module, "select_target_sectors", lambda *_args, **_kwargs: ["科技"])
    monkeypatch.setattr(module, "build_sector_flow_map_for_opportunities", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "build_sector_divergence_map_for_opportunities", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "select_sector_opportunities", lambda *_args, **_kwargs: [])

    def fake_build(*_args, **kwargs):
        kwargs["recall_audit_sink"].update(_recall_snapshot(rows))
        return deepcopy(rows)

    monkeypatch.setattr(module, "build_candidate_pool", fake_build)
    monkeypatch.setattr(module, "enrich_candidates", lambda pool, **_kwargs: pool)
    monkeypatch.setattr(module, "load_decision_benchmark_specs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "attach_candidate_benchmark_research", lambda pool, *_args, **_kwargs: pool)
    monkeypatch.setattr(module, "build_fund_benchmark_research_batch", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "attach_fund_benchmark_metrics", lambda pool, *_args, **_kwargs: pool)
    monkeypatch.setattr(module, "summarize_benchmark_research", lambda *_args, **_kwargs: {})

    monkeypatch.setattr(module, "prefetch_fund_announcements_compat", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(module, "announcement_fetch_facts", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "merge_market_news_with_announcements", lambda news, *_args, **_kwargs: news)
    monkeypatch.setattr(module, "NewsService", lambda: SimpleNamespace(prefetch_topics=lambda *_args, **_kwargs: []))
    monkeypatch.setattr(module, "summarize_all_topics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "build_discovery_facts", lambda **kwargs: {"candidate_pool": kwargs["candidate_pool"]})
    monkeypatch.setattr(module, "attach_discovery_data_evidence", lambda facts, **_kwargs: facts)
    monkeypatch.setattr(module, "save_discovery_report", lambda report: report)


def test_sync_and_sse_persist_identical_v2_without_sending_it_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import discovery_pipeline, discovery_streaming

    sync: dict = {}
    _patch_path(monkeypatch, discovery_pipeline, sync)

    class Client:
        def generate_report(self, **kwargs):
            sync["facts"] = kwargs["discovery_facts"]
            return FundDiscoveryReport(
                title="sync audit",
                discovery_facts=kwargs["discovery_facts"],
            )

    monkeypatch.setattr(discovery_pipeline, "DiscoveryClient", Client)
    discovery_pipeline.run_discovery(_request())

    sse: dict = {}
    _patch_path(monkeypatch, discovery_streaming, sse)
    monkeypatch.setattr(discovery_streaming, "build_pipeline_metadata", lambda **_kwargs: {})

    def offline(**kwargs):
        sse["facts"] = kwargs["discovery_facts"]
        return FundDiscoveryReport(
            id="audit-sse",
            title="sse audit",
            discovery_facts=kwargs["discovery_facts"],
        )

    monkeypatch.setattr(discovery_streaming, "build_offline_discovery_report", offline)
    list(discovery_streaming.stream_discovery(_request(), user_id=1))

    sync_audit = sync["facts"]["candidate_selection_audit"]
    sse_audit = sse["facts"]["candidate_selection_audit"]
    assert sync_audit == sse_audit
    assert sync_audit["schema_version"] == "discovery_candidate_selection_audit.v2"
    assert sync_audit["stage_counts"] == {"recall": 3, "gate": 3, "prescreen": 2, "final": 2}
    assert sync["facts"]["candidate_selection_audit_v1"]["schema_version"].endswith(".v1")
    assert "fund_lookthrough" not in sync["facts"]
    assert "fund_lookthrough" not in sse["facts"]

    payload = build_user_payload(
        discovery_facts=sync["facts"],
        profile=_profile(),
        focus_sectors=["科技"],
        market_news=[],
        topic_briefs=[],
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "candidate_selection_audit" not in payload["discovery_facts"]
    assert "candidate_selection_audit_v1" not in payload["discovery_facts"]
    assert sync_audit["snapshot_hash"] not in serialized
    assert sync["facts"]["candidate_selection_audit_v1"]["snapshot_hash"] not in serialized
    assert "fund_lookthrough" not in payload["discovery_facts"]
