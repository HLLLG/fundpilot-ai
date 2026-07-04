"""analysis_facts 单次计算 bundle 复用。"""

import time

from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
)


def _minimal_request() -> AnalysisRequest:
    profile = InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
    )
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10000,
        ),
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _minimal_risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )


def test_build_user_payload_reuses_analysis_bundle(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    build_calls = {"count": 0}
    original = __import__(
        "app.services.analysis_facts",
        fromlist=["build_analysis_facts"],
    ).build_analysis_facts

    def _counting_build(*args, **kwargs):
        build_calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        _counting_build,
    )
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )

    bundle = AnalysisFactsBundle(
        session={"session_kind": "trading_day_intraday"},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={"readonly": True, "holdings": [], "portfolio": {}},
    )
    payload = build_user_payload(
        request,
        risk,
        snapshots,
        [],
        analysis_bundle=bundle,
    )
    assert build_calls["count"] == 0
    assert payload["analysis_facts"]["readonly"] is True


def test_finalize_analysis_facts_overlays_pipeline():
    base = {"readonly": True, "holdings": [], "news": {"freshness_label": "old"}}
    pipeline = {"analysis_mode": "deep", "model": "deepdestination"}
    finalized = finalize_analysis_facts(
        base,
        market_news=[],
        topic_briefs=[],
        pipeline=pipeline,
    )
    assert finalized["pipeline"] == pipeline
    assert "news" in finalized


def test_prepare_analysis_bundle_calls_build_once(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    build_calls = {"count": 0}

    def _fake_build(*_args, **_kwargs):
        build_calls["count"] += 1
        return {"readonly": True, "holdings": [], "portfolio": {}}

    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        _fake_build,
    )
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    bundle = prepare_analysis_bundle(request, risk, snapshots, [])
    build_user_payload(request, risk, snapshots, [], analysis_bundle=bundle)
    assert build_calls["count"] == 1


def test_prepare_analysis_bundle_budget_degrades_slow_enhancements(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    monkeypatch.setattr("app.services.analysis_payload.FACTOR_SCORE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_payload.RISK_METRICS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_FLOW_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_INTRADAY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_FLOW_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.GUARD_POLICY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_BREADTH_TIMEOUT_SECONDS", 0.01)

    # 每个 enhancement 的假延迟须明显大于「6 个前置 enhancement 顺序 .result(timeout=0.01)
    # 检查」累积耗时（7 个 enhancement * 0.01s ≈ 0.07s，加上线程创建等系统开销），否则最后
    # 检查的那个 enhancement 可能在真正超时前就已经跑完，导致断言随机失败（新增
    # market_breadth 后从 6 个变 7 个，0.08s 的原余量已不够，提到 0.3s）。
    _SLOW_SLEEP_SECONDS = 0.3

    def slow_factor(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    def slow_risk(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    def slow_signal(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"has_data": True}

    def slow_flow(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"半导体": {"available": True}}

    def slow_intraday(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"半导体": {"pattern_label": "steady_rally"}}

    def slow_market_flow(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    def slow_sector_opportunity(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True, "held": {}, "market_top": []}

    def slow_breadth(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    monkeypatch.setattr("app.services.analysis_payload.build_factor_scores_for_facts", slow_factor)
    monkeypatch.setattr("app.services.analysis_payload.build_risk_metrics_for_facts", slow_risk)
    monkeypatch.setattr("app.services.analysis_facts.build_signal_backtest_context", slow_signal)
    monkeypatch.setattr("app.services.analysis_facts.build_sector_fund_flow_map", slow_flow)
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", slow_intraday)
    monkeypatch.setattr("app.services.analysis_facts.build_market_flow_context", slow_market_flow)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        slow_sector_opportunity,
    )
    monkeypatch.setattr("app.services.analysis_facts.build_market_breadth_signal", slow_breadth)

    start = time.monotonic()
    bundle = prepare_analysis_bundle(
        request,
        risk,
        snapshots,
        [],
        budget_enhancements=True,
    )
    elapsed = time.monotonic() - start

    # 阈值留足余量（CI 并行跑多进程时机器负载高，避免机器慢导致的偶发误报）。
    assert elapsed < 1.2
    assert bundle.factor_scores == {"available": False, "reason": "timeout"}
    assert bundle.risk_metrics == {"available": False, "reason": "timeout"}
    assert bundle.facts["signal_backtest"]["has_data"] is False
    assert bundle.facts["signal_backtest"]["reason"] == "timeout"
    assert bundle.facts["market_flow"]["available"] is False
    assert bundle.facts["market_flow"]["reason"] == "timeout"
    assert bundle.facts["market_breadth"]["available"] is False
    assert bundle.facts["market_breadth"]["reason"] == "timeout"
    holding = bundle.facts["holdings"][0]
    assert holding["sector_intraday"] is None
    assert holding["sector_fund_flow"]["available"] is False
    assert holding["sector_fund_flow"]["reason"] == "timeout"


def test_prepare_analysis_bundle_budget_skips_slow_display_metrics(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    monkeypatch.setattr("app.services.analysis_facts.build_signal_backtest_context", lambda *_args: {})
    monkeypatch.setattr("app.services.analysis_facts.resolve_signal_guard_policy", lambda *_args: {})
    monkeypatch.setattr("app.services.analysis_facts.build_sector_fund_flow_map", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", lambda *_args: {})
    monkeypatch.setattr("app.services.analysis_facts.build_market_flow_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {"available": False, "held": {}, "market_top": []},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_breadth_signal",
        lambda *_args, **_kwargs: {"available": False},
    )

    def slow_display(*_args, **_kwargs):
        time.sleep(0.2)
        return {
            "holding_return_percent_settled": 0,
            "estimated_holding_return_percent": 0,
            "estimated_holding_profit": 0,
            "holding_return_is_estimated": False,
        }

    monkeypatch.setattr("app.services.analysis_facts.build_holding_display_metrics", slow_display)

    start = time.monotonic()
    bundle = prepare_analysis_bundle(
        request,
        risk,
        snapshots,
        [],
        budget_enhancements=True,
    )
    elapsed = time.monotonic() - start

    # 阈值留足余量（CI 并行跑多进程时机器负载高，避免机器慢导致的偶发误报）。
    assert elapsed < 0.5
    holding = bundle.facts["holdings"][0]
    assert holding["estimated_holding_return_percent"] == 0.0


def test_prepare_analysis_bundle_budget_runs_fact_enhancements_in_parallel(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    monkeypatch.setattr("app.services.analysis_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_FLOW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_INTRADAY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_FLOW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.GUARD_POLICY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_BREADTH_TIMEOUT_SECONDS", 0.2)

    def slow_signal(*_args, **_kwargs):
        time.sleep(0.08)
        return {"has_data": False, "summary_lines": ["signal"]}

    def slow_guard(*_args, **_kwargs):
        time.sleep(0.08)
        return {"reason": "guard"}

    def slow_flow(*_args, **_kwargs):
        time.sleep(0.08)
        return {"半导体": {"available": True, "reason": "flow"}}

    def slow_intraday(*_args, **_kwargs):
        time.sleep(0.08)
        return {"半导体": {"pattern_label": "range_bound"}}

    def slow_market_flow(*_args, **_kwargs):
        time.sleep(0.08)
        return {"available": True, "reason": "market"}

    def slow_sector_opportunity(*_args, **_kwargs):
        time.sleep(0.08)
        return {"available": True, "held": {}, "market_top": []}

    def slow_breadth(*_args, **_kwargs):
        time.sleep(0.08)
        return {"available": True, "reason": "breadth"}

    monkeypatch.setattr("app.services.analysis_facts.build_signal_backtest_context", slow_signal)
    monkeypatch.setattr("app.services.analysis_facts.resolve_signal_guard_policy", slow_guard)
    monkeypatch.setattr("app.services.analysis_facts.build_sector_fund_flow_map", slow_flow)
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", slow_intraday)
    monkeypatch.setattr("app.services.analysis_facts.build_market_flow_context", slow_market_flow)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        slow_sector_opportunity,
    )
    monkeypatch.setattr("app.services.analysis_facts.build_market_breadth_signal", slow_breadth)

    start = time.monotonic()
    bundle = prepare_analysis_bundle(
        request,
        risk,
        snapshots,
        [],
        budget_enhancements=True,
    )
    elapsed = time.monotonic() - start

    # 阈值留足余量（CI 并行跑多进程时机器负载高，避免机器慢导致的偶发误报）。
    assert elapsed < 0.7
    assert bundle.facts["market_flow"]["reason"] == "market"
    assert bundle.facts["holdings"][0]["sector_fund_flow"]["reason"] == "flow"
    assert bundle.facts["market_breadth"]["reason"] == "breadth"
