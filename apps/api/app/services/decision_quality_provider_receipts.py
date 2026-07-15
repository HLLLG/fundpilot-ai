"""Pure, content-addressed receipts for decision-quality provider reads.

The receipt deliberately captures the adapter boundary rather than pretending
that AkShare exposes the upstream HTTP response.  Exact adapter stdout bytes are
preserved alongside the parsed payload and the hash of the payload consumed by
downstream normalization.  Cache delivery metadata is kept separate so a cache
hit never rewrites the origin observation clock.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping


PROVIDER_ORIGIN_RECEIPT_SCHEMA_VERSION = (
    "decision_quality_provider_origin_receipt.v1"
)
PROVIDER_DELIVERY_SCHEMA_VERSION = "decision_quality_provider_delivery.v1"
PROVIDER_READ_SCHEMA_VERSION = "decision_quality_provider_read.v1"

_ALLOWED_RESPONSE_STATUSES = frozenset(
    {
        "success",
        "empty",
        "invalid_json",
        "invalid_payload",
        "provider_error",
        "subprocess_error",
        "timeout",
        "exception",
    }
)
_ALLOWED_CACHE_STATUSES = frozenset({"hit", "miss"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNSET = object()


class ProviderReceiptValidationError(ValueError):
    """A provider receipt or cache delivery violated its frozen contract."""


@dataclass(frozen=True, slots=True)
class DecisionQualityProviderRead:
    """Typed result returned by quality-only provider adapters."""

    origin_receipt: dict[str, Any]
    normalized_payload: object
    delivery: dict[str, Any]

    @property
    def status(self) -> str:
        response = self.origin_receipt.get("response")
        return str(response.get("status") or "") if isinstance(response, Mapping) else ""

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROVIDER_READ_SCHEMA_VERSION,
            "origin_receipt": deepcopy(self.origin_receipt),
            "normalized_payload": deepcopy(self.normalized_payload),
            "delivery": deepcopy(self.delivery),
        }


def canonical_provider_json(value: object) -> str:
    """Return deterministic JSON for request, response, and receipt hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_provider_hash(value: object) -> str:
    return hashlib.sha256(canonical_provider_json(value).encode("utf-8")).hexdigest()


def build_provider_origin_receipt(
    *,
    provider_id: str,
    operation: str,
    request_parameters: Mapping[str, Any],
    request_started_at: str | datetime,
    response_completed_at: str | datetime,
    response_status: str,
    adapter_contract_version: str,
    adapter_script: str | bytes,
    library_name: str,
    library_version: str,
    python_version: str,
    cache_policy: str,
    cache_key_material: Mapping[str, Any],
    stdout_bytes: bytes,
    parsed_payload: object,
    normalized_payload: object,
    upstream_raw_unavailable_reason: str,
) -> dict[str, Any]:
    """Build one immutable receipt for a live adapter invocation."""

    provider = _required_text(provider_id, "provider_id")
    operation_name = _required_text(operation, "operation")
    contract = _required_text(adapter_contract_version, "adapter_contract_version")
    script_bytes = (
        adapter_script.encode("utf-8")
        if isinstance(adapter_script, str)
        else bytes(adapter_script)
    )
    if not script_bytes:
        raise ProviderReceiptValidationError("adapter_script must not be empty")
    raw_stdout = bytes(stdout_bytes)
    started_at = _aware_utc_text(request_started_at, "request_started_at")
    completed_at = _aware_utc_text(
        response_completed_at,
        "response_completed_at",
    )
    if datetime.fromisoformat(completed_at) < datetime.fromisoformat(started_at):
        raise ProviderReceiptValidationError(
            "response_completed_at cannot predate request_started_at"
        )
    status = _required_text(response_status, "response_status")
    if status not in _ALLOWED_RESPONSE_STATUSES:
        raise ProviderReceiptValidationError("response_status is unsupported")
    if status == "success" and (parsed_payload is None or normalized_payload is None):
        raise ProviderReceiptValidationError(
            "successful provider receipts require parsed and normalized payloads"
        )

    parameters = deepcopy(dict(request_parameters))
    cache_material = deepcopy(dict(cache_key_material))
    parsed = deepcopy(parsed_payload)
    request_material = {
        "provider_id": provider,
        "operation": operation_name,
        "adapter_contract_version": contract,
        "parameters": parameters,
    }
    receipt: dict[str, Any] = {
        "schema_version": PROVIDER_ORIGIN_RECEIPT_SCHEMA_VERSION,
        "capture_mode": "live",
        "provider_id": provider,
        "operation": operation_name,
        "upstream_raw_available": False,
        "upstream_raw_unavailable_reason": _required_text(
            upstream_raw_unavailable_reason,
            "upstream_raw_unavailable_reason",
        ),
        "adapter": {
            "contract_version": contract,
            "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
            "library_name": _required_text(library_name, "library_name"),
            "library_version": _required_text(library_version, "library_version"),
            "python_version": _required_text(python_version, "python_version"),
        },
        "request": {
            "started_at": started_at,
            "parameters": parameters,
            "request_hash": canonical_provider_hash(request_material),
        },
        "cache": {
            "policy": _required_text(cache_policy, "cache_policy"),
            "key_hash": canonical_provider_hash(cache_material),
            "origin_fetched_at": completed_at,
        },
        "response": {
            "completed_at": completed_at,
            "status": status,
            "stdout_encoding": "base64",
            "stdout_base64": base64.b64encode(raw_stdout).decode("ascii"),
            "stdout_sha256": hashlib.sha256(raw_stdout).hexdigest(),
            "stdout_size_bytes": len(raw_stdout),
            "parsed_payload": parsed,
            "parsed_payload_hash": canonical_provider_hash(parsed),
            "normalized_payload_hash": canonical_provider_hash(normalized_payload),
        },
        "automatic_promotion_allowed": False,
    }
    receipt["origin_receipt_hash"] = canonical_provider_hash(receipt)
    validate_provider_origin_receipt(
        receipt,
        normalized_payload=normalized_payload,
    )
    return receipt


def validate_provider_origin_receipt(
    receipt: Mapping[str, Any],
    *,
    normalized_payload: object = _UNSET,
) -> None:
    """Recompute the self-contained hashes of one origin receipt."""

    if not isinstance(receipt, Mapping):
        raise ProviderReceiptValidationError("provider origin receipt must be an object")
    _require_exact_fields(
        receipt,
        {
            "schema_version",
            "capture_mode",
            "provider_id",
            "operation",
            "upstream_raw_available",
            "upstream_raw_unavailable_reason",
            "adapter",
            "request",
            "cache",
            "response",
            "automatic_promotion_allowed",
            "origin_receipt_hash",
        },
        "provider origin receipt",
    )
    if receipt.get("schema_version") != PROVIDER_ORIGIN_RECEIPT_SCHEMA_VERSION:
        raise ProviderReceiptValidationError("provider origin receipt schema is invalid")
    if receipt.get("capture_mode") != "live":
        raise ProviderReceiptValidationError("provider capture_mode must be live")
    if receipt.get("upstream_raw_available") is not False:
        raise ProviderReceiptValidationError(
            "adapter receipt must not claim unavailable upstream raw bytes"
        )
    if receipt.get("automatic_promotion_allowed") is not False:
        raise ProviderReceiptValidationError(
            "provider receipts must never allow automatic promotion"
        )
    provider = _required_text(receipt.get("provider_id"), "provider_id")
    operation = _required_text(receipt.get("operation"), "operation")
    _required_text(
        receipt.get("upstream_raw_unavailable_reason"),
        "upstream_raw_unavailable_reason",
    )

    adapter = receipt.get("adapter")
    request = receipt.get("request")
    cache = receipt.get("cache")
    response = receipt.get("response")
    if not all(isinstance(value, Mapping) for value in (adapter, request, cache, response)):
        raise ProviderReceiptValidationError("provider receipt sections must be objects")
    assert isinstance(adapter, Mapping)
    assert isinstance(request, Mapping)
    assert isinstance(cache, Mapping)
    assert isinstance(response, Mapping)
    _require_exact_fields(
        adapter,
        {
            "contract_version",
            "script_sha256",
            "library_name",
            "library_version",
            "python_version",
        },
        "provider adapter",
    )
    _require_exact_fields(
        request,
        {"started_at", "parameters", "request_hash"},
        "provider request",
    )
    _require_exact_fields(
        cache,
        {"policy", "key_hash", "origin_fetched_at"},
        "provider cache",
    )
    _require_exact_fields(
        response,
        {
            "completed_at",
            "status",
            "stdout_encoding",
            "stdout_base64",
            "stdout_sha256",
            "stdout_size_bytes",
            "parsed_payload",
            "parsed_payload_hash",
            "normalized_payload_hash",
        },
        "provider response",
    )
    contract = _required_text(adapter.get("contract_version"), "contract_version")
    for name in ("script_sha256",):
        _require_sha256(adapter.get(name), name)
    for name in ("library_name", "library_version", "python_version"):
        _required_text(adapter.get(name), name)
    parameters = request.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ProviderReceiptValidationError("request.parameters must be an object")
    expected_request_hash = canonical_provider_hash(
        {
            "provider_id": provider,
            "operation": operation,
            "adapter_contract_version": contract,
            "parameters": dict(parameters),
        }
    )
    if request.get("request_hash") != expected_request_hash:
        raise ProviderReceiptValidationError("provider request hash mismatch")
    _required_text(cache.get("policy"), "cache.policy")
    _require_sha256(cache.get("key_hash"), "cache.key_hash")

    started_at = _aware_utc_text(request.get("started_at"), "request.started_at")
    completed_at = _aware_utc_text(
        response.get("completed_at"),
        "response.completed_at",
    )
    if request.get("started_at") != started_at or response.get("completed_at") != completed_at:
        raise ProviderReceiptValidationError("provider receipt timestamps are not canonical UTC")
    if datetime.fromisoformat(completed_at) < datetime.fromisoformat(started_at):
        raise ProviderReceiptValidationError("provider response predates its request")
    origin_fetched_at = _aware_utc_text(
        cache.get("origin_fetched_at"),
        "cache.origin_fetched_at",
    )
    if cache.get("origin_fetched_at") != origin_fetched_at:
        raise ProviderReceiptValidationError(
            "provider cache origin time is not canonical UTC"
        )
    if origin_fetched_at != completed_at:
        raise ProviderReceiptValidationError(
            "provider cache origin time conflicts with response completion"
        )
    status = _required_text(response.get("status"), "response.status")
    if status not in _ALLOWED_RESPONSE_STATUSES:
        raise ProviderReceiptValidationError("provider response status is unsupported")
    if response.get("stdout_encoding") != "base64":
        raise ProviderReceiptValidationError("provider stdout encoding is invalid")
    stdout_text = response.get("stdout_base64")
    if not isinstance(stdout_text, str):
        raise ProviderReceiptValidationError("provider stdout_base64 must be a string")
    try:
        stdout = base64.b64decode(stdout_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProviderReceiptValidationError("provider stdout_base64 is invalid") from exc
    if type(response.get("stdout_size_bytes")) is not int or int(
        response["stdout_size_bytes"]
    ) < 0:
        raise ProviderReceiptValidationError("provider stdout size is invalid")
    if len(stdout) != response.get("stdout_size_bytes"):
        raise ProviderReceiptValidationError("provider stdout size mismatch")
    if hashlib.sha256(stdout).hexdigest() != response.get("stdout_sha256"):
        raise ProviderReceiptValidationError("provider stdout hash mismatch")
    if canonical_provider_hash(response.get("parsed_payload")) != response.get(
        "parsed_payload_hash"
    ):
        raise ProviderReceiptValidationError("provider parsed payload hash mismatch")
    _require_sha256(
        response.get("normalized_payload_hash"),
        "response.normalized_payload_hash",
    )
    if normalized_payload is not _UNSET and canonical_provider_hash(
        normalized_payload
    ) != response.get("normalized_payload_hash"):
        raise ProviderReceiptValidationError("provider normalized payload hash mismatch")
    if status == "success" and response.get("parsed_payload") is None:
        raise ProviderReceiptValidationError(
            "successful provider receipt has no parsed payload"
        )
    expected_receipt_hash = canonical_provider_hash(
        {key: value for key, value in receipt.items() if key != "origin_receipt_hash"}
    )
    if receipt.get("origin_receipt_hash") != expected_receipt_hash:
        raise ProviderReceiptValidationError("provider origin receipt hash mismatch")


def build_provider_delivery(
    *,
    origin_receipt: Mapping[str, Any],
    cache_status: str,
    cache_layer: str,
    served_at: str | datetime,
) -> dict[str, Any]:
    """Build delivery metadata without changing the frozen origin receipt."""

    validate_provider_origin_receipt(origin_receipt)
    status = _required_text(cache_status, "cache_status")
    if status not in _ALLOWED_CACHE_STATUSES:
        raise ProviderReceiptValidationError("cache_status is unsupported")
    served = _aware_utc_text(served_at, "served_at")
    response = origin_receipt["response"]
    assert isinstance(response, Mapping)
    completed = _aware_utc_text(response.get("completed_at"), "response.completed_at")
    if datetime.fromisoformat(served) < datetime.fromisoformat(completed):
        raise ProviderReceiptValidationError("provider delivery predates its origin")
    cache = origin_receipt["cache"]
    assert isinstance(cache, Mapping)
    delivery: dict[str, Any] = {
        "schema_version": PROVIDER_DELIVERY_SCHEMA_VERSION,
        "cache_status": status,
        "cache_layer": _required_text(cache_layer, "cache_layer"),
        "served_at": served,
        "origin_receipt_hash": origin_receipt["origin_receipt_hash"],
        "cache_key_hash": cache["key_hash"],
    }
    delivery["delivery_hash"] = canonical_provider_hash(delivery)
    validate_provider_delivery(delivery, origin_receipt=origin_receipt)
    return delivery


def validate_provider_delivery(
    delivery: Mapping[str, Any],
    *,
    origin_receipt: Mapping[str, Any],
) -> None:
    if not isinstance(delivery, Mapping):
        raise ProviderReceiptValidationError("provider delivery must be an object")
    validate_provider_origin_receipt(origin_receipt)
    _require_exact_fields(
        delivery,
        {
            "schema_version",
            "cache_status",
            "cache_layer",
            "served_at",
            "origin_receipt_hash",
            "cache_key_hash",
            "delivery_hash",
        },
        "provider delivery",
    )
    if delivery.get("schema_version") != PROVIDER_DELIVERY_SCHEMA_VERSION:
        raise ProviderReceiptValidationError("provider delivery schema is invalid")
    if delivery.get("cache_status") not in _ALLOWED_CACHE_STATUSES:
        raise ProviderReceiptValidationError("provider delivery cache status is invalid")
    _required_text(delivery.get("cache_layer"), "cache_layer")
    served = _aware_utc_text(delivery.get("served_at"), "served_at")
    if delivery.get("served_at") != served:
        raise ProviderReceiptValidationError("provider delivery time is not canonical UTC")
    response = origin_receipt["response"]
    cache = origin_receipt["cache"]
    assert isinstance(response, Mapping) and isinstance(cache, Mapping)
    completed = _aware_utc_text(response.get("completed_at"), "response.completed_at")
    if datetime.fromisoformat(served) < datetime.fromisoformat(completed):
        raise ProviderReceiptValidationError("provider delivery predates its origin")
    if delivery.get("origin_receipt_hash") != origin_receipt.get(
        "origin_receipt_hash"
    ):
        raise ProviderReceiptValidationError("provider delivery origin hash mismatch")
    if delivery.get("cache_key_hash") != cache.get("key_hash"):
        raise ProviderReceiptValidationError("provider delivery cache key mismatch")
    expected = canonical_provider_hash(
        {key: value for key, value in delivery.items() if key != "delivery_hash"}
    )
    if delivery.get("delivery_hash") != expected:
        raise ProviderReceiptValidationError("provider delivery hash mismatch")


def build_provider_read(
    *,
    origin_receipt: Mapping[str, Any],
    normalized_payload: object,
    cache_status: str,
    cache_layer: str,
    served_at: str | datetime,
) -> DecisionQualityProviderRead:
    origin = deepcopy(dict(origin_receipt))
    normalized = deepcopy(normalized_payload)
    validate_provider_origin_receipt(origin, normalized_payload=normalized)
    delivery = build_provider_delivery(
        origin_receipt=origin,
        cache_status=cache_status,
        cache_layer=cache_layer,
        served_at=served_at,
    )
    result = DecisionQualityProviderRead(
        origin_receipt=origin,
        normalized_payload=normalized,
        delivery=delivery,
    )
    validate_provider_read(result)
    return result


def validate_provider_read(read: DecisionQualityProviderRead) -> None:
    if not isinstance(read, DecisionQualityProviderRead):
        raise ProviderReceiptValidationError("provider read has an unsupported type")
    validate_provider_origin_receipt(
        read.origin_receipt,
        normalized_payload=read.normalized_payload,
    )
    validate_provider_delivery(
        read.delivery,
        origin_receipt=read.origin_receipt,
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProviderReceiptValidationError(f"{name} must be a canonical non-empty string")
    return value


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ProviderReceiptValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _aware_utc_text(value: object, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        if not isinstance(value, str) or not value.strip():
            raise ProviderReceiptValidationError(f"{name} must be an aware timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderReceiptValidationError(
                f"{name} must be an ISO timestamp"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderReceiptValidationError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _require_exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ProviderReceiptValidationError(
            f"{name} fields are invalid; missing={missing}, extra={extra}"
        )


__all__ = [
    "DecisionQualityProviderRead",
    "PROVIDER_DELIVERY_SCHEMA_VERSION",
    "PROVIDER_ORIGIN_RECEIPT_SCHEMA_VERSION",
    "PROVIDER_READ_SCHEMA_VERSION",
    "ProviderReceiptValidationError",
    "build_provider_delivery",
    "build_provider_origin_receipt",
    "build_provider_read",
    "canonical_provider_hash",
    "canonical_provider_json",
    "validate_provider_delivery",
    "validate_provider_origin_receipt",
    "validate_provider_read",
]
