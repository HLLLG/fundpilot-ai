"""M6.3：灰度复盘摘要（shadow_escalation_digest.py）单测。

覆盖：无触发时的空摘要、日报侧 holdings[].escalation 结构化字段读取、荐基侧
discovery_facts.escalation_hints 结构化字段读取、按板块/动作聚合、次日走势对照
（当日估算涨跌近似）、7 天窗口过滤。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.shadow_escalation_digest import build_shadow_escalation_digest


def _iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _report_with_escalation(
    *,
    fund_code: str = "519674",
    sector: str = "半导体",
    min_bucket: int = 2,
    min_action_label: str = "暂停追涨",
    reasons: list[str] | None = None,
    daily_return: float | None = -1.5,
    created_days_ago: int = 1,
    mode: str = "shadow",
) -> dict:
    return {
        "id": f"report-{fund_code}",
        "created_at": _iso(created_days_ago),
        "analysis_facts": {
            "pipeline": {"decision_escalation_mode": mode},
            "holdings": [
                {
                    "fund_code": fund_code,
                    "sector_name": sector,
                    "estimated_daily_return_percent": daily_return,
                    "escalation": {
                        "min_bucket": min_bucket,
                        "min_action_label": min_action_label,
                        "reasons": reasons or ["量价背离信号显著"],
                        "basis": "量价背离信号显著",
                    },
                }
            ],
        },
    }


def _report_without_escalation(*, created_days_ago: int = 1) -> dict:
    return {
        "id": "report-clean",
        "created_at": _iso(created_days_ago),
        "analysis_facts": {
            "pipeline": {"decision_escalation_mode": "shadow"},
            "holdings": [
                {
                    "fund_code": "008586",
                    "sector_name": "AI",
                    "escalation": {"min_bucket": None},
                }
            ],
        },
    }


def _discovery_report_with_escalation(
    *,
    fund_code: str = "020357",
    sector: str = "半导体材料",
    action: str = "exclude",
    created_days_ago: int = 1,
) -> dict:
    return {
        "id": f"discovery-{fund_code}",
        "created_at": _iso(created_days_ago),
        "candidate_pool": [{"fund_code": fund_code, "sector_label": sector}],
        "discovery_facts": {
            "decision_escalation_mode": "shadow",
            "escalation_hints": {
                fund_code: {
                    "action": action,
                    "reasons": ["量价背离信号显著，板块方向不构成机会"],
                    "basis": "量价背离信号显著",
                }
            },
        },
    }


def test_empty_when_no_reports_at_all():
    result = build_shadow_escalation_digest(reports=[], discovery_reports=[])
    assert result["available"] is True
    assert result["trigger_count"] == 0
    assert result["by_sector"] == {}


def test_response_includes_current_escalation_mode(monkeypatch):
    """前端 ShadowEscalationDigestCard.tsx 靠此字段判断是否渲染（设计文档要求
    "仅 shadow 模式下展示"），复用同一次请求即可拿到判断依据，不必新增专门暴露
    配置的端点。"""
    from app.config import refresh_settings

    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "shadow")
    refresh_settings()
    result = build_shadow_escalation_digest(reports=[], discovery_reports=[])
    assert result["escalation_mode"] == "shadow"

    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()
    result = build_shadow_escalation_digest(reports=[], discovery_reports=[])
    assert result["escalation_mode"] == "enforced"
    refresh_settings()


def test_empty_when_reports_exist_but_none_triggered():
    result = build_shadow_escalation_digest(
        reports=[_report_without_escalation()],
        discovery_reports=[],
    )
    assert result["trigger_count"] == 0
    assert result["report_count"] == 1


def test_extracts_report_side_escalation_trigger():
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation()],
        discovery_reports=[],
    )
    assert result["trigger_count"] == 1
    assert result["by_sector"] == {"半导体": 1}
    assert result["by_would_be_action"] == {"暂停追涨": 1}


def test_extracts_discovery_side_escalation_trigger():
    result = build_shadow_escalation_digest(
        reports=[],
        discovery_reports=[_discovery_report_with_escalation(action="exclude")],
    )
    assert result["trigger_count"] == 1
    assert result["by_sector"] == {"半导体材料": 1}
    assert result["by_would_be_action"] == {"从候选池剔除": 1}


def test_discovery_boost_action_maps_to_readable_label():
    result = build_shadow_escalation_digest(
        reports=[],
        discovery_reports=[_discovery_report_with_escalation(action="boost")],
    )
    assert result["by_would_be_action"] == {"提高建议金额上限": 1}


def test_aggregates_across_both_surfaces_and_multiple_reports():
    result = build_shadow_escalation_digest(
        reports=[
            _report_with_escalation(fund_code="519674", sector="半导体"),
            _report_with_escalation(fund_code="519674", sector="半导体", created_days_ago=2),
        ],
        discovery_reports=[_discovery_report_with_escalation()],
    )
    assert result["trigger_count"] == 3
    assert result["by_sector"]["半导体"] == 2
    assert result["by_sector"]["半导体材料"] == 1


def test_excludes_reports_outside_lookback_window():
    """9 天前的报告不应计入默认 7 天窗口。"""
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(created_days_ago=9)],
        discovery_reports=[],
        lookback_days=7,
    )
    assert result["trigger_count"] == 0
    assert result["report_count"] == 0


def test_custom_lookback_days_widens_window():
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(created_days_ago=9)],
        discovery_reports=[],
        lookback_days=14,
    )
    assert result["trigger_count"] == 1


def test_outcome_alignment_counts_negative_same_day_return_as_aligned():
    """升级判断方向天然更谨慎；触发当日估算涨跌为负时，视为初步对齐（当日层面的
    近似对照，非严格次日复盘，见模块 docstring 的诚实划界）。"""
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(daily_return=-2.0)],
        discovery_reports=[],
    )
    outcomes = result["outcomes"]
    assert outcomes["verified_count"] == 1
    assert outcomes["aligned_count"] == 1
    assert outcomes["items"][0]["aligned"] is True


def test_outcome_alignment_counts_positive_same_day_return_as_not_aligned():
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(daily_return=1.2)],
        discovery_reports=[],
    )
    outcomes = result["outcomes"]
    assert outcomes["verified_count"] == 1
    assert outcomes["aligned_count"] == 0


def test_outcome_skips_triggers_without_actual_return_data():
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(daily_return=None)],
        discovery_reports=[],
    )
    assert result["outcomes"]["verified_count"] == 0


def test_summary_line_mentions_trigger_count_and_top_sector():
    result = build_shadow_escalation_digest(
        reports=[_report_with_escalation(sector="半导体")],
        discovery_reports=[],
    )
    assert "1 次" in result["summary"]
    assert "半导体" in result["summary"]


def test_ignores_malformed_rows_defensively():
    """holdings/candidate_pool 里混入非法条目（非 dict）不应导致崩溃。"""
    reports = [
        {
            "id": "malformed",
            "created_at": _iso(1),
            "analysis_facts": {"holdings": ["not-a-dict", {"fund_code": "x"}]},
        }
    ]
    result = build_shadow_escalation_digest(reports=reports, discovery_reports=[])
    assert result["trigger_count"] == 0
