"""Immutable, redacted metadata for one provider chat-completion call.

The trace deliberately contains no request body, response body, credentials,
headers, or exception messages.  Exact request/content/envelope bytes are
represented only by SHA-256 digests and byte counts.  Callers retain their
existing return values and may opt into tracing with ``ProviderCallTraceCollector``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


PROVIDER_CALL_TRACE_SCHEMA_VERSION = "provider_call_trace.v1"
PROVIDER_CALL_TRACE_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
_TRACE_FIELDS = {
    "schema_version",
    "provider",
    "operation",
    "transport",
    "request_hash",
    "requested_model",
    "requested_at",
    "response_started_at",
    "first_content_at",
    "completed_at",
    "http_status",
    "provider_request_id_hash",
    "actual_model",
    "finish_reason",
    "usage",
    "chunk_count",
    "content_sha256",
    "content_bytes",
    "transport_envelope_sha256",
    "transport_envelope_bytes",
    "envelope_hash_basis",
    "outcome",
    "error_category",
    "interrupted_salvaged",
    "trace_hash",
}
_TRANSPORTS = frozenset({"sync", "stream"})
_ENVELOPE_HASH_BASIS = {
    "sync": "sync_http_body_bytes",
    "stream": "stream_nonempty_utf8_lines_lf_v1",
}
_OUTCOMES = frozenset(
    {
        "success",
        "provider_error",
        "http_error",
        "timeout",
        "transport_error",
        "interrupted",
    }
)
_ERROR_CATEGORIES = frozenset(
    {
        "empty_content",
        "invalid_envelope",
        "invalid_json",
        "http_status",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "timeout",
        "stream_interrupted",
        "consumer_cancelled",
        "connection_error",
        "transport_error",
        "provider_output_error",
        "unknown_provider_error",
    }
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class ProviderCallTraceError(ValueError):
    """The provider trace or collector lifecycle violated its contract."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def provider_request_hash(request_body: Mapping[str, Any]) -> str:
    """Hash the exact JSON object passed as the provider request body."""

    if not isinstance(request_body, Mapping):
        raise ProviderCallTraceError("provider request body must be an object")
    try:
        frozen = json.loads(_canonical_json(dict(request_body)))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ProviderCallTraceError(
            "provider request body is not canonical JSON"
        ) from exc
    return _canonical_hash(frozen)


def provider_request_id_from_headers(headers: object) -> str | None:
    """Read only a request id from response headers; never retain the headers."""

    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for name in (
        "x-request-id",
        "request-id",
        "x-trace-id",
        "trace-id",
    ):
        value = getter(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderCallTraceError(f"{name} is required")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _sha256(value: object, name: str) -> str:
    text = _required_text(value, name)
    if (
        text != text.lower()
        or len(text) != 64
        or any(character not in "0123456789abcdef" for character in text)
    ):
        raise ProviderCallTraceError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return text


def _optional_sha256(value: object, name: str) -> str | None:
    return None if value is None else _sha256(value, name)


def _timestamp(value: object, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderCallTraceError(f"{name} must be an ISO timestamp") from exc
    else:
        raise ProviderCallTraceError(f"{name} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderCallTraceError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _optional_timestamp(value: object, name: str) -> str | None:
    return None if value is None else _timestamp(value, name)


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderCallTraceError(f"{name} must be a non-negative integer")
    return value


def _nullable_usage(value: object) -> dict[str, int | None]:
    if not isinstance(value, Mapping):
        raise ProviderCallTraceError("usage must be an object")
    if set(value) != set(PROVIDER_CALL_TRACE_USAGE_FIELDS):
        raise ProviderCallTraceError("usage fields conflict with the trace contract")
    result: dict[str, int | None] = {}
    for field in PROVIDER_CALL_TRACE_USAGE_FIELDS:
        item = value.get(field)
        result[field] = None if item is None else _nonnegative_int(item, f"usage.{field}")
    return result


def normalize_provider_call_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize and hash-check a complete ``provider_call_trace.v1`` value."""

    if not isinstance(trace, Mapping):
        raise ProviderCallTraceError("provider call trace must be an object")
    if any(not isinstance(key, str) for key in trace):
        raise ProviderCallTraceError("provider call trace field names must be strings")
    unknown = set(trace) - _TRACE_FIELDS
    if unknown:
        raise ProviderCallTraceError(
            "provider call trace contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    schema_version = _required_text(trace.get("schema_version"), "schema_version")
    if schema_version != PROVIDER_CALL_TRACE_SCHEMA_VERSION:
        raise ProviderCallTraceError("provider call trace schema_version is unsupported")
    transport = _required_text(trace.get("transport"), "transport")
    if transport not in _TRANSPORTS:
        raise ProviderCallTraceError("provider call trace transport is unsupported")
    requested_at = _timestamp(trace.get("requested_at"), "requested_at")
    response_started_at = _optional_timestamp(
        trace.get("response_started_at"), "response_started_at"
    )
    first_content_at = _optional_timestamp(
        trace.get("first_content_at"), "first_content_at"
    )
    completed_at = _timestamp(trace.get("completed_at"), "completed_at")
    requested_dt = datetime.fromisoformat(requested_at)
    completed_dt = datetime.fromisoformat(completed_at)
    if completed_dt < requested_dt:
        raise ProviderCallTraceError("completed_at predates requested_at")
    if response_started_at is not None:
        response_dt = datetime.fromisoformat(response_started_at)
        if response_dt < requested_dt or response_dt > completed_dt:
            raise ProviderCallTraceError("response_started_at is out of order")
    else:
        response_dt = None
    if first_content_at is not None:
        first_dt = datetime.fromisoformat(first_content_at)
        if (
            response_dt is None
            or first_dt < response_dt
            or first_dt > completed_dt
        ):
            raise ProviderCallTraceError("first_content_at is out of order")

    http_status_raw = trace.get("http_status")
    http_status = (
        None
        if http_status_raw is None
        else _nonnegative_int(http_status_raw, "http_status")
    )
    if http_status is not None and not 100 <= http_status <= 599:
        raise ProviderCallTraceError("http_status is outside the HTTP status range")
    if http_status is not None and response_started_at is None:
        raise ProviderCallTraceError("http_status requires response_started_at")

    chunk_count = _nonnegative_int(trace.get("chunk_count"), "chunk_count")
    content_bytes = _nonnegative_int(trace.get("content_bytes"), "content_bytes")
    envelope_bytes = _nonnegative_int(
        trace.get("transport_envelope_bytes"), "transport_envelope_bytes"
    )
    content_sha256 = _sha256(trace.get("content_sha256"), "content_sha256")
    envelope_sha256 = _sha256(
        trace.get("transport_envelope_sha256"),
        "transport_envelope_sha256",
    )
    if content_bytes == 0 and content_sha256 != _EMPTY_SHA256:
        raise ProviderCallTraceError("empty content has a non-empty digest")
    if envelope_bytes == 0 and envelope_sha256 != _EMPTY_SHA256:
        raise ProviderCallTraceError("empty transport envelope has a non-empty digest")
    if (content_bytes == 0) != (chunk_count == 0):
        raise ProviderCallTraceError("chunk_count conflicts with content_bytes")

    basis = _required_text(trace.get("envelope_hash_basis"), "envelope_hash_basis")
    if basis != _ENVELOPE_HASH_BASIS[transport]:
        raise ProviderCallTraceError("envelope_hash_basis conflicts with transport")
    outcome = _required_text(trace.get("outcome"), "outcome")
    if outcome not in _OUTCOMES:
        raise ProviderCallTraceError("provider call trace outcome is unsupported")
    error_category = _optional_text(trace.get("error_category"), "error_category")
    if outcome == "success":
        if error_category is not None:
            raise ProviderCallTraceError("successful trace cannot contain an error")
        if response_started_at is None:
            raise ProviderCallTraceError("successful trace requires a response clock")
    elif error_category not in _ERROR_CATEGORIES:
        raise ProviderCallTraceError("failed trace error_category is unsupported")
    interrupted_salvaged = trace.get("interrupted_salvaged")
    if not isinstance(interrupted_salvaged, bool):
        raise ProviderCallTraceError("interrupted_salvaged must be a boolean")
    if interrupted_salvaged and outcome != "interrupted":
        raise ProviderCallTraceError(
            "only an interrupted trace can be marked as salvaged"
        )

    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "provider": _required_text(trace.get("provider"), "provider"),
        "operation": _required_text(trace.get("operation"), "operation"),
        "transport": transport,
        "request_hash": _sha256(trace.get("request_hash"), "request_hash"),
        "requested_model": _required_text(
            trace.get("requested_model"), "requested_model"
        ),
        "requested_at": requested_at,
        "response_started_at": response_started_at,
        "first_content_at": first_content_at,
        "completed_at": completed_at,
        "http_status": http_status,
        "provider_request_id_hash": _optional_sha256(
            trace.get("provider_request_id_hash"),
            "provider_request_id_hash",
        ),
        "actual_model": _optional_text(trace.get("actual_model"), "actual_model"),
        "finish_reason": _optional_text(
            trace.get("finish_reason"), "finish_reason"
        ),
        "usage": _nullable_usage(trace.get("usage")),
        "chunk_count": chunk_count,
        "content_sha256": content_sha256,
        "content_bytes": content_bytes,
        "transport_envelope_sha256": envelope_sha256,
        "transport_envelope_bytes": envelope_bytes,
        "envelope_hash_basis": basis,
        "outcome": outcome,
        "error_category": error_category,
        "interrupted_salvaged": interrupted_salvaged,
    }
    trace_hash = _canonical_hash(normalized)
    supplied_hash = trace.get("trace_hash")
    if supplied_hash is not None and _sha256(supplied_hash, "trace_hash") != trace_hash:
        raise ProviderCallTraceError("provider call trace_hash mismatch")
    normalized["trace_hash"] = trace_hash
    return normalized


class ProviderCallTraceCollector:
    """Incrementally collect one trace without retaining sensitive byte bodies."""

    def __init__(
        self,
        *,
        provider: str = "deepseek",
        operation: str = "chat_completions",
        transport: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._operation = _required_text(operation, "operation")
        if transport not in _TRANSPORTS:
            raise ProviderCallTraceError("collector transport is unsupported")
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._request_hash: str | None = None
        self._requested_model: str | None = None
        self._requested_at: str | None = None
        self._response_started_at: str | None = None
        self._first_content_at: str | None = None
        self._http_status: int | None = None
        self._provider_request_id_hash: str | None = None
        self._actual_model: str | None = None
        self._finish_reason: str | None = None
        self._usage: dict[str, int | None] = {
            field: None for field in PROVIDER_CALL_TRACE_USAGE_FIELDS
        }
        self._chunk_count = 0
        self._content_hash = hashlib.sha256()
        self._content_bytes = 0
        self._envelope_hash = hashlib.sha256()
        self._envelope_bytes = 0
        self._sync_envelope_observed = False
        self._stream_invalid_envelope = False
        self._trace: dict[str, Any] | None = None

    @property
    def finalized(self) -> bool:
        return self._trace is not None

    @property
    def content_bytes(self) -> int:
        return self._content_bytes

    @property
    def trace(self) -> dict[str, Any] | None:
        return deepcopy(self._trace) if self._trace is not None else None

    def require_trace(self) -> dict[str, Any]:
        if self._trace is None:
            raise ProviderCallTraceError("provider call trace is not finalized")
        return deepcopy(self._trace)

    def start_request(self, request_body: Mapping[str, Any]) -> None:
        self._ensure_mutable()
        if self._requested_at is not None:
            raise ProviderCallTraceError("provider request was already started")
        self._request_hash = provider_request_hash(request_body)
        self._requested_model = _required_text(
            request_body.get("model"), "request_body.model"
        )
        body_stream = request_body.get("stream", False)
        if not isinstance(body_stream, bool):
            raise ProviderCallTraceError("request_body.stream must be a boolean")
        if body_stream != (self._transport == "stream"):
            raise ProviderCallTraceError(
                "request_body.stream conflicts with collector transport"
            )
        self._requested_at = self._now()

    def mark_response_started(
        self,
        *,
        http_status: int | None,
        provider_request_id: object = None,
    ) -> None:
        self._require_started()
        self._ensure_mutable()
        if self._response_started_at is None:
            self._response_started_at = self._now()
        if http_status is not None:
            self._http_status = _nonnegative_int(http_status, "http_status")
        self._observe_provider_request_id(provider_request_id)

    def observe_sync_envelope(self, body: bytes | bytearray | memoryview) -> None:
        self._require_started()
        self._ensure_mutable()
        if self._transport != "sync":
            raise ProviderCallTraceError("sync envelope used with stream collector")
        if self._sync_envelope_observed:
            raise ProviderCallTraceError("sync response envelope was already observed")
        raw = bytes(body)
        self._envelope_hash.update(raw)
        self._envelope_bytes += len(raw)
        self._sync_envelope_observed = True

    def observe_stream_line(self, line: str | bytes) -> None:
        self._require_started()
        self._ensure_mutable()
        if self._transport != "stream":
            raise ProviderCallTraceError("stream line used with sync collector")
        if isinstance(line, bytes):
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                self._stream_invalid_envelope = True
                raise ProviderCallTraceError("stream line is not UTF-8") from exc
        elif isinstance(line, str):
            text = line
        else:
            raise ProviderCallTraceError("stream line must be text or bytes")
        if not text:
            return
        raw = text.encode("utf-8") + b"\n"
        self._envelope_hash.update(raw)
        self._envelope_bytes += len(raw)
        self._observe_stream_metadata(text)

    def observe_content(self, content: str) -> None:
        self._require_started()
        self._ensure_mutable()
        if not isinstance(content, str):
            raise ProviderCallTraceError("assistant content must be text")
        if not content:
            return
        raw = content.encode("utf-8")
        if self._first_content_at is None:
            if self._response_started_at is None:
                raise ProviderCallTraceError(
                    "assistant content cannot predate the response"
                )
            self._first_content_at = self._now()
        self._content_hash.update(raw)
        self._content_bytes += len(raw)
        self._chunk_count += 1

    def observe_metadata(
        self,
        *,
        actual_model: object = None,
        finish_reason: object = None,
        usage: object = None,
        provider_request_id: object = None,
    ) -> None:
        self._require_started()
        self._ensure_mutable()
        if isinstance(actual_model, str) and actual_model.strip():
            self._actual_model = actual_model.strip()
        if isinstance(finish_reason, str) and finish_reason.strip():
            self._finish_reason = finish_reason.strip()
        if isinstance(usage, Mapping):
            for field in PROVIDER_CALL_TRACE_USAGE_FIELDS:
                value = usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self._usage[field] = value
        self._observe_provider_request_id(provider_request_id)

    def finish_success(self) -> dict[str, Any]:
        if self._transport == "stream" and self._stream_invalid_envelope:
            return self.finish_error(
                outcome="provider_error",
                error_category="invalid_envelope",
            )
        if self._content_bytes == 0:
            return self.finish_error(
                outcome="provider_error",
                error_category="empty_content",
            )
        return self._finish(
            outcome="success",
            error_category=None,
            interrupted_salvaged=False,
        )

    def finish_error(
        self,
        *,
        outcome: str,
        error_category: str,
    ) -> dict[str, Any]:
        if outcome == "success":
            raise ProviderCallTraceError("finish_error requires a failed outcome")
        return self._finish(
            outcome=outcome,
            error_category=error_category,
            interrupted_salvaged=False,
        )

    def mark_interrupted_salvaged(self) -> dict[str, Any]:
        if self._trace is None or self._trace.get("outcome") != "interrupted":
            raise ProviderCallTraceError("only an interrupted trace can be salvaged")
        value = dict(self._trace)
        value["interrupted_salvaged"] = True
        value.pop("trace_hash", None)
        self._trace = normalize_provider_call_trace(value)
        return self.require_trace()

    def _finish(
        self,
        *,
        outcome: str,
        error_category: str | None,
        interrupted_salvaged: bool,
    ) -> dict[str, Any]:
        self._require_started()
        self._ensure_mutable()
        value = {
            "schema_version": PROVIDER_CALL_TRACE_SCHEMA_VERSION,
            "provider": self._provider,
            "operation": self._operation,
            "transport": self._transport,
            "request_hash": self._request_hash,
            "requested_model": self._requested_model,
            "requested_at": self._requested_at,
            "response_started_at": self._response_started_at,
            "first_content_at": self._first_content_at,
            "completed_at": self._now(),
            "http_status": self._http_status,
            "provider_request_id_hash": self._provider_request_id_hash,
            "actual_model": self._actual_model,
            "finish_reason": self._finish_reason,
            "usage": dict(self._usage),
            "chunk_count": self._chunk_count,
            "content_sha256": self._content_hash.hexdigest(),
            "content_bytes": self._content_bytes,
            "transport_envelope_sha256": self._envelope_hash.hexdigest(),
            "transport_envelope_bytes": self._envelope_bytes,
            "envelope_hash_basis": _ENVELOPE_HASH_BASIS[self._transport],
            "outcome": outcome,
            "error_category": error_category,
            "interrupted_salvaged": interrupted_salvaged,
        }
        self._trace = normalize_provider_call_trace(value)
        return self.require_trace()

    def _observe_provider_request_id(self, value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
        if self._provider_request_id_hash is None:
            self._provider_request_id_hash = digest

    def _observe_stream_metadata(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        raw_payload = line[5:].strip()
        if raw_payload == "[DONE]":
            return
        if not raw_payload:
            self._stream_invalid_envelope = True
            return
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            self._stream_invalid_envelope = True
            return
        if not isinstance(payload, Mapping):
            self._stream_invalid_envelope = True
            return
        choices = payload.get("choices")
        usage = payload.get("usage")
        valid_choices = isinstance(choices, list) and all(
            isinstance(choice, Mapping) for choice in choices
        )
        if not valid_choices and not isinstance(usage, Mapping):
            self._stream_invalid_envelope = True
            return
        finish_reason: object = None
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, Mapping) and choice.get("finish_reason") is not None:
                    finish_reason = choice.get("finish_reason")
                    break
        self.observe_metadata(
            actual_model=payload.get("model"),
            finish_reason=finish_reason,
            usage=usage,
            provider_request_id=payload.get("id"),
        )

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise ProviderCallTraceError("collector clock must return datetime")
        return _timestamp(value, "collector clock")

    def _require_started(self) -> None:
        if self._requested_at is None:
            raise ProviderCallTraceError("provider request has not started")

    def _ensure_mutable(self) -> None:
        if self._trace is not None:
            raise ProviderCallTraceError("provider call trace is already finalized")


__all__ = [
    "PROVIDER_CALL_TRACE_SCHEMA_VERSION",
    "PROVIDER_CALL_TRACE_USAGE_FIELDS",
    "ProviderCallTraceCollector",
    "ProviderCallTraceError",
    "normalize_provider_call_trace",
    "provider_request_hash",
    "provider_request_id_from_headers",
]
