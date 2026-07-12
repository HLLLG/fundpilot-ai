from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.config import refresh_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, NewsItem, RiskAssessment
from app.services.analyze_streaming import stream_analysis

_FAKE_DEEPSEEK_KEY = "sk-" + "b" * 32


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="Galaxy Growth",
                sector_name="semiconductor",
                holding_amount=10000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
        analysis_mode="deep",
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.0,
        suggested_action="watch",
        alerts=[],
    )


def test_stream_analysis_uses_offline_topic_briefs_to_avoid_slow_summary_llm(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.evaluate_portfolio_risk",
        lambda holdings, profile: _risk(),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: MagicMock(
            get_snapshots_with_nav_trends=lambda holdings: (
                [FundSnapshot(fund_code="519674", fund_name="Galaxy Growth", source="test")],
                {},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(
            prefetch_for_holdings=lambda holdings, max_topics: [
                NewsItem(topic="semiconductor", title="Semiconductor news", source="test")
            ]
        ),
    )
    summary_spy = MagicMock(return_value=[])
    monkeypatch.setattr(
        "app.services.analyze_streaming.summarize_all_topics",
        summary_spy,
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.prepare_analysis_bundle",
        lambda *args, **kwargs: MagicMock(facts={"holdings": [], "portfolio": {}}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        lambda **kwargs: iter(
            [
                '{"title":"t","summary":"s","fund_recommendations":[',
                '{"fund_code":"519674","fund_name":"x","action":"watch","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="briefs-1",
            model_dump=lambda mode="json": {"id": "briefs-1", "title": "t"},
        ),
    )
    monkeypatch.setattr("app.services.analyze_streaming.save_report", lambda report: report)

    events = list(stream_analysis(_request(), user_id=1))

    assert events[-1]["type"] == "done"
    summary_spy.assert_called_once()
    assert summary_spy.call_args.kwargs["offline_only"] is True


def test_stream_analysis_times_out_slow_topic_briefs_and_continues(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    # This assertion measures the topic-summary timeout only. A cold temporary
    # database migration in the portfolio provenance preflight is covered by its
    # own contract tests and must not consume this 200 ms budget.
    monkeypatch.setattr(
        "app.services.analyze_streaming.resolve_portfolio_preflight",
        lambda holdings, **_kwargs: MagicMock(holdings=holdings, context={}),
    )
    monkeypatch.setattr("app.services.analyze_streaming.NEWS_SUMMARY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.services.analyze_streaming.NEWS_SUMMARY_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.evaluate_portfolio_risk",
        lambda holdings, profile: _risk(),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: MagicMock(
            get_snapshots_with_nav_trends=lambda holdings: (
                [FundSnapshot(fund_code="519674", fund_name="Galaxy Growth", source="test")],
                {},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(
            prefetch_for_holdings=lambda holdings, max_topics: [
                NewsItem(topic="semiconductor", title="Semiconductor news", source="test")
            ]
        ),
    )

    def slow_summary(*_args, **_kwargs):
        time.sleep(0.3)
        return []

    monkeypatch.setattr("app.services.analyze_streaming._build_topic_briefs", slow_summary)
    monkeypatch.setattr(
        "app.services.analyze_streaming.prepare_analysis_bundle",
        lambda *args, **kwargs: MagicMock(facts={"holdings": [], "portfolio": {}}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        lambda **kwargs: iter(
            [
                '{"title":"t","summary":"s","fund_recommendations":[',
                '{"fund_code":"519674","fund_name":"x","action":"watch","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="summary-timeout",
            model_dump=lambda mode="json": {"id": "summary-timeout", "title": "t"},
        ),
    )
    monkeypatch.setattr("app.services.analyze_streaming.save_report", lambda report: report)

    start = time.monotonic()
    events = list(stream_analysis(_request(), user_id=1))
    elapsed = time.monotonic() - start

    assert events[-1]["type"] == "done"
    assert elapsed < 0.2
    labels = [event.get("label", "") for event in events if event.get("type") == "stage"]
    assert any("标题规则摘要" in label for label in labels)


def test_stream_analysis_emits_skeleton_before_slow_context_bundle(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    monkeypatch.setattr("app.services.analyze_streaming.CONTEXT_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.evaluate_portfolio_risk",
        lambda holdings, profile: _risk(),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: MagicMock(
            get_snapshots_with_nav_trends=lambda holdings: (
                [FundSnapshot(fund_code="519674", fund_name="Galaxy Growth", source="test")],
                {},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(prefetch_for_holdings=lambda holdings, max_topics: []),
    )
    monkeypatch.setattr("app.services.analyze_streaming._build_topic_briefs", lambda *_args: [])

    def slow_bundle(*_args, **_kwargs):
        time.sleep(0.05)
        return MagicMock(facts={"holdings": [], "portfolio": {}})

    monkeypatch.setattr("app.services.analyze_streaming.prepare_analysis_bundle", slow_bundle)
    monkeypatch.setattr(
        "app.services.analyze_streaming.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        lambda **kwargs: iter(
            [
                '{"title":"t","summary":"s","fund_recommendations":[',
                '{"fund_code":"519674","fund_name":"x","action":"watch","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="context-slow",
            model_dump=lambda mode="json": {"id": "context-slow", "title": "t"},
        ),
    )
    monkeypatch.setattr("app.services.analyze_streaming.save_report", lambda report: report)

    events = list(stream_analysis(_request(), user_id=1))
    types = [event.get("type") for event in events]
    labels = [event.get("label", "") for event in events if event.get("type") == "stage"]

    assert types.index("skeleton") < types.index("done")
    assert any("整理分析上下文" in label for label in labels)


def test_stream_analysis_uses_budgeted_context_bundle(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.evaluate_portfolio_risk",
        lambda holdings, profile: _risk(),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: MagicMock(
            get_snapshots_with_nav_trends=lambda holdings: (
                [FundSnapshot(fund_code="519674", fund_name="Galaxy Growth", source="test")],
                {},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(prefetch_for_holdings=lambda holdings, max_topics: []),
    )
    monkeypatch.setattr("app.services.analyze_streaming._build_topic_briefs", lambda *_args: [])
    captured_kwargs = {}

    def fake_bundle(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock(facts={"holdings": [], "portfolio": {}})

    monkeypatch.setattr("app.services.analyze_streaming.prepare_analysis_bundle", fake_bundle)
    monkeypatch.setattr(
        "app.services.analyze_streaming.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        lambda **kwargs: iter(
            [
                '{"title":"t","summary":"s","fund_recommendations":[',
                '{"fund_code":"519674","fund_name":"x","action":"watch","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="context-budget",
            model_dump=lambda mode="json": {"id": "context-budget", "title": "t"},
        ),
    )
    monkeypatch.setattr("app.services.analyze_streaming.save_report", lambda report: report)

    events = list(stream_analysis(_request(), user_id=1))

    assert events[-1]["type"] == "done"
    assert captured_kwargs["budget_enhancements"] is True
