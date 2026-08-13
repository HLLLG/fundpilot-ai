"""诊断摘要的数据来源契约。

回归背景（这是一个静默失效了很久的真实缺陷）：`shadow_escalation_digest` 原本走
`list_reports()` / `list_discovery_reports()` 取历史记录，但这两个函数按
`_REPORT_SUMMARY_FIELDS` / `_DISCOVERY_SUMMARY_FIELDS` 投影，而 `analysis_facts`、
`discovery_facts`、`candidate_pool` 全部被**刻意**排除（体积原因，`database.py` 的注释
写明了）。于是 `report.get("analysis_facts")` 恒为 `{}`，`trigger_count` 恒为 0——摘要
一直在如实汇报"未触发任何灰度升级判定"，而它根本读不到判定结果。

这个缺陷能活下来是因为原有测试全部走 `reports=[...]` 注入参数（"便于离线测试"），
真实读路径从未被执行过；那些测试后来在 2026-08-05 的测试减半中被删掉，只剩下
`__pycache__` 里的 .pyc。所以本文件的用例**一律不注入**，全部走真实落库 → 真实读取。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.database import (
    _project_discovery_report_diagnostics,
    list_discovery_report_decision_diagnostics,
    list_discovery_reports,
    list_report_decision_diagnostics,
    list_reports,
    save_discovery_report,
    save_report,
)
from app.models import FundDiscoveryReport, Holding, Report, RiskAssessment
from app.services.decision_score_shadow import (
    build_decision_score_shadow,
    build_decision_score_shadow_digest,
)
from app.services.llm_judge_digest import build_llm_judge_digest
from app.services.shadow_escalation_digest import build_shadow_escalation_digest


def _escalation() -> dict:
    """`resolve_escalation_floor` 命中时的形状：min_bucket 非空即视为触发。"""
    return {
        "min_bucket": 0,
        "min_action_label": "减仓评估",
        "reasons": ["板块资金持续流出", "组合已超集中度上限"],
        "basis": "板块方向不成立且量化证据转负",
    }


def _report(
    *,
    escalation: dict | None = None,
    pipeline: dict | None = None,
    created_at: datetime | None = None,
) -> Report:
    row: dict = {
        "fund_code": "519674",
        "sector_name": "半导体",
        "estimated_daily_return_percent": -1.8,
    }
    if escalation is not None:
        row["escalation"] = escalation
    return Report(
        created_at=created_at or datetime.now(timezone.utc),
        title="测试日报",
        risk=RiskAssessment(
            level="medium",
            weighted_return_percent=-2.0,
            suggested_action="watch",
            alerts=[],
        ),
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            )
        ],
        summary="摘要",
        recommendations=["建议"],
        caveats=["以上不构成投资建议。"],
        analysis_facts={
            "pipeline": pipeline if pipeline is not None else {},
            "holdings": [row],
        },
    )


# --------------------------------------------------------------------------- #
# shadow escalation digest
# --------------------------------------------------------------------------- #


def test_list_projection_does_not_carry_analysis_facts() -> None:
    """先钉住前提：列表投影不含 analysis_facts，所以摘要不能走那条路。

    如果哪天有人把 analysis_facts 加进 `_REPORT_SUMMARY_FIELDS`，这条会失败，
    提醒改动者先想清楚 /api/reports 的响应体积（数十份 × 27 KB）。
    """
    save_report(_report(escalation=_escalation()))

    summaries = list_reports()

    assert summaries, "至少应有一份报告"
    assert all("analysis_facts" not in summary for summary in summaries)


def test_digest_finds_a_trigger_through_the_real_read_path() -> None:
    """不注入任何数据：落库一份带 escalation 的日报，摘要必须真的数出来。

    修复前这条恒失败（trigger_count 永远是 0）。
    """
    save_report(_report(escalation=_escalation()))

    digest = build_shadow_escalation_digest(lookback_days=7)

    assert digest["trigger_count"] == 1
    assert digest["by_sector"] == {"半导体": 1}
    assert digest["by_would_be_action"] == {"减仓评估": 1}
    # 触发当日该持仓估算下跌 → 计入"当日走势偏弱"的粗粒度对照。
    assert digest["outcomes"]["verified_count"] == 1
    assert digest["outcomes"]["aligned_count"] == 1
    assert "未触发任何灰度升级判定" not in digest["summary"]


def test_holdings_without_escalation_are_not_counted_as_triggers() -> None:
    save_report(_report(escalation=None))

    digest = build_shadow_escalation_digest(lookback_days=7)

    assert digest["report_count"] == 1
    assert digest["trigger_count"] == 0
    assert "未触发任何灰度升级判定" in digest["summary"]


def test_reports_outside_the_window_are_excluded() -> None:
    save_report(
        _report(
            escalation=_escalation(),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
    )

    digest = build_shadow_escalation_digest(lookback_days=7)

    assert digest["report_count"] == 0
    assert digest["trigger_count"] == 0


def test_diagnostic_slice_keeps_only_what_the_digests_need() -> None:
    """切片必须小：诊断是低频访问，但不该顺手把整份 payload 拖进内存。"""
    save_report(_report(escalation=_escalation(), pipeline={"analysis_mode": "deep"}))

    slices = list_report_decision_diagnostics(limit=10)

    assert len(slices) == 1
    row = slices[0]["analysis_facts"]["holdings"][0]
    assert set(row) == {
        "fund_code",
        "sector_name",
        "escalation",
        "estimated_daily_return_percent",
    }
    # 持仓金额、基准、穿透等大字段不该出现在诊断切片里。
    assert "benchmark_metrics" not in row
    assert "fund_lookthrough" not in slices[0]["analysis_facts"]


def test_discovery_diagnostic_projection_keeps_hints_and_sector_labels() -> None:
    """荐基侧同理：escalation_hints 与候选池的板块标签列必须留下，其余不要。"""
    projected = _project_discovery_report_diagnostics(
        {
            "analysis_mode": "deep",
            "discovery_facts": {
                "escalation_hints": {"519212": {"action": "boost", "reasons": ["证据共振"]}},
                "decision_escalation_mode": "shadow",
                "pipeline": {"analysis_mode": "deep", "llm_judge_attempted": False},
                # 这一份在生产里约 2.7 MB，绝不能进诊断切片。
                "sector_opportunities": [{"sector_label": "白酒"}] * 50,
            },
            "candidate_pool": [
                {"fund_code": "519212", "sector_label": "白酒", "quality_gate": {"eligible": True}},
            ],
        }
    )

    facts = projected["discovery_facts"]
    assert facts["escalation_hints"]["519212"]["action"] == "boost"
    assert facts["decision_escalation_mode"] == "shadow"
    assert "sector_opportunities" not in facts
    assert projected["candidate_pool"] == [
        {"fund_code": "519212", "sector_label": "白酒"}
    ]


# --------------------------------------------------------------------------- #
# decision score shadow digest —— 与上面同源的第三个消费者，当时被漏掉了
# --------------------------------------------------------------------------- #


def _shadow_artifact() -> dict:
    return build_decision_score_shadow(
        [{"fund_code": "519212", "quality_gate": {"status": "eligible"}}],
        candidate_factor_scores=None,
        portfolio_gap=None,
        profile=None,
        decision_at=datetime.now(timezone.utc),
    )


def _discovery_report_with_shadow() -> FundDiscoveryReport:
    return FundDiscoveryReport(
        title="测试荐基",
        discovery_facts={
            "decision_score_shadow": _shadow_artifact(),
            "pipeline": {"analysis_mode": "deep"},
        },
        candidate_pool=[{"fund_code": "519212", "sector_label": "白酒"}],
    )


def test_discovery_diagnostic_projection_keeps_the_whole_shadow_artifact() -> None:
    """整份保留，含 rows：validate_* 要逐行复核 row_hash，缺 rows 会判成无效。"""
    projected = _project_discovery_report_diagnostics(
        {
            "discovery_facts": {
                "decision_score_shadow": {
                    "schema_version": "decision_score_shadow.v3",
                    "model_version": "decision_score.v3",
                    "rows": [{"fund_code": "519212", "row_hash": "deadbeef"}],
                },
                "sector_opportunities": [{"sector_label": "白酒"}] * 50,
            },
        }
    )

    facts = projected["discovery_facts"]
    assert facts["decision_score_shadow"]["rows"] == [
        {"fund_code": "519212", "row_hash": "deadbeef"}
    ]
    # 体积大户照旧不许进来。
    assert "sector_opportunities" not in facts


def test_decision_score_digest_sees_artifacts_through_the_real_read_path() -> None:
    """不注入：落库一份带制品的荐基报告，digest 必须真的数出来。"""
    save_discovery_report(_discovery_report_with_shadow())

    digest = build_decision_score_shadow_digest(
        list_discovery_report_decision_diagnostics(limit=10)
    )

    assert digest["report_count"] == 1
    assert digest["total_artifact_count"] == 1
    assert digest["artifact_count"] == 1
    assert digest["latest"] is not None


def test_old_data_source_would_have_reported_zero_decision_score_artifacts() -> None:
    """反证：同一份数据用列表投影喂进去，制品数恒为 0，而报告数两边都是 1。

    修复前 evidence-maturity 的 decision_score_shadow 就是这样恒为 0 的，面板却把
    这个「读不到」显示成「还在积累」，并建议用户多生成报告——那个建议不可能奏效。
    """
    save_discovery_report(_discovery_report_with_shadow())

    via_projection = build_decision_score_shadow_digest(list_discovery_reports())
    via_diagnostics = build_decision_score_shadow_digest(
        list_discovery_report_decision_diagnostics(limit=10)
    )

    assert via_projection["report_count"] == 1
    assert via_projection["total_artifact_count"] == 0

    assert via_diagnostics["report_count"] == 1
    assert via_diagnostics["total_artifact_count"] == 1


# --------------------------------------------------------------------------- #
# llm judge digest
# --------------------------------------------------------------------------- #


def test_fast_mode_reports_are_not_counted_as_judge_eligible() -> None:
    """分母必须是有资格的报告：fast 模式压根不调用审校。

    把 fast 报告算进分母会让"0 次尝试"看起来像坏了，其实是不该跑。
    """
    save_report(_report(pipeline={"analysis_mode": "fast"}))

    digest = build_llm_judge_digest(lookback_days=7)

    assert digest["report"]["report_count"] == 1
    assert digest["report"]["judge_eligible_count"] == 0
    assert digest["combined"]["health"] == "no_eligible_reports"
    assert digest["combined"]["attempt_rate_percent"] is None


def test_always_timing_out_is_reported_as_degraded_not_healthy() -> None:
    """审校每次都超时降级时，结果看起来正常（确定性 guard 兜底），但钱白花了。"""
    for _ in range(3):
        save_report(
            _report(
                pipeline={
                    "analysis_mode": "deep",
                    "llm_judge_attempted": True,
                    "llm_judge_applied": False,
                    "llm_judge_timeout": True,
                }
            )
        )

    digest = build_llm_judge_digest(lookback_days=7)
    surface = digest["report"]

    assert surface["judge_eligible_count"] == 3
    assert surface["attempted_count"] == 3
    assert surface["timeout_count"] == 3
    assert surface["timeout_rate_percent"] == 100.0
    assert surface["applied_rate_percent"] == 0.0
    assert digest["combined"]["health"] == "degraded_always_timeout"
    assert "白付" in digest["summary"]


def test_shadow_skip_reason_is_aggregated_rather_than_looking_like_a_failure() -> None:
    save_report(
        _report(
            pipeline={
                "analysis_mode": "deep",
                "llm_judge_attempted": False,
                "llm_judge_skipped_reason": "decision_escalation_shadow",
            }
        )
    )

    digest = build_llm_judge_digest(lookback_days=7)

    assert digest["report"]["judge_eligible_count"] == 1
    assert digest["report"]["attempted_count"] == 0
    assert digest["report"]["skipped_reasons"] == {"decision_escalation_shadow": 1}
    assert digest["combined"]["health"] == "never_attempted"


def test_healthy_run_reports_applied_rate() -> None:
    save_report(
        _report(
            pipeline={
                "analysis_mode": "deep",
                "llm_judge_attempted": True,
                "llm_judge_applied": True,
                "llm_judge_timeout": False,
            }
        )
    )

    digest = build_llm_judge_digest(lookback_days=7)

    assert digest["report"]["applied_count"] == 1
    assert digest["report"]["applied_rate_percent"] == 100.0
    assert digest["combined"]["health"] == "healthy"


def test_pre_telemetry_reports_are_counted_separately_not_as_no_timeout() -> None:
    """`llm_judge_timeout` 落库之前生成的老报告，不能被读成"没超时"。"""
    save_report(
        _report(
            pipeline={
                "analysis_mode": "deep",
                "llm_judge_attempted": True,
                "llm_judge_applied": False,
            }
        )
    )

    digest = build_llm_judge_digest(lookback_days=7)

    assert digest["report"]["reports_without_judge_telemetry"] == 1
    assert digest["report"]["timeout_count"] == 0


@pytest.mark.parametrize("mode", ["shadow", "enforced"])
def test_digest_always_reports_the_mode_that_gates_the_judge(mode: str) -> None:
    """0 次尝试在 shadow 下是预期行为，摘要必须自带这个上下文才可解读。"""
    from app.config import refresh_settings

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FUND_AI_DECISION_ESCALATION_MODE", mode)
        refresh_settings()
        try:
            digest = build_llm_judge_digest(lookback_days=7)
        finally:
            pass
    refresh_settings()

    assert digest["decision_escalation_mode"] == mode
    if mode == "shadow":
        assert "发请求前即短路" in digest["summary"]
    else:
        assert "发请求前即短路" not in digest["summary"]


def test_old_data_source_would_have_reported_zero_triggers() -> None:
    """反证修复的必要性：把同一份数据用旧数据源（列表投影）喂进去，摘要数不出任何触发。

    这条用例是这次修复的核心证据——`report_count` 两边都是 1，只有 `trigger_count`
    一边 0 一边 1，说明差别不在"有没有报告"而在"读不读得到判定结果"。
    """
    save_report(_report(escalation=_escalation()))

    via_projection = build_shadow_escalation_digest(
        lookback_days=7,
        reports=list_reports(),
        discovery_reports=[],
    )
    via_diagnostics = build_shadow_escalation_digest(lookback_days=7)

    assert via_projection["report_count"] == 1
    assert via_projection["trigger_count"] == 0

    assert via_diagnostics["report_count"] == 1
    assert via_diagnostics["trigger_count"] == 1


def test_both_diagnostic_endpoints_respond(client) -> None:
    """端点冒烟：两份摘要都必须可读，且自带解读所需的模式上下文。"""
    escalation = client.get("/api/diagnostics/shadow-escalation-digest?days=7")
    judge = client.get("/api/diagnostics/llm-judge-digest?days=7")

    assert escalation.status_code == 200
    assert escalation.json()["available"] is True
    assert "escalation_mode" in escalation.json()

    payload = judge.json()
    assert judge.status_code == 200
    assert payload["available"] is True
    assert payload["schema_version"] == "llm_judge_digest.v1"
    assert "decision_escalation_mode" in payload
    assert "deepseek_configured" in payload
    assert payload["combined"]["health"] in {
        "no_eligible_reports",
        "never_attempted",
        "degraded_always_timeout",
        "degraded_frequent_timeout",
        "healthy",
    }


def test_days_parameter_is_clamped_to_a_sane_window(client) -> None:
    assert client.get("/api/diagnostics/llm-judge-digest?days=0").json()["lookback_days"] == 1
    assert client.get("/api/diagnostics/llm-judge-digest?days=999").json()["lookback_days"] == 30
