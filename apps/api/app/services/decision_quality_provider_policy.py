"""Project-anchored adapter policy for formal candidate provider evidence.

Content-addressing proves that a receipt is internally self-consistent.  It
does not prove that the self-declared adapter is one this project actually
runs.  This module closes that gap by rebuilding the two production adapters
from their owning modules and comparing the exact contract, script, request,
and cache-key material before an origin can enter D4 source-verified evidence.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

from app.services.decision_quality_provider_receipts import (
    ProviderReceiptValidationError,
    canonical_provider_hash,
    validate_provider_origin_receipt,
)


CANDIDATE_PROVIDER_ADAPTER_POLICY_SCHEMA_VERSION = (
    "decision_quality_candidate_provider_adapter_policy.v1"
)
CANDIDATE_CALENDAR_ADAPTER_POLICY_ID = (
    "candidate_provider_adapter_policy.akshare_trade_calendar.v1"
)
CANDIDATE_NAV_ADAPTER_POLICY_ID = (
    "candidate_provider_adapter_policy.akshare_fund_nav.v1"
)
CANDIDATE_PROVIDER_ADAPTER_POLICY_REGISTRY = (
    (
        "akshare.tool_trade_date_hist_sina",
        "tool_trade_date_hist_sina",
        "decision_quality_trade_calendar_adapter.v1",
        CANDIDATE_CALENDAR_ADAPTER_POLICY_ID,
    ),
    (
        "akshare.fund_open_fund_info_em",
        "fund_open_fund_info_em",
        "decision_quality_fund_nav_adapter.v1",
        CANDIDATE_NAV_ADAPTER_POLICY_ID,
    ),
)


class CandidateProviderAdapterPolicyError(ValueError):
    """A self-consistent origin is not an approved production adapter read."""


def verify_candidate_provider_adapter_policy(
    origin_receipt: Mapping[str, Any],
) -> dict[str, str]:
    """Return the deterministic formal-policy binding for one exact origin.

    Library and Python versions remain captured source metadata.  They are not
    silently elevated into policy identity, which keeps them available for
    later stratification without allowing a version label to substitute for
    the exact project contract and script hash.
    """

    try:
        validate_provider_origin_receipt(origin_receipt)
    except ProviderReceiptValidationError as exc:
        raise CandidateProviderAdapterPolicyError(
            "provider origin failed its content-integrity contract"
        ) from exc

    adapter = origin_receipt.get("adapter")
    request = origin_receipt.get("request")
    cache = origin_receipt.get("cache")
    if not all(isinstance(value, Mapping) for value in (adapter, request, cache)):
        raise CandidateProviderAdapterPolicyError(
            "provider origin lacks adapter policy sections"
        )
    assert isinstance(adapter, Mapping)
    assert isinstance(request, Mapping)
    assert isinstance(cache, Mapping)
    parameters = request.get("parameters")
    if not isinstance(parameters, Mapping):
        raise CandidateProviderAdapterPolicyError(
            "provider origin request parameters are missing"
        )
    declared_contract = adapter.get("contract_version")
    policy_id = candidate_adapter_policy_id_for_contract(
        origin_receipt.get("provider_id"),
        origin_receipt.get("operation"),
        declared_contract,
    )
    if policy_id is None:
        raise CandidateProviderAdapterPolicyError(
            "provider adapter contract is not in the append-only policy registry"
        )

    provider = origin_receipt.get("provider_id")
    operation = origin_receipt.get("operation")
    if (
        provider == "akshare.tool_trade_date_hist_sina"
        and operation == "tool_trade_date_hist_sina"
    ):
        from app.services.trade_calendar_cache import (
            trade_calendar_quality_adapter_policy_material,
        )

        try:
            expected = trade_calendar_quality_adapter_policy_material(
                contract_version=str(declared_contract),
            )
        except ValueError as exc:
            raise CandidateProviderAdapterPolicyError(
                "calendar adapter contract has no historical verifier"
            ) from exc
    elif (
        provider == "akshare.fund_open_fund_info_em"
        and operation == "fund_open_fund_info_em"
    ):
        from app.services.akshare_subprocess import (
            fund_nav_quality_adapter_policy_material,
        )

        trading_days = parameters.get("trading_days")
        fund_code = parameters.get("fund_code")
        if (
            set(parameters) != {"fund_code", "trading_days", "indicator"}
            or not isinstance(fund_code, str)
            or not fund_code
            or fund_code != fund_code.strip()
            or type(trading_days) is not int
            or int(trading_days) <= 0
        ):
            raise CandidateProviderAdapterPolicyError(
                "NAV request cannot rebuild the production adapter"
            )
        try:
            expected = fund_nav_quality_adapter_policy_material(
                fund_code=fund_code,
                trading_days=int(trading_days),
                cache_hour=_request_cache_hour(request.get("started_at")),
                contract_version=str(declared_contract),
            )
        except ValueError as exc:
            raise CandidateProviderAdapterPolicyError(
                "NAV adapter contract has no historical verifier"
            ) from exc
    else:
        raise CandidateProviderAdapterPolicyError(
            "provider operation is outside the formal candidate policy"
        )

    script = expected["adapter_script"]
    if not isinstance(script, str) or not script:
        raise CandidateProviderAdapterPolicyError(
            "production adapter policy has no script"
        )
    script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
    expected_parameters = expected["request_parameters"]
    expected_cache_key_hash = canonical_provider_hash(expected["cache_key_material"])
    if (
        origin_receipt.get("capture_mode") != "live"
        or provider != expected["provider_id"]
        or operation != expected["operation"]
        or dict(parameters) != expected_parameters
        or adapter.get("contract_version")
        != expected["adapter_contract_version"]
        or adapter.get("script_sha256") != script_sha256
        or adapter.get("library_name") != expected["library_name"]
        or cache.get("policy") != expected["cache_policy"]
        or cache.get("key_hash") != expected_cache_key_hash
    ):
        raise CandidateProviderAdapterPolicyError(
            "provider origin does not match the production adapter policy"
        )

    identity = _adapter_policy_identity(expected, policy_id=policy_id)
    return {
        "adapter_policy_id": policy_id,
        "adapter_policy_hash": identity["adapter_policy_hash"],
        "adapter_contract_version": str(adapter["contract_version"]),
        "adapter_script_sha256": script_sha256,
        "adapter_policy_script_sha256": identity[
            "adapter_policy_script_sha256"
        ],
        "adapter_library_name": str(adapter["library_name"]),
        "adapter_library_version": str(adapter["library_version"]),
        "adapter_python_version": str(adapter["python_version"]),
    }


def rebuild_candidate_provider_normalized_payload(
    origin_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the consumed payload from exact adapter stdout bytes."""

    verify_candidate_provider_adapter_policy(origin_receipt)
    response = origin_receipt.get("response")
    if not isinstance(response, Mapping) or response.get("status") != "success":
        raise CandidateProviderAdapterPolicyError(
            "candidate provider receipt is not a successful origin"
        )
    stdout_text = response.get("stdout_base64")
    if not isinstance(stdout_text, str):
        raise CandidateProviderAdapterPolicyError(
            "candidate provider adapter stdout is missing"
        )
    try:
        decoded_stdout = base64.b64decode(stdout_text, validate=True).decode(
            "utf-8"
        ).strip()
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise CandidateProviderAdapterPolicyError(
            "candidate provider adapter stdout is not canonical UTF-8"
        ) from exc
    parsed_from_stdout: object | None = None
    for candidate in (decoded_stdout, *reversed(decoded_stdout.splitlines())):
        if not candidate.strip():
            continue
        try:
            parsed_from_stdout = json.loads(candidate.strip())
            break
        except json.JSONDecodeError:
            continue
    parsed = response.get("parsed_payload")
    if (
        parsed_from_stdout is None
        or canonical_provider_hash(parsed_from_stdout)
        != response.get("parsed_payload_hash")
        or canonical_provider_hash(parsed_from_stdout)
        != canonical_provider_hash(parsed)
    ):
        raise CandidateProviderAdapterPolicyError(
            "candidate provider parsed payload is not derived from adapter stdout"
        )

    provider = origin_receipt.get("provider_id")
    operation = origin_receipt.get("operation")
    if (
        provider == "akshare.fund_open_fund_info_em"
        and operation == "fund_open_fund_info_em"
    ):
        if not isinstance(parsed, Mapping):
            raise CandidateProviderAdapterPolicyError(
                "candidate NAV provider parsed payload is invalid"
            )
        rows = parsed.get("data")
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) for row in rows
        ):
            raise CandidateProviderAdapterPolicyError(
                "candidate NAV provider parsed rows are invalid"
            )
        normalized: dict[str, Any] = {
            "data": [deepcopy(dict(row)) for row in rows]
        }
    elif (
        provider == "akshare.tool_trade_date_hist_sina"
        and operation == "tool_trade_date_hist_sina"
    ):
        if not isinstance(parsed, list) or not parsed:
            raise CandidateProviderAdapterPolicyError(
                "candidate calendar provider parsed payload is invalid"
            )
        dates: set[str] = set()
        for value in parsed:
            raw = str(value or "")[:10]
            try:
                dates.add(date.fromisoformat(raw).isoformat())
            except ValueError:
                continue
        if not dates:
            raise CandidateProviderAdapterPolicyError(
                "candidate calendar provider has no valid trade date"
            )
        normalized = {"dates": sorted(dates)}
    else:
        raise CandidateProviderAdapterPolicyError(
            "candidate outcome references an unsupported provider operation"
        )
    if canonical_provider_hash(normalized) != response.get(
        "normalized_payload_hash"
    ):
        raise CandidateProviderAdapterPolicyError(
            "candidate provider normalized payload is detached from stdout"
        )
    return normalized


def candidate_adapter_policy_id_for_contract(
    provider: object,
    operation: object,
    contract_version: object,
) -> str | None:
    """Resolve only exact append-only registry entries."""

    matches = [
        policy_id
        for (
            registered_provider,
            registered_operation,
            registered_contract,
            policy_id,
        ) in CANDIDATE_PROVIDER_ADAPTER_POLICY_REGISTRY
        if provider == registered_provider
        and operation == registered_operation
        and contract_version == registered_contract
    ]
    return matches[0] if len(matches) == 1 else None


def candidate_adapter_policy_is_registered(
    *,
    provider: object,
    operation: object,
    contract_version: object,
    policy_id: object,
) -> bool:
    expected = candidate_adapter_policy_id_for_contract(
        provider,
        operation,
        contract_version,
    )
    return expected is not None and policy_id == expected


def registered_candidate_adapter_policy_binding(
    *,
    provider: object,
    operation: object,
    contract_version: object,
) -> dict[str, str] | None:
    """Return the stable registry identity without any request-instance data."""

    policy_id = candidate_adapter_policy_id_for_contract(
        provider,
        operation,
        contract_version,
    )
    if policy_id is None or not isinstance(contract_version, str):
        return None
    try:
        if (
            provider == "akshare.tool_trade_date_hist_sina"
            and operation == "tool_trade_date_hist_sina"
        ):
            from app.services.trade_calendar_cache import (
                trade_calendar_quality_adapter_policy_material,
            )

            expected = trade_calendar_quality_adapter_policy_material(
                contract_version=contract_version,
            )
        elif (
            provider == "akshare.fund_open_fund_info_em"
            and operation == "fund_open_fund_info_em"
        ):
            from app.services.akshare_subprocess import (
                fund_nav_quality_adapter_policy_material,
            )

            expected = fund_nav_quality_adapter_policy_material(
                fund_code="__policy_identity_request__",
                trading_days=1,
                cache_hour=0,
                contract_version=contract_version,
            )
        else:
            return None
        identity = _adapter_policy_identity(expected, policy_id=policy_id)
    except (KeyError, TypeError, ValueError, CandidateProviderAdapterPolicyError):
        return None
    return {
        "adapter_policy_id": policy_id,
        "adapter_policy_hash": identity["adapter_policy_hash"],
        "adapter_contract_version": contract_version,
        "adapter_policy_script_sha256": identity[
            "adapter_policy_script_sha256"
        ],
        "adapter_library_name": str(expected["library_name"]),
    }


def candidate_provider_adapter_stratum(
    refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Project request-specific refs into stable, source-runtime strata."""

    result: dict[str, dict[str, str]] = {}
    fields = (
        "provider",
        "operation",
        "adapter_policy_id",
        "adapter_policy_hash",
        "adapter_contract_version",
        "adapter_policy_script_sha256",
        "adapter_library_name",
        "adapter_library_version",
        "adapter_python_version",
    )
    for raw in refs:
        if not isinstance(raw, Mapping):
            raise CandidateProviderAdapterPolicyError(
                "provider stratum reference is not an object"
            )
        descriptor: dict[str, str] = {}
        for field in fields:
            value = raw.get(field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise CandidateProviderAdapterPolicyError(
                    "provider stratum metadata is incomplete"
                )
            descriptor[field] = value
        if not candidate_adapter_policy_is_registered(
            provider=descriptor["provider"],
            operation=descriptor["operation"],
            contract_version=descriptor["adapter_contract_version"],
            policy_id=descriptor["adapter_policy_id"],
        ) or any(
            not _is_sha256(descriptor[field])
            for field in (
                "adapter_policy_hash",
                "adapter_policy_script_sha256",
            )
        ):
            raise CandidateProviderAdapterPolicyError(
                "provider stratum policy binding is invalid"
            )
        registered = registered_candidate_adapter_policy_binding(
            provider=descriptor["provider"],
            operation=descriptor["operation"],
            contract_version=descriptor["adapter_contract_version"],
        )
        if (
            registered is None
            or descriptor["adapter_policy_id"]
            != registered["adapter_policy_id"]
            or descriptor["adapter_policy_hash"]
            != registered["adapter_policy_hash"]
            or descriptor["adapter_policy_script_sha256"]
            != registered["adapter_policy_script_sha256"]
            or descriptor["adapter_library_name"]
            != registered["adapter_library_name"]
        ):
            raise CandidateProviderAdapterPolicyError(
                "provider stratum does not match its registered policy identity"
            )
        identity = canonical_provider_hash(descriptor)
        result[identity] = descriptor
    return sorted(
        result.values(),
        key=lambda item: (
            item["provider"],
            item["operation"],
            item["adapter_policy_id"],
            item["adapter_library_version"],
            item["adapter_python_version"],
        ),
    )


def candidate_provider_adapter_stratum_hash(
    refs: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_provider_hash(candidate_provider_adapter_stratum(refs))


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _adapter_policy_identity(
    expected: Mapping[str, object],
    *,
    policy_id: str,
) -> dict[str, str]:
    policy_script = expected.get("adapter_policy_script")
    if not isinstance(policy_script, str) or not policy_script:
        raise CandidateProviderAdapterPolicyError(
            "production adapter policy has no stable script template"
        )
    policy_script_sha256 = hashlib.sha256(
        policy_script.encode("utf-8")
    ).hexdigest()
    cache_key_policy_material = expected.get("cache_key_policy_material")
    if not isinstance(cache_key_policy_material, Mapping):
        raise CandidateProviderAdapterPolicyError(
            "production adapter policy has no stable cache-key algorithm"
        )
    policy_material = {
        "schema_version": CANDIDATE_PROVIDER_ADAPTER_POLICY_SCHEMA_VERSION,
        "adapter_policy_id": policy_id,
        "provider_id": expected.get("provider_id"),
        "operation": expected.get("operation"),
        "adapter_contract_version": expected.get("adapter_contract_version"),
        "adapter_policy_script_sha256": policy_script_sha256,
        "cache_policy": expected.get("cache_policy"),
        "cache_key_policy_hash": canonical_provider_hash(
            cache_key_policy_material
        ),
        "library_name_allowlist": [expected.get("library_name")],
    }
    return {
        "adapter_policy_hash": canonical_provider_hash(policy_material),
        "adapter_policy_script_sha256": policy_script_sha256,
    }


def _request_cache_hour(value: object) -> int:
    if not isinstance(value, str):
        raise CandidateProviderAdapterPolicyError(
            "provider request start clock is missing"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CandidateProviderAdapterPolicyError(
            "provider request start clock is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateProviderAdapterPolicyError(
            "provider request start clock is naive"
        )
    return int(parsed.astimezone(timezone.utc).timestamp() // 3600)


__all__ = [
    "CANDIDATE_CALENDAR_ADAPTER_POLICY_ID",
    "CANDIDATE_NAV_ADAPTER_POLICY_ID",
    "CANDIDATE_PROVIDER_ADAPTER_POLICY_SCHEMA_VERSION",
    "CANDIDATE_PROVIDER_ADAPTER_POLICY_REGISTRY",
    "CandidateProviderAdapterPolicyError",
    "candidate_adapter_policy_id_for_contract",
    "candidate_adapter_policy_is_registered",
    "candidate_provider_adapter_stratum",
    "candidate_provider_adapter_stratum_hash",
    "registered_candidate_adapter_policy_binding",
    "rebuild_candidate_provider_normalized_payload",
    "verify_candidate_provider_adapter_policy",
]
