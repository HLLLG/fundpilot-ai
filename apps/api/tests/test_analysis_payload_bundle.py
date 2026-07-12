"""analysis_facts 单次计算 bundle 复用。"""

import time

from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
)
from app.services.analysis_facts import build_analysis_facts


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


def _stub_non_flow_fact_enhancements(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts._build_sector_intraday_map",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_breadth_signal",
        lambda *_args, **_kwargs: {"available": False},
    )


def test_analysis_facts_reuses_opportunity_flow_map_without_duplicate_fetch(monkeypatch):
    request = _minimal_request()
    flow_row = {"available": True, "reason": "opportunity-context"}
    flow_map = {"半导体": flow_row}
    calls = {"opportunity": 0}

    def opportunity_context(*_args, **kwargs):
        calls["opportunity"] += 1
        assert kwargs["trade_date"] == "2026-07-10"
        return {
            "available": True,
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": flow_map,
        }

    _stub_non_flow_fact_enhancements(monkeypatch)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        opportunity_context,
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.build_sector_fund_flow_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("analysis facts must not fetch sector flow independently")
        ),
    )

    facts = build_analysis_facts(
        request.holdings,
        _minimal_risk(),
        [],
        request.profile,
        session={"effective_trade_date": "2026-07-10"},
    )

    assert calls == {"opportunity": 1}
    assert facts["holdings"][0]["sector_fund_flow"] is flow_row
    assert facts["sector_rotation"] == {
        "available": True,
        "reason": None,
        "market_top": [],
    }
    assert "sector_flow_by_label" not in facts["sector_rotation"]


def test_analysis_facts_opportunity_error_uses_safe_flow_fallback(monkeypatch):
    request = _minimal_request()
    _stub_non_flow_fact_enhancements(monkeypatch)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    facts = build_analysis_facts(
        request.holdings,
        _minimal_risk(),
        [],
        request.profile,
        session={"effective_trade_date": "2026-07-10"},
    )

    assert facts["holdings"][0]["sector_fund_flow"]["available"] is False
    assert facts["holdings"][0]["sector_fund_flow"]["reason"] == "error"
    assert facts["sector_rotation"]["available"] is False
    assert "sector_flow_by_label" not in facts["sector_rotation"]


def test_analysis_facts_empty_opportunity_flow_map_uses_safe_rows(monkeypatch):
    request = _minimal_request()
    _stub_non_flow_fact_enhancements(monkeypatch)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": False,
            "reason": "sector_heat_error",
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": {},
        },
    )

    facts = build_analysis_facts(
        request.holdings,
        _minimal_risk(),
        [],
        request.profile,
        session={"effective_trade_date": "2026-07-10"},
    )

    flow = facts["holdings"][0]["sector_fund_flow"]
    assert flow["available"] is False
    assert flow["reason"] == "sector_heat_error"


def test_analysis_facts_partial_opportunity_flow_map_fills_missing_labels(monkeypatch):
    request = _minimal_request()
    request.holdings.append(
        Holding(
            fund_code="000002",
            fund_name="Second Fund",
            sector_name="other-sector",
            holding_amount=2000,
        )
    )
    real_flow = {"available": True, "today_main_force_net_yi": 1.25}
    first_label = request.holdings[0].sector_name
    _stub_non_flow_fact_enhancements(monkeypatch)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": False,
            "reason": "partial",
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": {first_label: real_flow},
        },
    )

    facts = build_analysis_facts(
        request.holdings,
        _minimal_risk(),
        [],
        request.profile,
        session={"effective_trade_date": "2026-07-10"},
    )

    assert facts["holdings"][0]["sector_fund_flow"] is real_flow
    missing_flow = facts["holdings"][1]["sector_fund_flow"]
    assert missing_flow["available"] is False
    assert missing_flow["reason"] == "partial"
    assert "sector_flow_by_label" not in facts["sector_rotation"]


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
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_INTRADAY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "app.services.analysis_facts.STOCK_CONNECT_FLOW_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr("app.services.analysis_facts.GUARD_POLICY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_BREADTH_TIMEOUT_SECONDS", 0.01)

    # 每个 enhancement 的假延迟须明显大于「5 个前置 enhancement 顺序 .result(timeout=0.01)
    # 检查」累积耗时（6 个 enhancement * 0.01s ≈ 0.06s，加上线程创建等系统开销），否则最后
    # 检查的那个 enhancement 可能在真正超时前就已经跑完，导致断言随机失败。
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

    def slow_intraday(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"半导体": {"pattern_label": "steady_rally"}}

    def slow_stock_connect_flow(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    def slow_sector_opportunity(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {
            "available": True,
            "held": {},
            "market_top": [],
            "sector_flow_by_label": {"半导体": {"available": True}},
        }

    def slow_breadth(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {"available": True}

    monkeypatch.setattr("app.services.analysis_payload.build_factor_scores_for_facts", slow_factor)
    monkeypatch.setattr("app.services.analysis_payload.build_risk_metrics_for_facts", slow_risk)
    monkeypatch.setattr("app.services.analysis_facts.build_signal_backtest_context", slow_signal)
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", slow_intraday)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        slow_stock_connect_flow,
    )
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
    assert bundle.facts["stock_connect_flow"]["available"] is False
    assert bundle.facts["stock_connect_flow"]["reason"] == "timeout"
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
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", lambda *_args: {})
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        lambda *_args, **_kwargs: {},
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
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_INTRADAY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.STOCK_CONNECT_FLOW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.GUARD_POLICY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.analysis_facts.MARKET_BREADTH_TIMEOUT_SECONDS", 0.2)

    def slow_signal(*_args, **_kwargs):
        time.sleep(0.08)
        return {"has_data": False, "summary_lines": ["signal"]}

    def slow_guard(*_args, **_kwargs):
        time.sleep(0.08)
        return {"reason": "guard"}

    def slow_intraday(*_args, **_kwargs):
        time.sleep(0.08)
        return {"半导体": {"pattern_label": "range_bound"}}

    def slow_stock_connect_flow(*_args, **_kwargs):
        time.sleep(0.08)
        return {"available": True, "reason": "market"}

    def slow_sector_opportunity(*_args, **_kwargs):
        time.sleep(0.08)
        return {
            "available": True,
            "held": {},
            "market_top": [],
            "sector_flow_by_label": {
                "半导体": {"available": True, "reason": "flow"}
            },
        }

    def slow_breadth(*_args, **_kwargs):
        time.sleep(0.08)
        return {"available": True, "reason": "breadth"}

    monkeypatch.setattr("app.services.analysis_facts.build_signal_backtest_context", slow_signal)
    monkeypatch.setattr("app.services.analysis_facts.resolve_signal_guard_policy", slow_guard)
    monkeypatch.setattr("app.services.analysis_facts._build_sector_intraday_map", slow_intraday)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_stock_connect_flow_context",
        slow_stock_connect_flow,
    )
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
    assert bundle.facts["stock_connect_flow"]["reason"] == "market"
    assert bundle.facts["holdings"][0]["sector_fund_flow"]["reason"] == "flow"
    assert bundle.facts["market_breadth"]["reason"] == "breadth"
