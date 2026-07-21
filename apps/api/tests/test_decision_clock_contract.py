from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import (
    AnalysisRequest,
    DiscoveryRequest,
    FundDiscoveryReport,
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    Report,
    RiskAssessment,
)
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    build_user_payload as build_analysis_user_payload,
    finalize_analysis_facts,
)
from app.services.analysis_runtime import AnalysisRuntime
from app.services.decision_clock import capture_decision_clock
from app.services.deepseek_client import _build_final_report
from app.services.discovery_client import build_discovery_report_from_parsed
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_payload import build_user_payload as build_discovery_user_payload
from app.services.discovery_candidate_pool import _with_data_quality_gate
from app.services.news_summarizer import _summarize_topic_with_flash
from app.services.news_cache import get_cached_news, save_cached_news
from app.services.news_service import NewsService


SHANGHAI = ZoneInfo("Asia/Shanghai")
BEFORE_MIDNIGHT = datetime(2026, 7, 14, 23, 59, 59, tzinfo=SHANGHAI)
AFTER_MIDNIGHT = datetime(2026, 7, 15, 0, 0, 1, tzinfo=SHANGHAI)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="测试持仓",
                holding_amount=10_000,
                sector_name="半导体",
            )
        ],
        profile=InvestorProfile(expected_investment_amount=20_000),
        analysis_mode="deep",
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _patch_analysis_stream_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundProfileService",
        lambda: SimpleNamespace(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.evaluate_portfolio_risk",
        lambda holdings, profile: _risk(),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.FundDataService",
        lambda: SimpleNamespace(
            get_snapshots_with_nav_trends=lambda holdings: (
                [
                    FundSnapshot(
                        fund_code="519674",
                        fund_name="test",
                        source="test",
                    )
                ],
                {},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.NewsService",
        lambda: SimpleNamespace(
            prefetch_for_holdings=lambda holdings, max_topics: []
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming._build_topic_briefs",
        lambda market_news, settings=None: [],
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.prepare_analysis_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            facts={"holdings": [], "portfolio": {}}
        ),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.judge_parsed_report",
        lambda parsed, *args, **kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.analyze_streaming.save_report",
        lambda report: report,
    )


def _stream_discovery_request() -> DiscoveryRequest:
    return DiscoveryRequest(
        holdings=_request().holdings,
        profile=InvestorProfile(expected_investment_amount=20_000),
        analysis_mode="fast",
        focus_sectors=["test"],
    )


def _patch_discovery_stream_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_sector_heat_ranking",
        lambda **_kwargs: [{"sector_label": "test", "heat_score": 1.0}],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.select_target_sectors",
        lambda holdings, focus, heat, profile, scan_mode: ["test"],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_candidate_pool",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.enrich_candidates",
        lambda pool, **kwargs: pool,
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.NewsService",
        lambda: SimpleNamespace(prefetch_topics=lambda topics: []),
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


def _boundary_news() -> list[NewsItem]:
    return [
        NewsItem(
            topic="半导体",
            title="午夜前新闻",
            published_at="2026-07-14T23:58:00+08:00",
            source="test",
            is_today=True,
        ),
        NewsItem(
            topic="半导体",
            title="午夜后新闻",
            published_at="2026-07-15T00:00:00+08:00",
            source="test",
            is_today=False,
        ),
    ]


def test_decision_clock_freezes_shanghai_session_across_midnight() -> None:
    before = capture_decision_clock(BEFORE_MIDNIGHT)
    after = capture_decision_clock(AFTER_MIDNIGHT)

    assert before.decision_at == BEFORE_MIDNIGHT
    assert before.session["timezone"] == "Asia/Shanghai"
    assert before.session["decision_at"] == BEFORE_MIDNIGHT.isoformat()
    assert before.session["calendar_date"] == "2026-07-14"

    # A separately captured request after midnight correctly belongs to the next
    # day; it must not mutate the already-captured request clock above.
    assert after.session["decision_at"] == AFTER_MIDNIGHT.isoformat()
    assert after.session["calendar_date"] == "2026-07-15"
    assert before.session["calendar_date"] == "2026-07-14"


def test_daily_payload_and_news_facts_share_the_frozen_pre_midnight_date(
    monkeypatch,
) -> None:
    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    news = _boundary_news()
    facts = finalize_analysis_facts(
        {
            "readonly": True,
            "session": clock.session,
            "holdings": [],
            "portfolio": {},
        },
        market_news=news,
        topic_briefs=[],
        decision_at=clock.decision_at,
    )
    bundle = AnalysisFactsBundle(
        session=clock.session,
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts=facts,
    )

    # Simulate the process wall clock crossing midnight. The payload must prefer
    # the immutable session date and never re-sample this fallback clock.
    monkeypatch.setattr(
        "app.services.analysis_payload.normalize_news_now",
        lambda _value=None: AFTER_MIDNIGHT,
    )
    payload = build_analysis_user_payload(
        _request(),
        _risk(),
        [],
        news,
        analysis_bundle=bundle,
        decision_at=clock.decision_at,
    )

    assert payload["today"] == "2026-07-14"
    assert payload["analysis_facts"]["session"]["decision_at"] == BEFORE_MIDNIGHT.isoformat()
    assert payload["analysis_facts"]["news"]["calendar_date"] == "2026-07-14"
    assert payload["analysis_facts"]["news"]["today_items"] == 1
    assert facts["news"]["topics"][0]["today_count"] == 1


def test_discovery_facts_and_payload_share_the_frozen_pre_midnight_date(
    monkeypatch,
) -> None:
    clock = capture_decision_clock(BEFORE_MIDNIGHT)

    # Keep the contract test independent from network-backed enhancement facts.
    monkeypatch.setattr(
        "app.services.discovery_facts.build_signal_backtest_context",
        lambda _sectors: {},
    )
    monkeypatch.setattr(
        "app.services.discovery_facts.build_target_sector_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.discovery_facts.build_stock_connect_flow_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.discovery_facts.build_candidate_factor_scores",
        lambda _pool: {},
    )

    profile = InvestorProfile(expected_investment_amount=20_000)
    facts = build_discovery_facts(
        holdings=[],
        profile=profile,
        target_sectors=[],
        sector_heat=[],
        candidate_pool=[],
        market_news=_boundary_news(),
        topic_briefs=[],
        decision_at=clock.decision_at,
    )

    monkeypatch.setattr(
        "app.services.discovery_payload.normalize_news_now",
        lambda _value=None: AFTER_MIDNIGHT,
    )
    payload = build_discovery_user_payload(
        discovery_facts=facts,
        profile=profile,
        focus_sectors=[],
        market_news=_boundary_news(),
        topic_briefs=[],
        analysis_mode="deep",
    )

    assert facts["session"]["decision_at"] == BEFORE_MIDNIGHT.isoformat()
    assert facts["session"]["calendar_date"] == "2026-07-14"
    assert facts["news"]["calendar_date"] == "2026-07-14"
    assert facts["news"]["today_items"] == 1
    assert payload["today"] == "2026-07-14"
    assert payload["discovery_facts"]["session"]["decision_at"] == BEFORE_MIDNIGHT.isoformat()
    assert payload["discovery_facts"]["news"]["calendar_date"] == "2026-07-14"


def test_news_summarizer_uses_the_frozen_date_in_provider_payload(
    monkeypatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "基于午夜前新闻的摘要",
                                    "points": [
                                        {
                                            "headline": "午夜前要点",
                                            "sentiment": "neutral",
                                            "source_titles": ["午夜前新闻"],
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(*_args, **kwargs):
        captured["request"] = kwargs["json"]
        return FakeResponse()

    class FakeClient:
        post = staticmethod(fake_post)

    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "d" * 32)
    monkeypatch.setattr(
        "app.services.news_summarizer.get_deepseek_http_client",
        lambda _settings: FakeClient(),
    )

    brief = _summarize_topic_with_flash(
        "半导体",
        [_boundary_news()[0]],
        settings,
        now=BEFORE_MIDNIGHT,
    )
    provider_user_payload = json.loads(captured["request"]["messages"][1]["content"])

    assert provider_user_payload["today"] == "2026-07-14"
    assert brief.summarized_at == BEFORE_MIDNIGHT.astimezone(timezone.utc)
    assert brief.points[0].is_today is True


def test_daily_and_discovery_report_created_at_replay_the_same_decision_clock(
    monkeypatch,
) -> None:
    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    request = _request()
    risk = _risk()
    base_facts = {
        "readonly": True,
        "session": clock.session,
        "holdings": [],
        "portfolio": {},
        "data_evidence_guard": {"execution_blocked": False},
    }
    bundle = AnalysisFactsBundle(
        session=clock.session,
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts=base_facts,
    )
    fallback = Report(
        created_at=clock.decision_at,
        title="fallback",
        risk=risk,
        holdings=request.holdings,
        summary="fallback",
        recommendations=[],
        caveats=[],
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._offline_report",
        lambda *_args, **_kwargs: fallback,
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._finalize_recommendations",
        lambda *_args, **_kwargs: ([], []),
    )

    daily = _build_final_report(
        {"title": "日报", "summary": "固定时钟日报", "caveats": []},
        request=request,
        risk=risk,
        snapshots=[],
        market_news=[],
        topic_briefs=[],
        nav_trends={},
        analysis_bundle=bundle,
        judge_meta={},
        runtime=AnalysisRuntime(
            mode="deep",
            model="test-pro",
            news_enabled=True,
            news_max_topics=5,
            news_tool_max_rounds=0,
            news_tool_rounds_configured=3,
        ),
        decision_at=clock.decision_at,
    )
    discovery = build_discovery_report_from_parsed(
        {
            "title": "荐基",
            "summary": "固定时钟荐基",
            "market_view": "",
            "recommendations": [],
            "caveats": [],
        },
        target_sectors=[],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=[],
        discovery_facts={
            "session": clock.session,
            "portfolio_gap": {"holdings_slim": []},
            "data_evidence_guard": {"execution_blocked": False},
        },
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=0,
        sector_heat=[],
        decision_at=clock.decision_at,
    )

    assert daily.created_at.isoformat() == BEFORE_MIDNIGHT.isoformat()
    assert discovery.created_at.isoformat() == BEFORE_MIDNIGHT.isoformat()


def test_news_cache_date_and_session_policy_use_the_supplied_decision_clock(
    monkeypatch,
) -> None:
    captured: dict = {}
    service = NewsService()
    service.settings = SimpleNamespace(news_enabled=True, news_per_topic=3)

    def fake_session(now=None):
        captured["session_now"] = now
        return {"session_kind": "trading_day_intraday"}

    def fake_cache(_topic, *, cache_date, max_age_seconds, now=None):
        captured["cache_date"] = cache_date
        captured["max_age_seconds"] = max_age_seconds
        captured["cache_now"] = now
        return []

    monkeypatch.setattr("app.services.news_service.build_trading_session", fake_session)
    monkeypatch.setattr("app.services.news_service.get_cached_news", fake_cache)

    assert service.search("半导体", now=BEFORE_MIDNIGHT) == []
    assert captured["session_now"] == BEFORE_MIDNIGHT
    assert captured["cache_now"] == BEFORE_MIDNIGHT
    assert captured["cache_date"] == "2026-07-14"
    assert isinstance(captured["max_age_seconds"], int)


def test_news_cache_ttl_replay_uses_the_fixed_decision_clock(monkeypatch) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr("app.services.news_cache._connect", lambda: connection)
    cached_at = datetime(2020, 1, 2, 1, 0, tzinfo=timezone.utc)
    item = NewsItem(topic="clock", title="historical evidence")

    save_cached_news(
        "clock",
        [item],
        cache_date="2020-01-02",
        now=cached_at,
    )

    replayed = get_cached_news(
        "clock",
        cache_date="2020-01-02",
        max_age_seconds=900,
        now=cached_at + timedelta(seconds=899),
    )
    expired = get_cached_news(
        "clock",
        cache_date="2020-01-02",
        max_age_seconds=900,
        now=cached_at + timedelta(seconds=901),
    )
    from_the_future = get_cached_news(
        "clock",
        cache_date="2020-01-02",
        max_age_seconds=900,
        now=cached_at - timedelta(seconds=1),
    )

    assert replayed == [item]
    assert expired is None
    assert from_the_future is None
    stored = connection.execute(
        "SELECT updated_at FROM news_cache WHERE cache_key = ?",
        ("clock:2020-01-02",),
    ).fetchone()
    assert stored["updated_at"] == cached_at.isoformat()


def _daily_report(decision_at: datetime) -> Report:
    request = _request()
    return Report(
        created_at=decision_at,
        title="clock",
        risk=_risk(),
        holdings=request.holdings,
        summary="clock",
        recommendations=[],
        caveats=[],
    )


def _discovery_report(decision_at: datetime) -> FundDiscoveryReport:
    return FundDiscoveryReport(
        created_at=decision_at,
        title="clock",
        summary="clock",
        target_sectors=[],
        candidate_pool=[],
        recommendations=[],
        caveats=[],
    )


def test_run_analysis_captures_once_and_passes_the_same_decision_at(
    monkeypatch,
) -> None:
    import app.services.analyze_pipeline as pipeline

    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    calls = {"capture": 0}
    captured: dict = {}

    def fake_capture():
        calls["capture"] += 1
        return clock

    monkeypatch.setattr(pipeline, "capture_decision_clock", fake_capture)

    def preflight(holdings, **kwargs):
        captured["preflight_decision_at"] = kwargs.get("now")
        return SimpleNamespace(holdings=holdings, context={})

    monkeypatch.setattr(
        pipeline,
        "resolve_portfolio_preflight",
        preflight,
    )
    monkeypatch.setattr(
        pipeline,
        "FundProfileService",
        lambda: SimpleNamespace(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(pipeline, "evaluate_portfolio_risk", lambda *_args: _risk())
    monkeypatch.setattr(
        pipeline,
        "FundDataService",
        lambda: SimpleNamespace(
            get_snapshots_with_nav_trends=lambda holdings: (
                [
                    FundSnapshot(
                        fund_code=holdings[0].fund_code,
                        fund_name=holdings[0].fund_name,
                        source="test",
                    )
                ],
                {},
            )
        ),
    )

    def generate_report(*_args, **kwargs):
        captured["decision_at"] = kwargs["decision_at"]
        return _daily_report(kwargs["decision_at"])

    monkeypatch.setattr(
        pipeline,
        "DeepSeekClient",
        lambda: SimpleNamespace(generate_report=generate_report),
    )
    monkeypatch.setattr(pipeline, "save_report", lambda report: report)

    result = pipeline.run_analysis(_request())

    assert calls["capture"] == 1
    assert {captured["decision_at"], captured["preflight_decision_at"]} == {
        BEFORE_MIDNIGHT
    }
    assert result.created_at == BEFORE_MIDNIGHT


def _patch_discovery_clock_prep(monkeypatch, module, captured: dict) -> None:
    def preflight(holdings, **kwargs):
        captured["preflight_decision_at"] = kwargs.get("now")
        return SimpleNamespace(holdings=holdings, context={})

    monkeypatch.setattr(
        module,
        "resolve_portfolio_preflight",
        preflight,
    )

    def sector_heat(*_args, **kwargs):
        captured["sector_heat_decision_at"] = kwargs.get("decision_at")
        return []

    monkeypatch.setattr(module, "build_sector_heat_ranking", sector_heat)
    monkeypatch.setattr(module, "select_target_sectors", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        module,
        "build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(module, "select_sector_opportunities", lambda *_args, **_kwargs: [])
    def build_pool(*_args, **kwargs):
        captured["candidate_pool_decision_at"] = kwargs.get("decision_at")
        captured["prepared_universe_rows"] = kwargs.get("prepared_universe_rows")
        return []

    def enrich_pool(pool, **kwargs):
        captured["candidate_enrich_decision_at"] = kwargs.get("decision_at")
        return pool

    monkeypatch.setattr(module, "build_candidate_pool", build_pool)
    monkeypatch.setattr(module, "enrich_candidates", enrich_pool)
    monkeypatch.setattr(module, "finalize_candidate_pool", lambda pool, *_args, **_kwargs: pool)

    class FakeNewsService:
        def prefetch_topics(self, _topics, *, now=None):
            captured["news_decision_at"] = now
            return []

        def prefetch_fund_announcements(self, _codes, *, now=None):
            captured["announcement_decision_at"] = now
            return {"items": [], "requested": 0}

    monkeypatch.setattr(module, "NewsService", FakeNewsService)
    monkeypatch.setattr(
        module,
        "summarize_all_topics",
        lambda *_args, **kwargs: captured.setdefault("summary_decision_at", kwargs.get("now"))
        and [],
    )

    def facts(**kwargs):
        captured["facts_decision_at"] = kwargs.get("decision_at")
        return {
            "readonly": True,
            "session": capture_decision_clock(kwargs["decision_at"]).session,
            "candidate_pool": [],
            "portfolio_gap": {"target_sectors": [], "holdings_slim": []},
        }

    monkeypatch.setattr(module, "build_discovery_facts", facts)
    monkeypatch.setattr(module, "attach_discovery_data_evidence", lambda value, **_kwargs: value)


def test_run_discovery_captures_once_and_passes_one_clock_to_all_consumers(
    monkeypatch,
) -> None:
    import app.services.discovery_pipeline as pipeline

    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    calls = {"capture": 0}
    captured: dict = {}
    order: list[str] = []
    prepared_universe = [{"fund_code": "000001"}]

    def fake_capture():
        order.append("decision_clock")
        calls["capture"] += 1
        return clock

    monkeypatch.setattr(
        pipeline,
        "fetch_discovery_fund_universe_cached",
        lambda **_kwargs: order.append("fund_universe") or prepared_universe,
    )
    monkeypatch.setattr(pipeline, "capture_decision_clock", fake_capture)
    _patch_discovery_clock_prep(monkeypatch, pipeline, captured)

    def generate_report(**kwargs):
        captured["client_decision_at"] = kwargs["decision_at"]
        return _discovery_report(kwargs["decision_at"])

    monkeypatch.setattr(
        pipeline,
        "DiscoveryClient",
        lambda: SimpleNamespace(generate_report=generate_report),
    )
    monkeypatch.setattr(pipeline, "save_discovery_report", lambda report: report)
    request = DiscoveryRequest(
        holdings=[],
        profile=InvestorProfile(expected_investment_amount=0),
    )

    result = pipeline.run_discovery(request)

    assert calls["capture"] == 1
    assert order[:2] == ["fund_universe", "decision_clock"]
    assert captured["prepared_universe_rows"] == prepared_universe
    assert result.created_at == BEFORE_MIDNIGHT
    assert {
        captured["sector_heat_decision_at"],
        captured["news_decision_at"],
        captured["announcement_decision_at"],
        captured["summary_decision_at"],
        captured["facts_decision_at"],
        captured["client_decision_at"],
        captured["preflight_decision_at"],
        captured["candidate_pool_decision_at"],
        captured["candidate_enrich_decision_at"],
    } == {BEFORE_MIDNIGHT}


def test_stream_analysis_captures_once_and_reuses_the_clock_for_fallback(
    monkeypatch,
) -> None:
    import app.services.analyze_streaming as streaming

    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    calls = {"capture": 0}
    captured: dict = {}

    def fake_capture():
        calls["capture"] += 1
        return clock

    _patch_analysis_stream_pipeline(monkeypatch)
    monkeypatch.setattr(streaming, "capture_decision_clock", fake_capture)

    def preflight(holdings, **kwargs):
        captured["preflight_decision_at"] = kwargs.get("now")
        return SimpleNamespace(holdings=holdings, context={})

    monkeypatch.setattr(streaming, "resolve_portfolio_preflight", preflight)

    monkeypatch.setattr(
        streaming,
        "get_settings",
        lambda: SimpleNamespace(deepseek_configured=False),
    )
    monkeypatch.setattr(
        streaming,
        "resolve_analysis_runtime",
        lambda *_args: AnalysisRuntime(
            mode="fast",
            model="test",
            news_enabled=True,
            news_max_topics=3,
            news_tool_max_rounds=0,
        ),
    )

    def offline_report(*_args, **kwargs):
        captured["decision_at"] = kwargs["decision_at"]
        return _daily_report(kwargs["decision_at"])

    monkeypatch.setattr(streaming, "_offline_report", offline_report)

    events = list(streaming.stream_analysis(_request(), user_id=1))

    assert calls["capture"] == 1
    assert {
        captured["decision_at"],
        captured["preflight_decision_at"],
    } == {
        BEFORE_MIDNIGHT
    }
    assert events[-1]["type"] == "done"
    assert events[-1]["report"]["created_at"] == BEFORE_MIDNIGHT.isoformat()


def test_stream_discovery_captures_once_and_reuses_the_clock_for_fallback(
    monkeypatch,
) -> None:
    import app.services.discovery_streaming as streaming

    clock = capture_decision_clock(BEFORE_MIDNIGHT)
    calls = {"capture": 0}
    captured: dict = {}

    def fake_capture():
        order.append("decision_clock")
        calls["capture"] += 1
        return clock

    _patch_discovery_stream_pipeline(monkeypatch)
    order: list[str] = []
    prepared_universe = [{"fund_code": "000001"}]
    monkeypatch.setattr(
        streaming,
        "fetch_discovery_fund_universe_cached",
        lambda **_kwargs: order.append("fund_universe") or prepared_universe,
    )
    monkeypatch.setattr(streaming, "capture_decision_clock", fake_capture)

    def preflight(holdings, **kwargs):
        captured["preflight_decision_at"] = kwargs.get("now")
        return SimpleNamespace(holdings=holdings, context={})

    monkeypatch.setattr(streaming, "resolve_portfolio_preflight", preflight)

    def build_pool(*_args, **kwargs):
        captured["candidate_pool_decision_at"] = kwargs.get("decision_at")
        captured["prepared_universe_rows"] = kwargs.get("prepared_universe_rows")
        return []

    def enrich_pool(pool, **kwargs):
        captured["candidate_enrich_decision_at"] = kwargs.get("decision_at")
        return pool

    monkeypatch.setattr(streaming, "build_candidate_pool", build_pool)
    monkeypatch.setattr(streaming, "enrich_candidates", enrich_pool)
    monkeypatch.setattr(
        streaming,
        "get_settings",
        lambda: SimpleNamespace(deepseek_configured=False),
    )
    monkeypatch.setattr(
        streaming,
        "resolve_analysis_runtime",
        lambda *_args: AnalysisRuntime(
            mode="fast",
            model="test",
            news_enabled=True,
            news_max_topics=3,
            news_tool_max_rounds=0,
        ),
    )

    def offline_report(**kwargs):
        captured["decision_at"] = kwargs["decision_at"]
        return _discovery_report(kwargs["decision_at"])

    monkeypatch.setattr(streaming, "build_offline_discovery_report", offline_report)

    events = list(streaming.stream_discovery(_stream_discovery_request(), user_id=1))

    assert calls["capture"] == 1
    assert order[:2] == ["fund_universe", "decision_clock"]
    assert captured["prepared_universe_rows"] == prepared_universe
    assert {
        captured["decision_at"],
        captured["preflight_decision_at"],
        captured["candidate_pool_decision_at"],
        captured["candidate_enrich_decision_at"],
    } == {
        BEFORE_MIDNIGHT
    }
    assert events[-1]["type"] == "done"
    assert events[-1]["report"]["created_at"] == BEFORE_MIDNIGHT.isoformat()


def test_candidate_quality_age_uses_explicit_decision_date() -> None:
    candidate = {
        "return_3m_percent": 5.0,
        "return_6m_percent": 8.0,
        "max_drawdown_1y_percent": -10.0,
        "fund_scale_yi": 10.0,
        "established_date": "2025-07-15",
        "fund_manager": "测试经理",
        "nav_date": "2026-07-14",
    }

    before_anniversary = _with_data_quality_gate(
        candidate,
        as_of_date=date(2026, 7, 14),
    )
    on_anniversary = _with_data_quality_gate(
        candidate,
        as_of_date=date(2026, 7, 15),
    )

    assert before_anniversary["quality_gate"]["status"] == "excluded"
    assert on_anniversary["quality_gate"]["status"] == "eligible"
