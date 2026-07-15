"""analysis_facts 单次计算 bundle 复用。"""

from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

from app.models import (
    AnalysisRequest,
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
    slim_profile_for_llm,
    trim_analysis_facts_for_llm,
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


def test_slim_profile_always_preserves_style_and_horizon() -> None:
    conservative = _minimal_request().profile
    aggressive = conservative.model_copy(
        update={
            "style": "偏成长",
            "horizon": "3～7天",
            "decision_style": "aggressive",
        }
    )

    for profile in (conservative, aggressive):
        slim = slim_profile_for_llm(profile)
        assert slim["style"] == profile.style
        assert slim["horizon"] == profile.horizon
        assert all(not isinstance(value, (dict, list)) for value in slim.values())


def test_holding_trim_restores_type_and_wraps_management_fee_semantics() -> None:
    trimmed = trim_analysis_facts_for_llm(
        {
            "holdings": [
                {
                    "fund_code": "519674",
                    "fund_type": "混合型-偏股",
                    "management_fee": "1.50%/年",
                    "fund_scale_yi": 88.8,
                }
            ]
        }
    )
    holding = trimmed["holdings"][0]

    assert holding["fund_type"] == "混合型-偏股"
    # A bare scale has no point-in-time provenance and must not reach the model.
    assert "fund_scale_yi" not in holding
    assert "fund_scale_evidence" not in holding
    assert "management_fee" not in holding
    fee = holding["management_fee_annual_recurring"]
    assert fee["annual_rate"] == "1.50%/年"
    assert fee["already_reflected_in_nav"] is True
    assert fee["transaction_fee"] is False
    semantics = trimmed["fund_fact_semantics"]["management_fee_annual_recurring"]
    assert "不是本次申购费或赎回费" in semantics
    assert "不得" in semantics and "再次扣除" in semantics


def test_management_fee_semantics_are_constant_size_not_repeated_per_holding() -> None:
    trimmed = trim_analysis_facts_for_llm(
        {
            "holdings": [
                {
                    "fund_code": f"{index:06d}",
                    "fund_type": "混合型",
                    "management_fee": "1.50%/年",
                }
                for index in range(20)
            ]
        }
    )
    encoded = json.dumps(trimmed, ensure_ascii=False)
    semantic = trimmed["fund_fact_semantics"]["management_fee_annual_recurring"]

    assert encoded.count(semantic) == 1
    assert len(encoded) < 5000


def test_holding_scale_requires_source_as_of_and_freshness_together() -> None:
    base = {
        "fund_code": "519674",
        "fund_scale_yi": 88.8,
        "fund_scale_source": "eastmoney.fund_overview",
        "fund_scale_as_of": "2026-06-30",
    }
    incomplete = trim_analysis_facts_for_llm({"holdings": [base]})["holdings"][0]
    assert "fund_scale_yi" not in incomplete

    fresh = trim_analysis_facts_for_llm(
        {
            "holdings": [
                {
                    **base,
                    "fund_scale_freshness": "fresh",
                    "fund_scale_fetched_at": "2026-07-14T09:30:00+08:00",
                    "fund_scale_basis": "reported_aum",
                }
            ]
        }
    )["holdings"][0]
    assert fresh["fund_scale_yi"] == 88.8
    assert fresh["fund_scale_evidence"] == {
        "source": "eastmoney.fund_overview",
        "as_of": "2026-06-30",
        "freshness": "fresh",
        "decision_eligible": True,
        "fetched_at": "2026-07-14T09:30:00+08:00",
        "basis": "reported_aum",
    }
    assert "fund_scale_source" not in fresh
    assert "fund_scale_as_of" not in fresh

    stale = trim_analysis_facts_for_llm(
        {
            "holdings": [
                {
                    **base,
                    "fund_scale_freshness": "stale",
                }
            ]
        }
    )["holdings"][0]
    assert stale["fund_scale_yi"] == 88.8
    assert stale["fund_scale_evidence"]["decision_eligible"] is False


def test_real_snapshot_scale_provenance_reaches_trimmed_llm_facts(monkeypatch) -> None:
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
    request = _minimal_request()
    snapshot = FundSnapshot(
        fund_code="519674",
        fund_name="银河创新成长",
        source="akshare",
        fund_scale_yi=88.8,
        fund_scale_source="akshare.fund_overview_em",
        fund_scale_as_of="2026-06-30",
    )

    facts = build_analysis_facts(
        request.holdings,
        _minimal_risk(),
        [snapshot],
        request.profile,
        session={
            "calendar_date": "2026-07-14",
            "effective_trade_date": "2026-07-14",
        },
        budget_enhancements=True,
        decision_at=datetime(2026, 7, 14, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    holding = trim_analysis_facts_for_llm(facts)["holdings"][0]

    assert holding["fund_scale_yi"] == 88.8
    assert holding["fund_scale_evidence"] == {
        "source": "akshare.fund_overview_em",
        "as_of": "2026-06-30",
        "freshness": "fresh",
        "decision_eligible": True,
    }


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
        session={
            "session_kind": "trading_day_intraday",
            "calendar_date": "2026-07-14",
        },
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
    assert payload["today"] == "2026-07-14"


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


def test_analysis_payload_reuses_one_decision_at_across_session_facts_and_news(monkeypatch):
    decision_at = datetime(2026, 7, 15, 0, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    captured: dict[str, object] = {}

    def fake_context(*_args, **kwargs):
        captured["context_decision_at"] = kwargs.get("decision_at")
        return (
            {
                "calendar_date": "2026-07-15",
                "effective_trade_date": "2026-07-14",
            },
            None,
            None,
            None,
        )

    def fake_facts(*_args, **kwargs):
        captured["facts_decision_at"] = kwargs.get("decision_at")
        return {"holdings": [], "portfolio": {}}

    monkeypatch.setattr("app.services.analysis_payload._compute_analysis_context", fake_context)
    monkeypatch.setattr("app.services.analysis_payload.build_analysis_facts", fake_facts)
    monkeypatch.setattr(
        "app.services.analysis_payload.attach_analysis_data_evidence",
        lambda facts, **_kwargs: facts,
    )

    request = _minimal_request()
    bundle = prepare_analysis_bundle(
        request,
        _minimal_risk(),
        [],
        [],
        decision_at=decision_at,
    )
    payload = build_user_payload(
        request,
        _minimal_risk(),
        [],
        [],
        analysis_bundle=bundle,
        decision_at=decision_at,
    )
    finalized = finalize_analysis_facts(
        bundle.facts,
        market_news=[
            NewsItem(
                topic="测试",
                title="跨午夜新闻",
                published_at="2026-07-15 00:00:00",
            )
        ],
        decision_at=decision_at,
    )

    assert captured == {
        "context_decision_at": decision_at,
        "facts_decision_at": decision_at,
    }
    assert bundle.session["calendar_date"] == "2026-07-15"
    assert payload["today"] == "2026-07-15"
    assert finalized["news"]["calendar_date"] == "2026-07-15"
    assert finalized["news"]["today_items"] == 1


def test_build_user_payload_today_fallback_uses_supplied_decision_at() -> None:
    decision_at = datetime(2026, 7, 15, 0, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    bundle = AnalysisFactsBundle(
        session={},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={"holdings": [], "portfolio": {}},
    )
    payload = build_user_payload(
        _minimal_request(),
        _minimal_risk(),
        [],
        [],
        analysis_bundle=bundle,
        decision_at=decision_at,
    )
    assert payload["today"] == "2026-07-15"


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


def test_prepare_analysis_bundle_loads_pit_benchmark_before_payload(monkeypatch) -> None:
    decision_at = datetime(2026, 7, 14, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: (
            {"calendar_date": "2026-07-14", "effective_trade_date": "2026-07-14"},
            None,
            None,
            None,
        ),
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        lambda *_args, **_kwargs: {"holdings": [], "portfolio": {}},
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.attach_analysis_data_evidence",
        lambda facts, **_kwargs: facts,
    )

    def resolve_benchmark(codes, *, decision_at):
        captured["codes"] = list(codes)
        captured["decision_at"] = decision_at
        return {
            "519674": {
                "schema_version": "fund_benchmark_mapping.v1",
                "tier": "fund_contract_exact",
                "status": "complete",
                "benchmark_kind": "official_contract",
                "contract_verification_kind": "verified_fund_contract",
                "formal_excess_eligible": True,
                "benchmark_name": "沪深300指数收益率×60%+中证全债指数收益率×40%",
                "raw_contract_text": "一段只用于审计、不应重复进入模型上下文的完整合同原文",
                "components": [
                    {
                        "benchmark_code": "000300",
                        "weight_percent": 60,
                        "source_symbol": "sh000300",
                    },
                    {"benchmark_code": "H11001", "weight_percent": 40},
                ],
            }
        }

    request = _minimal_request()
    bundle = prepare_analysis_bundle(
        request,
        _minimal_risk(),
        [],
        [],
        decision_at=decision_at,
        tradeability_resolver=lambda *_args, **_kwargs: {},
        benchmark_resolver=resolve_benchmark,
        benchmark_research_resolver=lambda rows, **_kwargs: {
            str(row["fund_code"]): {
                "schema_version": "fund_benchmark_research.v1",
                "status": "qualified",
                "qualified": True,
                "descriptive_only": True,
                "execution_tilt_eligible": False,
                "comparison_role": "formal_excess",
                "formal_excess_eligible": True,
                "horizons": {
                    "3m": {"formal_excess_return_percent": 1.25}
                },
            }
            for row in rows
        },
    )
    payload = build_user_payload(
        request,
        _minimal_risk(),
        [],
        [],
        analysis_bundle=bundle,
        decision_at=decision_at,
    )

    assert captured == {"codes": ["519674"], "decision_at": decision_at}
    assert bundle.facts["benchmark_specs"]["519674"]["tier"] == "fund_contract_exact"
    assert bundle.facts["benchmark_contract"] == {
        "schema_version": "fund_benchmark_mapping.v1",
        "lookup_policy": "cached_point_in_time_before_generation",
        "formal_excess_policy": "verified_fund_contract_only",
        "reference_policy": "tracked_index_never_formal",
        "formal_count": 1,
        "reference_count": 0,
        "unavailable_count": 0,
    }
    assert payload["analysis_facts"]["benchmark_specs"]["519674"][
        "formal_excess_eligible"
    ] is True
    compact_spec = payload["analysis_facts"]["benchmark_specs"]["519674"]
    assert "raw_contract_text" not in compact_spec
    assert "source_symbol" not in compact_spec["components"][0]
    assert compact_spec["components"][0]["weight_percent"] == 60
    assert payload["analysis_facts"]["benchmark_research"]["519674"][
        "horizons"
    ]["3m"]["formal_excess_return_percent"] == 1.25
    assert bundle.facts["benchmark_research_contract"]["qualified_count"] == 1


def test_prepare_analysis_bundle_benchmark_failure_is_explicitly_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        lambda *_args, **_kwargs: {"holdings": [], "portfolio": {}},
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.attach_analysis_data_evidence",
        lambda facts, **_kwargs: facts,
    )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("benchmark cache unavailable")

    bundle = prepare_analysis_bundle(
        _minimal_request(),
        _minimal_risk(),
        [],
        [],
        tradeability_resolver=lambda *_args, **_kwargs: {},
        benchmark_resolver=unavailable,
    )

    spec = bundle.facts["benchmark_specs"]["519674"]
    assert spec["tier"] == "unavailable"
    assert spec["formal_excess_eligible"] is False
    assert spec["reason"] == "point_in_time_benchmark_mapping_unavailable"
    assert bundle.facts["benchmark_contract"]["formal_count"] == 0
    assert bundle.facts["benchmark_contract"]["unavailable_count"] == 1
    research = bundle.facts["benchmark_research"]["519674"]
    assert research["status"] == "unavailable"
    assert research["formal_excess_eligible"] is False
    assert bundle.facts["benchmark_research_contract"]["qualified_count"] == 0


def test_daily_bundle_persists_full_lookthrough_but_llm_gets_compact(monkeypatch) -> None:
    decision_at = datetime(2026, 7, 14, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    full = {
        "schema_version": "fund_lookthrough_research.v1",
        "status": "qualified",
        "scope": "portfolio_only",
        "research_qualified": True,
        "execution_qualified": False,
        "portfolio_execution_qualified": True,
        "reason_codes": [],
        "qualification": {
            "research_qualified": True,
            "execution_qualified": False,
            "reason_codes": [],
        },
        "capabilities": {
            "portfolio_lookthrough": {"status": "qualified"},
            "candidate_overlap": {"status": "not_requested"},
        },
        "portfolio": {
            "scope": "whole_account",
            "whole_account_denominator_qualified": True,
            "security_exposure_lower_bounds": [],
            "industry_exposure_lower_bounds": [],
            "listing_market_exposure_lower_bounds": [],
        },
        "existing_funds": [
            {
                "fund_code": "519674",
                "snapshot": {
                    "snapshot_hash": "a" * 64,
                    "holdings": [{"security_code": "600001", "weight_percent": 20}],
                },
            }
        ],
        "candidates": [],
        "resolution_audit": {
            "rows": [{"fund_code": "519674", "snapshot_ref": "a" * 12}],
            "raw_holdings_included": False,
        },
        "research_hash": "b" * 64,
        "raw_snapshots_included": False,
        "raw_holdings_included": False,
    }
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: (
            {"calendar_date": "2026-07-14", "effective_trade_date": "2026-07-14"},
            None,
            None,
            None,
        ),
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        lambda *_args, **_kwargs: {"holdings": [], "portfolio": {}},
    )
    monkeypatch.setattr(
        "app.services.analysis_payload.attach_analysis_data_evidence",
        lambda facts, **_kwargs: facts,
    )

    def build_lookthrough(holdings, candidates, **kwargs):
        captured["codes"] = [item.fund_code for item in holdings]
        captured["candidates"] = candidates
        captured["decision_at"] = kwargs["decision_at"]
        return full

    monkeypatch.setattr(
        "app.services.analysis_payload.build_fund_lookthrough_context",
        build_lookthrough,
    )
    request = _minimal_request()
    bundle = prepare_analysis_bundle(
        request,
        _minimal_risk(),
        [],
        [],
        decision_at=decision_at,
        tradeability_resolver=lambda *_args, **_kwargs: {},
        benchmark_resolver=lambda *_args, **_kwargs: {},
        benchmark_research_resolver=lambda *_args, **_kwargs: {},
    )
    payload = build_user_payload(
        request,
        _minimal_risk(),
        [],
        [],
        analysis_bundle=bundle,
        decision_at=decision_at,
    )

    assert captured == {
        "codes": ["519674"],
        "candidates": [],
        "decision_at": decision_at,
    }
    assert bundle.facts["fund_lookthrough"] is full
    compact = payload["analysis_facts"]["fund_lookthrough"]
    assert compact["scope"] == "portfolio_only"
    assert compact["raw_holdings_included"] is False
    assert "resolution_audit" not in compact
    serialized = json.dumps(compact, ensure_ascii=False)
    assert '"holdings":' not in serialized
    assert "600001" not in serialized
