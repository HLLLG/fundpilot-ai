from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import refresh_settings
from app.models import (
    AnalysisRequest,
    DiscoveryRequest,
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.analysis_payload import AnalysisFactsBundle, compact_news_titles
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_client import DeepSeekClient
from app.services.discovery_client import DiscoveryClient
from app.services.news_service import (
    announcement_fetch_facts,
    compact_announcement_fetch_status,
    merge_market_news_with_announcements,
)

NOW = datetime(2026, 7, 14, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
_FAKE_KEY = "sk-" + "n" * 32


def _announcement_result(*, status: str = "timeout") -> dict:
    counts = {"ok": 0, "empty": 0, "error": 0, "timeout": 0}
    counts[status] = 1
    return {
        "items": [],
        "requested": 1,
        **counts,
        "coverage": 0.0,
        "evidence_coverage": 0.0,
        "fetched_at": "2026-07-14T09:30:00+08:00",
        "requested_codes": ["519674"],
        "funds": [{"fund_code": "519674", "status": status}],
    }


def _expected_status(status: str = "timeout") -> dict:
    return compact_announcement_fetch_status(_announcement_result(status=status))


def _daily_request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="test fund",
                sector_name="semiconductor",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(),
        analysis_mode="fast",
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=0,
        suggested_action="watch",
        alerts=[],
    )


def _runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        mode="fast",
        model="deepseek-test",
        news_enabled=True,
        news_max_topics=5,
        news_tool_max_rounds=0,
    )


def test_merge_market_and_announcement_news_uses_one_global_recency_order() -> None:
    merged = merge_market_news_with_announcements(
        [
            NewsItem(
                topic="market",
                title="market-old",
                source="eastmoney",
                published_at="2026-07-13 18:00:00",
            ),
            NewsItem(
                topic="market",
                title="market-new",
                source="eastmoney",
                published_at="2026-07-14 09:25:00",
            ),
        ],
        [
            NewsItem(
                topic="000001",
                title="announcement-middle",
                source="fund-announcement",
                published_at="2026-07-14 08:30:00",
            )
        ],
        now=NOW,
    )

    assert [item.title for item in merged] == [
        "market-new",
        "announcement-middle",
        "market-old",
    ]
    assert all(item.is_today == (item.title != "market-old") for item in merged)


def test_merge_does_not_dedupe_generic_announcement_titles_across_funds() -> None:
    announcements = [
        NewsItem(
            topic=code,
            title="quarterly-report-notice",
            source="fund-announcement",
            published_at="2026-07-14",
        )
        for code in ("000001", "000002")
    ]

    merged = merge_market_news_with_announcements([], announcements, now=NOW)

    assert [item.topic for item in merged] == ["000001", "000002"]
    compact = compact_news_titles(merged, min_items=0)
    assert [item["topic"] for item in compact] == ["000001", "000002"]


@pytest.mark.parametrize(
    ("counts", "expected_status"),
    [
        ({"requested": 2, "ok": 0, "empty": 2}, "empty"),
        ({"requested": 2, "ok": 1, "timeout": 1}, "partial"),
        ({"requested": 2, "timeout": 2}, "timeout"),
        ({"requested": 2, "error": 2}, "error"),
    ],
)
def test_compact_status_distinguishes_empty_from_failures(
    counts: dict,
    expected_status: str,
) -> None:
    compact = compact_announcement_fetch_status(
        {
            **counts,
            "coverage": 0.5,
            "evidence_coverage": 0.25,
            "fetched_at": "2026-07-14T09:30:00+08:00",
            "items": ["must-not-leak"],
            "funds": [{"fund_code": "000001"}],
        }
    )

    assert compact["status"] == expected_status
    assert compact["requested"] == 2
    assert compact["coverage"] == 0.5
    assert compact["evidence_coverage"] == 0.25
    assert "items" not in compact
    assert "funds" not in compact


def test_persisted_announcement_facts_keep_per_fund_audit_detail_but_recompact_for_llm():
    raw = _announcement_result(status="empty")

    facts = announcement_fetch_facts(raw)

    assert facts["details"]["requested_codes"] == ["519674"]
    assert facts["details"]["funds"] == [
        {"fund_code": "519674", "status": "empty"}
    ]
    assert compact_announcement_fetch_status(facts) == _expected_status("empty")


def test_daily_sync_sends_compact_announcement_state_in_actual_user_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    client = DeepSeekClient()
    client.news_service.prefetch_for_holdings = lambda *_args, **_kwargs: []
    client.news_service.prefetch_fund_announcements = (
        lambda *_args, **_kwargs: _announcement_result()
    )
    bundle = AnalysisFactsBundle(
        session={"calendar_date": "2026-07-14"},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={"holdings": [], "portfolio": {}},
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    captured: dict = {}

    def generate(messages, _runtime):
        captured["messages"] = messages
        return {
            "title": "t",
            "summary": "s",
            "fund_recommendations": [
                {"fund_code": "519674", "fund_name": "test fund", "action": "watch"}
            ],
            "caveats": [],
        }

    monkeypatch.setattr(client, "_generate_report_json", generate)
    monkeypatch.setattr(
        "app.services.deepseek_client.judge_parsed_report",
        lambda parsed, *_args, **_kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._build_final_report",
        lambda parsed, **_kwargs: parsed,
    )

    client.generate_report(
        _daily_request(),
        _risk(),
        [FundSnapshot(fund_code="519674", fund_name="test fund", source="test")],
        decision_at=NOW,
    )

    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["analysis_facts"]["fund_announcements"] == _expected_status()
    refresh_settings()


def test_discovery_sync_sends_compact_announcement_state_in_actual_user_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    client = DiscoveryClient()
    captured: dict = {}

    def call_model(_system_prompt, user_payload, _model):
        captured["user_payload"] = user_payload
        return {"title": "t", "summary": "s", "recommendations": [], "caveats": []}

    monkeypatch.setattr(client, "_call_model", call_model)
    monkeypatch.setattr(
        "app.services.discovery_client.judge_parsed_discovery_report",
        lambda parsed, **_kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.discovery_client.build_discovery_report_from_parsed",
        lambda parsed, **_kwargs: parsed,
    )
    facts = {
        "session": {"calendar_date": "2026-07-14"},
        "candidate_pool": [],
        "portfolio_gap": {},
    }

    client.generate_report(
        target_sectors=[],
        focus_sectors=[],
        candidate_pool=[],
        discovery_facts=facts,
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=0,
        sector_heat=[],
        market_news=[],
        topic_briefs=[],
        analysis_mode="fast",
        decision_at=NOW,
        announcement_meta=_expected_status(),
    )

    assert (
        captured["user_payload"]["discovery_facts"]["fund_announcements"]
        == _expected_status()
    )
    refresh_settings()


def test_daily_sse_sends_compact_announcement_state_in_actual_user_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.analyze_streaming as streaming

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    request = _daily_request()
    bundle = AnalysisFactsBundle(
        session={"calendar_date": "2026-07-14"},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={"holdings": [], "portfolio": {}},
    )
    monkeypatch.setattr(
        streaming,
        "capture_decision_clock",
        lambda: SimpleNamespace(decision_at=NOW),
    )
    monkeypatch.setattr(
        streaming,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: SimpleNamespace(holdings=holdings, context={}),
    )
    monkeypatch.setattr(
        streaming,
        "FundProfileService",
        lambda: SimpleNamespace(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(streaming, "evaluate_portfolio_risk", lambda *_args: _risk())
    monkeypatch.setattr(
        streaming,
        "FundDataService",
        lambda: SimpleNamespace(
            get_snapshots_with_nav_trends=lambda _holdings: (
                [FundSnapshot(fund_code="519674", fund_name="test fund", source="test")],
                {},
            )
        ),
    )

    class FakeNewsService:
        def prefetch_for_holdings(self, *_args, **_kwargs):
            return []

        def prefetch_fund_announcements(self, *_args, **_kwargs):
            return _announcement_result()

    monkeypatch.setattr(streaming, "NewsService", FakeNewsService)
    monkeypatch.setattr(streaming, "summarize_all_topics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(streaming, "prepare_analysis_bundle", lambda *_args, **_kwargs: bundle)
    monkeypatch.setattr(streaming, "resolve_analysis_runtime", lambda *_args: _runtime())
    session = SimpleNamespace(session_id="announcement-daily", operator_notes=[])
    monkeypatch.setattr(streaming, "create_stream_session", lambda: session)
    monkeypatch.setattr(streaming, "set_stream_session_stage", lambda *_args: None)
    monkeypatch.setattr(streaming, "delete_stream_session", lambda *_args: None)
    captured: dict = {}

    def stream_chat(*, messages, **_kwargs):
        captured["messages"] = messages
        yield (
            '{"title":"t","summary":"s","fund_recommendations":['
            '{"fund_code":"519674","fund_name":"test fund","action":"watch"}'
            '],"caveats":[]}'
        )

    monkeypatch.setattr(streaming, "stream_chat_completion", stream_chat)
    monkeypatch.setattr(
        streaming,
        "judge_parsed_report",
        lambda parsed, *_args, **_kwargs: (parsed, {}),
    )
    report = SimpleNamespace(
        id="daily-sse",
        model_dump=lambda mode="json": {"id": "daily-sse", "title": "t"},
    )
    monkeypatch.setattr(streaming, "_build_final_report", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(streaming, "save_report", lambda value: value)

    events = list(streaming.stream_analysis(request, user_id=1))

    assert events[-1]["type"] == "done"
    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["analysis_facts"]["fund_announcements"] == _expected_status()
    refresh_settings()


def test_discovery_sse_sends_compact_announcement_state_in_actual_user_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.discovery_streaming as streaming

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    request = DiscoveryRequest(
        holdings=[],
        profile=InvestorProfile(expected_investment_amount=10_000),
        focus_sectors=["semiconductor"],
        analysis_mode="fast",
    )
    monkeypatch.setattr(
        streaming,
        "capture_decision_clock",
        lambda: SimpleNamespace(decision_at=NOW),
    )
    monkeypatch.setattr(
        streaming,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: SimpleNamespace(holdings=holdings, context={}),
    )
    monkeypatch.setattr(
        streaming,
        "build_sector_heat_ranking",
        lambda **_kwargs: [{"sector_label": "semiconductor", "heat_score": 1}],
    )
    monkeypatch.setattr(
        streaming,
        "select_target_sectors",
        lambda *_args, **_kwargs: ["semiconductor"],
    )
    monkeypatch.setattr(
        streaming,
        "build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        streaming,
        "build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(streaming, "select_sector_opportunities", lambda *_args, **_kwargs: [])
    candidate = {
        "fund_code": "161725",
        "fund_name": "candidate",
        "sector_label": "semiconductor",
    }
    monkeypatch.setattr(
        streaming,
        "build_candidate_pool",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        streaming,
        "enrich_candidates",
        lambda pool, **_kwargs: pool,
    )
    monkeypatch.setattr(
        streaming,
        "finalize_candidate_pool",
        lambda pool, *_args, **_kwargs: pool,
    )

    class FakeNewsService:
        def prefetch_topics(self, *_args, **_kwargs):
            return []

        def prefetch_fund_announcements(self, *_args, **_kwargs):
            result = _announcement_result()
            result["requested_codes"] = ["161725"]
            return result

    monkeypatch.setattr(streaming, "NewsService", FakeNewsService)
    monkeypatch.setattr(streaming, "summarize_all_topics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        streaming,
        "build_discovery_facts",
        lambda **kwargs: {
            "session": {"calendar_date": "2026-07-14"},
            "candidate_pool": kwargs["candidate_pool"],
            "portfolio_gap": {},
        },
    )
    monkeypatch.setattr(
        streaming,
        "attach_discovery_data_evidence",
        lambda facts, **_kwargs: facts,
    )
    monkeypatch.setattr(streaming, "resolve_analysis_runtime", lambda *_args: _runtime())
    captured: dict = {}

    def stream_chat(*, messages, **_kwargs):
        captured["messages"] = messages
        yield '{"title":"t","summary":"s","recommendations":[],"caveats":[]}'

    monkeypatch.setattr(streaming, "stream_chat_completion", stream_chat)
    monkeypatch.setattr(
        streaming,
        "judge_parsed_discovery_report",
        lambda parsed, **_kwargs: (parsed, {}),
    )
    report = SimpleNamespace(
        id="discovery-sse",
        model_dump=lambda mode="json": {"id": "discovery-sse", "title": "t"},
    )
    monkeypatch.setattr(
        streaming,
        "build_discovery_report_from_parsed",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(streaming, "save_discovery_report", lambda value: value)

    events = list(streaming.stream_discovery(request, user_id=1))

    assert events[-1]["type"] == "done"
    user_payload = json.loads(captured["messages"][1]["content"])
    assert (
        user_payload["discovery_facts"]["fund_announcements"]
        == _expected_status()
    )
    refresh_settings()
