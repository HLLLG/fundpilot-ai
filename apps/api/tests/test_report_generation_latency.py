"""报告生成耗时：prompt 体积、不可能成功的二次审校、净值拉取窗口。"""

from __future__ import annotations

from threading import Event
from types import SimpleNamespace

from app.models import AnalysisRequest, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import (
    OUTPUT_REQUIREMENTS_SYSTEM,
    OUTPUT_REQUIREMENTS_USER,
    compact_news_titles,
    compact_topic_briefs,
    trim_analysis_facts_for_llm,
)
from app.services.analysis_runtime import AnalysisRuntime
from app.services.discovery_judge import judge_parsed_discovery_report
from app.services.fund_data import _map_holdings_concurrently
from app.services.report_judge import judge_parsed_report


def _deep_runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        mode="deep",
        model="deepseek-v4-pro",
        news_enabled=True,
        news_max_topics=5,
        news_tool_max_rounds=0,
    )


def test_trim_drops_duplicate_instruction_and_pipeline_from_prompt() -> None:
    trimmed = trim_analysis_facts_for_llm(
        {
            "instruction": "与系统提示重复的长说明" * 50,
            "pipeline": {
                "prompt_contract": {"effective_system_prompt_snapshot": "x" * 4000}
            },
            "holdings": [{"fund_code": "519674"}],
        },
        analysis_mode="deep",
    )

    assert "instruction" not in trimmed
    assert "pipeline" not in trimmed
    assert trimmed["holdings"][0]["fund_code"] == "519674"


def test_trim_drops_descriptive_research_but_keeps_action_rationale() -> None:
    """落库仍挂描述性证据；喂模型时只留守卫会用、模型必须讲对的数字。"""
    import json

    fat_market_top = {
        "sector_label": "半导体",
        "track": "momentum",
        "score": 86.2,
        "confidence": "高",
        "entry_state": "ready_to_start",
        "today_main_force_net_yi": 12.4,
        "sector_group": "成长",
        "history": [{"i": i, "payload": "x" * 80} for i in range(40)],
    }
    facts = {
        "instruction": "archive only",
        "pipeline": {"prompt_contract": {"snapshot": "x" * 200}},
        "benchmark_specs": {"519674": {"components": [{"name": "中证全指" + "详" * 40}]}},
        "benchmark_research": {"519674": {"fund_series": {"points": list(range(300))}}},
        "benchmark_research_contract": {"qualified_count": 1},
        "transaction_behavior_review": {"notes": ["长文"] * 20},
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": [
                {"fact_id": f"holdings.{i}", "source": "nav", "payload": "y" * 120}
                for i in range(40)
            ],
        },
        "factor_scores": {
            "ic_status": {"state": "stale", "as_of": "2024-01-01", "detail": "z" * 200},
            "funds": [{"fund_code": "519674", "percentiles": list(range(30))}],
        },
        "sector_rotation": {"available": True, "market_top": [fat_market_top]},
        "fund_lookthrough": {
            "status": "unavailable",
            "research_qualified": False,
            "reason_codes": ["lookthrough_context_timeout"],
            "portfolio": {"top_security_exposure_lower_bounds": [{"security_key": "600519"}]},
        },
        "daily_action_proposal": {
            "mode": "enforced",
            "by_fund": [
                {
                    "fund_code": "519674",
                    "action": "观察",
                    "reason_codes": ["entry_not_ready"],
                    "supports_add": False,
                    "debug": {"trace": "w" * 80},
                }
            ],
        },
        "holdings": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "peer_research": {
                    "status": "descriptive_only",
                    "execution_tilt_eligible": False,
                    "metrics": {f"m{i}": {"percentile": i} for i in range(20)},
                },
                "tradeability": {"raw_profile": "t" * 400, "add_status": "blocked"},
                "signal_backtest": {"rules": [{"id": i, "series": list(range(50))} for i in range(8)]},
                "benchmark_metrics": {"status": "qualified", "tracking_metrics": {"error": 0.9}},
                "lot_maturity": {
                    "available": True,
                    "short_hold_share_percent": 12.0,
                    "lots": [{"lot_id": i, "shares": 100} for i in range(15)],
                },
                "evidence": {
                    "schema_version": "v1",
                    "composite": {"level": "低"},
                    "components": [{"name": "factor", "tree": "e" * 200} for _ in range(6)],
                },
                "sector_opportunity": {
                    "track": "momentum",
                    "confidence": "高",
                    "opportunity_available": True,
                    "entry_state": "ready_to_start",
                    "first_tranche_scale": 0.6,
                    "entry_reason": "方向已确认",
                    "sector_group": "成长",
                    "verbose_history": [{"i": i} for i in range(20)],
                },
                "sector_fund_flow": {
                    "date_aligned": True,
                    "pattern_label": "inflow_with_rise",
                    "pattern_hint": "资金与价格同向",
                    "flow_tiers": {"super_large_net_yi": 1.2},
                    "history_points": [{"d": i} for i in range(60)],
                },
                "flow_divergence_backtest": {
                    "resolved": True,
                    "by_rule": {
                        "noise": {"rule_id": "noise", "significant": False, "series": list(range(20))},
                        "edge": {
                            "rule_id": "edge",
                            "label": "价涨量缩",
                            "trigger_count": 12,
                            "hit_rate_percent": 61.0,
                            "edge_percent": 8.0,
                            "significant": True,
                        },
                    },
                },
            }
        ],
    }

    raw_size = len(json.dumps(facts, ensure_ascii=False))
    trimmed = trim_analysis_facts_for_llm(facts, analysis_mode="deep")
    holding = trimmed["holdings"][0]

    assert "peer_research" not in holding
    assert "tradeability" not in holding
    assert "signal_backtest" not in holding
    assert "benchmark_metrics" not in holding
    assert "lots" not in holding.get("lot_maturity", {})
    assert holding["evidence"] == {"composite_level": "低", "schema_version": "v1"}
    assert holding["sector_opportunity"]["entry_state"] == "ready_to_start"
    assert holding["sector_opportunity"]["first_tranche_scale"] == 0.6
    assert "verbose_history" not in holding["sector_opportunity"]
    assert holding["sector_fund_flow"]["date_aligned"] is True
    assert "history_points" not in holding["sector_fund_flow"]
    assert holding["flow_divergence_backtest"]["significant"] is True
    assert holding["flow_divergence_backtest"]["by_rule"] == [
        {
            "rule_id": "edge",
            "label": "价涨量缩",
            "trigger_count": 12,
            "hit_rate_percent": 61.0,
            "edge_percent": 8.0,
            "significant": True,
        }
    ]
    assert "items" not in trimmed["data_evidence"]
    assert trimmed["data_evidence"]["decision_ready"] is True
    assert trimmed["factor_scores"] == {"ic_status": {"state": "stale"}}
    assert trimmed["sector_rotation"]["market_top"][0]["entry_state"] == "ready_to_start"
    assert "history" not in trimmed["sector_rotation"]["market_top"][0]
    assert trimmed["fund_lookthrough"] == {
        "status": "unavailable",
        "research_eligible": False,
        "execution_qualified": False,
        "reason_codes": ["lookthrough_context_timeout"],
    }
    assert trimmed["daily_action_proposal"]["by_fund"][0]["action"] == "观察"
    assert "debug" not in trimmed["daily_action_proposal"]["by_fund"][0]
    assert "benchmark_specs" not in trimmed
    assert "benchmark_research" not in trimmed
    assert "transaction_behavior_review" not in trimmed

    trimmed_size = len(json.dumps(trimmed, ensure_ascii=False))
    assert trimmed_size < raw_size * 0.35
    assert trimmed_size < 8_000


def test_llm_judge_skips_when_outer_budget_cannot_beat_first_byte(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.report_judge.get_settings",
        lambda: SimpleNamespace(
            deepseek_configured=True,
            decision_escalation_mode="enforced",
            deepseek_first_byte_timeout_seconds=60.0,
        ),
    )
    called = {"judge": False}

    def _should_not_run(*_args, **_kwargs):
        called["judge"] = True
        raise AssertionError("LLM judge must not be scheduled")

    monkeypatch.setattr(
        "app.services.report_judge._llm_judge_with_budget",
        _should_not_run,
    )

    parsed, meta = judge_parsed_report(
        {
            "title": "日报",
            "summary": "观察",
            "fund_recommendations": [
                {
                    "fund_code": "519674",
                    "fund_name": "银河创新成长A",
                    "action": "观察",
                }
            ],
        },
        AnalysisRequest(
            holdings=[
                Holding(
                    fund_code="519674",
                    fund_name="银河创新成长A",
                    holding_amount=1000,
                )
            ],
            profile=InvestorProfile(),
        ),
        RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=0.0,
            alerts=[],
        ),
        [],
        _deep_runtime(),
        facts={"holdings": [{"fund_code": "519674"}], "allowed_actions": ["观察"]},
    )

    assert called["judge"] is False
    assert meta["llm_judge_attempted"] is False
    assert meta["llm_judge_skipped_reason"] == "timeout_below_provider_first_byte"
    assert parsed["fund_recommendations"][0]["action"] == "观察"


def test_discovery_judge_skips_when_outer_budget_cannot_beat_first_byte(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.discovery_judge.get_settings",
        lambda: SimpleNamespace(
            deepseek_configured=True,
            decision_escalation_mode="enforced",
            deepseek_first_byte_timeout_seconds=60.0,
        ),
    )
    monkeypatch.setattr(
        "app.services.discovery_judge._llm_judge_with_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("LLM judge must not be scheduled")
        ),
    )

    parsed, meta = judge_parsed_discovery_report(
        {"recommendations": [{"fund_code": "000001", "action": "建议关注"}]},
        candidate_pool=[{"fund_code": "000001"}],
        discovery_facts={},
        analysis_mode="deep",
    )

    assert meta["llm_judge_attempted"] is False
    assert meta["llm_judge_skipped_reason"] == "timeout_below_provider_first_byte"
    assert parsed["recommendations"][0]["fund_code"] == "000001"


def test_system_prompt_stays_stable_across_session_minutes() -> None:
    """分钟时钟只能出现在 user facts，否则会砸掉 system 前缀缓存。"""
    from app.services.deepseek_client import _system_prompt

    early = _system_prompt(
        True, session={"local_datetime": "2026-08-19 17:01", "session_kind": "after_close"}
    )
    late = _system_prompt(
        True, session={"local_datetime": "2026-08-19 17:59", "session_kind": "after_close"}
    )

    assert early == late
    assert "当前分析时点约为" not in early


def test_map_holdings_releases_a_slot_before_the_slowest_item_finishes() -> None:
    """9 只、上限 8：第 9 只必须在第 1 只仍阻塞时启动，而不是等整波结束。"""
    ninth_started = Event()

    def worker(item: int) -> str:
        if item == 0:
            assert ninth_started.wait(timeout=2.0), "ninth worker never started"
            return "slow"
        if item == 8:
            ninth_started.set()
            return "ninth"
        return f"fast-{item}"

    results = _map_holdings_concurrently(list(range(9)), worker)
    assert results[0] == "slow"
    assert results[8] == "ninth"
    assert results[1] == "fast-1"


def test_daily_output_requirements_stop_asking_for_redundant_evidence_fields() -> None:
    joined_system = OUTPUT_REQUIREMENTS_SYSTEM
    joined_user = "\n".join(OUTPUT_REQUIREMENTS_USER)

    assert "不要输出 decision_path" in joined_system
    assert "points（2-3 条" in joined_system
    assert "暂无明确利好" in joined_system
    assert "禁止写「暂无明确利好」" in joined_system
    assert "decision_path 为 1 句话" not in joined_system
    assert "每只基金须含 confidence、decision_path" not in joined_user
    assert "不要输出 decision_path/sector_evidence/fund_evidence/validation_notes" in joined_user


def test_daily_news_titles_drop_fund_announcements() -> None:
    from app.models import NewsItem, TopicBrief, TopicBriefPoint

    titles = compact_news_titles(
        [
            NewsItem(
                topic="上证指数",
                title="上证指数跌2%",
                source="第一财经",
                is_today=True,
            ),
            NewsItem(
                topic="015788",
                title="鹏扬中证数字经济主题交易型开放式指数证券投资基金发起式联接基金2026年第2季度报告",
                source="fund-announcement",
                is_today=False,
            ),
        ],
        [
            TopicBrief(
                topic="015788",
                summary="季报",
                points=[
                    TopicBriefPoint(
                        headline="季报",
                        source_titles=["鹏扬中证数字经济主题交易型开放式指数证券投资基金发起式联接基金2026年第2季度报告"],
                    )
                ],
            )
        ],
        include_announcements=False,
    )

    assert [row["title"] for row in titles] == ["上证指数跌2%"]
    briefs = compact_topic_briefs(
        [
            TopicBrief(topic="上证指数", summary="指数下跌", news_count=1),
            TopicBrief(topic="015788", summary="季报", news_count=3),
        ],
        exclude_fund_code_topics=True,
    )
    assert [row["topic"] for row in briefs] == ["上证指数"]
