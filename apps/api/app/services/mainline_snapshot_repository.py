"""Append-only persistence for discovery mainline research snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.services.decision_repository import (
    ImmutableRecordConflict,
    canonical_hash,
    list_decision_quality_input_artifacts,
    put_decision_quality_input_artifact,
)
from app.services.mainline_regime import MAINLINE_SNAPSHOT_SCHEMA_VERSION


MAINLINE_SNAPSHOT_ARTIFACT_TYPE = "mainline_daily_snapshot"
MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION = (
    "decision_quality_mainline_snapshot_artifact.v1"
)


def persist_discovery_mainline_snapshot(
    *,
    user_id: int,
    report: Mapping[str, Any],
    store_authority: str,
    report_recorded_at: str | datetime,
    connection: Any,
) -> dict[str, Any] | None:
    facts = report.get("discovery_facts")
    facts_map = facts if isinstance(facts, Mapping) else {}
    snapshot = facts_map.get("mainline_snapshot")
    if not isinstance(snapshot, Mapping) or not snapshot:
        return None
    if snapshot.get("schema_version") != MAINLINE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("mainline snapshot schema_version is unsupported")
    if snapshot.get("automatic_promotion_allowed") is not False:
        raise ValueError("mainline snapshot must disable automatic promotion")
    if snapshot.get("execution_gate_changed") is not False:
        raise ValueError("mainline snapshot must not change execution gates")

    report_id = str(report.get("id") or "").strip()
    if not report_id:
        raise ValueError("mainline snapshot source report id is required")
    decision_at = _aware_text(snapshot.get("decision_at"), "snapshot.decision_at")
    captured_at = _aware_text(snapshot.get("captured_at"), "snapshot.captured_at")
    recorded_at = _aware_text(report_recorded_at, "report_recorded_at")
    if _as_datetime(decision_at) > _as_datetime(captured_at):
        raise ValueError("mainline snapshot cannot be captured before decision_at")
    if _as_datetime(captured_at) > _as_datetime(recorded_at):
        raise ValueError("mainline snapshot cannot be recorded before capture")

    frozen_snapshot = dict(snapshot)
    supplied_hash = str(frozen_snapshot.pop("snapshot_hash", "")).strip()
    expected_hash = canonical_hash(frozen_snapshot)
    if supplied_hash != expected_hash:
        raise ValueError("mainline snapshot_hash mismatch")
    wrapper = {
        "schema_version": MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION,
        "source_report_id": report_id,
        "decision_at": decision_at,
        "captured_at": captured_at,
        "recorded_at": recorded_at,
        "snapshot_hash": supplied_hash,
        "snapshot": dict(snapshot),
        "evaluation_mode": "shadow_research_only",
        "automatic_promotion_allowed": False,
    }
    existing = _existing_for_report(
        user_id=user_id,
        report_id=report_id,
        connection=connection,
    )
    if existing is not None:
        existing_wrapper = _artifact_payload(existing)
        if existing_wrapper.get("snapshot_hash") != supplied_hash:
            raise ImmutableRecordConflict(
                "mainline snapshot report identity already exists with different evidence"
            )
        return existing

    return put_decision_quality_input_artifact(
        user_id=user_id,
        artifact={
            "artifact_type": MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
            "artifact_schema_version": MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION,
            "logical_key": f"mainline_snapshot:{report_id}",
            "source_type": "discovery",
            "source_report_id": report_id,
            "decision_event_id": None,
            "decision_at": decision_at,
            "available_at": captured_at,
            "recorded_at": recorded_at,
            "store_authority": store_authority,
            "audit_eligible": False,
            "artifact": wrapper,
        },
        connection=connection,
    )


def load_mainline_snapshot_for_trade_date(
    *,
    user_id: int,
    trade_date: str | None,
    connection: Any | None = None,
    scan_limit: int = 20,
) -> dict[str, Any] | None:
    """取该用户在 `trade_date` 这一交易日已冻结的主线快照；没有则返回 None。

    这个模块此前**只有写没有读**——荐基落库时写入，然后没人取用。日报要复用主线判断
    就必须走这里，原因是自己重算的代价不可接受：

    * 每个板块一次日线序列请求（`build_sector_position_map_for_opportionities` 默认
      45 秒预算），放到日报请求路径上不现实；
    * 只对"用户持有的那几个板块"算，横截面分位的分母就只有 3~5 个样本——
      `build_mainline_regime_snapshot` 的 docstring 明确警告过这个失真（荐基当初就是
      靠新增 `percentile_position_by_label` 把分母扩到全白名单才修掉的）；
    * 纯缓存零网络路径拿不到基准腿（它靠 `reference_positions` 反推），
      `relative_return_*` 会全空，而相对强度是 regime 打分的核心。

    所以日报只消费当天已经算好的那一份。取不到时上层 fail closed 回退到旧版机会分，
    并如实说明原因，不猜、不用过期快照顶替。
    """
    normalized_date = str(trade_date or "").strip()
    if not normalized_date:
        return None
    try:
        rows = list_decision_quality_input_artifacts(
            user_id=user_id,
            artifact_type=MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
            source_type="discovery",
            limit=max(1, int(scan_limit)),
            connection=connection,
        )
    except Exception:  # noqa: BLE001 - 主线复用是增强项，读不到只回退，不拖垮日报
        return None
    for row in rows:
        wrapper = _artifact_payload(row)
        snapshot = wrapper.get("snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        if str(snapshot.get("effective_trade_date") or "").strip() != normalized_date:
            continue
        if snapshot.get("schema_version") != MAINLINE_SNAPSHOT_SCHEMA_VERSION:
            continue
        return dict(snapshot)
    return None


def _existing_for_report(
    *,
    user_id: int,
    report_id: str,
    connection: Any,
) -> dict[str, Any] | None:
    rows = list_decision_quality_input_artifacts(
        user_id=user_id,
        artifact_type=MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
        source_type="discovery",
        source_report_id=report_id,
        limit=2,
        connection=connection,
    )
    return rows[0] if rows else None


def _artifact_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    envelope = row.get("payload")
    if isinstance(envelope, Mapping):
        artifact = envelope.get("artifact")
        if isinstance(artifact, Mapping):
            return artifact
    return {}


def _aware_text(value: object, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


__all__ = [
    "load_mainline_snapshot_for_trade_date",
    "MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION",
    "MAINLINE_SNAPSHOT_ARTIFACT_TYPE",
    "persist_discovery_mainline_snapshot",
]
