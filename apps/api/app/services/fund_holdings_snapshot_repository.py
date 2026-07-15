"""PIT repository boundary for immutable fund-holdings disclosures.

Compatibility clients must read the append-only store before considering a
live provider.  Historical decisions are store-only: data first observed
today can never be backfilled into an earlier replay merely because the
underlying report had an older publication date.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from app import database
from app.config import get_settings
from app.services.fund_holdings_snapshot import (
    CN_TZ,
    DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS,
    materialize_fund_holdings_snapshot_for_decision,
    resolve_fund_holdings_snapshot,
)

logger = logging.getLogger(__name__)

_refresh_state_guard = threading.Lock()
_refresh_attempt_monotonic: dict[str, float] = {}
_refresh_success_monotonic: dict[str, float] = {}
_refresh_locks: dict[str, threading.Lock] = {}


def resolve_fund_holdings_snapshot_at_decision(
    fund_code: object,
    *,
    decision_at: str | datetime | None = None,
    force_refresh: bool = False,
    allow_live: bool = True,
    refresh_check_ttl_seconds: int | float | None = None,
    refresh_retry_ttl_seconds: int | float | None = None,
) -> dict[str, Any]:
    """Resolve one stored-or-live disclosure at an aware decision clock.

    The returned envelope is intentionally explicit about source and failure.
    ``snapshot`` is either a decision-materialized v1 payload or ``None``.
    Historical decisions and ``allow_live=False`` are store-only.  For a
    current deep-research decision a fresh stored disclosure is returned
    directly, while an aging/stale disclosure is refreshed.  Failed refreshes
    are throttled and retain an explicit stored fallback rather than silently
    pinning the old disclosure forever.
    """

    code = _normalize_fund_code(fund_code)
    decision = _normalize_decision_at(decision_at)
    if code is None:
        return _resolution(
            decision=decision,
            status="invalid",
            reasons=["fund_code_invalid"],
        )

    stored_record: Mapping[str, Any] | None = None
    try:
        stored_record = database.get_latest_fund_holdings_snapshot(
            fund_code=code,
            decision_at=decision,
            qualified_only=True,
        )
    except Exception:
        logger.exception("holdings snapshot store read failed for %s", code)

    stored_snapshot = _record_snapshot(stored_record, expected_code=code)
    stored_code_mismatch = bool(
        isinstance(stored_record, Mapping)
        and _record_snapshot(stored_record) is not None
        and stored_snapshot is None
    )
    stored_materialization_failed = False
    if stored_snapshot is not None:
        try:
            stored_snapshot = materialize_fund_holdings_snapshot_for_decision(
                stored_snapshot,
                decision_at=decision,
            )
        except Exception:
            logger.exception("holdings snapshot store materialization failed for %s", code)
            stored_snapshot = None
            stored_materialization_failed = True
        if stored_snapshot is not None:
            stored_snapshot = _annotate_repository(
                stored_snapshot,
                source="append_only_store",
                live_attempted=False,
                first_observed_at=(stored_record or {}).get("first_observed_at"),
                refresh_reason="not_evaluated",
            )
            if not _is_current_decision(decision):
                return _resolution(
                    decision=decision,
                    status=str(stored_snapshot.get("status") or "unavailable"),
                    snapshot=stored_snapshot,
                    record=stored_record,
                    source="append_only_store",
                    reasons=list(stored_snapshot.get("reason_codes") or []),
                )

    if not _is_current_decision(decision):
        if stored_snapshot is not None:
            return _resolution(
                decision=decision,
                status=str(stored_snapshot.get("status") or "unavailable"),
                snapshot=stored_snapshot,
                record=stored_record,
                source="append_only_store",
                reasons=list(stored_snapshot.get("reason_codes") or []),
            )
        return _resolution(
            decision=decision,
            status="unavailable",
            source="append_only_store",
            reasons=[
                "stored_snapshot_fund_code_mismatch"
                if stored_code_mismatch
                else (
                    "stored_snapshot_materialization_failed"
                    if stored_materialization_failed
                    else "historical_snapshot_not_observed"
                )
            ],
        )

    stored_freshness = _stored_freshness(stored_snapshot)
    check_ttl = _refresh_check_ttl_seconds(refresh_check_ttl_seconds)
    scheduled_refresh_due = _scheduled_refresh_due(
        code,
        stored_record,
        ttl_seconds=check_ttl,
    )
    if (
        stored_snapshot is not None
        and not force_refresh
        and stored_freshness == "fresh"
        and not scheduled_refresh_due
    ):
        stored_snapshot = _annotate_repository(
            stored_snapshot,
            source="append_only_store",
            live_attempted=False,
            first_observed_at=(stored_record or {}).get("first_observed_at"),
            refresh_reason="stored_snapshot_fresh_recently_checked",
        )
        return _resolution(
            decision=decision,
            status=str(stored_snapshot.get("status") or "unavailable"),
            snapshot=stored_snapshot,
            record=stored_record,
            source="append_only_store",
            reasons=list(stored_snapshot.get("reason_codes") or []),
        )

    refresh_reason = (
        "forced_refresh"
        if force_refresh
        else (
            (
                "scheduled_disclosure_recheck"
                if stored_freshness == "fresh" and scheduled_refresh_due
                else f"stored_snapshot_{stored_freshness}"
            )
            if stored_snapshot is not None
            else (
                "stored_snapshot_fund_code_mismatch"
                if stored_code_mismatch
                else (
                    "stored_snapshot_materialization_failed"
                    if stored_materialization_failed
                    else "store_miss"
                )
            )
        )
    )
    if not allow_live:
        if stored_snapshot is not None:
            stored_snapshot = _annotate_repository(
                stored_snapshot,
                source="append_only_store",
                live_attempted=False,
                first_observed_at=(stored_record or {}).get("first_observed_at"),
                refresh_reason=refresh_reason,
                refresh_suppressed_reason="store_only_mode",
            )
            return _resolution(
                decision=decision,
                status=str(stored_snapshot.get("status") or "unavailable"),
                snapshot=stored_snapshot,
                record=stored_record,
                source="append_only_store",
                reasons=list(stored_snapshot.get("reason_codes") or []),
            )
        return _resolution(
            decision=decision,
            status="unavailable",
            source="append_only_store",
            reasons=[
                "stored_snapshot_fund_code_mismatch"
                if stored_code_mismatch
                else (
                    "stored_snapshot_materialization_failed"
                    if stored_materialization_failed
                    else "store_only_snapshot_missing"
                )
            ],
            refresh={
                "reason": refresh_reason,
                "suppressed_reason": "store_only_mode",
                "live_attempted": False,
                "throttled": False,
            },
        )

    ttl = _refresh_ttl_seconds(refresh_retry_ttl_seconds)
    refresh_lock = _refresh_lock(code)
    with refresh_lock:
        if not force_refresh and _refresh_is_throttled(code, ttl):
            # A concurrent waiter must re-read after the leader completed its
            # full fetch+save path.  Reusing the pre-lock miss/old record would
            # incorrectly return unavailable/stale even though a new immutable
            # row has just been committed.
            latest_record, latest_snapshot, latest_mismatch = _read_stored_snapshot(
                code,
                decision=decision,
            )
            fallback_record = latest_record or stored_record
            fallback_snapshot = latest_snapshot or stored_snapshot
            if fallback_snapshot is not None:
                fallback_snapshot = _annotate_repository(
                    fallback_snapshot,
                    source="append_only_store_refresh_throttled",
                    live_attempted=False,
                    first_observed_at=(fallback_record or {}).get("first_observed_at"),
                    refresh_reason=refresh_reason,
                    refresh_throttled=True,
                )
                return _resolution(
                    decision=decision,
                    status=str(fallback_snapshot.get("status") or "unavailable"),
                    snapshot=fallback_snapshot,
                    record=fallback_record,
                    source="append_only_store_refresh_throttled",
                    reasons=list(fallback_snapshot.get("reason_codes") or []),
                )
            return _resolution(
                decision=decision,
                status="unavailable",
                source="none",
                reasons=[
                    "stored_snapshot_fund_code_mismatch"
                    if latest_mismatch or stored_code_mismatch
                    else "live_refresh_throttled"
                ],
                refresh={
                    "reason": refresh_reason,
                    "suppressed_reason": "retry_ttl",
                    "live_attempted": False,
                    "throttled": True,
                },
            )
        _mark_refresh_attempt(code)
        return _resolve_live_refresh(
            code,
            decision=decision,
            stored_record=stored_record,
            stored_snapshot=stored_snapshot,
            refresh_reason=refresh_reason,
        )


def _resolve_live_refresh(
    code: str,
    *,
    decision: datetime,
    stored_record: Mapping[str, Any] | None,
    stored_snapshot: Mapping[str, Any] | None,
    refresh_reason: str,
) -> dict[str, Any]:
    try:
        live = resolve_fund_holdings_snapshot(code, decision_at=decision)
        live = materialize_fund_holdings_snapshot_for_decision(
            live,
            decision_at=decision,
        )
    except Exception as exc:  # one provider failure must not block the report
        logger.exception("holdings snapshot live refresh failed for %s", code)
        live = {
            "status": "unavailable",
            "qualified": False,
            "reason_codes": ["live_snapshot_resolution_error", type(exc).__name__],
            "fund_code": code,
        }
    if _normalize_fund_code(live.get("fund_code")) != code:
        live = {
            "status": "invalid",
            "qualified": False,
            "reason_codes": ["live_snapshot_fund_code_mismatch"],
            "fund_code": code,
        }
    live = _annotate_repository(
        live,
        source="live_resolver",
        live_attempted=True,
        first_observed_at=None,
        refresh_reason=refresh_reason,
    )
    if live.get("qualified") is True:
        persistence_failed = False
        try:
            saved = database.save_fund_holdings_snapshot(live)
            record = saved.get("record") if isinstance(saved, Mapping) else None
            persisted = _record_snapshot(record, expected_code=code)
            if persisted is not None:
                persisted = materialize_fund_holdings_snapshot_for_decision(
                    persisted,
                    decision_at=decision,
                )
                persisted = _annotate_repository(
                    persisted,
                    source="live_resolver_saved",
                    live_attempted=True,
                    first_observed_at=(record or {}).get("first_observed_at"),
                    refresh_reason=refresh_reason,
                )
                _mark_refresh_success(code)
                return _resolution(
                    decision=decision,
                    status="qualified",
                    snapshot=persisted,
                    record=record,
                    source="live_resolver_saved",
                )
            persistence_failed = True
        except Exception:
            # An unpersisted provider frame is not a frozen decision fact.
            logger.exception("holdings snapshot store write failed for %s", code)
            persistence_failed = True
        if persistence_failed:
            if stored_snapshot is not None:
                stored_snapshot = _annotate_repository(
                    stored_snapshot,
                    source="append_only_store_fallback",
                    live_attempted=True,
                    first_observed_at=(stored_record or {}).get("first_observed_at"),
                    persistence_failed=True,
                    live_failure_reasons=["live_snapshot_persistence_failed"],
                    refresh_reason=refresh_reason,
                )
                return _resolution(
                    decision=decision,
                    status=str(stored_snapshot.get("status") or "unavailable"),
                    snapshot=stored_snapshot,
                    record=stored_record,
                    source="append_only_store_fallback",
                    reasons=[
                        *list(stored_snapshot.get("reason_codes") or []),
                        "live_snapshot_persistence_failed",
                    ],
                )
            return _resolution(
                decision=decision,
                status="unavailable",
                source="live_resolver_unpersisted_audit_only",
                reasons=["live_snapshot_persistence_failed"],
                refresh={
                    "reason": refresh_reason,
                    "suppressed_reason": None,
                    "live_attempted": True,
                    "throttled": False,
                    "persistence_failed": True,
                },
            )
        return _resolution(
            decision=decision,
            status="qualified",
            snapshot=live,
            source="live_resolver",
        )

    # A forced current refresh may fail while a valid stored snapshot still
    # exists.  Return it only as an explicit fallback, never silently.
    if stored_snapshot is not None:
        stored_snapshot = _annotate_repository(
            stored_snapshot,
            source="append_only_store_fallback",
            live_attempted=True,
            first_observed_at=(stored_record or {}).get("first_observed_at"),
            live_failure_reasons=list(live.get("reason_codes") or []),
            refresh_reason=refresh_reason,
        )
        return _resolution(
            decision=decision,
            status=str(stored_snapshot.get("status") or "unavailable"),
            snapshot=stored_snapshot,
            record=stored_record,
            source="append_only_store_fallback",
            reasons=list(stored_snapshot.get("reason_codes") or []),
        )
    return _resolution(
        decision=decision,
        status=str(live.get("status") or "unavailable"),
        snapshot=live,
        source="live_resolver",
        reasons=list(live.get("reason_codes") or []),
    )


def _normalize_decision_at(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(CN_TZ)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("decision_at must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    return parsed.astimezone(CN_TZ)


def _normalize_fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,6}(?:\.0+)?", text):
        return str(int(float(text))).zfill(6)
    return None


def _is_current_decision(decision: datetime) -> bool:
    skew = (
        datetime.now(timezone.utc) - decision.astimezone(timezone.utc)
    ).total_seconds()
    return -300 <= skew <= DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS


def _stored_freshness(snapshot: Mapping[str, Any] | None) -> str:
    if not isinstance(snapshot, Mapping):
        return "missing"
    if snapshot.get("qualified") is not True or snapshot.get("status") != "qualified":
        return "invalid"
    freshness = snapshot.get("freshness")
    if isinstance(freshness, Mapping):
        label = str(freshness.get("label") or "").strip().lower()
        if label in {"fresh", "aging", "stale"}:
            return label
    return "invalid"


def _read_stored_snapshot(
    code: str,
    *,
    decision: datetime,
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None, bool]:
    try:
        record = database.get_latest_fund_holdings_snapshot(
            fund_code=code,
            decision_at=decision,
            qualified_only=True,
        )
    except Exception:
        logger.exception("holdings snapshot store re-read failed for %s", code)
        return None, None, False
    snapshot = _record_snapshot(record, expected_code=code)
    mismatch = bool(
        isinstance(record, Mapping)
        and _record_snapshot(record) is not None
        and snapshot is None
    )
    if snapshot is not None:
        try:
            snapshot = materialize_fund_holdings_snapshot_for_decision(
                snapshot,
                decision_at=decision,
            )
        except Exception:
            logger.exception("holdings snapshot store re-materialization failed for %s", code)
            snapshot = None
    return record, snapshot, mismatch


def _refresh_check_ttl_seconds(value: int | float | None) -> float:
    raw = (
        value
        if value is not None
        else get_settings().fund_holdings_refresh_check_ttl_seconds
    )
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 21_600.0
    return max(parsed, 0.0)


def _scheduled_refresh_due(
    code: str,
    record: Mapping[str, Any] | None,
    *,
    ttl_seconds: float,
) -> bool:
    if ttl_seconds <= 0:
        return True
    now = time.monotonic()
    with _refresh_state_guard:
        successful = _refresh_success_monotonic.get(code)
    if successful is not None and now - successful < ttl_seconds:
        return False
    first_observed = _parse_aware_datetime((record or {}).get("first_observed_at"))
    if first_observed is not None:
        age = (datetime.now(timezone.utc) - first_observed.astimezone(timezone.utc)).total_seconds()
        if 0 <= age < ttl_seconds:
            return False
    return True


def _parse_aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _refresh_ttl_seconds(value: int | float | None) -> float:
    raw = (
        value
        if value is not None
        else get_settings().fund_holdings_refresh_retry_ttl_seconds
    )
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 900.0
    return max(parsed, 0.0)


def _refresh_lock(code: str) -> threading.Lock:
    with _refresh_state_guard:
        return _refresh_locks.setdefault(code, threading.Lock())


def _refresh_is_throttled(code: str, ttl_seconds: float) -> bool:
    if ttl_seconds <= 0:
        return False
    with _refresh_state_guard:
        attempted = _refresh_attempt_monotonic.get(code)
    return attempted is not None and time.monotonic() - attempted < ttl_seconds


def _mark_refresh_attempt(code: str) -> None:
    with _refresh_state_guard:
        _refresh_attempt_monotonic[code] = time.monotonic()


def _mark_refresh_success(code: str) -> None:
    with _refresh_state_guard:
        _refresh_success_monotonic[code] = time.monotonic()


def clear_fund_holdings_snapshot_refresh_state() -> None:
    """Reset process-local refresh throttling (tests/maintenance only)."""

    with _refresh_state_guard:
        _refresh_attempt_monotonic.clear()
        _refresh_success_monotonic.clear()
        _refresh_locks.clear()


def _record_snapshot(
    record: Mapping[str, Any] | None,
    *,
    expected_code: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        payload = record.get("payload_json")
    if not isinstance(payload, Mapping):
        return None
    copy = deepcopy(dict(payload))
    if expected_code is not None and _normalize_fund_code(copy.get("fund_code")) != expected_code:
        return None
    return copy


def _annotate_repository(
    snapshot: Mapping[str, Any],
    *,
    source: str,
    live_attempted: bool,
    first_observed_at: object,
    persistence_failed: bool = False,
    live_failure_reasons: list[str] | None = None,
    refresh_reason: str | None = None,
    refresh_throttled: bool = False,
    refresh_suppressed_reason: str | None = None,
) -> dict[str, Any]:
    payload = deepcopy(dict(snapshot))
    audit = payload.get("audit")
    audit = deepcopy(dict(audit)) if isinstance(audit, Mapping) else {}
    audit["snapshot_repository"] = {
        "source": source,
        "live_attempted": live_attempted,
        "persistence_failed": persistence_failed,
        "first_observed_at": (
            str(first_observed_at) if first_observed_at is not None else None
        ),
        "live_failure_reason_codes": list(live_failure_reasons or []),
        "refresh_reason": refresh_reason,
        "refresh_throttled": refresh_throttled,
        "refresh_suppressed_reason": refresh_suppressed_reason,
    }
    payload["audit"] = audit
    return payload


def _resolution(
    *,
    decision: datetime,
    status: str,
    reasons: list[str] | None = None,
    snapshot: Mapping[str, Any] | None = None,
    record: Mapping[str, Any] | None = None,
    source: str = "none",
    refresh: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "qualified": bool(snapshot and snapshot.get("qualified") is True),
        "reason_codes": list(dict.fromkeys(reasons or [])),
        "decision_at": decision.isoformat(),
        "source": source,
        "snapshot": deepcopy(dict(snapshot)) if isinstance(snapshot, Mapping) else None,
        "record": deepcopy(dict(record)) if isinstance(record, Mapping) else None,
    }
    if isinstance(refresh, Mapping):
        payload["refresh"] = deepcopy(dict(refresh))
    elif isinstance(snapshot, Mapping):
        audit = snapshot.get("audit")
        repository = audit.get("snapshot_repository") if isinstance(audit, Mapping) else None
        if isinstance(repository, Mapping):
            payload["refresh"] = {
                "reason": repository.get("refresh_reason"),
                "suppressed_reason": repository.get("refresh_suppressed_reason"),
                "live_attempted": repository.get("live_attempted") is True,
                "throttled": repository.get("refresh_throttled") is True,
            }
    return payload


__all__ = [
    "clear_fund_holdings_snapshot_refresh_state",
    "resolve_fund_holdings_snapshot_at_decision",
]
