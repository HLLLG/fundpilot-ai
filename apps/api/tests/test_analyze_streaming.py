"""阶段 2：analyze_streaming 端到端单测（mock LLM / 外部 IO）。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest

from app.config import refresh_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analyze_streaming import stream_analysis

_FAKE_DEEPSEEK_KEY = "sk-" + "a" * 32


def _request(*, mode: str = "fast") -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
        analysis_mode=mode,  # type: ignore[arg-type]
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.0,
        suggested_action="watch",
        alerts=[],
    )


def _snapshot() -> FundSnapshot:
    return FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
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
            get_snapshots_with_nav_trends=lambda holdings: ([_snapshot()], {})
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(prefetch_for_holdings=lambda holdings, max_topics: []),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_topic_briefs",
        lambda market_news, settings=None: [],
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
        "app.services.analyze_streaming.save_report",
        lambda report: report,
    )


def test_stream_analysis_deep_skips_pre_generation_tool_rounds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)

    from app.services.analysis_runtime import AnalysisRuntime

    deep_runtime = AnalysisRuntime(
        mode="deep",
        model="deepseek-test",
        news_enabled=True,
        news_max_topics=5,
        news_tool_max_rounds=2,
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.resolve_analysis_runtime",
        lambda settings, mode: deep_runtime if mode == "deep" else deep_runtime,
    )

    def fake_tool_rounds(self, *args, **kwargs):
        on_stage = kwargs.get("on_stage")
        if on_stage:
            on_stage("tool_round_1", "正在检索新闻 (1/2)…")
        return ([{"role": "user", "content": "{}"}], [])

    monkeypatch.setattr(
        "app.services.analyze_streaming.DeepSeekClient.run_news_tool_rounds",
        fake_tool_rounds,
    )

    def fake_stream(*, messages, model, max_tokens, response_format=None):
        yield '{"title":"t","summary":"s","fund_recommendations":['
        yield '{"fund_code":"519674","fund_name":"x","action":"观察","points":["p"]}'
        yield '],"caveats":["c"]}'

    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        fake_stream,
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="deep-1",
            model_dump=lambda mode="json": {"id": "deep-1", "title": "t"},
        ),
    )

    events = list(stream_analysis(_request(mode="deep"), user_id=1))
    types = [e["type"] for e in events]
    stage_names = [e.get("stage") for e in events if e.get("type") == "stage"]
    assert "tool_round_1" not in stage_names
    assert types[-1] == "done"
    assert all(
        isinstance(e.get("elapsed_ms"), int)
        for e in events
        if e.get("type") == "stage"
    )


def test_stream_analysis_emits_skeleton_and_done(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)

    def fake_stream(*, messages, model, max_tokens, response_format=None):
        yield '{"title":"t","summary":"s","fund_recommendations":['
        yield '{"fund_code":"519674","fund_name":"x","action":"观察","points":["p"]}'
        yield '],"caveats":["c"]}'

    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        fake_stream,
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="report-1",
            model_dump=lambda mode="json": {"id": "report-1", "title": "t"},
        ),
    )

    events = list(stream_analysis(_request(), user_id=1))
    types = [e["type"] for e in events]
    assert types[0] == "session"
    assert "skeleton" in types
    assert "report_partial" in types
    assert types[-1] == "done"
    assert events[-1]["report_id"] == "report-1"


def test_stream_analysis_handles_llm_failure_with_salvage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)

    def failing_stream(**kwargs):
        yield '{"title":"t","fund_recommendations":[{"fund_code":"519674"'
        raise httpx.ReadError("connection lost")

    monkeypatch.setattr(
        "app.services.analyze_streaming.stream_chat_completion",
        failing_stream,
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="salvaged",
            model_dump=lambda mode="json": {"id": "salvaged"},
        ),
    )

    events = list(stream_analysis(_request(), user_id=1))
    assert events[-1]["type"] in {"done", "error"}
    stage_types = [e.get("stage") for e in events if e.get("type") == "stage"]
    assert "salvage" in stage_types or events[-1]["type"] == "error"


def test_stream_analysis_prefetches_fund_data_and_news_in_parallel(
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

    def slow_fund_data(holdings):
        time.sleep(0.35)
        return ([_snapshot()], {})

    def slow_news(holdings, max_topics):
        time.sleep(0.35)
        return []

    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: MagicMock(get_snapshots_with_nav_trends=slow_fund_data),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: MagicMock(prefetch_for_holdings=slow_news),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_topic_briefs",
        lambda market_news, settings=None: [],
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
                '{"fund_code":"519674","fund_name":"x","action":"观察","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_final_report",
        lambda parsed, **kwargs: MagicMock(
            id="parallel-1",
            model_dump=lambda mode="json": {"id": "parallel-1", "title": "t"},
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.save_report",
        lambda report: report,
    )

    start = time.monotonic()
    events = list(stream_analysis(_request(), user_id=1))
    elapsed = time.monotonic() - start

    assert events[-1]["type"] == "done"
    assert elapsed < 0.55, f"fund_data 与 news_prefetch 应并行，实际 {elapsed:.2f}s"
