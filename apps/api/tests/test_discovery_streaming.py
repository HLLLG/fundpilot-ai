"""阶段 4.2：discovery_streaming 端到端单测（mock LLM / 外部 IO）。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest

from app.config import refresh_settings
from app.models import DiscoveryRequest, Holding, InvestorProfile
from app.services.discovery_streaming import stream_discovery

_FAKE_DEEPSEEK_KEY = "sk-" + "a" * 32


def _request(*, mode: str = "fast") -> DiscoveryRequest:
    return DiscoveryRequest(
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
        focus_sectors=["半导体"],
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_sector_heat_ranking",
        lambda **_kwargs: [{"sector_label": "半导体", "heat_score": 1.0}],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.select_target_sectors",
        lambda holdings, focus, heat, profile, scan_mode: ["半导体"],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_candidate_pool",
        lambda *args, **kwargs: [
            {"fund_code": "161725", "fund_name": "招商中证白酒", "sector_label": "白酒"}
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.enrich_candidates",
        lambda pool: pool,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.NewsService",
        lambda: MagicMock(prefetch_topics=lambda topics: []),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.summarize_all_topics",
        lambda market_news, offline_only=False: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_facts",
        lambda **kwargs: {"candidate_pool": kwargs.get("candidate_pool") or []},
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.save_discovery_report",
        lambda report: report,
    )


def test_stream_discovery_deep_emits_tool_stages_and_done(monkeypatch: pytest.MonkeyPatch):
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
        "app.services.discovery_streaming.resolve_analysis_runtime",
        lambda settings, mode: deep_runtime,
    )

    def fake_tool_rounds(self, **kwargs):
        on_stage = kwargs.get("on_stage")
        if on_stage:
            on_stage("tool_round_1", "正在检索新闻 (1/2)…")
        return ([{"role": "user", "content": "{}"}], [])

    monkeypatch.setattr(
        "app.services.discovery_streaming.DiscoveryClient.run_discovery_news_tool_rounds",
        fake_tool_rounds,
    )

    def fake_stream(*, messages, model, max_tokens, response_format=None):
        yield '{"title":"t","summary":"s","recommendations":['
        yield '{"fund_code":"161725","fund_name":"x","action":"建议关注","points":["p"]}'
        yield '],"caveats":["c"]}'

    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        fake_stream,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="disc-deep-1",
            model_dump=lambda mode="json": {"id": "disc-deep-1", "title": "t"},
        ),
    )

    events = list(stream_discovery(_request(mode="deep"), user_id=1))
    types = [e["type"] for e in events]
    stage_names = [e.get("stage") for e in events if e.get("type") == "stage"]
    assert "connected" in stage_names
    assert "tool_round_1" in stage_names
    assert types[-1] == "done"
    assert all(
        isinstance(e.get("elapsed_ms"), int)
        for e in events
        if e.get("type") == "stage"
    )


def test_stream_discovery_emits_skeleton_and_done(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)

    def fake_stream(*, messages, model, max_tokens, response_format=None):
        yield '{"title":"t","summary":"s","recommendations":['
        yield '{"fund_code":"161725","fund_name":"x","action":"建议关注","points":["p"]}'
        yield '],"caveats":["c"]}'

    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        fake_stream,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="disc-1",
            model_dump=lambda mode="json": {"id": "disc-1", "title": "t"},
        ),
    )

    events = list(stream_discovery(_request(), user_id=1))
    types = [e["type"] for e in events]
    assert "skeleton" in types
    assert "report_partial" in types
    assert types[-1] == "done"
    assert events[-1]["report_id"] == "disc-1"


def test_stream_discovery_emits_context_stage_before_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(
        "app.services.discovery_streaming.select_sector_opportunities",
        lambda heat, **kwargs: [{"sector_label": "半导体", "track": "momentum", "score": 70}],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        lambda **kwargs: iter(['{"title":"t","summary":"s","recommendations":[],"caveats":[]}']),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="ctx-1",
            model_dump=lambda mode="json": {"id": "ctx-1"},
        ),
    )

    events = list(stream_discovery(_request(), user_id=1))
    labels = [event.get("label", "") for event in events if event.get("type") == "stage"]
    assert any("整理荐基上下文" in label for label in labels)
    assert events[-1]["type"] == "done"


def test_stream_discovery_does_not_fetch_position_context(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_sector_position_map_for_opportunities",
        lambda labels: (_ for _ in ()).throw(AssertionError("position context should not be fetched")),
        raising=False,
    )

    def fake_select(heat, **kwargs):
        captured.update(kwargs)
        return [{"sector_label": "半导体", "track": "momentum", "score": 70}]

    monkeypatch.setattr(
        "app.services.discovery_streaming.select_sector_opportunities",
        fake_select,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        lambda **kwargs: iter(['{"title":"t","summary":"s","recommendations":[],"caveats":[]}']),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="position-ctx-1",
            model_dump=lambda mode="json": {"id": "position-ctx-1"},
        ),
    )

    events = list(stream_discovery(_request(), user_id=1))

    assert events[-1]["type"] == "done"
    assert "sector_flow_by_label" in captured
    assert "sector_position_by_label" not in captured


def test_stream_discovery_handles_llm_failure_with_salvage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)

    def failing_stream(**kwargs):
        yield '{"title":"t","recommendations":[{"fund_code":"161725"'
        raise httpx.ReadError("connection lost")

    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        failing_stream,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="salvaged",
            model_dump=lambda mode="json": {"id": "salvaged"},
        ),
    )

    events = list(stream_discovery(_request(), user_id=1))
    assert events[-1]["type"] in {"done", "error"}
    stage_types = [e.get("stage") for e in events if e.get("type") == "stage"]
    assert "salvage" in stage_types or events[-1]["type"] == "error"


def test_stream_discovery_prefetches_news_while_building_candidates(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.request_context import get_request_user_id

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_sector_heat_ranking",
        lambda **_kwargs: [{"sector_label": "半导体", "heat_score": 1.0}],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.select_target_sectors",
        lambda holdings, focus, heat, profile, scan_mode: ["半导体"],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    def slow_candidate_pool(*args, **kwargs):
        assert get_request_user_id() == 1
        time.sleep(0.35)
        return [{"fund_code": "161725", "fund_name": "招商中证白酒", "sector_label": "白酒"}]

    def slow_enrich(pool):
        time.sleep(0.35)
        return pool

    def slow_news(topics):
        time.sleep(0.35)
        return []

    monkeypatch.setattr(
        "app.services.discovery_streaming.build_candidate_pool",
        slow_candidate_pool,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.enrich_candidates",
        slow_enrich,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.NewsService",
        lambda: MagicMock(prefetch_topics=slow_news),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.summarize_all_topics",
        lambda market_news, offline_only=False: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_facts",
        lambda **kwargs: {"candidate_pool": kwargs.get("candidate_pool") or []},
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        lambda **kwargs: iter(
            [
                '{"title":"t","summary":"s","recommendations":[',
                '{"fund_code":"161725","fund_name":"x","action":"建议关注","points":["p"]}',
                '],"caveats":["c"]}',
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(
            id="disc-parallel-1",
            model_dump=lambda mode="json": {"id": "disc-parallel-1", "title": "t"},
        ),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.save_discovery_report",
        lambda report: report,
    )

    start = time.monotonic()
    events = list(stream_discovery(_request(), user_id=1))
    elapsed = time.monotonic() - start

    assert events[-1]["type"] == "done"
    assert elapsed < 0.9, f"候选池构建与新闻预取应并行，实际 {elapsed:.2f}s"
