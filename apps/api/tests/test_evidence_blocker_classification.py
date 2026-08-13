"""证据缺口的阻塞类型分类：区分「该等」与「该做事」。

背景：此前两类缺口在面板上长得一样——都只是 `missing_component_counts` 里的一个数字，
外层统一显示 `collecting`。于是无法判断一条证据线是在推进还是在装死：
  · `factor_peer` 缺失等的是 PIT 锚点与经济显著性样本累积，**会自愈**；
  · `downside_control` 缺失等的是同类目录里根本没有的一列（2026-08-12 实测
    `max_drawdown_1y_percent` 在 25000 行目录中非空数为 0），**等待无用**。

这些用例锁住三件事：
  1. 原因码 → 阻塞类型的归因，且未登记的原因必须落 `blocked_unclassified` 而不是被
     默认成会自愈；
  2. 等不到数据的线显示 `blocked` 而不是 `collecting`，并发出 warning；
  3. 归因信号来自 `fund_peer_ranking.catalogue_uncovered_metrics`，只有本组件真正需要
     的指标落在那份清单里时才断言是数据源缺口（反证：不相关的缺列不得触发）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import evidence_maturity
from app.services.decision_score_shadow import (
    build_decision_score_shadow,
    build_decision_score_shadow_digest,
)
from app.services.evidence_maturity import (
    BLOCKER_DATA_SOURCE,
    BLOCKER_NONE,
    BLOCKER_REMOVED_INPUT,
    BLOCKER_TIME,
    BLOCKER_UNCLASSIFIED,
    _accumulation_blocker,
    _classify_blocker,
)

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 原因码归因
# --------------------------------------------------------------------------- #


class TestClassifyBlocker:
    def test_no_reasons_is_not_blocked(self) -> None:
        result = _classify_blocker({})
        assert result["blocker"] == BLOCKER_NONE
        assert result["self_healing"] is None

    def test_pit_dependency_is_blocked_on_time(self) -> None:
        result = _classify_blocker({"factor_ic_not_decision_eligible": 12})
        assert result["blocker"] == BLOCKER_TIME
        assert result["self_healing"] is True

    def test_catalogue_gap_is_blocked_on_data_source(self) -> None:
        result = _classify_blocker({"peer_catalogue_metric_not_covered": 12})
        assert result["blocker"] == BLOCKER_DATA_SOURCE
        assert result["self_healing"] is False

    def test_unregistered_reason_is_never_assumed_self_healing(self) -> None:
        """未登记的原因码不得被默认成「等时间」——那会重新制造这次要修的问题。

        `benchmark_research_not_qualified` 在生产里真实出现（845 次）但尚未归因，
        正是这一类的代表。
        """
        result = _classify_blocker({"benchmark_research_not_qualified": 12})
        assert result["blocker"] == BLOCKER_UNCLASSIFIED
        assert result["self_healing"] is None

    def test_removed_upstream_input_is_its_own_category(self) -> None:
        """读上游已移除的输入既不是等时间也不是等数据源，修法是改代码。"""
        result = _classify_blocker({"tradeability_gate_not_eligible": 134})
        assert result["blocker"] == BLOCKER_REMOVED_INPUT
        assert result["self_healing"] is False

    def test_removed_input_outranks_a_data_source_gap(self) -> None:
        """契约失效排最前：它是唯一改代码今天就能解决的一类。"""
        result = _classify_blocker(
            {
                "peer_catalogue_metric_not_covered": 100,
                "tradeability_gate_not_eligible": 1,
            }
        )
        assert result["blocker"] == BLOCKER_REMOVED_INPUT

    def test_data_source_gap_outranks_a_time_gap(self) -> None:
        result = _classify_blocker(
            {
                "factor_ic_not_decision_eligible": 100,
                "peer_catalogue_metric_not_covered": 1,
            }
        )
        assert result["blocker"] == BLOCKER_DATA_SOURCE
        assert result["self_healing"] is False

    def test_zero_counts_do_not_create_a_blocker(self) -> None:
        assert _classify_blocker({"peer_catalogue_metric_not_covered": 0})[
            "blocker"
        ] == BLOCKER_NONE


class TestAccumulationBlocker:
    def test_fresh_collecting_line_is_blocked_on_time(self) -> None:
        result = _accumulation_blocker(status="collecting", age_days=1)
        assert result["blocker"] == BLOCKER_TIME
        assert result["self_healing"] is True

    def test_stale_collector_is_not_called_waiting(self) -> None:
        """采集停了就不是「等样本累积」，否则故障会伪装成进度。"""
        result = _accumulation_blocker(status="collecting", age_days=30)
        assert result["blocker"] == BLOCKER_UNCLASSIFIED
        assert result["self_healing"] is None

    def test_explicit_stale_flag_is_honoured(self) -> None:
        assert _accumulation_blocker(status="collecting", stale=True)[
            "blocker"
        ] == BLOCKER_UNCLASSIFIED

    def test_unavailable_line_is_not_classified_as_waiting(self) -> None:
        assert _accumulation_blocker(status="unavailable")[
            "blocker"
        ] == BLOCKER_UNCLASSIFIED

    def test_ready_line_has_no_blocker(self) -> None:
        assert _accumulation_blocker(status="ready", age_days=1)[
            "blocker"
        ] == BLOCKER_NONE


# --------------------------------------------------------------------------- #
# 组件侧原因码富化：只在本组件真正需要的指标缺列时才断言数据源缺口
# --------------------------------------------------------------------------- #


def _candidate(*, uncovered: list[str]) -> dict:
    return {
        "fund_code": "519212",
        "quality_gate": {"status": "eligible"},
        "peer_rank": {
            "metric_profile": "equity",
            "status": "descriptive_only",
            "qualified": False,
            "research_shadow_rerank_eligible": False,
            "reason": "one_or_more_peer_metrics_not_qualified",
            "catalogue_uncovered_metrics": uncovered,
            "metrics": {},
        },
    }


def _artifact(*, uncovered: list[str]) -> dict:
    return build_decision_score_shadow(
        [_candidate(uncovered=uncovered)],
        candidate_factor_scores=None,
        portfolio_gap=None,
        profile=None,
        decision_at=NOW,
        minimum_holding_days=7,
    )


def _downside_reasons(artifact: dict) -> list[str]:
    row = (artifact.get("rows") or [])[0]
    return list(row["components"]["downside_control"]["reason_codes"])


def test_required_metric_in_the_uncovered_list_marks_a_data_source_gap() -> None:
    reasons = _downside_reasons(_artifact(uncovered=["max_drawdown_1y_percent"]))
    assert reasons[0] == "peer_rank_not_shadow_qualified"
    assert "peer_catalogue_metric_not_covered" in reasons


def test_an_unrelated_uncovered_metric_does_not_mark_a_data_source_gap() -> None:
    """反证：缺的列与本组件无关时，不得断言这是数据源缺口。"""
    reasons = _downside_reasons(_artifact(uncovered=["tracking_error_1y_percent"]))
    assert reasons == ["peer_rank_not_shadow_qualified"]


def test_digest_aggregates_missing_component_reason_counts() -> None:
    artifact = _artifact(uncovered=["max_drawdown_1y_percent"])
    digest = build_decision_score_shadow_digest(
        [{"discovery_facts": {"decision_score_shadow": artifact}}]
    )
    downside = digest["missing_component_reason_counts"]["downside_control"]
    assert downside["peer_rank_not_shadow_qualified"] == 1
    assert downside["peer_catalogue_metric_not_covered"] == 1
    # factor_peer 走的是 PIT 依赖，应归为会自愈的那一类。
    assert (
        "factor_ic_not_decision_eligible"
        in digest["missing_component_reason_counts"]["factor_peer"]
    )


# --------------------------------------------------------------------------- #
# 面板集成：等不到数据的线不再显示成「还在积累」
# --------------------------------------------------------------------------- #


def _patch_sources(monkeypatch, *, reports: list[dict]) -> None:
    monkeypatch.setattr(
        evidence_maturity,
        "inspect_worker_health",
        lambda **_kwargs: {
            "healthy": True,
            "reason": "ok",
            "heartbeat_at": "2026-08-12T07:59:55+00:00",
            "age_seconds": 5.0,
            "started_at": "2026-08-12T06:00:00+00:00",
            "jobs": [],
        },
    )
    monkeypatch.setattr(
        evidence_maturity,
        "build_factor_ic_status",
        lambda **_kwargs: {
            "available": True,
            "stale": False,
            "confidence_eligible": True,
            "run_date": "2026-08-12",
            "age_days": 0,
            "schema_version": 2,
            "source": "database",
            "universe_size": 1500,
            "cohort_mode": "current_survivors",
            "point_in_time": {},
            "confidence_block_reasons": [],
        },
    )
    monkeypatch.setattr(
        evidence_maturity,
        "read_factor_ic_universe_history",
        lambda **_kwargs: {
            "snapshots": [
                {
                    "snapshot_date": "2026-08-11",
                    "available_at": "2026-08-11T12:00:00+00:00",
                    "sampled_fund_count": 1500,
                    "fund_type_count": 6,
                }
            ]
        },
    )
    monkeypatch.setattr(
        evidence_maturity,
        "read_nav_observation_status",
        lambda: {
            "status": "collecting",
            "observation_count": 28245,
            "fund_count": 9413,
            "capture_run_count": 19,
            "revision_count": 0,
            "first_observed_at": "2026-07-18T08:02:05+00:00",
            "latest_observed_at": "2026-08-11T13:49:10+00:00",
            "latest_nav_date": "2026-08-11",
            "latest_capture_fund_count": 1500,
            "availability_basis": "collector_first_observed_at",
            "revision_policy": "first_observed_value",
            "minimum_feature_history_points": 250,
            "full_model_ready": False,
            "automatic_promotion_allowed": False,
        },
    )
    monkeypatch.setattr(
        evidence_maturity,
        "read_latest_decision_quality_snapshot",
        lambda *, user_id: {
            "evaluation_as_of": "2026-08-11T00:00:00+00:00",
            "readiness": {
                "status": "insufficient_data",
                "mature_decision_day_count": 8,
                "formal_label_coverage_percent": 5.3,
                "minimum_shadow_mature_decision_days": 20,
                "minimum_manual_review_mature_decision_days": 60,
                "minimum_manual_review_label_coverage_percent": 80,
            },
            "input_counts": {"decision_event_count": 173},
            "automatic_promotion_allowed": False,
        },
    )
    monkeypatch.setattr(
        evidence_maturity,
        "list_discovery_report_decision_diagnostics",
        lambda **_kwargs: reports,
    )


def _report_with(artifact: dict) -> dict:
    return {
        "id": "report-1",
        "created_at": "2026-08-12T07:00:00+00:00",
        "discovery_facts": {"decision_score_shadow": artifact},
        "candidate_pool": [],
    }


def test_data_source_blocked_line_is_reported_as_blocked_not_collecting(
    monkeypatch,
) -> None:
    _patch_sources(
        monkeypatch,
        reports=[_report_with(_artifact(uncovered=["max_drawdown_1y_percent"]))],
    )

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)
    score = result["decision_score_shadow"]

    assert score["status"] == "blocked"
    assert score["self_healing"] is False
    downside = score["component_blockers"]["downside_control"]
    assert downside["blocker"] == BLOCKER_DATA_SOURCE
    assert downside["self_healing"] is False
    # 同一份制品里，PIT 依赖那一维仍应标成会自愈。
    assert score["component_blockers"]["factor_peer"]["blocker"] == BLOCKER_TIME
    assert score["component_blockers"]["factor_peer"]["self_healing"] is True
    # 这份 fixture 的候选没有 tradeability，因此同时复现了生产的硬门情形；契约失效
    # 优先级高于数据源，所以整条线的 blocker 是 removed_input，而逐维分类仍各自准确。
    assert score["hard_gate_blocker"]["blocker"] == BLOCKER_REMOVED_INPUT
    assert score["blocker"] == BLOCKER_REMOVED_INPUT

    codes = {alert["code"] for alert in result["alerts"]}
    assert "decision_score_component_blocked_on_data_source" in codes
    assert "decision_score_shadow_empty" not in codes


def test_hard_gate_blocking_every_row_is_surfaced(monkeypatch) -> None:
    """硬门先于组件生效：全量拦住时必须单独报出来，否则组件缺口会盖住真正原因。"""
    _patch_sources(
        monkeypatch,
        reports=[_report_with(_artifact(uncovered=["max_drawdown_1y_percent"]))],
    )

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)
    score = result["decision_score_shadow"]

    assert score["hard_gate_blocked_count"] == score["candidate_count"]
    assert score["hard_gate_blocked_percent"] == 100.0
    assert (
        "tradeability_gate_not_eligible"
        in score["hard_gate_blocker"]["reason_counts"]
    )

    codes = {alert["code"] for alert in result["alerts"]}
    assert "decision_score_all_rows_hard_gate_blocked" in codes
    assert "decision_score_hard_gate_reads_removed_input" in codes

    by_code = {entry["code"]: entry for entry in result["blockers"]}
    assert by_code["decision_score_hard_gate"]["blocker"] == BLOCKER_REMOVED_INPUT
    assert by_code["decision_score_hard_gate"]["self_healing"] is False


def test_blocker_rollup_lists_gaps_with_whether_waiting_helps(monkeypatch) -> None:
    _patch_sources(
        monkeypatch,
        reports=[_report_with(_artifact(uncovered=["max_drawdown_1y_percent"]))],
    )

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)
    by_code = {entry["code"]: entry for entry in result["blockers"]}

    assert by_code["pit_universe_membership"]["blocker"] == BLOCKER_TIME
    assert by_code["nav_observation_pit"]["self_healing"] is True
    downside = by_code["decision_score_component.downside_control"]
    assert downside["blocker"] == BLOCKER_DATA_SOURCE
    assert downside["self_healing"] is False
    assert "peer_catalogue_metric_not_covered" in downside["reason_counts"]
    # 未阻塞的条目不进清单，避免清单变成"全部维度"的复述。
    assert all(entry["blocker"] != BLOCKER_NONE for entry in result["blockers"])


def test_line_without_artifacts_still_reads_as_collecting(monkeypatch) -> None:
    """没有制品时不能报 blocked——那会把"还没开始"说成"等不到"。"""
    _patch_sources(monkeypatch, reports=[])

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)

    assert result["decision_score_shadow"]["status"] == "collecting"
    assert result["decision_score_shadow"]["blocker"] == BLOCKER_NONE
