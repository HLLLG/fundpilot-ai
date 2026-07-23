"""Low-overhead, bounded in-process performance telemetry.

The registry deliberately keeps only aggregate counters and bounded latency
samples.  It never records request bodies, query parameters, authorization
data, database bind parameters, holdings, or provider credentials.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

logger = logging.getLogger("fundpilot.performance")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_UUID_SEGMENT_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_HEX_SEGMENT_RE = re.compile(r"(?i)^[0-9a-f]{16,}$")
_NUMBER_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?")
_STRING_RE = re.compile(r"'(?:''|[^'])*'")
_SPACE_RE = re.compile(r"\s+")
_SAFE_LABEL_RE = re.compile(r"[^a-zA-Z0-9_.:/{}-]+")

_MAX_ROUTE_SERIES = 256
_MAX_PROVIDER_SERIES = 128
_MAX_DB_FINGERPRINTS = 128
_MAX_CACHE_SERIES = 64
_MAX_WEB_VITAL_SERIES = 64


@dataclass
class _Accumulator:
    samples: deque[float]
    count: int = 0
    errors: int = 0
    total: float = 0.0
    maximum: float = 0.0
    status_codes: Counter[int] = field(default_factory=Counter)

    def observe(
        self,
        value: float,
        *,
        error: bool = False,
        status_code: int | None = None,
    ) -> None:
        resolved = max(0.0, float(value))
        self.samples.append(resolved)
        self.count += 1
        self.total += resolved
        self.maximum = max(self.maximum, resolved)
        if error:
            self.errors += 1
        if status_code is not None:
            self.status_codes[int(status_code)] += 1


@dataclass
class RequestObservation:
    db_query_count: int = 0
    db_seconds: float = 0.0
    provider_call_count: int = 0
    provider_seconds: float = 0.0


_request_observation: contextvars.ContextVar[RequestObservation | None] = (
    contextvars.ContextVar("fundpilot_request_performance", default=None)
)
_registry_lock = threading.RLock()
_started_at = time.time()
_request_latency: dict[tuple[str, str], _Accumulator] = {}
_request_ttfb: dict[tuple[str, str], _Accumulator] = {}
_request_response_bytes: Counter[tuple[str, str]] = Counter()
_db_latency: dict[tuple[str, str], _Accumulator] = {}
_provider_latency: dict[tuple[str, str], _Accumulator] = {}
_provider_errors: Counter[tuple[str, str, str]] = Counter()
_cache_events: Counter[tuple[str, str]] = Counter()
_web_vitals: dict[tuple[str, str], _Accumulator] = {}
_background_jobs: dict[tuple[str, str], _Accumulator] = {}
_last_cpu_sample: tuple[float, float] | None = None


def _sample_limit() -> int:
    try:
        return max(128, min(20_000, int(get_settings().performance_sample_size)))
    except Exception:
        return 2048


def _new_accumulator() -> _Accumulator:
    return _Accumulator(samples=deque(maxlen=_sample_limit()))


def _bounded_series(
    registry: dict[Any, _Accumulator],
    key: Any,
    *,
    limit: int,
) -> _Accumulator:
    accumulator = registry.get(key)
    if accumulator is not None:
        return accumulator
    if len(registry) >= limit:
        key = ("other", "other")
        accumulator = registry.get(key)
        if accumulator is not None:
            return accumulator
    accumulator = _new_accumulator()
    registry[key] = accumulator
    return accumulator


def _safe_label(value: object, *, fallback: str = "unknown", limit: int = 80) -> str:
    normalized = _SAFE_LABEL_RE.sub("_", str(value or "").strip())[:limit]
    return normalized or fallback


def normalize_request_path(path: object) -> str:
    raw = str(path or "/").split("?", 1)[0]
    segments: list[str] = []
    for segment in raw.split("/"):
        if not segment:
            continue
        if (
            segment.isdigit()
            or _UUID_SEGMENT_RE.fullmatch(segment)
            or _HEX_SEGMENT_RE.fullmatch(segment)
        ):
            segments.append("{id}")
        else:
            segments.append(_safe_label(segment, limit=60))
    normalized = "/" + "/".join(segments)
    return normalized[:240] or "/"


def _route_from_scope(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return str(template)[:240]
    return normalize_request_path(scope.get("path"))


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if bytes(name).lower() != b"x-request-id":
            continue
        candidate = bytes(value).decode("latin-1", errors="ignore").strip()
        if _REQUEST_ID_RE.fullmatch(candidate):
            return candidate
    return uuid4().hex


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _accumulator_snapshot(accumulator: _Accumulator, *, milliseconds: bool) -> dict:
    values = list(accumulator.samples)
    scale = 1000.0 if milliseconds else 1.0
    return {
        "count": accumulator.count,
        "errors": accumulator.errors,
        "error_rate_percent": (
            round(accumulator.errors / accumulator.count * 100.0, 3)
            if accumulator.count
            else 0.0
        ),
        "sample_count": len(values),
        "mean": (
            round(accumulator.total / accumulator.count * scale, 3)
            if accumulator.count
            else None
        ),
        "p50": _rounded(_percentile(values, 0.50), scale),
        "p95": _rounded(_percentile(values, 0.95), scale),
        "p99": _rounded(_percentile(values, 0.99), scale),
        "max": round(accumulator.maximum * scale, 3) if accumulator.count else None,
        "status_codes": {
            str(code): count
            for code, count in sorted(accumulator.status_codes.items())
        },
    }


def _rounded(value: float | None, scale: float) -> float | None:
    return round(value * scale, 3) if value is not None else None


def _sql_fingerprint(statement: str) -> str:
    normalized = _STRING_RE.sub("?", str(statement or ""))
    normalized = _NUMBER_RE.sub("?", normalized)
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    return normalized[:240] or "unknown"


def record_db_query(
    dialect: str,
    statement: str,
    duration_seconds: float,
    *,
    error: BaseException | None = None,
) -> None:
    observation = _request_observation.get()
    if observation is not None:
        observation.db_query_count += 1
        observation.db_seconds += max(0.0, duration_seconds)
    dialect_label = _safe_label(dialect)
    fingerprint = _sql_fingerprint(statement)
    with _registry_lock:
        accumulator = _bounded_series(
            _db_latency,
            (dialect_label, fingerprint),
            limit=_MAX_DB_FINGERPRINTS,
        )
        accumulator.observe(duration_seconds, error=error is not None)


def record_provider_call(
    provider: str,
    operation: str,
    duration_seconds: float,
    *,
    error: object | None = None,
    status_code: int | None = None,
) -> None:
    observation = _request_observation.get()
    if observation is not None:
        observation.provider_call_count += 1
        observation.provider_seconds += max(0.0, duration_seconds)
    provider_label = _safe_label(provider, limit=40)
    operation_label = _safe_label(operation, limit=80)
    is_error = error is not None or (
        status_code is not None and int(status_code) >= 400
    )
    with _registry_lock:
        accumulator = _bounded_series(
            _provider_latency,
            (provider_label, operation_label),
            limit=_MAX_PROVIDER_SERIES,
        )
        accumulator.observe(
            duration_seconds,
            error=is_error,
            status_code=status_code,
        )
        if is_error:
            category = _safe_label(
                error.__class__.__name__ if isinstance(error, BaseException) else error,
                limit=48,
            )
            _provider_errors[(provider_label, operation_label, category)] += 1


def cache_family(cache_key: object) -> str:
    raw = str(cache_key or "").strip()
    if not raw:
        return "unknown"
    parts = raw.split(":")
    if len(parts) >= 2 and parts[0] == "fund":
        return _safe_label(":".join(parts[:2]), limit=48)
    return _safe_label(parts[0], limit=48)


def record_cache_event(cache_key: object, event: str) -> None:
    event_label = _safe_label(event, limit=24)
    with _registry_lock:
        if len(_cache_events) >= _MAX_CACHE_SERIES * 8:
            family = "other"
        else:
            family = cache_family(cache_key)
        _cache_events[(family, event_label)] += 1


def record_web_vital(
    name: object,
    value: float,
    *,
    route: object,
) -> None:
    vital = _safe_label(name, limit=16).upper()
    route_label = normalize_request_path(route)
    with _registry_lock:
        accumulator = _bounded_series(
            _web_vitals,
            (vital, route_label),
            limit=_MAX_WEB_VITAL_SERIES,
        )
        accumulator.observe(max(0.0, float(value)))


def record_background_job(
    kind: object,
    duration_seconds: float,
    *,
    outcome: object,
) -> None:
    kind_label = _safe_label(kind, limit=32)
    outcome_label = _safe_label(outcome, limit=24)
    with _registry_lock:
        accumulator = _bounded_series(
            _background_jobs,
            (kind_label, outcome_label),
            limit=32,
        )
        accumulator.observe(
            duration_seconds,
            error=outcome_label in {"failed", "cancelled", "timeout"},
        )


class PerformanceMetricsMiddleware:
    """Measure complete HTTP and SSE lifetimes with a bounded registry."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or not get_settings().performance_metrics_enabled
        ):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = _request_id(scope)
        method = str(scope.get("method") or "GET").upper()
        status_code = 500
        response_bytes = 0
        first_byte_seconds: float | None = None
        observation = RequestObservation()
        token = _request_observation.set(observation)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id

        async def measured_send(message: Message) -> None:
            nonlocal first_byte_seconds, response_bytes, status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 500)
                first_byte_seconds = max(0.0, time.perf_counter() - started)
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                headers.append(
                    (
                        b"server-timing",
                        f"app;dur={first_byte_seconds * 1000.0:.2f}".encode("ascii"),
                    )
                )
                message["headers"] = headers
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body") or b"")
            await send(message)

        raised: BaseException | None = None
        try:
            await self.app(scope, receive, measured_send)
        except BaseException as exc:
            raised = exc
            raise
        finally:
            _request_observation.reset(token)
            duration_seconds = max(0.0, time.perf_counter() - started)
            route = _route_from_scope(scope)
            is_error = raised is not None or status_code >= 500
            with _registry_lock:
                key = (method, route)
                latency = _bounded_series(
                    _request_latency,
                    key,
                    limit=_MAX_ROUTE_SERIES,
                )
                metric_key = (
                    key
                    if _request_latency.get(key) is latency
                    else ("other", "other")
                )
                latency.observe(
                    duration_seconds,
                    error=is_error,
                    status_code=status_code,
                )
                if first_byte_seconds is not None:
                    ttfb = _bounded_series(
                        _request_ttfb,
                        metric_key,
                        limit=_MAX_ROUTE_SERIES,
                    )
                    ttfb.observe(first_byte_seconds, error=is_error)
                _request_response_bytes[metric_key] += response_bytes
            _log_request_summary(
                request_id=request_id,
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
                first_byte_seconds=first_byte_seconds,
                response_bytes=response_bytes,
                observation=observation,
            )


def _log_request_summary(
    *,
    request_id: str,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
    first_byte_seconds: float | None,
    response_bytes: int,
    observation: RequestObservation,
) -> None:
    settings = get_settings()
    slow = duration_seconds * 1000.0 >= max(
        1.0,
        float(settings.performance_slow_request_ms),
    )
    sampled = False
    sample_rate = max(
        0.0,
        min(1.0, float(settings.performance_log_sample_rate)),
    )
    if sample_rate > 0:
        sample = hashlib.blake2s(
            request_id.encode("ascii", errors="ignore"),
            digest_size=4,
        ).digest()
        sampled = int.from_bytes(sample, "big") / 0xFFFFFFFF < sample_rate
    if status_code < 500 and not slow and not sampled:
        return
    payload = {
        "event": "http_request",
        "request_id": request_id,
        "method": method,
        "route": route,
        "status": status_code,
        "duration_ms": round(duration_seconds * 1000.0, 3),
        "ttfb_ms": (
            round(first_byte_seconds * 1000.0, 3)
            if first_byte_seconds is not None
            else None
        ),
        "response_bytes": response_bytes,
        "db_query_count": observation.db_query_count,
        "db_ms": round(observation.db_seconds * 1000.0, 3),
        "provider_call_count": observation.provider_call_count,
        "provider_ms": round(observation.provider_seconds * 1000.0, 3),
    }
    logger.info("%s", json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def _process_snapshot() -> dict[str, Any]:
    global _last_cpu_sample
    wall_now = time.monotonic()
    cpu_now = time.process_time()
    with _registry_lock:
        previous = _last_cpu_sample
        _last_cpu_sample = (wall_now, cpu_now)
    cpu_percent: float | None = None
    if previous is not None and wall_now > previous[0]:
        cpu_percent = max(
            0.0,
            (cpu_now - previous[1]) / (wall_now - previous[0]) * 100.0,
        )
    rss_bytes = _current_rss_bytes()
    disk = shutil.disk_usage(Path.cwd())
    network = _linux_network_totals()
    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        load_average = None
    return {
        "pid": os.getpid(),
        "uptime_seconds": round(max(0.0, time.time() - _started_at), 3),
        "cpu_percent_since_last_snapshot": (
            round(cpu_percent, 3) if cpu_percent is not None else None
        ),
        "rss_bytes": rss_bytes,
        "thread_count": threading.active_count(),
        "load_average": load_average,
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round(disk.used / disk.total * 100.0, 3),
        },
        "network": network,
    }


def _current_rss_bytes() -> int | None:
    statm = Path("/proc/self/statm")
    try:
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, IndexError, OSError, ValueError):
        return None


def _linux_network_totals() -> dict[str, int] | None:
    path = Path("/proc/net/dev")
    try:
        received = 0
        transmitted = 0
        for line in path.read_text(encoding="ascii").splitlines()[2:]:
            _interface, values = line.split(":", 1)
            columns = values.split()
            received += int(columns[0])
            transmitted += int(columns[8])
        return {
            "received_bytes": received,
            "transmitted_bytes": transmitted,
        }
    except (IndexError, OSError, ValueError):
        return None


def performance_snapshot() -> dict[str, Any]:
    with _registry_lock:
        request_rows = []
        for (method, route), accumulator in _request_latency.items():
            row = {
                "method": method,
                "route": route,
                "latency_ms": _accumulator_snapshot(
                    accumulator,
                    milliseconds=True,
                ),
                "ttfb_ms": _accumulator_snapshot(
                    _request_ttfb.get((method, route), _new_accumulator()),
                    milliseconds=True,
                ),
                "response_bytes": _request_response_bytes.get((method, route), 0),
            }
            request_rows.append(row)
        request_rows.sort(
            key=lambda item: (
                -(item["latency_ms"]["p95"] or 0.0),
                item["route"],
            )
        )
        database_rows = [
            {
                "dialect": dialect,
                "fingerprint": fingerprint,
                "latency_ms": _accumulator_snapshot(
                    accumulator,
                    milliseconds=True,
                ),
            }
            for (dialect, fingerprint), accumulator in _db_latency.items()
        ]
        database_rows.sort(
            key=lambda item: (
                -(item["latency_ms"]["p95"] or 0.0),
                item["fingerprint"],
            )
        )
        provider_rows = [
            {
                "provider": provider,
                "operation": operation,
                "latency_ms": _accumulator_snapshot(
                    accumulator,
                    milliseconds=True,
                ),
            }
            for (provider, operation), accumulator in _provider_latency.items()
        ]
        provider_rows.sort(
            key=lambda item: (
                -(item["latency_ms"]["p95"] or 0.0),
                item["provider"],
                item["operation"],
            )
        )
        provider_errors = [
            {
                "provider": provider,
                "operation": operation,
                "category": category,
                "count": count,
            }
            for (provider, operation, category), count in _provider_errors.items()
        ]
        cache_rows = [
            {"family": family, "event": event, "count": count}
            for (family, event), count in _cache_events.items()
        ]
        vital_rows = [
            {
                "name": name,
                "route": route,
                "value": _accumulator_snapshot(
                    accumulator,
                    milliseconds=False,
                ),
            }
            for (name, route), accumulator in _web_vitals.items()
        ]
        background_job_rows = [
            {
                "kind": kind,
                "outcome": outcome,
                "duration_ms": _accumulator_snapshot(
                    accumulator,
                    milliseconds=True,
                ),
            }
            for (kind, outcome), accumulator in _background_jobs.items()
        ]
    return {
        "contract_version": "fundpilot.performance.v1",
        "generated_at_epoch": round(time.time(), 3),
        "process": _process_snapshot(),
        "requests": request_rows,
        "database": database_rows[:20],
        "providers": provider_rows,
        "provider_errors": provider_errors,
        "cache": cache_rows,
        "web_vitals": vital_rows,
        "background_jobs": background_job_rows,
        "privacy": {
            "request_bodies_recorded": False,
            "query_parameters_recorded": False,
            "database_parameters_recorded": False,
            "authorization_recorded": False,
        },
    }


def reset_performance_metrics_for_tests() -> None:
    global _last_cpu_sample
    with _registry_lock:
        _request_latency.clear()
        _request_ttfb.clear()
        _request_response_bytes.clear()
        _db_latency.clear()
        _provider_latency.clear()
        _provider_errors.clear()
        _cache_events.clear()
        _web_vitals.clear()
        _background_jobs.clear()
        _last_cpu_sample = None


__all__ = [
    "PerformanceMetricsMiddleware",
    "cache_family",
    "normalize_request_path",
    "performance_snapshot",
    "record_cache_event",
    "record_background_job",
    "record_db_query",
    "record_provider_call",
    "record_web_vital",
    "reset_performance_metrics_for_tests",
]
