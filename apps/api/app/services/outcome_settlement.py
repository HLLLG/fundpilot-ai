"""定时结算正式决策的待观察 T+N 结果。

该服务直接读取持久化 decision_events/outcome_observations，不依赖任何 GET
端点。只重算调度开始时仍处于非终态的 event+horizon；终态证据保持不可变，
并发写入冲突会 fail-closed 抛出异常。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Iterable, Mapping

from app.services.akshare_subprocess import fetch_fund_nav_history
from app.services.benchmark_fee_evaluation import (
    BenchmarkFetcher,
    default_benchmark_fetcher,
)
from app.services.decision_outcome_persistence import (
    OutcomeEvidenceConflict,
    OutcomeEvidencePersistenceError,
    persist_daily_outcome_result,
    persist_discovery_outcome_result,
)
from app.services.decision_repository import _fetchall, _fetchone
from app.services.discovery_outcomes import build_discovery_outcomes
from app.services.recommendation_outcomes import build_recommendation_outcomes
from app.services.trade_calendar_cache import get_trade_date_set

DAILY_HORIZONS = frozenset({1, 5, 20})
DISCOVERY_HORIZONS = frozenset({5, 20, 60})


class OutcomeSettlementError(RuntimeError):
    """Scheduled settlement could not safely complete."""


class OutcomeSettlementConflict(OutcomeSettlementError):
    """New source evidence conflicts with an already frozen terminal result."""


def settle_pending_outcomes(
    *,
    user_ids: Iterable[int] | None = None,
    as_of_date: str | None = None,
    max_reports: int = 500,
    fetch_nav=fetch_fund_nav_history,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    trade_dates: frozenset[str] | None = None,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Evaluate and persist every pending formal V2 horizon.

    The function is idempotent: once all selected horizons are terminal, the next
    run selects nothing. ``user_ids`` is optional so a server cron can process all
    tenants; specifying it is useful for a personal deployment or a scoped retry.
    """
    anchor = (
        _iso_date(as_of_date)
        if as_of_date is not None
        else date.today().isoformat()
    )
    if anchor is None:
        raise ValueError("as_of_date must be an ISO date")
    safe_limit = max(1, min(int(max_reports), 10_000))
    allowed_users = _normalize_user_ids(user_ids)
    targets, orphaned = _load_pending_targets(
        allowed_users=allowed_users,
        as_of_date=anchor,
        max_reports=safe_limit,
        connection_factory=connection_factory,
    )
    if not targets:
        return _summary(anchor=anchor, targets=[], orphaned=orphaned, results=[])

    resolved_trade_dates = (
        trade_dates if trade_dates is not None else get_trade_date_set()
    )
    nav_fetch = _memoized_fetcher(fetch_nav)
    results: list[dict[str, Any]] = []

    from app.request_context import reset_request_user_id, set_request_user_id

    for target in targets:
        user_id = int(target["user_id"])
        source_type = str(target["source_type"])
        report_id = str(target["report_id"])
        pending = target["pending_event_horizons"]
        report = target["report"]
        token = set_request_user_id(user_id)
        try:
            if source_type == "daily":
                result = _settle_daily(
                    report,
                    pending=pending,
                    fetch_nav=nav_fetch,
                    fetch_benchmark=fetch_benchmark,
                    trade_dates=resolved_trade_dates,
                )
                results.append(
                    _result_row(user_id, source_type, report_id, result, pending)
                )
            elif source_type == "discovery":
                for horizon in sorted(_all_horizons(pending)):
                    allowed = {
                        event_id: {horizon}
                        for event_id, horizons in pending.items()
                        if horizon in horizons
                    }
                    if not allowed:
                        continue
                    outcome = build_discovery_outcomes(
                        report,
                        days=horizon,
                        fetch_nav=nav_fetch,
                        fetch_benchmark=fetch_benchmark,
                    )
                    result = persist_discovery_outcome_result(
                        report,
                        outcome,
                        allowed_event_horizons=allowed,
                    )
                    results.append(
                        _result_row(user_id, source_type, report_id, result, allowed)
                    )
        except OutcomeEvidenceConflict as exc:
            raise OutcomeSettlementConflict(
                f"terminal outcome conflict: user={user_id}, source={source_type}, report={report_id}"
            ) from exc
        except OutcomeEvidencePersistenceError as exc:
            raise OutcomeSettlementError(
                f"outcome persistence failed: user={user_id}, source={source_type}, report={report_id}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - one report failure aborts the cron run
            raise OutcomeSettlementError(
                f"outcome evaluation failed: user={user_id}, source={source_type}, report={report_id}"
            ) from exc
        finally:
            reset_request_user_id(token)

    return _summary(anchor=anchor, targets=targets, orphaned=orphaned, results=results)


def _settle_daily(
    report: dict[str, Any],
    *,
    pending: Mapping[str, set[int]],
    fetch_nav,
    fetch_benchmark: BenchmarkFetcher | None,
    trade_dates: frozenset[str] | None,
) -> dict[str, Any]:
    horizons = tuple(sorted(_all_horizons(pending) & DAILY_HORIZONS))
    if not horizons:
        return {"outcome_evidence": {"status": "nothing_to_persist", "attempted_count": 0}}
    outcome = build_recommendation_outcomes(
        report,
        None,
        horizons=horizons,
        fetch_nav=fetch_nav,
        trade_dates=trade_dates,
        fetch_benchmark=fetch_benchmark,
        formal_v2_only=True,
    )
    return persist_daily_outcome_result(
        report,
        outcome,
        allowed_event_horizons=pending,
    )


def _load_pending_targets(
    *,
    allowed_users: set[int] | None,
    as_of_date: str,
    max_reports: int,
    connection_factory: Callable[[], Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if connection_factory is None:
        from app.database import _connect

        connection_factory = _connect
    connection = connection_factory()
    try:
        from app.config import get_settings

        if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
            raise OutcomeSettlementError(
                "结算主证据库不可用，生产 MySQL 配置下拒绝回落 SQLite"
            )
        rows = _fetchall(
            connection,
            """
            SELECT o.userId, o.decision_event_id, o.horizon_trading_days,
                   o.target_date, e.source_type, e.source_report_id,
                   e.payload AS event_payload
            FROM outcome_observations o
            JOIN decision_events e
              ON e.userId = o.userId AND e.event_id = o.decision_event_id
            WHERE o.is_terminal = 0 AND e.eligible = 1 AND e.metric_eligible = 1
              AND e.source_type IN ('daily', 'discovery')
            ORDER BY o.userId, e.source_type, e.source_report_id,
                     o.decision_event_id, o.horizon_trading_days
            """,
        )
        grouped: dict[tuple[int, str, str], dict[str, Any]] = {}
        for row in rows:
            user_id = int(row.get("userId") or 0)
            if user_id <= 0 or (
                allowed_users is not None and user_id not in allowed_users
            ):
                continue
            target_date = _iso_date(row.get("target_date"))
            if target_date is not None and target_date > as_of_date:
                continue
            source_type = str(row.get("source_type") or "")
            horizon = _positive_int(row.get("horizon_trading_days"))
            supported = DAILY_HORIZONS if source_type == "daily" else DISCOVERY_HORIZONS
            report_id = str(row.get("source_report_id") or "").strip()
            event_id = str(row.get("decision_event_id") or "").strip()
            if not report_id or not event_id or horizon not in supported:
                continue
            key = (user_id, source_type, report_id)
            bucket = grouped.setdefault(
                key,
                {"pending": defaultdict(set), "events": {}},
            )
            bucket["pending"][event_id].add(horizon)
            event = _decode_payload(row.get("event_payload"))
            if event is not None:
                bucket["events"][event_id] = event

        targets: list[dict[str, Any]] = []
        orphaned: list[dict[str, Any]] = []
        for (user_id, source_type, report_id), bucket in list(grouped.items())[:max_reports]:
            pending = bucket["pending"]
            table = "reports" if source_type == "daily" else "fund_discovery_reports"
            row = _fetchone(
                connection,
                f"SELECT payload FROM {table} WHERE userId = ? AND id = ?",
                (user_id, report_id),
            )
            report = _decode_payload(row.get("payload") if row else None)
            if report is None:
                frozen_rows = _fetchall(
                    connection,
                    """
                    SELECT payload
                    FROM decision_events
                    WHERE userId = ? AND source_type = ? AND source_report_id = ?
                    ORDER BY decision_at, event_id
                    """,
                    (user_id, source_type, report_id),
                )
                frozen_events = [
                    event
                    for frozen_row in frozen_rows
                    if (
                        event := _decode_payload(frozen_row.get("payload"))
                    ) is not None
                ]
                report = _rebuild_report_from_frozen_events(
                    source_type=source_type,
                    report_id=report_id,
                    events=frozen_events or list(bucket["events"].values()),
                )
            if report is None:
                # Decision events are intentionally immutable and may outlive a
                # user-visible report. A malformed old event must not make every
                # future settlement workflow fail or block other users.
                orphaned.append(
                    {
                        "user_id": user_id,
                        "source_type": source_type,
                        "report_id": report_id,
                        "reason": "source_report_and_frozen_event_unavailable",
                    }
                )
                continue
            targets.append(
                {
                    "user_id": user_id,
                    "source_type": source_type,
                    "report_id": report_id,
                    "pending_event_horizons": {
                        event_id: set(horizons) for event_id, horizons in pending.items()
                    },
                    "report": report,
                }
            )
        return targets, orphaned
    finally:
        connection.close()


def _rebuild_report_from_frozen_events(
    *,
    source_type: str,
    report_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Recreate the minimal evaluator input after a visible report is deleted.

    Formal DecisionEvent v2 already freezes the post-guard recommendation,
    decision time, fee policy and benchmark contract. Reusing that immutable
    payload is more faithful than abandoning pending observations or reading
    today's mutable report context.
    """
    if source_type not in {"daily", "discovery"} or not events:
        return None
    valid: list[dict[str, Any]] = []
    for event in events:
        if (
            str(event.get("schema_version") or "") != "decision_event.v2"
            or str(event.get("source_type") or "") != source_type
            or str(event.get("source_report_id") or event.get("report_id") or "")
            != report_id
            or not isinstance(event.get("recommendation"), dict)
            or not str(event.get("decision_at") or "").strip()
        ):
            continue
        valid.append(dict(event))
    if not valid:
        return None
    valid.sort(
        key=lambda event: (
            _non_negative_int(event.get("recommendation_index")),
            str(event.get("event_id") or ""),
        )
    )
    indexes = [_non_negative_int(event.get("recommendation_index")) for event in valid]
    if indexes != list(range(len(valid))):
        return None
    decision_at = min(str(event["decision_at"]) for event in valid)
    recommendations = [dict(event["recommendation"]) for event in valid]
    report: dict[str, Any] = {
        "id": report_id,
        "created_at": decision_at,
        "provider": str(valid[0].get("model_version") or "unknown"),
        "decision_contract": {
            "schema_version": "decision_contract.v1",
            "persistence": "persisted",
            "store_authority": valid[0].get("store_authority") or "primary",
            "audit_eligible": bool(valid[0].get("audit_eligible", True)),
            "decision_kind": source_type,
            "reconstructed_from_frozen_events": True,
        },
        "decision_events": valid,
    }
    if source_type == "daily":
        report["fund_recommendations"] = recommendations
        report["analysis_facts"] = {}
    else:
        report["recommendations"] = recommendations
        report["discovery_facts"] = {}
    return report


def _memoized_fetcher(fetcher):
    cache: dict[tuple[str, int], Any] = {}

    def fetch(code: str, *, trading_days: int):
        key = (str(code), int(trading_days))
        if key not in cache:
            cache[key] = fetcher(code, trading_days=trading_days)
        return cache[key]

    return fetch


def _result_row(
    user_id: int,
    source_type: str,
    report_id: str,
    result: Mapping[str, Any],
    pending: Mapping[str, set[int]],
) -> dict[str, Any]:
    evidence = result.get("outcome_evidence") or {}
    return {
        "user_id": user_id,
        "source_type": source_type,
        "report_id": report_id,
        "pending_horizon_count": sum(len(values) for values in pending.values()),
        "status": evidence.get("status"),
        "attempted_count": int(evidence.get("attempted_count") or 0),
        "persisted_count": int(evidence.get("persisted_count") or 0),
        "terminal_count": int(evidence.get("terminal_count") or 0),
    }


def _summary(
    *,
    anchor: str,
    targets: list[dict[str, Any]],
    orphaned: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    pending_count = sum(
        len(horizons)
        for target in targets
        for horizons in target["pending_event_horizons"].values()
    )
    return {
        "schema_version": "outcome_settlement.v1",
        "status": "completed_with_orphans" if orphaned else "completed",
        "as_of_date": anchor,
        "report_count": len(targets),
        "pending_horizon_count": pending_count,
        "attempted_count": sum(row["attempted_count"] for row in results),
        "persisted_count": sum(row["persisted_count"] for row in results),
        "terminal_count": sum(row["terminal_count"] for row in results),
        "orphaned_count": len(orphaned),
        "orphaned": orphaned,
        "results": results,
    }


def _decode_payload(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _normalize_user_ids(values: Iterable[int] | None) -> set[int] | None:
    if values is None:
        return None
    result: set[int] = set()
    for value in values:
        parsed = _positive_int(value)
        if parsed is not None:
            result.add(parsed)
    return result


def _all_horizons(mapping: Mapping[str, set[int]]) -> set[int]:
    return {int(value) for values in mapping.values() for value in values}


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


__all__ = [
    "DAILY_HORIZONS",
    "DISCOVERY_HORIZONS",
    "OutcomeSettlementConflict",
    "OutcomeSettlementError",
    "settle_pending_outcomes",
]
