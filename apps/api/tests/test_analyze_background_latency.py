from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import refresh_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, NewsItem, RiskAssessment
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_client import DeepSeekClient

_FAKE_DEEPSEEK_KEY = "sk-" + "c" * 32


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


def _snapshot() -> FundSnapshot:
    return FundSnapshot(
        fund_code="519674",
        fund_name="Galaxy Growth",
        latest_nav=1.0,
        nav_date="2026-06-25",
        source="test",
    )


def _deep_runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        mode="deep",
        model="deepseek-test",
        news_enabled=True,
        news_max_topics=5,
        news_tool_max_rounds=2,
    )


def test_background_generate_report_uses_offline_topic_briefs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    monkeypatch.setenv("FUND_AI_NEWS_SUMMARIZE", "true")
    refresh_settings()
    monkeypatch.setattr(
        "app.services.deepseek_client.resolve_analysis_runtime",
        lambda settings, mode: _deep_runtime(),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.NewsService",
        lambda: MagicMock(
            prefetch_for_holdings=lambda holdings, max_topics: [
                NewsItem(topic="semiconductor", title="Semiconductor news", source="test")
            ]
        ),
    )
    summary_spy = MagicMock(return_value=[])
    monkeypatch.setattr("app.services.deepseek_client.summarize_all_topics", summary_spy)
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *args, **kwargs: MagicMock(facts={"holdings": [], "portfolio": {}}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._build_final_report",
        lambda parsed, **kwargs: MagicMock(provider="deepseek-test"),
    )
    monkeypatch.setattr(
        DeepSeekClient,
        "_chat_completion",
        lambda self, **kwargs: {
            "content": (
                '{"title":"t","summary":"s","fund_recommendations":[],'
                '"recommendations":[],"caveats":[]}'
            )
        },
    )

    report = DeepSeekClient().generate_report(_request(), _risk(), [_snapshot()])

    assert report.provider == "deepseek-test"
    summary_spy.assert_called_once()
    assert summary_spy.call_args.kwargs["offline_only"] is True


def test_background_generate_report_skips_pre_generation_tool_rounds(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.deepseek_client.resolve_analysis_runtime",
        lambda settings, mode: _deep_runtime(),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.NewsService",
        lambda: MagicMock(prefetch_for_holdings=lambda holdings, max_topics: []),
    )
    monkeypatch.setattr("app.services.deepseek_client.summarize_all_topics", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *args, **kwargs: MagicMock(facts={"holdings": [], "portfolio": {}}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._build_final_report",
        lambda parsed, **kwargs: MagicMock(provider="deepseek-test"),
    )

    def fail_tool_rounds(*_args, **_kwargs):
        raise AssertionError("background analysis should not run pre-generation news tool rounds")

    monkeypatch.setattr(DeepSeekClient, "run_news_tool_rounds", fail_tool_rounds)
    monkeypatch.setattr(
        DeepSeekClient,
        "_chat_completion",
        lambda self, **kwargs: {
            "content": (
                '{"title":"t","summary":"s","fund_recommendations":[],'
                '"recommendations":[],"caveats":[]}'
            )
        },
    )

    report = DeepSeekClient().generate_report(_request(), _risk(), [_snapshot()])

    assert report.provider == "deepseek-test"
