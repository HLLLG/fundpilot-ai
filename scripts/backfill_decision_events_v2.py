#!/usr/bin/env python3
"""Backfill trace-only decision evidence for reports created before Decision V2.

Safety is deliberately conservative:

* the default mode is a read-only dry run;
* once the storage-owned D2 rollout marker exists, ``--apply`` is explicitly
  refused; the flag remains only so old operational calls fail closed with an
  auditable reason instead of silently changing behavior;
* source reports are read directly with keyset pagination (no list API limits);
* source report JSON is never updated;
* backfilled events are permanently excluded from formal V2 metrics;
* current benchmark mappings, fee settings, portfolio state and trade calendars
  are never consulted while reconstructing historical evidence.

The script currently targets the local SQLite store.  Current schema migrations
activate D2 before any write, so supported use is historical inventory/preview,
not reconstruction of formal evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


DecisionKind = Literal["daily", "discovery"]
_CN_TZ = ZoneInfo("Asia/Shanghai")
_SOURCE_TABLES: dict[DecisionKind, str] = {
    "daily": "reports",
    "discovery": "fund_discovery_reports",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _keyset_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: Sequence[str],
    batch_size: int,
) -> Iterator[sqlite3.Row]:
    """Scan one entire source table without OFFSET or application list caps."""

    if table not in _SOURCE_TABLES.values():
        raise ValueError(f"unsupported source table: {table}")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    selected = ", ".join(columns)
    last_created_at: str | None = None
    last_id: str | None = None
    while True:
        if last_created_at is None:
            rows = connection.execute(
                f"SELECT {selected} FROM {table} "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (batch_size,),
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT {selected} FROM {table} "
                "WHERE created_at > ? OR (created_at = ? AND id > ?) "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (last_created_at, last_created_at, last_id, batch_size),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield row
        last_created_at = str(rows[-1]["created_at"])
        last_id = str(rows[-1]["id"])


def _canonical_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("created_at is missing")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _local_date(canonical_datetime: str) -> str:
    return (
        datetime.fromisoformat(canonical_datetime)
        .astimezone(_CN_TZ)
        .date()
        .isoformat()
    )


def _normalise_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    code = text.zfill(6)
    if len(code) != 6 or code == "000000":
        return None
    return code


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _report_has_v2_contract(report: Mapping[str, Any]) -> bool:
    contract = report.get("decision_contract")
    if isinstance(contract, Mapping) and (
        contract.get("persistence") == "persisted"
        or str(contract.get("schema_version") or "").startswith("decision_contract.")
    ):
        return True
    events = report.get("decision_events")
    return bool(
        isinstance(events, list)
        and any(
            isinstance(event, Mapping)
            and str(event.get("schema_version") or "").startswith("decision_event.v2")
            and not bool(event.get("backfilled") or event.get("is_backfilled"))
            for event in events
        )
    )


def _has_persisted_v2_event(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    report_id: str,
    source_type: DecisionKind,
) -> bool:
    if not _table_exists(connection, "decision_events"):
        return False
    row = connection.execute(
        """
        SELECT 1 FROM decision_events
        WHERE userId = ? AND source_type = ? AND source_report_id = ?
          AND is_backfilled = 0
        LIMIT 1
        """,
        (user_id, source_type, report_id),
    ).fetchone()
    return row is not None


def _latest_daily_report_ids(
    connection: sqlite3.Connection, *, batch_size: int
) -> dict[tuple[int, str], str]:
    """Return the latest report id for each user and China-local decision day."""

    if not _table_exists(connection, "reports"):
        return {}
    latest: dict[tuple[int, str], tuple[datetime, str]] = {}
    for row in _keyset_rows(
        connection,
        table="reports",
        columns=("id", "created_at", "userId"),
        batch_size=batch_size,
    ):
        try:
            canonical = _canonical_datetime(row["created_at"])
            moment = datetime.fromisoformat(canonical)
            key = (int(row["userId"]), _local_date(canonical))
        except (TypeError, ValueError):
            continue
        candidate = (moment, str(row["id"]))
        if key not in latest or candidate > latest[key]:
            latest[key] = candidate
    return {key: value[1] for key, value in latest.items()}


def _historical_facts(report: Mapping[str, Any], kind: DecisionKind) -> Mapping[str, Any]:
    key = "analysis_facts" if kind == "daily" else "discovery_facts"
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


def _historical_position_rows(
    report: Mapping[str, Any], kind: DecisionKind
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract only position evidence already embedded in the historical report."""

    facts = _historical_facts(report, kind)
    if kind == "daily":
        raw_holdings = report.get("holdings")
        if not isinstance(raw_holdings, list) or not raw_holdings:
            raw_holdings = facts.get("holdings")
        source_paths = [
            path
            for path, value in (
                ("report.holdings", report.get("holdings")),
                ("analysis_facts.holdings", facts.get("holdings")),
            )
            if isinstance(value, list) and value
        ]
        rows = raw_holdings if isinstance(raw_holdings, list) else []
    else:
        # Discovery reports historically had no top-level holdings.  Only an
        # embedded point-in-time position snapshot qualifies; current holdings
        # must never be pulled in to fill the gap.
        raw_snapshot = facts.get("portfolio_position_snapshot")
        source_path = "discovery_facts.portfolio_position_snapshot"
        if not isinstance(raw_snapshot, Mapping):
            preflight = facts.get("portfolio_snapshot")
            nested = preflight.get("position_snapshot") if isinstance(preflight, Mapping) else None
            if isinstance(nested, Mapping):
                raw_snapshot = nested
                source_path = "discovery_facts.portfolio_snapshot.position_snapshot"
        rows = raw_snapshot.get("positions") if isinstance(raw_snapshot, Mapping) else []
        source_paths = [source_path] if isinstance(rows, list) and rows else []

    fact_rows = facts.get("holdings") if kind == "daily" else None
    facts_by_code = {
        code: row
        for row in fact_rows or []
        if isinstance(row, Mapping) and (code := _normalise_code(row.get("fund_code")))
    }
    positions: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        code = _normalise_code(raw.get("fund_code"))
        if code is None:
            continue
        fact = facts_by_code.get(code, {})
        market_value = _finite_number(
            raw.get("market_value_yuan")
            or raw.get("holding_amount")
            or raw.get("settled_holding_amount")
            or fact.get("holding_amount")
        )
        shares = _finite_number(
            raw.get("settled_shares")
            or raw.get("holding_shares")
            or fact.get("holding_shares")
        )
        cost_basis = _finite_number(
            raw.get("cost_basis_cny")
            or raw.get("cost_basis_yuan")
            or fact.get("cost_basis_yuan")
        )
        positions.append(
            {
                "fund_code": code,
                "fund_name": str(raw.get("fund_name") or fact.get("fund_name") or "").strip(),
                "settled_shares": shares,
                "shares_quality": "legacy_report_unverified" if shares is not None else "unknown",
                "cost_basis_cny": cost_basis,
                "cost_quality": "legacy_report_unverified" if cost_basis is not None else "unknown",
                "market_value_cny": market_value,
                "market_value_quality": (
                    "legacy_report_value" if market_value is not None else "unknown"
                ),
                "source": "historical_report_payload",
            }
        )
    positions.sort(key=lambda item: str(item.get("fund_code") or ""))
    return positions, source_paths


def _snapshot_id(*, user_id: int, report_id: str, kind: DecisionKind) -> str:
    digest = hashlib.sha256(f"{user_id}:{kind}:{report_id}".encode("utf-8")).hexdigest()
    return f"legacy-{kind}-{digest[:40]}"


def _build_legacy_snapshot(
    report: Mapping[str, Any],
    *,
    kind: DecisionKind,
    user_id: int,
    report_id: str,
    decision_at: str,
) -> dict[str, Any] | None:
    positions, source_paths = _historical_position_rows(report, kind)
    if not positions:
        return None
    total_market_value = sum(
        float(row["market_value_cny"])
        for row in positions
        if row.get("market_value_cny") is not None
    )
    return {
        "schema_version": "portfolio_position_snapshot.legacy_backfill.v1",
        "snapshot_id": _snapshot_id(user_id=user_id, report_id=report_id, kind=kind),
        "account_id": "default",
        "snapshot_at": decision_at,
        "captured_at": decision_at,
        "snapshot_date": _local_date(decision_at),
        "position_as_of": _local_date(decision_at),
        "source_type": "legacy_report_context",
        "source": "legacy_report_context",
        "source_report_id": report_id,
        "source_paths": source_paths,
        "truth_status": "legacy_estimated",
        "authoritative": False,
        "position_complete": False,
        "ledger_version": None,
        "cash_yuan": None,
        "cash": {"balance_cny": None, "status": "unknown", "known": False},
        "total_market_value_yuan": total_market_value if total_market_value else None,
        "positions": positions,
        "completeness": {
            "position_complete": False,
            "position_truth_status": "legacy_estimated",
            "cash_known": False,
            "reason": "historical_report_did_not_freeze_a_complete_position_ledger",
        },
        "legacy": True,
        "backfilled": True,
    }


def _recommendations(report: Mapping[str, Any], kind: DecisionKind) -> list[Mapping[str, Any]]:
    key = "fund_recommendations" if kind == "daily" else "recommendations"
    rows = report.get(key)
    return [row for row in rows or [] if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _action_category(action: str, kind: DecisionKind) -> str:
    if kind == "daily":
        if any(token in action for token in ("清仓", "减仓", "卖出", "赎回", "暂停追涨")):
            return "bearish"
        if any(token in action for token in ("加仓", "定投", "买入", "申购", "分批")):
            return "bullish"
        return "observation" if action else "invalid"
    if any(token in action for token in ("买入", "申购")):
        return "buy"
    if any(token in action for token in ("关注", "观察")):
        return "watch_only"
    if "等待" in action:
        return "conditional_wait"
    return "invalid"


def _build_legacy_events(
    report: Mapping[str, Any],
    *,
    kind: DecisionKind,
    report_id: str,
    decision_at: str,
    snapshot_id: str | None,
    audit_status: str,
) -> list[dict[str, Any]]:
    facts = _historical_facts(report, kind)
    evidence_hash = _payload_hash(facts)
    provider = str(report.get("provider") or "").strip() or "unknown_at_decision_time"
    events: list[dict[str, Any]] = []
    for index, recommendation in enumerate(_recommendations(report, kind)):
        code = _normalise_code(recommendation.get("fund_code"))
        action = str(recommendation.get("action") or "").strip()
        action_category = _action_category(action, kind)
        event = {
            "schema_version": "decision_event.legacy_backfill.v1",
            "event_id": f"backfill:{kind}:{report_id}:{index}:{code or 'invalid'}",
            "event_type": (
                "daily_fund_decision_legacy"
                if kind == "daily"
                else "fund_discovery_decision_legacy"
            ),
            "source_type": kind,
            "decision_kind": kind,
            "source_report_id": report_id,
            "report_id": report_id,
            "recommendation_index": index,
            "decision_at": decision_at,
            "decision_date": _local_date(decision_at),
            "executable_calendar_date": None,
            "execution_policy": "unknown_for_legacy_report",
            "fund_code": code,
            "fund_name": str(recommendation.get("fund_name") or "").strip(),
            "proposed_action": action or None,
            "final_action": action or "unknown",
            "action": action,
            "action_category": action_category,
            "evaluation_class": action_category,
            # Legacy evidence is visible for traceability but never enters the
            # formal V2 denominator, regardless of how actionable its text was.
            "eligible": False,
            "metric_eligible": False,
            "audit_eligible": audit_status != "superseded_same_day",
            "audit_status": audit_status,
            "amount_yuan": _finite_number(
                recommendation.get("amount_yuan")
                or recommendation.get("suggested_amount_yuan")
            ),
            "portfolio_snapshot_id": snapshot_id,
            "position_complete": False,
            "position_truth_status": "legacy_estimated" if snapshot_id else "unknown",
            "benchmark_mapping_id": None,
            "benchmark": {
                "tier": "unavailable",
                "status": "unavailable",
                "formal_excess_eligible": False,
                "reason": "legacy_report_did_not_freeze_point_in_time_benchmark_contract",
                "components": [],
            },
            "fee_model": "unavailable",
            "fee_policy": {
                "status": "unavailable",
                "fee_source": "unavailable",
                "round_trip_fee_percent": None,
                "fee_calculation": None,
                "is_actual_cost": False,
                "reason": "legacy_report_did_not_freeze_point_in_time_fee_assumption",
            },
            "model_version": provider,
            "prompt_version": "unknown_at_decision_time",
            "policy_version": "legacy_unversioned",
            "evidence_hash": evidence_hash,
            "store_authority": "legacy_backfill",
            "is_backfilled": True,
            "backfilled": True,
            "recommendation": dict(recommendation),
        }
        event["payload_hash"] = _payload_hash(event)
        events.append(event)
    return events


def _blank_summary(*, apply: bool, batch_size: int) -> dict[str, Any]:
    return {
        "mode": "apply" if apply else "dry-run",
        "batch_size": batch_size,
        "reports_scanned": 0,
        "daily_reports_scanned": 0,
        "discovery_reports_scanned": 0,
        "reports_skipped_v2": 0,
        "reports_without_recommendations": 0,
        "malformed_reports": 0,
        "snapshots_planned": 0,
        "snapshots_inserted": 0,
        "snapshots_existing": 0,
        "events_planned": 0,
        "events_inserted": 0,
        "events_existing": 0,
        "immutable_conflicts": 0,
        "rollout_status": "not_checked",
        "errors": [],
    }


def _immutable_record_state(
    connection: sqlite3.Connection,
    *,
    table: str,
    user_id: int,
    id_column: str,
    identity: str,
    expected_hash: str,
) -> Literal["missing", "same", "conflict"]:
    if not _table_exists(connection, table):
        return "missing"
    row = connection.execute(
        f"SELECT content_hash FROM {table} "
        f"WHERE userId = ? AND {id_column} = ? LIMIT 1",
        (user_id, identity),
    ).fetchone()
    if row is None:
        return "missing"
    return "same" if str(row["content_hash"]) == expected_hash else "conflict"


def backfill_database(
    sqlite_path: str | Path,
    *,
    apply: bool = False,
    batch_size: int = 200,
) -> dict[str, Any]:
    """Preview legacy evidence; fail closed if apply reaches a D2 store."""

    path = Path(sqlite_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    summary = _blank_summary(apply=apply, batch_size=batch_size)
    try:
        if apply:
            # Schema creation is intentionally confined to explicit apply mode;
            # a default dry run performs no DDL and no DML.
            from app.db_migrations import run_migrations

            run_migrations(connection)
            connection.commit()

        rollout_marker = None
        if _table_exists(connection, "decision_quality_contract_rollouts"):
            rollout_marker = connection.execute(
                "SELECT required_from, marker_hash "
                "FROM decision_quality_contract_rollouts "
                "WHERE contract_name = 'decision_quality_formal_replay'"
            ).fetchone()
        apply_blocked = rollout_marker is not None
        summary["rollout_status"] = (
            "legacy_apply_blocked_after_d2_rollout"
            if apply_blocked
            else "pre_d2_rollout_preview"
        )
        if apply and apply_blocked:
            summary["errors"].append(
                {
                    "table": "decision_quality_contract_rollouts",
                    "report_id": None,
                    "error": "legacy_decision_backfill_apply_blocked_after_d2_rollout",
                    "required_from": str(rollout_marker["required_from"]),
                    "marker_hash": str(rollout_marker["marker_hash"]),
                }
            )

        latest_daily = _latest_daily_report_ids(connection, batch_size=batch_size)
        for kind, table in _SOURCE_TABLES.items():
            if not _table_exists(connection, table):
                continue
            for row in _keyset_rows(
                connection,
                table=table,
                columns=("id", "created_at", "payload", "userId"),
                batch_size=batch_size,
            ):
                summary["reports_scanned"] += 1
                summary[f"{kind}_reports_scanned"] += 1
                report_id = str(row["id"])
                try:
                    user_id = int(row["userId"])
                    payload = json.loads(str(row["payload"]))
                    if not isinstance(payload, dict):
                        raise ValueError("report payload is not a JSON object")
                    decision_at = _canonical_datetime(row["created_at"])
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    summary["malformed_reports"] += 1
                    summary["errors"].append(
                        {"table": table, "report_id": report_id, "error": str(exc)}
                    )
                    continue

                # Row identity/timestamp, not mutable JSON fields, are the source
                # of truth for an old database record.
                payload = dict(payload)
                payload["id"] = report_id
                payload["created_at"] = decision_at
                if _report_has_v2_contract(payload) or _has_persisted_v2_event(
                    connection,
                    user_id=user_id,
                    report_id=report_id,
                    source_type=kind,
                ):
                    summary["reports_skipped_v2"] += 1
                    continue

                recommendations = _recommendations(payload, kind)
                if not recommendations:
                    summary["reports_without_recommendations"] += 1
                    continue

                local_day = _local_date(decision_at)
                audit_status = "legacy_backfilled"
                if kind == "daily" and latest_daily.get((user_id, local_day)) != report_id:
                    audit_status = "superseded_same_day"

                snapshot = _build_legacy_snapshot(
                    payload,
                    kind=kind,
                    user_id=user_id,
                    report_id=report_id,
                    decision_at=decision_at,
                )
                snapshot_id = str(snapshot["snapshot_id"]) if snapshot else None
                events = _build_legacy_events(
                    payload,
                    kind=kind,
                    report_id=report_id,
                    decision_at=decision_at,
                    snapshot_id=snapshot_id,
                    audit_status=audit_status,
                )
                summary["events_planned"] += len(events)
                if snapshot:
                    summary["snapshots_planned"] += 1

                from app.services.decision_repository import (
                    decision_event_content_hash,
                    decision_portfolio_snapshot_content_hash,
                )

                snapshot_state: Literal["missing", "same", "conflict"] = "missing"
                if snapshot:
                    snapshot_state = _immutable_record_state(
                        connection,
                        table="decision_portfolio_snapshots",
                        user_id=user_id,
                        id_column="snapshot_id",
                        identity=snapshot_id or "",
                        expected_hash=decision_portfolio_snapshot_content_hash(snapshot),
                    )
                event_states = {
                    str(event["event_id"]): _immutable_record_state(
                        connection,
                        table="decision_events",
                        user_id=user_id,
                        id_column="event_id",
                        identity=str(event["event_id"]),
                        expected_hash=decision_event_content_hash(event),
                    )
                    for event in events
                }
                if snapshot_state == "same":
                    summary["snapshots_existing"] += 1
                summary["events_existing"] += sum(
                    state == "same" for state in event_states.values()
                )

                conflicts: list[dict[str, Any]] = []
                if snapshot_state == "conflict":
                    conflicts.append(
                        {
                            "record_type": "portfolio_snapshot",
                            "record_id": snapshot_id,
                        }
                    )
                conflicts.extend(
                    {
                        "record_type": "decision_event",
                        "record_id": event_id,
                    }
                    for event_id, state in event_states.items()
                    if state == "conflict"
                )
                if conflicts:
                    summary["immutable_conflicts"] += len(conflicts)
                    summary["errors"].append(
                        {
                            "table": table,
                            "report_id": report_id,
                            "error": "immutable_content_hash_conflict",
                            "records": conflicts,
                        }
                    )
                    # Preserve report-level atomicity: do not insert missing
                    # siblings when any immutable child has diverged.
                    continue

                if not apply or apply_blocked:
                    continue

                try:
                    from app.services.decision_repository import (
                        put_decision_event,
                        put_decision_portfolio_snapshot,
                    )

                    if snapshot:
                        put_decision_portfolio_snapshot(
                            user_id=user_id,
                            snapshot=snapshot,
                            connection=connection,
                        )
                    for event in events:
                        put_decision_event(
                            user_id=user_id,
                            event=event,
                            connection=connection,
                        )
                    connection.commit()
                except Exception as exc:  # noqa: BLE001 - report-scoped rollback and audit
                    connection.rollback()
                    summary["errors"].append(
                        {"table": table, "report_id": report_id, "error": str(exc)}
                    )
                    continue

                if snapshot and snapshot_state == "missing":
                    summary["snapshots_inserted"] += 1
                summary["events_inserted"] += sum(
                    1
                    for event in events
                    if event_states[str(event["event_id"])] == "missing"
                )
        return summary
    finally:
        connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="幂等回填历史日报/荐基决策事件（默认只读 dry-run）"
    )
    parser.add_argument(
        "--sqlite",
        default=str(ROOT / "data" / "app.db"),
        help="SQLite 数据库路径",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="keyset 分页批大小（默认 200）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="旧运维兼容参数；D2 rollout marker 存在时明确拒绝写入",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        summary = backfill_database(
            args.sqlite,
            apply=bool(args.apply),
            batch_size=int(args.batch_size),
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"回填失败: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
