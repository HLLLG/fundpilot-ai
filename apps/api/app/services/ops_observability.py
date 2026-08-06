"""Durable, bounded ops observability store behind ``/admin/ops``.

``performance_metrics`` keeps only in-process aggregates: a restart erases
every counter, and no individual failure ever retains its stack. That is not
enough to act on a user reporting "网站报错了" — support needs the actual
traceback, the route, and when it started.

This module is the durable half of the telemetry story:

* Errors are grouped by a normalized fingerprint. Each group keeps triage
  state (open/resolved) while a bounded ring of occurrences keeps the stack.
* Traffic is aggregated in-process and flushed as one row per minute per
  process, plus one row per route per hour, so trends survive restarts.

Two invariants hold everywhere in this file:

1. Nothing here may raise into a caller. A broken telemetry store must never
   turn a working request into a 500.
2. Nothing here may block the request path on the database. Callers only
   append to bounded in-memory buffers; a single daemon thread persists them.

Privacy: request bodies, query strings, Authorization headers, and database
bind parameters are never recorded. Error ``message``/``stack`` come from the
raised exception, so callers must not interpolate secrets into exception text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.services.performance_metrics import _percentile, normalize_request_path

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "fundpilot.ops.v1"

#: Loggers that must never be captured as error events. Recording an error
#: through this module can itself log, and capturing that would recurse.
OPS_INTERNAL_LOGGER_PREFIXES = (
    "app.services.ops_observability",
    "app.services.ops_error_logging",
    "fundpilot.performance",
)

SOURCE_BACKEND = "backend"
SOURCE_FRONTEND = "frontend"
SOURCE_WORKER = "worker"
_VALID_SOURCES = frozenset({SOURCE_BACKEND, SOURCE_FRONTEND, SOURCE_WORKER})

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_IGNORED = "ignored"
VALID_GROUP_STATUSES = frozenset({STATUS_OPEN, STATUS_RESOLVED, STATUS_IGNORED})

_VALID_LEVELS = frozenset({"warning", "error", "fatal"})

# Column budgets. Values are truncated rather than rejected: a slightly
# clipped stack is still actionable, a dropped event is not.
MAX_MESSAGE_CHARS = 2000
MAX_STACK_CHARS = 20_000
MAX_CONTEXT_CHARS = 8000
MAX_ERROR_TYPE_CHARS = 180
MAX_USER_AGENT_CHARS = 500
MAX_RELEASE_CHARS = 120
MAX_ROUTE_CHARS = 240
MAX_NOTE_CHARS = 500

# Bounded buffers. Overflow drops the oldest entry and increments a counter
# that the panel surfaces, so silent data loss stays visible.
_MAX_QUEUED_EVENTS = 2048
#: Startup can capture errors before the database finishes bootstrapping, so a
#: failed write is retried a few times instead of losing the first failures.
_MAX_EVENT_PERSIST_ATTEMPTS = 3
_MAX_MINUTE_BUCKETS = 240
_MAX_ROUTE_BUCKETS = 200
_MAX_BUCKET_SAMPLES = 1024
_ROUTE_OVERFLOW_KEY = ("OTHER", "/{other}")

_WRITER_POLL_SECONDS = 5.0
_PRUNE_INTERVAL_SECONDS = 1800.0

MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 24 * 7

_state_lock = threading.RLock()
_queued_events: deque[dict[str, Any]] = deque()
_dropped_event_count = 0
_traffic_minutes: dict[str, "_TrafficBucket"] = {}
_route_hours: dict[tuple[str, str, str], "_TrafficBucket"] = {}
_dropped_traffic_count = 0
_writer_thread: threading.Thread | None = None
_writer_wake = threading.Event()
_last_prune_monotonic = 0.0
_persist_failure_count = 0

#: Reentrancy guard. Any capture attempted while this thread is already
#: persisting telemetry is dropped instead of recursing.
_write_guard = threading.local()

_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_HEX_RE = re.compile(r"(?i)\b[0-9a-f]{12,}\b")
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s'\"<>)]+")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUMBER_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._:/@#{}()\[\]<>+-]+")
_STACK_FRAME_RE = re.compile(r"^(?:File \"|at |\S+@)")


@dataclass
class _TrafficBucket:
    """One (minute | route-hour) rollup awaiting a flush."""

    request_count: int = 0
    server_error_count: int = 0
    client_error_count: int = 0
    duration_sum_ms: float = 0.0
    duration_max_ms: float = 0.0
    response_bytes: int = 0
    samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=_MAX_BUCKET_SAMPLES)
    )

    def observe(
        self,
        duration_ms: float,
        *,
        status_code: int,
        response_bytes: int,
    ) -> None:
        resolved = max(0.0, float(duration_ms))
        self.request_count += 1
        self.duration_sum_ms += resolved
        self.duration_max_ms = max(self.duration_max_ms, resolved)
        self.response_bytes += max(0, int(response_bytes))
        self.samples.append(resolved)
        if status_code >= 500:
            self.server_error_count += 1
        elif status_code >= 400:
            self.client_error_count += 1

    def merge_percentiles(self) -> tuple[float | None, float | None, float | None]:
        values = list(self.samples)
        return (
            _rounded(_percentile(values, 0.50)),
            _rounded(_percentile(values, 0.95)),
            _rounded(_percentile(values, 0.99)),
        )


def _rounded(value: float | None) -> float | None:
    return round(float(value), 3) if value is not None else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _bucket_key(moment: datetime, *, seconds: int) -> str:
    """Return a fixed-width UTC instant so string ordering is chronological.

    Bucket keys are compared and range-scanned as strings on both SQLite and
    MySQL, so the width must never vary. ``YYYY-MM-DDTHH:MM:SSZ`` also parses
    directly in the browser via ``new Date(...)``.
    """

    aware = moment.astimezone(timezone.utc).replace(microsecond=0)
    if seconds >= 3600:
        aware = aware.replace(minute=0, second=0)
    else:
        aware = aware.replace(second=0)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _optional_clip(value: object, limit: int) -> str | None:
    text = _clip(value, limit).strip()
    return text or None


def instance_id() -> str:
    """Identify the writing process; traffic rows are per process."""

    raw = f"{socket.gethostname()}:{os.getpid()}"
    return _ASCII_SAFE_RE.sub("_", raw)[:128] or f"pid_{os.getpid()}"


def _release() -> str | None:
    return _optional_clip(os.getenv("FUND_AI_RELEASE"), MAX_RELEASE_CHARS)


def normalize_error_message(message: object) -> str:
    """Strip request-specific detail so equivalent failures share a group.

    ``user 41 not found`` and ``user 92 not found`` are one defect, so ids,
    hashes, URLs, quoted literals, and bare numbers collapse to placeholders.
    """

    text = str(message or "")
    text = _URL_RE.sub("<url>", text)
    text = _UUID_RE.sub("<id>", text)
    text = _HEX_RE.sub("<id>", text)
    text = _QUOTED_RE.sub("<str>", text)
    text = _NUMBER_RE.sub("<n>", text)
    return _WHITESPACE_RE.sub(" ", text).strip()[:300]


def stack_signature(stack: object, *, limit: int = 3) -> str:
    """Summarize the top frames of a Python traceback or a JS stack."""

    if not stack:
        return ""
    frames: list[str] = []
    for raw_line in str(stack).splitlines():
        line = raw_line.strip()
        if not line or not _STACK_FRAME_RE.match(line):
            continue
        normalized = _URL_RE.sub(
            lambda match: match.group(0).split("?", 1)[0],
            line,
        )
        normalized = _UUID_RE.sub("<id>", normalized)
        normalized = _HEX_RE.sub("<id>", normalized)
        normalized = _NUMBER_RE.sub("<n>", normalized)
        frames.append(_WHITESPACE_RE.sub(" ", normalized)[:200])
        if len(frames) >= limit:
            break
    return " | ".join(frames)


def build_fingerprint(
    *,
    source: str,
    error_type: str,
    message: object,
    stack: object = None,
    route: object = None,
) -> str:
    """Group identity for one defect.

    ``route`` participates so the same generic ``TypeError`` on two unrelated
    pages does not collapse into a single unactionable group.
    """

    parts = (
        source,
        error_type,
        normalize_error_message(message),
        stack_signature(stack),
        normalize_request_path(route) if route else "",
    )
    digest = sha256("\x1f".join(parts).encode("utf-8", errors="replace"))
    return digest.hexdigest()[:32]


# ---------------------------------------------------------------------------
# Capture (hot path). Never raises, never touches the database.
# ---------------------------------------------------------------------------


def _capture_blocked() -> bool:
    return bool(getattr(_write_guard, "active", False))


def record_error_event(
    *,
    source: str,
    error_type: str,
    message: object,
    stack: object = None,
    level: str = "error",
    route: object = None,
    method: object = None,
    status_code: object = None,
    request_id: object = None,
    user_id: object = None,
    release: object = None,
    user_agent: object = None,
    context: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> str | None:
    """Queue one error occurrence for durable storage.

    Returns the fingerprint so callers can surface it to the user (a support
    request quoting the fingerprint is instantly greppable), or ``None`` when
    capture is disabled or the event was dropped.
    """

    if _capture_blocked():
        return None
    try:
        if not get_settings().ops_error_capture_enabled:
            return None
        normalized_source = (
            str(source) if source in _VALID_SOURCES else SOURCE_BACKEND
        )
        normalized_level = str(level) if level in _VALID_LEVELS else "error"
        resolved_type = (
            _optional_clip(error_type, MAX_ERROR_TYPE_CHARS) or "UnknownError"
        )
        resolved_message = _clip(message, MAX_MESSAGE_CHARS).strip() or resolved_type
        resolved_stack = _optional_clip(stack, MAX_STACK_CHARS)
        resolved_route = (
            normalize_request_path(route)[:MAX_ROUTE_CHARS] if route else None
        )
        fingerprint = build_fingerprint(
            source=normalized_source,
            error_type=resolved_type,
            message=resolved_message,
            stack=resolved_stack,
            route=resolved_route,
        )
        event = {
            "event_id": uuid4().hex,
            "fingerprint": fingerprint,
            "occurred_at": _iso(occurred_at or _utc_now()),
            "source": normalized_source,
            "level": normalized_level,
            "error_type": resolved_type,
            "message": resolved_message,
            "stack": resolved_stack,
            "route": resolved_route,
            "method": _optional_clip(method, 16),
            "status_code": _coerce_int(status_code),
            "request_id": _optional_clip(request_id, 128),
            "user_id": _coerce_int(user_id),
            "release": _optional_clip(release, MAX_RELEASE_CHARS) or _release(),
            "user_agent": _optional_clip(user_agent, MAX_USER_AGENT_CHARS),
            "context": _encode_context(context),
        }
    except Exception:  # noqa: BLE001 - telemetry must not break the caller.
        return None

    global _dropped_event_count
    with _state_lock:
        if len(_queued_events) >= _MAX_QUEUED_EVENTS:
            _queued_events.popleft()
            _dropped_event_count += 1
        _queued_events.append(event)
    _ensure_writer_thread()
    _writer_wake.set()
    return event["fingerprint"]


def record_request_traffic(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_ms: float,
    response_bytes: int = 0,
    occurred_at: datetime | None = None,
) -> None:
    """Fold one finished request into the pending minute and route rollups."""

    if _capture_blocked():
        return
    try:
        if not get_settings().ops_traffic_capture_enabled:
            return
        moment = occurred_at or _utc_now()
        minute_key = _bucket_key(moment, seconds=60)
        hour_key = _bucket_key(moment, seconds=3600)
        safe_method = _clip(method, 16).upper() or "GET"
        safe_route = _clip(route, 191) or "/"
        resolved_status = int(status_code)
    except Exception:  # noqa: BLE001 - telemetry must not break the caller.
        return

    global _dropped_traffic_count
    with _state_lock:
        minute = _traffic_minutes.get(minute_key)
        if minute is None:
            if len(_traffic_minutes) >= _MAX_MINUTE_BUCKETS:
                _dropped_traffic_count += 1
                return
            minute = _TrafficBucket()
            _traffic_minutes[minute_key] = minute
        minute.observe(
            duration_ms,
            status_code=resolved_status,
            response_bytes=response_bytes,
        )

        route_key = (hour_key, safe_method, safe_route)
        bucket = _route_hours.get(route_key)
        if bucket is None:
            if len(_route_hours) >= _MAX_ROUTE_BUCKETS:
                # Keep the hour observable rather than losing the request:
                # excess cardinality folds into a single overflow series.
                route_key = (hour_key, *_ROUTE_OVERFLOW_KEY)
                bucket = _route_hours.get(route_key)
            if bucket is None:
                bucket = _TrafficBucket()
                _route_hours[route_key] = bucket
        bucket.observe(
            duration_ms,
            status_code=resolved_status,
            response_bytes=response_bytes,
        )


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _encode_context(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    try:
        encoded = json.dumps(
            {str(key): value for key, value in context.items() if value is not None},
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return encoded[:MAX_CONTEXT_CHARS] if encoded != "{}" else None


# ---------------------------------------------------------------------------
# Writer thread. The only place that talks to the database for writes.
# ---------------------------------------------------------------------------


def _ensure_writer_thread() -> None:
    global _writer_thread
    try:
        if not get_settings().ops_writer_thread_enabled:
            return
    except Exception:  # noqa: BLE001 - settings failure must not crash capture.
        return
    with _state_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return
        thread = threading.Thread(
            target=_writer_loop,
            name="fundpilot-ops-writer",
            daemon=True,
        )
        _writer_thread = thread
    thread.start()


def _writer_loop() -> None:
    while True:
        _writer_wake.wait(timeout=_WRITER_POLL_SECONDS)
        _writer_wake.clear()
        try:
            _drain_once()
        except Exception:  # noqa: BLE001 - a writer thread must never die.
            pass


def flush_ops_writes(*, force_traffic: bool = True) -> None:
    """Persist everything pending, synchronously, in the calling thread.

    Used by tests (which disable the writer thread) and by shutdown so the
    final partial minute is not lost.
    """

    _drain_once(force_traffic=force_traffic)


def _drain_once(*, force_traffic: bool = False) -> None:
    if _capture_blocked():
        return
    _write_guard.active = True
    try:
        events = _take_queued_events()
        if events:
            _persist_error_events(events)
        buckets = _take_flushable_traffic(force=force_traffic)
        if buckets[0] or buckets[1]:
            _persist_traffic(*buckets)
        _maybe_prune()
    finally:
        _write_guard.active = False


def _take_queued_events() -> list[dict[str, Any]]:
    with _state_lock:
        if not _queued_events:
            return []
        events = list(_queued_events)
        _queued_events.clear()
    return events


def _take_flushable_traffic(
    *,
    force: bool,
) -> tuple[dict[str, _TrafficBucket], dict[tuple[str, str, str], _TrafficBucket]]:
    """Detach buckets whose window has closed (or everything, when forced)."""

    now = _utc_now()
    current_minute = _bucket_key(now, seconds=60)
    current_hour = _bucket_key(now, seconds=3600)
    with _state_lock:
        minute_keys = [
            key
            for key in _traffic_minutes
            if force or key < current_minute
        ]
        minutes = {key: _traffic_minutes.pop(key) for key in minute_keys}
        route_keys = [
            key
            for key in _route_hours
            # The in-progress hour is flushed early on purpose: waiting an hour
            # would leave the route table empty for a freshly started process.
            if force or key[0] < current_hour or minutes
        ]
        routes = {key: _route_hours.pop(key) for key in route_keys}
    return minutes, routes


def _requeue_traffic(
    minutes: dict[str, _TrafficBucket],
    routes: dict[tuple[str, str, str], _TrafficBucket],
) -> None:
    """Return detached buckets after a failed flush, if room remains."""

    with _state_lock:
        for key, bucket in minutes.items():
            if key in _traffic_minutes or len(_traffic_minutes) >= _MAX_MINUTE_BUCKETS:
                continue
            _traffic_minutes[key] = bucket
        for route_key, bucket in routes.items():
            if route_key in _route_hours or len(_route_hours) >= _MAX_ROUTE_BUCKETS:
                continue
            _route_hours[route_key] = bucket


# ---------------------------------------------------------------------------
# Persistence. Cross-dialect upserts use INSERT OR IGNORE + additive UPDATE:
# SQLite has no ON DUPLICATE KEY and MySQL has no ON CONFLICT, and the seed
# row is written with zero counters so a lost insert race cannot double count.
# ---------------------------------------------------------------------------


def _persist_error_events(events: list[dict[str, Any]]) -> None:
    from app.database import _connect

    global _persist_failure_count
    try:
        per_minute_cap = max(
            1,
            int(get_settings().ops_error_events_per_fingerprint_per_minute),
        )
    except Exception:  # noqa: BLE001
        per_minute_cap = 20

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event["fingerprint"]), []).append(event)

    try:
        with _connect() as connection:
            for fingerprint, group_events in grouped.items():
                newest = max(group_events, key=lambda item: str(item["occurred_at"]))
                oldest = min(group_events, key=lambda item: str(item["occurred_at"]))
                _upsert_error_group(
                    connection,
                    fingerprint=fingerprint,
                    newest=newest,
                    first_seen_at=str(oldest["occurred_at"]),
                    observed_count=len(group_events),
                )
                for event in _sample_events(group_events, cap=per_minute_cap):
                    _insert_error_event(connection, event)
    except Exception:  # noqa: BLE001 - never propagate into the caller.
        with _state_lock:
            _persist_failure_count += 1
        _requeue_events(events)


def _requeue_events(events: list[dict[str, Any]]) -> None:
    """Return events after a failed write, giving up after a few attempts."""

    global _dropped_event_count
    with _state_lock:
        for event in events:
            attempts = _as_int(event.get("_attempts")) + 1
            if attempts >= _MAX_EVENT_PERSIST_ATTEMPTS:
                _dropped_event_count += 1
                continue
            event["_attempts"] = attempts
            if len(_queued_events) >= _MAX_QUEUED_EVENTS:
                _queued_events.popleft()
                _dropped_event_count += 1
            _queued_events.append(event)


def _sample_events(
    events: list[dict[str, Any]],
    *,
    cap: int,
) -> list[dict[str, Any]]:
    """Keep at most ``cap`` detail rows per fingerprint per minute.

    A tight retry loop can emit thousands of identical failures. The group's
    ``event_count`` still records every occurrence, so the panel's count stays
    truthful while stored stacks stay bounded.
    """

    kept: list[dict[str, Any]] = []
    per_minute: dict[str, int] = {}
    for event in sorted(events, key=lambda item: str(item["occurred_at"])):
        minute = str(event["occurred_at"])[:16]
        seen = per_minute.get(minute, 0)
        if seen >= cap:
            continue
        per_minute[minute] = seen + 1
        kept.append(event)
    return kept


def _upsert_error_group(
    connection: Any,
    *,
    fingerprint: str,
    newest: dict[str, Any],
    first_seen_at: str,
    observed_count: int,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO ops_error_groups (
            fingerprint, source, level, error_type, message, route,
            first_seen_at, last_seen_at, event_count, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            fingerprint,
            newest["source"],
            newest["level"],
            newest["error_type"],
            newest["message"],
            newest["route"],
            first_seen_at,
            newest["occurred_at"],
            STATUS_OPEN,
        ),
    )
    # ``resolved_at``/``resolved_by`` are assigned before ``status`` on
    # purpose: MySQL evaluates SET left to right using already-updated values,
    # so reading the old status in their CASE requires them to come first.
    connection.execute(
        """
        UPDATE ops_error_groups
        SET event_count = event_count + ?,
            level = ?,
            message = ?,
            route = ?,
            last_seen_at = CASE
                WHEN last_seen_at < ? THEN ? ELSE last_seen_at
            END,
            first_seen_at = CASE
                WHEN first_seen_at > ? THEN ? ELSE first_seen_at
            END,
            resolved_at = CASE WHEN status = ? THEN NULL ELSE resolved_at END,
            resolved_by = CASE WHEN status = ? THEN NULL ELSE resolved_by END,
            status = CASE WHEN status = ? THEN ? ELSE status END
        WHERE fingerprint = ?
        """,
        (
            int(observed_count),
            newest["level"],
            newest["message"],
            newest["route"],
            newest["occurred_at"],
            newest["occurred_at"],
            first_seen_at,
            first_seen_at,
            STATUS_RESOLVED,
            STATUS_RESOLVED,
            STATUS_RESOLVED,
            STATUS_OPEN,
            fingerprint,
        ),
    )


def _insert_error_event(connection: Any, event: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO ops_error_events (
            event_id, fingerprint, occurred_at, source, level, error_type,
            message, stack, route, method, status_code, request_id, userId,
            release_tag, user_agent, context
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["fingerprint"],
            event["occurred_at"],
            event["source"],
            event["level"],
            event["error_type"],
            event["message"],
            event["stack"],
            event["route"],
            event["method"],
            event["status_code"],
            event["request_id"],
            event["user_id"],
            event["release"],
            event["user_agent"],
            event["context"],
        ),
    )


def _persist_traffic(
    minutes: dict[str, _TrafficBucket],
    routes: dict[tuple[str, str, str], _TrafficBucket],
) -> None:
    from app.database import _connect

    global _persist_failure_count
    node = instance_id()
    try:
        with _connect() as connection:
            for bucket_start, bucket in minutes.items():
                _upsert_traffic_minute(
                    connection,
                    bucket_start=bucket_start,
                    node=node,
                    bucket=bucket,
                )
            for (bucket_start, method, route), bucket in routes.items():
                _upsert_route_hour(
                    connection,
                    bucket_start=bucket_start,
                    node=node,
                    method=method,
                    route=route,
                    bucket=bucket,
                )
    except Exception:  # noqa: BLE001 - retry on the next tick instead.
        with _state_lock:
            _persist_failure_count += 1
        _requeue_traffic(minutes, routes)


def _upsert_traffic_minute(
    connection: Any,
    *,
    bucket_start: str,
    node: str,
    bucket: _TrafficBucket,
) -> None:
    p50, p95, p99 = bucket.merge_percentiles()
    connection.execute(
        """
        INSERT OR IGNORE INTO ops_traffic_minutes (
            bucket_start, instance_id, request_count, server_error_count,
            client_error_count, duration_sum_ms, duration_max_ms,
            p50_ms, p95_ms, p99_ms, response_bytes
        ) VALUES (?, ?, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0)
        """,
        (bucket_start, node),
    )
    # A bucket is normally flushed once, so its percentiles are exact. Repeat
    # flushes of the same minute keep the worst observed value: merging true
    # percentiles would need per-request samples this store deliberately drops.
    connection.execute(
        """
        UPDATE ops_traffic_minutes
        SET request_count = request_count + ?,
            server_error_count = server_error_count + ?,
            client_error_count = client_error_count + ?,
            duration_sum_ms = duration_sum_ms + ?,
            duration_max_ms = CASE
                WHEN duration_max_ms < ? THEN ? ELSE duration_max_ms
            END,
            p50_ms = CASE WHEN p50_ms IS NULL OR p50_ms < ? THEN ? ELSE p50_ms END,
            p95_ms = CASE WHEN p95_ms IS NULL OR p95_ms < ? THEN ? ELSE p95_ms END,
            p99_ms = CASE WHEN p99_ms IS NULL OR p99_ms < ? THEN ? ELSE p99_ms END,
            response_bytes = response_bytes + ?
        WHERE bucket_start = ? AND instance_id = ?
        """,
        (
            bucket.request_count,
            bucket.server_error_count,
            bucket.client_error_count,
            round(bucket.duration_sum_ms, 3),
            round(bucket.duration_max_ms, 3),
            round(bucket.duration_max_ms, 3),
            p50,
            p50,
            p95,
            p95,
            p99,
            p99,
            bucket.response_bytes,
            bucket_start,
            node,
        ),
    )


def _upsert_route_hour(
    connection: Any,
    *,
    bucket_start: str,
    node: str,
    method: str,
    route: str,
    bucket: _TrafficBucket,
) -> None:
    _, p95, _unused = bucket.merge_percentiles()
    connection.execute(
        """
        INSERT OR IGNORE INTO ops_route_hours (
            bucket_start, instance_id, method, route, request_count,
            server_error_count, client_error_count, duration_sum_ms,
            duration_max_ms, p95_ms
        ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, NULL)
        """,
        (bucket_start, node, method, route),
    )
    connection.execute(
        """
        UPDATE ops_route_hours
        SET request_count = request_count + ?,
            server_error_count = server_error_count + ?,
            client_error_count = client_error_count + ?,
            duration_sum_ms = duration_sum_ms + ?,
            duration_max_ms = CASE
                WHEN duration_max_ms < ? THEN ? ELSE duration_max_ms
            END,
            p95_ms = CASE WHEN p95_ms IS NULL OR p95_ms < ? THEN ? ELSE p95_ms END
        WHERE bucket_start = ?
          AND instance_id = ?
          AND method = ?
          AND route = ?
        """,
        (
            bucket.request_count,
            bucket.server_error_count,
            bucket.client_error_count,
            round(bucket.duration_sum_ms, 3),
            round(bucket.duration_max_ms, 3),
            round(bucket.duration_max_ms, 3),
            p95,
            p95,
            bucket_start,
            node,
            method,
            route,
        ),
    )


def _maybe_prune() -> None:
    global _last_prune_monotonic
    now = time.monotonic()
    with _state_lock:
        if _last_prune_monotonic and now - _last_prune_monotonic < _PRUNE_INTERVAL_SECONDS:
            return
        _last_prune_monotonic = now
    prune_ops_data()


def prune_ops_data(*, now: datetime | None = None) -> dict[str, int]:
    """Drop rows past their retention window. Safe to call at any time."""

    from app.database import _connect

    moment = now or _utc_now()
    settings = get_settings()
    error_cutoff = _iso(
        moment - timedelta(days=max(1, int(settings.ops_error_retention_days)))
    )
    traffic_cutoff = _bucket_key(
        moment - timedelta(days=max(1, int(settings.ops_traffic_retention_days))),
        seconds=60,
    )
    removed = {"events": 0, "groups": 0, "traffic_minutes": 0, "route_hours": 0}
    try:
        with _connect() as connection:
            removed["events"] = _row_count(
                connection.execute(
                    "DELETE FROM ops_error_events WHERE occurred_at < ?",
                    (error_cutoff,),
                )
            )
            # Only groups with no surviving evidence are removed; a resolved
            # group with recent occurrences stays for regression tracking.
            removed["groups"] = _row_count(
                connection.execute(
                    """
                    DELETE FROM ops_error_groups
                    WHERE last_seen_at < ?
                      AND fingerprint NOT IN (
                          SELECT fingerprint FROM ops_error_events
                      )
                    """,
                    (error_cutoff,),
                )
            )
            removed["traffic_minutes"] = _row_count(
                connection.execute(
                    "DELETE FROM ops_traffic_minutes WHERE bucket_start < ?",
                    (traffic_cutoff,),
                )
            )
            removed["route_hours"] = _row_count(
                connection.execute(
                    "DELETE FROM ops_route_hours WHERE bucket_start < ?",
                    (traffic_cutoff,),
                )
            )
    except Exception:  # noqa: BLE001 - retention is best effort.
        return removed
    return removed


def _row_count(cursor: Any) -> int:
    try:
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Read side. Everything the admin panel renders.
# ---------------------------------------------------------------------------


def _as_int(value: object) -> int:
    """Coerce a driver value to int (MySQL returns Decimal from SUM())."""

    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def _clamp_hours(hours: object) -> int:
    try:
        resolved = int(hours)
    except (TypeError, ValueError):
        resolved = 24
    return max(MIN_WINDOW_HOURS, min(MAX_WINDOW_HOURS, resolved))


def _series_bucket_seconds(hours: int) -> int:
    """Keep any window between roughly 60 and 200 plotted points."""

    if hours <= 2:
        return 60
    if hours <= 12:
        return 300
    if hours <= 48:
        return 900
    return 3600


def _parse_bucket_key(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _floor_to_bucket(moment: datetime, *, seconds: int) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


def _sanitize_like_term(term: object) -> str:
    """Drop LIKE wildcards instead of escaping them.

    SQLite needs an explicit ESCAPE clause while MySQL defaults to backslash,
    so removing the two metacharacters keeps one portable statement.
    """

    return _clip(term, 120).replace("%", "").replace("_", "").strip()


def ops_overview(*, hours: object = 24, now: datetime | None = None) -> dict[str, Any]:
    """Traffic, latency, and error headline for the requested window."""

    from app.database import _connect

    window_hours = _clamp_hours(hours)
    moment = now or _utc_now()
    bucket_seconds = _series_bucket_seconds(window_hours)
    window_end = _floor_to_bucket(moment, seconds=bucket_seconds) + timedelta(
        seconds=bucket_seconds
    )
    window_start = window_end - timedelta(hours=window_hours)
    minute_from = _bucket_key(window_start, seconds=60)
    minute_to = _bucket_key(window_end, seconds=60)
    error_from = _iso(window_start)

    series: list[dict[str, Any]] = []
    totals = {
        "request_count": 0,
        "server_error_count": 0,
        "client_error_count": 0,
        "duration_sum_ms": 0.0,
        "duration_max_ms": 0.0,
        "p95_ms": None,
        "p99_ms": None,
        "response_bytes": 0,
    }
    errors: dict[str, Any] = {
        "event_count": 0,
        "frontend_event_count": 0,
        "backend_event_count": 0,
        "group_count": 0,
        "open_group_count": 0,
        "new_group_count": 0,
    }
    top_error_groups: list[dict[str, Any]] = []
    top_routes: list[dict[str, Any]] = []
    available = True

    try:
        with _connect() as connection:
            folded = _fold_traffic_series(
                connection,
                minute_from=minute_from,
                minute_to=minute_to,
                bucket_seconds=bucket_seconds,
            )
            series = _densify_series(
                folded,
                window_start=window_start,
                window_end=window_end,
                bucket_seconds=bucket_seconds,
            )
            for point in series:
                totals["request_count"] += point["request_count"]
                totals["server_error_count"] += point["server_error_count"]
                totals["client_error_count"] += point["client_error_count"]
                totals["duration_sum_ms"] += point["_duration_sum_ms"]
                totals["response_bytes"] += point["response_bytes"]
                totals["duration_max_ms"] = max(
                    totals["duration_max_ms"],
                    point["_duration_max_ms"],
                )
                for key in ("p95_ms", "p99_ms"):
                    candidate = point.get(key)
                    if candidate is not None:
                        current = totals[key]
                        totals[key] = (
                            candidate if current is None else max(current, candidate)
                        )
            errors = _error_headline(
                connection,
                error_from=error_from,
                window_start=window_start,
            )
            top_error_groups = _top_error_groups(
                connection,
                error_from=error_from,
                limit=5,
            )
            top_routes = _top_routes(
                connection,
                # Hour buckets are matched inclusively at both ends: any hour
                # that overlaps the window counts. An exclusive upper bound
                # would silently drop the hour currently in progress.
                hour_from=_bucket_key(window_start, seconds=3600),
                hour_through=_bucket_key(window_end, seconds=3600),
                limit=12,
            )
    except Exception:  # noqa: BLE001 - the panel degrades instead of 500ing.
        logger.warning("ops overview query failed", exc_info=True)
        available = False

    request_count = totals["request_count"]
    mean_ms = (
        round(totals["duration_sum_ms"] / request_count, 3) if request_count else None
    )
    error_rate = (
        round(totals["server_error_count"] / request_count * 100.0, 3)
        if request_count
        else 0.0
    )
    for point in series:
        point.pop("_duration_sum_ms", None)
        point.pop("_duration_max_ms", None)

    return {
        "contract_version": CONTRACT_VERSION,
        "available": available,
        "generated_at": _iso(moment),
        "window": {
            "hours": window_hours,
            "start": _iso(window_start),
            "end": _iso(window_end),
            "bucket_seconds": bucket_seconds,
        },
        "totals": {
            "request_count": request_count,
            "server_error_count": totals["server_error_count"],
            "client_error_count": totals["client_error_count"],
            "server_error_rate_percent": error_rate,
            "mean_ms": mean_ms,
            "p95_ms": totals["p95_ms"],
            "p99_ms": totals["p99_ms"],
            "max_ms": round(totals["duration_max_ms"], 3) or None,
            "response_bytes": totals["response_bytes"],
            "requests_per_minute": (
                round(request_count / (window_hours * 60.0), 3)
                if request_count
                else 0.0
            ),
        },
        "series": series,
        "errors": errors,
        "top_error_groups": top_error_groups,
        "top_routes": top_routes,
        "capture": ops_capture_state(),
        "notes": {
            "percentile_basis": (
                "p95/p99 是各进程各分钟桶内的精确分位数；跨桶与跨进程聚合时取最大值"
                "（保守上界）。mean 由 sum/count 精确得出。"
            ),
            "privacy": "不记录请求体、查询参数、Authorization 或数据库绑定参数。",
        },
    }


def _fold_traffic_series(
    connection: Any,
    *,
    minute_from: str,
    minute_to: str,
    bucket_seconds: int,
) -> dict[datetime, dict[str, Any]]:
    """Collapse per-process minute rows into the plotted bucket size."""

    rows = connection.execute(
        """
        SELECT bucket_start,
               SUM(request_count) AS request_count,
               SUM(server_error_count) AS server_error_count,
               SUM(client_error_count) AS client_error_count,
               SUM(duration_sum_ms) AS duration_sum_ms,
               MAX(duration_max_ms) AS duration_max_ms,
               MAX(p95_ms) AS p95_ms,
               MAX(p99_ms) AS p99_ms,
               SUM(response_bytes) AS response_bytes
        FROM ops_traffic_minutes
        WHERE bucket_start >= ? AND bucket_start < ?
        GROUP BY bucket_start
        ORDER BY bucket_start
        """,
        (minute_from, minute_to),
    ).fetchall()

    folded: dict[datetime, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        minute = _parse_bucket_key(row.get("bucket_start"))
        if minute is None:
            continue
        key = _floor_to_bucket(minute, seconds=bucket_seconds)
        entry = folded.setdefault(
            key,
            {
                "request_count": 0,
                "server_error_count": 0,
                "client_error_count": 0,
                "_duration_sum_ms": 0.0,
                "_duration_max_ms": 0.0,
                "p95_ms": None,
                "p99_ms": None,
                "response_bytes": 0,
            },
        )
        entry["request_count"] += _as_int(row.get("request_count"))
        entry["server_error_count"] += _as_int(row.get("server_error_count"))
        entry["client_error_count"] += _as_int(row.get("client_error_count"))
        entry["_duration_sum_ms"] += _as_float(row.get("duration_sum_ms")) or 0.0
        entry["_duration_max_ms"] = max(
            entry["_duration_max_ms"],
            _as_float(row.get("duration_max_ms")) or 0.0,
        )
        entry["response_bytes"] += _as_int(row.get("response_bytes"))
        for key_name in ("p95_ms", "p99_ms"):
            candidate = _as_float(row.get(key_name))
            if candidate is None:
                continue
            current = entry[key_name]
            entry[key_name] = candidate if current is None else max(current, candidate)
    return folded


def _densify_series(
    folded: dict[datetime, dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
) -> list[dict[str, Any]]:
    """Emit every bucket in the window, including the empty ones.

    Skipping gaps would make an outage look like a flat line instead of a
    hole, which is exactly the signal an operator is looking for.
    """

    series: list[dict[str, Any]] = []
    cursor = _floor_to_bucket(window_start, seconds=bucket_seconds)
    step = timedelta(seconds=bucket_seconds)
    while cursor < window_end:
        entry = folded.get(cursor)
        request_count = entry["request_count"] if entry else 0
        duration_sum = entry["_duration_sum_ms"] if entry else 0.0
        series.append(
            {
                "bucket_start": _bucket_key(cursor, seconds=bucket_seconds),
                "request_count": request_count,
                "server_error_count": entry["server_error_count"] if entry else 0,
                "client_error_count": entry["client_error_count"] if entry else 0,
                "mean_ms": (
                    round(duration_sum / request_count, 3) if request_count else None
                ),
                "p95_ms": entry["p95_ms"] if entry else None,
                "p99_ms": entry["p99_ms"] if entry else None,
                "response_bytes": entry["response_bytes"] if entry else 0,
                "_duration_sum_ms": duration_sum,
                "_duration_max_ms": entry["_duration_max_ms"] if entry else 0.0,
            }
        )
        cursor += step
    return series


def _error_headline(
    connection: Any,
    *,
    error_from: str,
    window_start: datetime,
) -> dict[str, Any]:
    by_source_rows = connection.execute(
        """
        SELECT source, COUNT(*) AS event_count
        FROM ops_error_events
        WHERE occurred_at >= ?
        GROUP BY source
        """,
        (error_from,),
    ).fetchall()
    per_source = {
        str(dict(raw).get("source") or ""): _as_int(dict(raw).get("event_count"))
        for raw in by_source_rows
    }
    group_row = dict(
        connection.execute(
            """
            SELECT COUNT(*) AS group_count,
                   SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_count
            FROM ops_error_groups
            WHERE last_seen_at >= ?
            """,
            (STATUS_OPEN, error_from, error_from),
        ).fetchone()
        or {}
    )
    open_total = dict(
        connection.execute(
            "SELECT COUNT(*) AS open_total FROM ops_error_groups WHERE status = ?",
            (STATUS_OPEN,),
        ).fetchone()
        or {}
    )
    return {
        "event_count": sum(per_source.values()),
        "frontend_event_count": per_source.get(SOURCE_FRONTEND, 0),
        "backend_event_count": per_source.get(SOURCE_BACKEND, 0)
        + per_source.get(SOURCE_WORKER, 0),
        "group_count": _as_int(group_row.get("group_count")),
        "open_group_count": _as_int(group_row.get("open_count")),
        "new_group_count": _as_int(group_row.get("new_count")),
        "open_group_count_all_time": _as_int(open_total.get("open_total")),
        "window_start": _iso(window_start),
    }


def _top_error_groups(
    connection: Any,
    *,
    error_from: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT e.fingerprint AS fingerprint,
               COUNT(*) AS window_event_count,
               COUNT(DISTINCT e.userId) AS affected_user_count,
               MAX(e.occurred_at) AS last_seen_at,
               g.event_count AS event_count,
               g.first_seen_at AS first_seen_at,
               g.error_type AS error_type,
               g.message AS message,
               g.source AS source,
               g.level AS level,
               g.route AS route,
               g.status AS status
        FROM ops_error_events e
        LEFT JOIN ops_error_groups g ON g.fingerprint = e.fingerprint
        WHERE e.occurred_at >= ?
        GROUP BY e.fingerprint, g.event_count, g.first_seen_at, g.error_type,
                 g.message, g.source, g.level, g.route, g.status
        ORDER BY window_event_count DESC, last_seen_at DESC
        LIMIT ?
        """,
        (error_from, int(limit)),
    ).fetchall()
    return [_error_group_summary(dict(raw)) for raw in rows]


def _top_routes(
    connection: Any,
    *,
    hour_from: str,
    hour_through: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT method,
               route,
               SUM(request_count) AS request_count,
               SUM(server_error_count) AS server_error_count,
               SUM(client_error_count) AS client_error_count,
               SUM(duration_sum_ms) AS duration_sum_ms,
               MAX(duration_max_ms) AS duration_max_ms,
               MAX(p95_ms) AS p95_ms
        FROM ops_route_hours
        WHERE bucket_start >= ? AND bucket_start <= ?
        GROUP BY method, route
        ORDER BY request_count DESC
        LIMIT ?
        """,
        (hour_from, hour_through, int(limit)),
    ).fetchall()
    routes: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        request_count = _as_int(row.get("request_count"))
        duration_sum = _as_float(row.get("duration_sum_ms")) or 0.0
        server_errors = _as_int(row.get("server_error_count"))
        routes.append(
            {
                "method": str(row.get("method") or ""),
                "route": str(row.get("route") or ""),
                "request_count": request_count,
                "server_error_count": server_errors,
                "client_error_count": _as_int(row.get("client_error_count")),
                "server_error_rate_percent": (
                    round(server_errors / request_count * 100.0, 3)
                    if request_count
                    else 0.0
                ),
                "mean_ms": (
                    round(duration_sum / request_count, 3) if request_count else None
                ),
                "p95_ms": _as_float(row.get("p95_ms")),
                "max_ms": _as_float(row.get("duration_max_ms")),
            }
        )
    routes.sort(key=lambda item: (-(item["p95_ms"] or 0.0), item["route"]))
    return routes


def _error_group_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": str(row.get("fingerprint") or ""),
        "source": str(row.get("source") or ""),
        "level": str(row.get("level") or "error"),
        "errorType": str(row.get("error_type") or ""),
        "message": str(row.get("message") or ""),
        "route": row.get("route") or None,
        "status": str(row.get("status") or STATUS_OPEN),
        "firstSeenAt": row.get("first_seen_at") or None,
        "lastSeenAt": row.get("last_seen_at") or None,
        "eventCount": _as_int(row.get("event_count")),
        "windowEventCount": _as_int(row.get("window_event_count")),
        "affectedUserCount": _as_int(row.get("affected_user_count")),
        "resolvedAt": row.get("resolved_at") or None,
        "resolvedBy": _coerce_int(row.get("resolved_by")),
        "note": row.get("note") or None,
    }


def list_error_groups(
    *,
    hours: object = 24,
    source: str = "all",
    status: str = "open",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Paginated triage list, newest activity first."""

    from app.database import _connect

    window_hours = _clamp_hours(hours)
    moment = now or _utc_now()
    window_start = moment - timedelta(hours=window_hours)
    error_from = _iso(window_start)
    resolved_page = max(1, int(page or 1))
    resolved_size = max(1, min(100, int(page_size or 20)))
    offset = (resolved_page - 1) * resolved_size

    filters = ["last_seen_at >= ?"]
    params: list[Any] = [error_from]
    if source in _VALID_SOURCES:
        filters.append("source = ?")
        params.append(source)
    if status in VALID_GROUP_STATUSES:
        filters.append("status = ?")
        params.append(status)
    term = _sanitize_like_term(query)
    if term:
        filters.append(
            "(message LIKE ? OR error_type LIKE ? OR route LIKE ? OR fingerprint LIKE ?)"
        )
        like = f"%{term}%"
        params.extend([like, like, like, like])
    where = " AND ".join(filters)

    items: list[dict[str, Any]] = []
    total = 0
    try:
        with _connect() as connection:
            total = _as_int(
                dict(
                    connection.execute(
                        f"SELECT COUNT(*) AS total FROM ops_error_groups WHERE {where}",
                        tuple(params),
                    ).fetchone()
                    or {}
                ).get("total")
            )
            rows = connection.execute(
                f"""
                SELECT fingerprint, source, level, error_type, message, route,
                       first_seen_at, last_seen_at, event_count, status,
                       resolved_at, resolved_by, note
                FROM ops_error_groups
                WHERE {where}
                ORDER BY last_seen_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, resolved_size, offset),
            ).fetchall()
            items = [_error_group_summary(dict(raw)) for raw in rows]
            _attach_window_counts(
                connection,
                items,
                error_from=error_from,
            )
    except Exception:  # noqa: BLE001 - degrade to an empty page.
        logger.warning("ops error group query failed", exc_info=True)

    total_pages = max(1, (total + resolved_size - 1) // resolved_size)
    return {
        "contract_version": CONTRACT_VERSION,
        "items": items,
        "page": resolved_page,
        "pageSize": resolved_size,
        "total": total,
        "totalPages": total_pages,
        "window": {"hours": window_hours, "start": error_from},
        "filters": {"source": source, "status": status, "query": term},
    }


def _attach_window_counts(
    connection: Any,
    items: list[dict[str, Any]],
    *,
    error_from: str,
) -> None:
    """Add in-window occurrence and distinct-user counts for one page.

    Scoped to the rendered fingerprints so the join never spans the whole
    events table.
    """

    fingerprints = [item["fingerprint"] for item in items if item["fingerprint"]]
    if not fingerprints:
        return
    placeholders = ", ".join(["?"] * len(fingerprints))
    rows = connection.execute(
        f"""
        SELECT fingerprint,
               COUNT(*) AS window_event_count,
               COUNT(DISTINCT userId) AS affected_user_count
        FROM ops_error_events
        WHERE occurred_at >= ? AND fingerprint IN ({placeholders})
        GROUP BY fingerprint
        """,
        (error_from, *fingerprints),
    ).fetchall()
    by_fingerprint = {
        str(dict(raw).get("fingerprint")): dict(raw) for raw in rows
    }
    for item in items:
        stats = by_fingerprint.get(item["fingerprint"])
        if not stats:
            continue
        item["windowEventCount"] = _as_int(stats.get("window_event_count"))
        item["affectedUserCount"] = _as_int(stats.get("affected_user_count"))


def get_error_group(
    fingerprint: str,
    *,
    hours: object = 24 * 7,
    event_limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Full triage detail: group state, recent stacks, occurrence histogram."""

    from app.database import _connect

    safe_fingerprint = _clip(fingerprint, 64)
    if not safe_fingerprint:
        return None
    window_hours = _clamp_hours(hours)
    moment = now or _utc_now()
    error_from = _iso(moment - timedelta(hours=window_hours))
    limit = max(1, min(100, int(event_limit or 20)))

    try:
        with _connect() as connection:
            group_row = connection.execute(
                """
                SELECT fingerprint, source, level, error_type, message, route,
                       first_seen_at, last_seen_at, event_count, status,
                       resolved_at, resolved_by, note
                FROM ops_error_groups
                WHERE fingerprint = ?
                """,
                (safe_fingerprint,),
            ).fetchone()
            if group_row is None:
                return None
            summary = _error_group_summary(dict(group_row))
            stats = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS stored_event_count,
                           COUNT(DISTINCT userId) AS affected_user_count
                    FROM ops_error_events
                    WHERE fingerprint = ?
                    """,
                    (safe_fingerprint,),
                ).fetchone()
                or {}
            )
            summary["affectedUserCount"] = _as_int(stats.get("affected_user_count"))
            event_rows = connection.execute(
                """
                SELECT event_id, occurred_at, source, level, error_type, message,
                       stack, route, method, status_code, request_id, userId,
                       release_tag, user_agent, context
                FROM ops_error_events
                WHERE fingerprint = ?
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (safe_fingerprint, limit),
            ).fetchall()
            histogram_rows = connection.execute(
                """
                SELECT SUBSTR(occurred_at, 1, 13) AS hour_key,
                       COUNT(*) AS event_count
                FROM ops_error_events
                WHERE fingerprint = ? AND occurred_at >= ?
                GROUP BY SUBSTR(occurred_at, 1, 13)
                ORDER BY hour_key
                """,
                (safe_fingerprint, error_from),
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("ops error group detail failed", exc_info=True)
        return None

    return {
        "contract_version": CONTRACT_VERSION,
        "group": summary,
        "storedEventCount": _as_int(stats.get("stored_event_count")),
        "events": [_error_event_payload(dict(raw)) for raw in event_rows],
        "hourly": [
            {
                "hour": str(dict(raw).get("hour_key") or ""),
                "eventCount": _as_int(dict(raw).get("event_count")),
            }
            for raw in histogram_rows
        ],
        "window": {"hours": window_hours, "start": error_from},
    }


def _error_event_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw_context = row.get("context")
    context: dict[str, Any] | None = None
    if raw_context:
        try:
            decoded = json.loads(str(raw_context))
            context = decoded if isinstance(decoded, dict) else None
        except (TypeError, ValueError):
            context = None
    return {
        "eventId": str(row.get("event_id") or ""),
        "occurredAt": row.get("occurred_at") or None,
        "source": str(row.get("source") or ""),
        "level": str(row.get("level") or "error"),
        "errorType": str(row.get("error_type") or ""),
        "message": str(row.get("message") or ""),
        "stack": row.get("stack") or None,
        "route": row.get("route") or None,
        "method": row.get("method") or None,
        "statusCode": _coerce_int(row.get("status_code")),
        "requestId": row.get("request_id") or None,
        "userId": _coerce_int(row.get("userId")),
        "release": row.get("release_tag") or None,
        "userAgent": row.get("user_agent") or None,
        "context": context,
    }


def set_error_group_status(
    fingerprint: str,
    *,
    status: str,
    actor_id: int | None = None,
    note: object = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Mark a group resolved/ignored/open. Returns the updated group."""

    from app.database import _connect

    safe_fingerprint = _clip(fingerprint, 64)
    if not safe_fingerprint or status not in VALID_GROUP_STATUSES:
        return None
    moment = now or _utc_now()
    resolved_at = _iso(moment) if status == STATUS_RESOLVED else None
    resolved_by = int(actor_id) if status == STATUS_RESOLVED and actor_id else None
    safe_note = _optional_clip(note, MAX_NOTE_CHARS)

    try:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE ops_error_groups
                SET status = ?,
                    resolved_at = ?,
                    resolved_by = ?,
                    note = ?
                WHERE fingerprint = ?
                """,
                (status, resolved_at, resolved_by, safe_note, safe_fingerprint),
            )
            row = connection.execute(
                """
                SELECT fingerprint, source, level, error_type, message, route,
                       first_seen_at, last_seen_at, event_count, status,
                       resolved_at, resolved_by, note
                FROM ops_error_groups
                WHERE fingerprint = ?
                """,
                (safe_fingerprint,),
            ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("ops error group status update failed", exc_info=True)
        return None
    return _error_group_summary(dict(row)) if row is not None else None


def ops_capture_state() -> dict[str, Any]:
    """Expose the pipeline's own health so silent data loss is visible."""

    settings = get_settings()
    with _state_lock:
        queue_depth = len(_queued_events)
        dropped_events = _dropped_event_count
        dropped_traffic = _dropped_traffic_count
        pending_minutes = len(_traffic_minutes)
        pending_routes = len(_route_hours)
        persist_failures = _persist_failure_count
        writer_alive = bool(_writer_thread is not None and _writer_thread.is_alive())
    return {
        "errorCaptureEnabled": bool(settings.ops_error_capture_enabled),
        "trafficCaptureEnabled": bool(settings.ops_traffic_capture_enabled),
        "clientIngestEnabled": bool(settings.ops_client_error_ingest_enabled),
        "errorRetentionDays": int(settings.ops_error_retention_days),
        "trafficRetentionDays": int(settings.ops_traffic_retention_days),
        "instanceId": instance_id(),
        "writerThreadAlive": writer_alive,
        "queueDepth": queue_depth,
        "droppedEventCount": dropped_events,
        "droppedTrafficCount": dropped_traffic,
        "pendingTrafficBuckets": pending_minutes,
        "pendingRouteBuckets": pending_routes,
        "persistFailureCount": persist_failures,
    }


def reset_ops_observability_for_tests() -> None:
    """Clear in-memory buffers and counters. Does not touch stored rows."""

    global _dropped_event_count, _dropped_traffic_count
    global _last_prune_monotonic, _persist_failure_count
    with _state_lock:
        _queued_events.clear()
        _traffic_minutes.clear()
        _route_hours.clear()
        _dropped_event_count = 0
        _dropped_traffic_count = 0
        _last_prune_monotonic = 0.0
        _persist_failure_count = 0
    _write_guard.active = False
    _writer_wake.clear()


__all__ = [
    "CONTRACT_VERSION",
    "OPS_INTERNAL_LOGGER_PREFIXES",
    "SOURCE_BACKEND",
    "SOURCE_FRONTEND",
    "SOURCE_WORKER",
    "STATUS_IGNORED",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "VALID_GROUP_STATUSES",
    "build_fingerprint",
    "flush_ops_writes",
    "get_error_group",
    "instance_id",
    "list_error_groups",
    "normalize_error_message",
    "ops_capture_state",
    "ops_overview",
    "prune_ops_data",
    "record_error_event",
    "record_request_traffic",
    "reset_ops_observability_for_tests",
    "set_error_group_status",
    "stack_signature",
]
