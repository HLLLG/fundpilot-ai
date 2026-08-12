"""Read-only operational view of FundPilot's evidence collection maturity.

The projection intentionally combines operational liveness with research
coverage, while keeping the boundaries explicit: a healthy collector is not a
validated model, a zero count is not substituted for missing evidence, and no
state in this module can promote a shadow model into live decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.background_worker import inspect_worker_health
from app.database import list_discovery_report_decision_diagnostics
from app.services.decision_quality_snapshot import (
    MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT,
    MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS,
    MIN_SHADOW_MATURE_DECISION_DAYS,
    DecisionQualitySnapshotError,
    read_latest_decision_quality_snapshot,
)
from app.services.decision_score_shadow import build_decision_score_shadow_digest
from app.services.factor_ic_snapshot import build_factor_ic_status
from app.services.factor_ic_nav_observation import (
    FactorIcNavObservationStorageUnavailable,
    read_nav_observation_status,
)
from app.services.factor_ic_universe_snapshot import (
    FactorIcUniverseStorageUnavailable,
    read_factor_ic_universe_history,
)


SCHEMA_VERSION = "evidence_maturity.v1"
PIT_MINIMUM_EFFECTIVE_ANCHORS = 24
ECONOMIC_MINIMUM_PERIODS = 36
PRIMARY_HORIZON_DAYS = 20
LONG_HORIZON_DAYS = 60
THEORETICAL_PRIMARY_TRADING_DAYS = 372
THEORETICAL_LONG_TRADING_DAYS = 412

# 「该等」与「该做事」必须能一眼分开。此前两类缺口在面板上长得一样（都只是
# `missing_component_counts` 里的一个数字、外面统一显示 `collecting`），于是无法判断
# 一条证据线是在推进还是在装死——`decision_score_shadow` 恒为 0 却显示"还在积累"就是
# 这样活下来的。
BLOCKER_NONE = "not_blocked"
BLOCKER_TIME = "blocked_on_time"
BLOCKER_DATA_SOURCE = "blocked_on_data_source"
BLOCKER_UNCLASSIFIED = "blocked_unclassified"

# 原因码 → 阻塞类型。**未列出的原因一律落 `blocked_unclassified`，不猜。**
# 归因依据：
#   `factor_ic_not_decision_eligible`：等 PIT 锚点与经济显著性样本累积，会自愈。
#   `peer_catalogue_metric_not_covered`：同类目录里没有任何成员携带该列
#     （2026-08-12 实测 `max_drawdown_1y_percent` 在 25000 行目录中非空数为 0，
#     `downside_capture_1y_percent` 字段压根不存在），不补数据源则永远不会出现，
#     继续等待无用。该原因码由 `fund_peer_ranking.catalogue_uncovered_metrics`
#     派生，不是这里另编的判断。
_BLOCKER_BY_REASON: dict[str, str] = {
    "factor_ic_not_decision_eligible": BLOCKER_TIME,
    "peer_catalogue_metric_not_covered": BLOCKER_DATA_SOURCE,
    "peer_catalogue_missing_required_metrics": BLOCKER_DATA_SOURCE,
}

# 永不自愈的缺口优先：一条线只要含有等不到的原因，就不该整体显示成「在积累」。
_BLOCKER_PRIORITY = (
    BLOCKER_DATA_SOURCE,
    BLOCKER_UNCLASSIFIED,
    BLOCKER_TIME,
    BLOCKER_NONE,
)

# 只有能证明的才给结论；无法归因时是 None（未知），不是 False（不会自愈）。
_SELF_HEALING_BY_BLOCKER: dict[str, bool | None] = {
    BLOCKER_TIME: True,
    BLOCKER_DATA_SOURCE: False,
    BLOCKER_UNCLASSIFIED: None,
    BLOCKER_NONE: None,
}

_BLOCKER_LABELS = {
    BLOCKER_NONE: "无阻塞",
    BLOCKER_TIME: "等样本累积（会自愈）",
    BLOCKER_DATA_SOURCE: "等数据源（不会自愈）",
    BLOCKER_UNCLASSIFIED: "原因未归类",
}


def _worst_blocker(blockers: Iterable[str]) -> str:
    present = {str(value) for value in blockers}
    return next(
        (name for name in _BLOCKER_PRIORITY if name in present),
        BLOCKER_NONE,
    )


def _classify_blocker(reason_counts: Any) -> dict[str, Any]:
    """把一个缺口的原因码分布归成阻塞类型。

    未登记的原因码显式落 `blocked_unclassified`——把它默认成「等时间」会重新制造
    这次要修的问题（看着在推进，其实永远不动）。
    """
    counts = reason_counts if isinstance(reason_counts, Mapping) else {}
    by_blocker: dict[str, int] = {}
    normalized: dict[str, int] = {}
    for reason, raw in counts.items():
        value = _nonnegative_int(raw) or 0
        if value <= 0:
            continue
        text = str(reason)
        normalized[text] = value
        blocker = _BLOCKER_BY_REASON.get(text, BLOCKER_UNCLASSIFIED)
        by_blocker[blocker] = by_blocker.get(blocker, 0) + value
    if not by_blocker:
        return {
            "blocker": BLOCKER_NONE,
            "blocker_label": _BLOCKER_LABELS[BLOCKER_NONE],
            "self_healing": None,
            "reason_counts": {},
        }
    blocker = _worst_blocker(by_blocker)
    return {
        "blocker": blocker,
        "blocker_label": _BLOCKER_LABELS[blocker],
        "self_healing": _SELF_HEALING_BY_BLOCKER[blocker],
        "reason_counts": dict(sorted(normalized.items())),
    }


# 与 `pit_universe_stale` / `nav_observation_stale` 两条告警同一阈值，避免两套口径。
_ACCUMULATION_STALE_AFTER_DAYS = 4


def _accumulation_blocker(
    *,
    status: Any,
    age_days: Any = None,
    stale: Any = None,
) -> dict[str, Any]:
    """累积型证据线的阻塞类型。

    采集一旦停了就不能再叫「等样本累积」——那正是这次要拆开的两种情况：会自愈的等待
    与需要动手的故障，此前都显示成 `collecting`。
    """
    text = str(status or "")
    if text not in {"collecting", "unavailable"}:
        blocker = BLOCKER_NONE
    elif (
        stale is True
        or text == "unavailable"
        or (
            isinstance(age_days, (int, float))
            and not isinstance(age_days, bool)
            and age_days > _ACCUMULATION_STALE_AFTER_DAYS
        )
    ):
        blocker = BLOCKER_UNCLASSIFIED
    else:
        blocker = BLOCKER_TIME
    return {
        "blocker": blocker,
        "blocker_label": _BLOCKER_LABELS[blocker],
        "self_healing": _SELF_HEALING_BY_BLOCKER[blocker],
    }


def _blocker_entry(
    *,
    code: str,
    label: str,
    classified: Mapping[str, Any],
    detail: str | None = None,
) -> dict[str, Any] | None:
    if classified.get("blocker") == BLOCKER_NONE:
        return None
    entry: dict[str, Any] = {
        "code": code,
        "label": label,
        "blocker": classified.get("blocker"),
        "blocker_label": classified.get("blocker_label"),
        "self_healing": classified.get("self_healing"),
    }
    reason_counts = classified.get("reason_counts")
    if reason_counts:
        entry["reason_counts"] = reason_counts
    if detail:
        entry["detail"] = detail
    return entry


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(value: Any, current: datetime) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0, (current.date() - parsed.date()).days)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _progress(value: int | None, target: int) -> float | None:
    if value is None:
        return None
    return round(min(100.0, value / target * 100.0), 2)


def _alert(
    code: str,
    severity: str,
    title: str,
    message: str,
    action: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "message": message,
        "action": action,
    }


def _worker_projection() -> tuple[dict[str, Any], list[dict[str, str]]]:
    health = inspect_worker_health(verify_process=False)
    jobs = health.get("jobs") if isinstance(health.get("jobs"), list) else []
    public_jobs = [
        {
            "name": str(job.get("name") or "unknown"),
            "persistent": job.get("persistent") is True,
            "alive": job.get("alive") is True,
        }
        for job in jobs
        if isinstance(job, Mapping)
    ]
    healthy = health.get("healthy") is True
    projection = {
        "status": "healthy" if healthy else "unavailable",
        "healthy": healthy,
        "reason": str(health.get("reason") or "unknown"),
        "heartbeat_at": health.get("heartbeat_at"),
        "heartbeat_age_seconds": _optional_number(health.get("age_seconds")),
        "started_at": health.get("started_at"),
        "jobs": public_jobs,
    }
    if healthy:
        return projection, []
    return projection, [
        _alert(
            "background_worker_unhealthy",
            "critical",
            "后台采集 Worker 不可确认",
            "市场刷新与研究采集可能已经停止；该状态不会被当作零样本。",
            "检查 worker 容器健康、leader 租约和共享心跳文件。",
        )
    ]


def _factor_projection(
    current: datetime,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    alerts: list[dict[str, str]] = []
    try:
        factor = build_factor_ic_status(now=current)
    except Exception:  # fail closed at this aggregate read boundary
        factor = {"available": False, "source": "unavailable"}
        alerts.append(
            _alert(
                "factor_ic_read_failed",
                "warning",
                "Factor IC 状态读取失败",
                "现有快照无法安全验证，量化可靠性保持不可用。",
                "检查 Factor IC 快照存储与完整性日志。",
            )
        )

    try:
        history = read_factor_ic_universe_history(
            days=3_650,
            max_snapshots=260,
            stride_days=1,
            include_members=False,
            now=current,
        )
        snapshots = history.get("snapshots") if isinstance(history, Mapping) else []
        if not isinstance(snapshots, list):
            snapshots = []
    except (FactorIcUniverseStorageUnavailable, ValueError):
        snapshots = []
        alerts.append(
            _alert(
                "pit_universe_read_failed",
                "warning",
                "PIT 基金池读取失败",
                "不能确认真实历史成员覆盖，系统不会用当前基金池回填过去。",
                "检查 universe 快照表与发布任务。",
            )
        )

    point_in_time = factor.get("point_in_time")
    pit = point_in_time if isinstance(point_in_time, Mapping) else {}
    pit_upgrade_value = factor.get("pit_upgrade")
    pit_upgrade = (
        pit_upgrade_value if isinstance(pit_upgrade_value, Mapping) else {}
    )
    effective_anchors = _nonnegative_int(pit.get("effective_anchor_count"))
    if effective_anchors is None:
        effective_anchors = _nonnegative_int(
            pit_upgrade.get("effective_anchor_count")
        )
    primary_horizon = str(pit.get("primary_maturity_horizon") or PRIMARY_HORIZON_DAYS)
    mature_map = pit.get("mature_anchor_count_by_horizon")
    mature_counts = mature_map if isinstance(mature_map, Mapping) else {}
    mature_primary = _nonnegative_int(
        mature_counts.get(primary_horizon, mature_counts.get(str(PRIMARY_HORIZON_DAYS)))
    )
    mature_long = _nonnegative_int(mature_counts.get(str(LONG_HORIZON_DAYS)))
    latest = snapshots[-1] if snapshots else None
    first = snapshots[0] if snapshots else None
    latest_date = latest.get("snapshot_date") if isinstance(latest, Mapping) else None
    latest_age = _age_days(
        latest.get("available_at") if isinstance(latest, Mapping) else None,
        current,
    )
    membership_ready = (
        effective_anchors is not None
        and effective_anchors >= PIT_MINIMUM_EFFECTIVE_ANCHORS
        and pit.get("publishable") is True
    )
    universe_status = (
        "unavailable"
        if not snapshots
        else "ready"
        if membership_ready
        else "collecting"
    )
    universe = {
        "status": universe_status,
        "snapshot_count": len(snapshots),
        "first_snapshot_date": (
            first.get("snapshot_date") if isinstance(first, Mapping) else None
        ),
        "latest_snapshot_date": latest_date,
        "latest_snapshot_age_days": latest_age,
        "latest_sampled_fund_count": (
            _nonnegative_int(latest.get("sampled_fund_count"))
            if isinstance(latest, Mapping)
            else None
        ),
        "latest_fund_type_count": (
            _nonnegative_int(latest.get("fund_type_count"))
            if isinstance(latest, Mapping)
            else None
        ),
        "effective_anchor_count": effective_anchors,
        "minimum_effective_anchor_count": PIT_MINIMUM_EFFECTIVE_ANCHORS,
        "anchor_progress_percent": _progress(
            effective_anchors, PIT_MINIMUM_EFFECTIVE_ANCHORS
        ),
        "publishable": pit.get("publishable") is True,
    }
    if snapshots and latest_age is not None and latest_age > 4:
        alerts.append(
            _alert(
                "pit_universe_stale",
                "warning",
                "PIT 基金池采集已滞后",
                f"最近真实成员快照距今 {latest_age} 个自然日。",
                "检查工作日 universe capture workflow 与发布回执。",
            )
        )
    if not snapshots:
        alerts.append(
            _alert(
                "pit_universe_empty",
                "info",
                "PIT 基金池尚无证据",
                "尚未观察到真实成员快照；这不是 0 分。",
                "保持工作日采集，禁止用当前目录伪造历史。",
            )
        )

    available = factor.get("available") is True
    stale = factor.get("stale") is True
    eligible = factor.get("confidence_eligible") is True
    nav_revision_pit = pit.get("nav_revision_pit") is True
    scope = str(
        pit.get("point_in_time_scope")
        or (
            "membership_only"
            if factor.get("cohort_mode") == "point_in_time"
            else "unavailable"
        )
    )
    factor_status = (
        "unavailable"
        if not available
        else "stale"
        if stale
        else "active"
        if eligible
        else "collecting"
    )
    factor_projection = {
        "status": factor_status,
        "available": available,
        "stale": stale,
        "confidence_eligible": eligible,
        "run_date": factor.get("run_date"),
        "age_days": _nonnegative_int(factor.get("age_days")),
        "schema_version": factor.get("schema_version"),
        "source": factor.get("source"),
        "universe_size": _nonnegative_int(factor.get("universe_size")),
        "cohort_mode": factor.get("cohort_mode"),
        "point_in_time_scope": scope,
        "nav_revision_pit": nav_revision_pit,
        "mature_period_count_20d": mature_primary,
        "mature_period_count_60d": mature_long,
        "economic_minimum_period_count": ECONOMIC_MINIMUM_PERIODS,
        "economic_progress_percent_20d": _progress(
            mature_primary, ECONOMIC_MINIMUM_PERIODS
        ),
        "economic_progress_percent_60d": _progress(
            mature_long, ECONOMIC_MINIMUM_PERIODS
        ),
        "confidence_block_reasons": factor.get("confidence_block_reasons") or [],
    }
    if not available or stale:
        alerts.append(
            _alert(
                "factor_ic_unavailable" if not available else "factor_ic_stale",
                "warning",
                "Factor IC 不可用于当前置信度",
                "快照缺失或过期时，系统保持 fail-closed，不把旧证据当作当前有效。",
                "检查周度 Factor IC workflow、发布质量门禁和数据库快照。",
            )
        )
    if not nav_revision_pit:
        alerts.append(
            _alert(
                "nav_observation_pit_collecting",
                "info",
                "NAV 时点证据尚未完整",
                "当前最多证明基金池成员 PIT，不能证明历史 NAV 修订在当时已可见。",
                "继续追加式采集 NAV observation；完整前维持成员 PIT 标识。",
            )
        )
    return universe, factor_projection, alerts


def _decision_score_projection() -> tuple[dict[str, Any], list[dict[str, str]]]:
    # 必须走诊断切片：`list_discovery_reports` 按 `_DISCOVERY_SUMMARY_FIELDS` 投影，
    # 整个 `discovery_facts` 都不在里面，于是 digest 恒数出 0 份制品，而面板会把这个
    # 结构性读不到显示成「还在积累」。同类缺陷在 shadow_escalation/llm_judge 两条
    # 摘要上已经修过一次，这条当时被漏掉了。
    digest = build_decision_score_shadow_digest(
        list_discovery_report_decision_diagnostics(limit=100)
    )
    artifacts = _nonnegative_int(digest.get("artifact_count")) or 0
    candidates = _nonnegative_int(digest.get("candidate_count")) or 0
    scored = _nonnegative_int(digest.get("scored_count")) or 0
    valid = _nonnegative_int(digest.get("valid_artifact_count")) or 0
    evaluable = _nonnegative_int(digest.get("shadow_evaluable_report_count")) or 0
    missing_counts = digest.get("missing_component_counts") or {}
    reason_counts = digest.get("missing_component_reason_counts") or {}
    component_blockers = {
        str(key): _classify_blocker(
            reason_counts.get(key) if isinstance(reason_counts, Mapping) else None
        )
        for key in sorted(missing_counts)
    }
    line_blocker = _worst_blocker(
        item["blocker"]
        for item in component_blockers.values()
        if item["blocker"] != BLOCKER_NONE
    )
    # 有制品但因为等不到的数据而永远打不出分时，显示成 `blocked` 而不是 `collecting`：
    # 后者会让人以为再等等就好了。校验失败（`attention`）更紧急，仍然优先。
    status = (
        "collecting"
        if artifacts == 0
        else "attention"
        if valid < artifacts
        else "blocked"
        if line_blocker == BLOCKER_DATA_SOURCE
        else "collecting"
        if evaluable == 0
        else "shadow_ready"
    )
    projection = {
        "status": status,
        "mode": digest.get("mode"),
        "model_version": digest.get("current_model_version"),
        "report_count": _nonnegative_int(digest.get("report_count")),
        "artifact_count": artifacts,
        "total_artifact_count": _nonnegative_int(digest.get("total_artifact_count")),
        "legacy_artifact_count": _nonnegative_int(digest.get("legacy_artifact_count")),
        "valid_artifact_count": valid,
        "shadow_evaluable_report_count": evaluable,
        "top_k_changed_report_count": _nonnegative_int(
            digest.get("top_k_changed_report_count")
        ),
        "candidate_count": candidates,
        "scored_count": scored,
        "scored_coverage_percent": (
            round(scored / candidates * 100.0, 2) if candidates else None
        ),
        "missing_component_counts": missing_counts,
        "blocker": line_blocker,
        "blocker_label": _BLOCKER_LABELS[line_blocker],
        "self_healing": _SELF_HEALING_BY_BLOCKER[line_blocker],
        "component_blockers": component_blockers,
        "latest": digest.get("latest"),
        "automatic_promotion_allowed": False,
    }
    alerts: list[dict[str, str]] = []
    data_source_blocked = sorted(
        key
        for key, item in component_blockers.items()
        if item["blocker"] == BLOCKER_DATA_SOURCE
    )
    if data_source_blocked:
        alerts.append(
            _alert(
                "decision_score_component_blocked_on_data_source",
                "warning",
                "DecisionScore 有维度等不到数据",
                "、".join(data_source_blocked)
                + " 缺的是同类目录里没有任何成员携带的列，继续生成报告或继续等待都不会让它出现。",
                "按补数据源或改口径排期，不要按「再等等」处理；在补上之前该维度维持 fail-closed。",
            )
        )
    unclassified = sorted(
        key
        for key, item in component_blockers.items()
        if item["blocker"] == BLOCKER_UNCLASSIFIED
    )
    if unclassified:
        alerts.append(
            _alert(
                "decision_score_component_blocker_unclassified",
                "info",
                "DecisionScore 有维度的缺失原因未归类",
                "、".join(unclassified)
                + " 的原因码尚未登记到阻塞分类表，因此无法判断它会不会随时间自愈。",
                "查清该原因码后登记进 _BLOCKER_BY_REASON，不要先按会自愈处理。",
            )
        )
    if artifacts == 0:
        alerts.append(
            _alert(
                "decision_score_shadow_empty",
                "info",
                "DecisionScore 尚无真实样本",
                "尚未生成包含当前版本 shadow 制品的新荐基报告；旧版本样本不会混入，这里显示缺证据而不是 0 分。",
                "下次登录后正常生成荐基报告即可开始积累，不需要绕过认证补样本。",
            )
        )
    elif valid < artifacts:
        alerts.append(
            _alert(
                "decision_score_shadow_invalid",
                "warning",
                "部分 DecisionScore 制品未通过校验",
                "无效制品不会进入 shadow 比较分母。",
                "检查快照 hash、模型版本与组件缺失原因。",
            )
        )
    return projection, alerts


def _nav_observation_projection(
    current: datetime,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    try:
        status = read_nav_observation_status()
    except FactorIcNavObservationStorageUnavailable:
        return (
            {
                "status": "unavailable",
                "observation_count": None,
                "fund_count": None,
                "capture_run_count": None,
                "latest_observed_at": None,
                "latest_capture_age_days": None,
                "full_model_ready": False,
                "automatic_promotion_allowed": False,
            },
            [
                _alert(
                    "nav_observation_read_failed",
                    "warning",
                    "NAV observation 状态读取失败",
                    "不能确认追加式净值观察链，本次不会声称完整 NAV-PIT。",
                    "检查 schema v17 观察账、不可变触发器和每日发布回执。",
                )
            ],
        )
    count = _nonnegative_int(status.get("observation_count")) or 0
    projection = {
        "status": "collecting" if count else "not_started",
        "observation_count": count,
        "fund_count": _nonnegative_int(status.get("fund_count")),
        "capture_run_count": _nonnegative_int(status.get("capture_run_count")),
        "revision_count": _nonnegative_int(status.get("revision_count")),
        "first_observed_at": status.get("first_observed_at"),
        "latest_observed_at": status.get("latest_observed_at"),
        "latest_capture_age_days": _age_days(status.get("latest_observed_at"), current),
        "latest_nav_date": status.get("latest_nav_date"),
        "latest_capture_fund_count": _nonnegative_int(
            status.get("latest_capture_fund_count")
        ),
        "availability_basis": status.get("availability_basis"),
        "revision_policy": status.get("revision_policy"),
        "minimum_feature_history_points": _nonnegative_int(
            status.get("minimum_feature_history_points")
        ),
        "full_model_ready": status.get("full_model_ready") is True,
        "automatic_promotion_allowed": False,
    }
    alerts: list[dict[str, str]] = []
    if count == 0:
        alerts.append(
            _alert(
                "nav_observation_not_started",
                "info",
                "NAV observation 尚未开始积累",
                "当前历史净值不能证明当时看到的是修订前数值。",
                "运行一次 Factor IC Universe Capture；之后按工作日增量追加。",
            )
        )
    elif projection["latest_capture_age_days"] is not None and int(
        projection["latest_capture_age_days"]
    ) > 4:
        alerts.append(
            _alert(
                "nav_observation_stale",
                "warning",
                "NAV observation 采集已滞后",
                f"最近观察批次距今 {projection['latest_capture_age_days']} 个自然日。",
                "检查工作日 universe capture 的 NAV 发布步骤。",
            )
        )
    return projection, alerts


def _decision_quality_projection(
    user_id: int,
    current: datetime,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    alerts: list[dict[str, str]] = []
    try:
        snapshot = read_latest_decision_quality_snapshot(user_id=user_id)
    except DecisionQualitySnapshotError:
        snapshot = None
        alerts.append(
            _alert(
                "decision_quality_read_failed",
                "warning",
                "决策质量快照读取失败",
                "不可变快照无法安全验证，本次不展示推断值。",
                "检查每日 evaluation、主存储和内容完整性回执。",
            )
        )
    if snapshot is None:
        projection = {
            "status": "collecting",
            "snapshot_available": False,
            "evaluation_as_of": None,
            "snapshot_age_days": None,
            "readiness_status": "insufficient_data",
            "mature_decision_day_count": None,
            "minimum_shadow_mature_decision_days": MIN_SHADOW_MATURE_DECISION_DAYS,
            "minimum_manual_review_mature_decision_days": (
                MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
            ),
            "formal_label_coverage_percent": None,
            "minimum_manual_review_label_coverage_percent": (
                MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT
            ),
            "maturity_progress_percent": None,
            "input_counts": {},
            "automatic_promotion_allowed": False,
        }
        if not alerts:
            alerts.append(
                _alert(
                    "decision_quality_snapshot_empty",
                    "info",
                    "决策质量尚无预计算快照",
                    "没有历史冻结样本时不会即时重算，也不会用 0 代替缺失。",
                    "等待每日结算与 evaluation 任务生成首个快照。",
                )
            )
        return projection, alerts

    readiness = snapshot.get("readiness")
    readiness_map = readiness if isinstance(readiness, Mapping) else {}
    readiness_status = str(readiness_map.get("status") or "insufficient_data")
    mature_days = _nonnegative_int(readiness_map.get("mature_decision_day_count"))
    shadow_target = (
        _nonnegative_int(readiness_map.get("minimum_shadow_mature_decision_days"))
        or MIN_SHADOW_MATURE_DECISION_DAYS
    )
    manual_target = (
        _nonnegative_int(
            readiness_map.get("minimum_manual_review_mature_decision_days")
        )
        or MIN_MANUAL_REVIEW_MATURE_DECISION_DAYS
    )
    label_target = (
        _optional_number(
            readiness_map.get("minimum_manual_review_label_coverage_percent")
        )
        or float(MIN_MANUAL_REVIEW_LABEL_COVERAGE_PERCENT)
    )
    label_coverage = _optional_number(
        readiness_map.get("formal_label_coverage_percent")
    )
    age = _age_days(snapshot.get("evaluation_as_of"), current)
    status = (
        "manual_review_ready"
        if readiness_status == "ready_for_manual_review"
        else "shadow"
        if readiness_status in {"shadow_evaluation", "shadow_only"}
        else "collecting"
    )
    projection = {
        "status": status,
        "snapshot_available": True,
        "evaluation_as_of": snapshot.get("evaluation_as_of"),
        "snapshot_age_days": age,
        "readiness_status": readiness_status,
        "mature_decision_day_count": mature_days,
        "minimum_shadow_mature_decision_days": shadow_target,
        "minimum_manual_review_mature_decision_days": manual_target,
        "formal_label_coverage_percent": label_coverage,
        "minimum_manual_review_label_coverage_percent": label_target,
        "maturity_progress_percent": _progress(mature_days, manual_target),
        "input_counts": snapshot.get("input_counts") or {},
        "automatic_promotion_allowed": False,
    }
    if age is not None and age > 2:
        alerts.append(
            _alert(
                "decision_quality_snapshot_stale",
                "warning",
                "决策质量快照已滞后",
                f"最近评估快照距今 {age} 个自然日。",
                "检查每日 outcome settlement 与 quality evaluation workflow。",
            )
        )
    return projection, alerts


def build_evidence_maturity_status(
    *,
    user_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one bounded, redacted and side-effect-free evidence status."""

    current = _utc_now(now)
    worker, worker_alerts = _worker_projection()
    universe, factor_ic, factor_alerts = _factor_projection(current)
    nav_observation, nav_alerts = _nav_observation_projection(current)
    try:
        decision_score, score_alerts = _decision_score_projection()
    except Exception:
        decision_score = {
            "status": "unavailable",
            "artifact_count": None,
            "scored_coverage_percent": None,
            "automatic_promotion_allowed": False,
        }
        score_alerts = [
            _alert(
                "decision_score_shadow_read_failed",
                "warning",
                "DecisionScore 状态读取失败",
                "本次不展示无法验证的 shadow 汇总。",
                "检查荐基报告存储与 shadow 制品契约。",
            )
        ]
    decision_quality, quality_alerts = _decision_quality_projection(user_id, current)
    alerts = (
        worker_alerts
        + factor_alerts
        + nav_alerts
        + score_alerts
        + quality_alerts
    )
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda item: (order.get(item["severity"], 9), item["code"]))
    severities = {item["severity"] for item in alerts}
    if "critical" in severities:
        overall = "degraded"
    elif "warning" in severities:
        overall = "attention"
    elif any(
        component.get("status") in {"collecting", "unavailable", "blocked"}
        for component in (
            universe,
            factor_ic,
            nav_observation,
            decision_score,
            decision_quality,
        )
    ):
        overall = "collecting"
    else:
        overall = "healthy"

    blockers = [
        entry
        for entry in (
            _blocker_entry(
                code="pit_universe_membership",
                label="PIT 成员快照锚点",
                classified=_accumulation_blocker(
                    status=universe.get("status"),
                    age_days=universe.get("latest_snapshot_age_days"),
                ),
                detail="按 10 个交易日一个锚点累积，快照采集正常时会自行推进。",
            ),
            _blocker_entry(
                code="factor_ic_economic_significance",
                label="因子经济显著性样本",
                classified=_accumulation_blocker(
                    status=factor_ic.get("status"),
                    stale=factor_ic.get("stale"),
                ),
                detail="需要 36 个成熟期；到期也不自动晋级，仍须 FDR 与扣费后门槛。",
            ),
            _blocker_entry(
                code="nav_observation_pit",
                label="NAV 首次观测时点",
                classified=_accumulation_blocker(
                    status=nav_observation.get("status"),
                    age_days=nav_observation.get("latest_capture_age_days"),
                ),
                detail="只能向前追加采集，历史修订时点无法回填。",
            ),
            _blocker_entry(
                code="decision_quality_manual_review",
                label="决策质量成熟决策日",
                classified=_accumulation_blocker(
                    status=decision_quality.get("status"),
                    age_days=decision_quality.get("snapshot_age_days"),
                ),
                detail="标签要求结局观察已终局且成熟，由前瞻窗口结算驱动。",
            ),
            *(
                _blocker_entry(
                    code=f"decision_score_component.{component}",
                    label=f"DecisionScore 维度 {component}",
                    classified=classified,
                )
                for component, classified in (
                    decision_score.get("component_blockers") or {}
                ).items()
            ),
        )
        if entry is not None
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": current.isoformat(),
        "overall_status": overall,
        "mode": "evidence_collection_and_shadow_validation",
        "automatic_promotion_allowed": False,
        "worker": worker,
        "universe": universe,
        "factor_ic": factor_ic,
        "nav_observation": nav_observation,
        "decision_score_shadow": decision_score,
        "decision_quality": decision_quality,
        "milestones": [
            {
                "code": "pit_membership_minimum",
                "label": "PIT 成员最低锚点",
                "observed": universe.get("effective_anchor_count"),
                "required": PIT_MINIMUM_EFFECTIVE_ANCHORS,
                "unit": "effective_anchors",
                "progress_percent": universe.get("anchor_progress_percent"),
            },
            {
                "code": "economic_20d_minimum",
                "label": "20 日经济样本最低期数",
                "observed": factor_ic.get("mature_period_count_20d"),
                "required": ECONOMIC_MINIMUM_PERIODS,
                "unit": "mature_periods",
                "progress_percent": factor_ic.get("economic_progress_percent_20d"),
                "theoretical_minimum_trading_days": THEORETICAL_PRIMARY_TRADING_DAYS,
                "theoretical_minimum_months": 17.5,
            },
            {
                "code": "economic_60d_minimum",
                "label": "60 日经济样本最低期数",
                "observed": factor_ic.get("mature_period_count_60d"),
                "required": ECONOMIC_MINIMUM_PERIODS,
                "unit": "mature_periods",
                "progress_percent": factor_ic.get("economic_progress_percent_60d"),
                "theoretical_minimum_trading_days": THEORETICAL_LONG_TRADING_DAYS,
                "theoretical_minimum_months": 19.5,
            },
            {
                "code": "decision_quality_manual_review",
                "label": "决策质量人工复核门槛",
                "observed": decision_quality.get("mature_decision_day_count"),
                "required": decision_quality.get(
                    "minimum_manual_review_mature_decision_days"
                ),
                "unit": "mature_decision_days",
                "progress_percent": decision_quality.get(
                    "maturity_progress_percent"
                ),
            },
        ],
        "blockers": blockers,
        "alerts": alerts,
        "notices": [
            "空值表示尚无可验证证据，不按 0 分处理。",
            "17.5/19.5 个月是理论最短样本窗口，不是到期自动通过；仍需 FDR、样本外一致性和扣费后经济门槛。",
            "所有新模型继续 shadow/fail-closed，任何成熟状态都不允许自动晋级。",
            "blockers 区分三类：blocked_on_time 会随采集自愈；blocked_on_data_source 等待无用，"
            "必须补数据源或改口径；blocked_unclassified 表示原因码尚未归类，不得当作会自愈。",
        ],
    }


__all__ = ["SCHEMA_VERSION", "build_evidence_maturity_status"]
