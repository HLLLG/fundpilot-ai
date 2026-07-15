from __future__ import annotations

import base64
from copy import deepcopy
import hashlib

import pytest

from app.services.decision_quality_provider_receipts import (
    ProviderReceiptValidationError,
    build_provider_origin_receipt,
    build_provider_read,
    canonical_provider_hash,
    validate_provider_origin_receipt,
    validate_provider_read,
)


_STARTED = "2026-07-15T00:00:00+00:00"
_COMPLETED = "2026-07-15T00:00:01+00:00"
_SERVED = "2026-07-15T00:00:02+00:00"


def _receipt(*, stdout: bytes = b'diagnostic\n{"data":[1]}\r\n') -> dict:
    return build_provider_origin_receipt(
        provider_id="akshare.fixture",
        operation="fixture_operation",
        request_parameters={"fund_code": "000001", "trading_days": 90},
        request_started_at=_STARTED,
        response_completed_at=_COMPLETED,
        response_status="success",
        adapter_contract_version="fixture_adapter.v1",
        adapter_script="print('fixture')",
        library_name="akshare",
        library_version="1.2.3",
        python_version="3.13.5",
        cache_policy="fixture_hour_cache.v1",
        cache_key_material={"code": "000001", "hour": 10},
        stdout_bytes=stdout,
        parsed_payload={"data": [1]},
        normalized_payload={"data": [1]},
        upstream_raw_unavailable_reason="fixture adapter boundary",
    )


def test_origin_receipt_freezes_exact_stdout_request_versions_and_payload_hashes() -> None:
    stdout = b'diagnostic\n{"data":[1]}\r\n'
    receipt = _receipt(stdout=stdout)

    validate_provider_origin_receipt(
        receipt,
        normalized_payload={"data": [1]},
    )
    response = receipt["response"]
    assert base64.b64decode(response["stdout_base64"], validate=True) == stdout
    assert response["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
    assert response["stdout_size_bytes"] == len(stdout)
    assert response["parsed_payload"] == {"data": [1]}
    assert receipt["request"]["parameters"] == {
        "fund_code": "000001",
        "trading_days": 90,
    }
    assert receipt["adapter"] == {
        "contract_version": "fixture_adapter.v1",
        "script_sha256": hashlib.sha256(b"print('fixture')").hexdigest(),
        "library_name": "akshare",
        "library_version": "1.2.3",
        "python_version": "3.13.5",
    }
    assert receipt["capture_mode"] == "live"
    assert receipt["upstream_raw_available"] is False
    assert receipt["cache"]["origin_fetched_at"] == _COMPLETED


def test_read_delivery_does_not_rewrite_origin_on_cache_hit() -> None:
    receipt = _receipt()
    origin_hash = receipt["origin_receipt_hash"]
    read = build_provider_read(
        origin_receipt=receipt,
        normalized_payload={"data": [1]},
        cache_status="hit",
        cache_layer="process",
        served_at=_SERVED,
    )

    validate_provider_read(read)
    assert read.origin_receipt["origin_receipt_hash"] == origin_hash
    assert read.origin_receipt["response"]["completed_at"] == _COMPLETED
    assert read.origin_receipt["cache"]["origin_fetched_at"] == _COMPLETED
    assert read.delivery["cache_status"] == "hit"
    assert read.delivery["served_at"] == _SERVED
    assert read.delivery["origin_receipt_hash"] == origin_hash


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda value: value["request"]["parameters"].update(trading_days=91),
            "request hash mismatch",
        ),
        (
            lambda value: value["response"].update(stdout_base64="eA=="),
            "stdout size mismatch|stdout hash mismatch",
        ),
        (
            lambda value: value["response"].update(parsed_payload={"data": [2]}),
            "parsed payload hash mismatch",
        ),
    ],
)
def test_origin_receipt_rejects_tampered_material(mutation, match: str) -> None:
    receipt = deepcopy(_receipt())
    mutation(receipt)

    with pytest.raises(ProviderReceiptValidationError, match=match):
        validate_provider_origin_receipt(
            receipt,
            normalized_payload={"data": [1]},
        )


def test_reconstructed_capture_cannot_be_resigned_as_live() -> None:
    receipt = deepcopy(_receipt())
    receipt["capture_mode"] = "reconstructed"
    receipt["origin_receipt_hash"] = canonical_provider_hash(
        {key: value for key, value in receipt.items() if key != "origin_receipt_hash"}
    )

    with pytest.raises(ProviderReceiptValidationError, match="capture_mode must be live"):
        validate_provider_origin_receipt(
            receipt,
            normalized_payload={"data": [1]},
        )


def test_normalized_payload_must_match_origin_hash() -> None:
    receipt = _receipt()

    with pytest.raises(
        ProviderReceiptValidationError,
        match="normalized payload hash mismatch",
    ):
        build_provider_read(
            origin_receipt=receipt,
            normalized_payload={"data": [999]},
            cache_status="miss",
            cache_layer="live",
            served_at=_COMPLETED,
        )
