"""Read-only operational view of FundPilot's evidence collection maturity.

The projection intentionally combines operational liveness with research
coverage, while keeping the boundaries explicit: a healthy collector is not a
validated model, a zero count is not substituted for missing evidence, and no
state in this module can promote a shadow model into live decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.background_worker import inspect_worker_health
from app.database import list_discovery_reports
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
    digest = build_decision_score_shadow_digest(list_discovery_reports(limit=100))
    artifacts = _nonnegative_int(digest.get("artifact_count")) or 0
    candidates = _nonnegative_int(digest.get("candidate_count")) or 0
    scored = _nonnegative_int(digest.get("scored_count")) or 0
    valid = _nonnegative_int(digest.get("valid_artifact_count")) or 0
    evaluable = _nonnegative_int(digest.get("shadow_evaluable_report_count")) or 0
    status = (
        "collecting"
        if artifacts == 0 or evaluable == 0
        else "attention"
        if valid < artifacts
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
        "missing_component_counts": digest.get("missing_component_counts") or {},
        "latest": digest.get("latest"),
        "automatic_promotion_allowed": False,
    }
    alerts: list[dict[str, str]] = []
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
        component.get("status") in {"collecting", "unavailable"}
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
        "alerts": alerts,
        "notices": [
            "空值表示尚无可验证证据，不按 0 分处理。",
            "17.5/19.5 个月是理论最短样本窗口，不是到期自动通过；仍需 FDR、样本外一致性和扣费后经济门槛。",
            "所有新模型继续 shadow/fail-closed，任何成熟状态都不允许自动晋级。",
        ],
    }


__all__ = ["SCHEMA_VERSION", "build_evidence_maturity_status"]
