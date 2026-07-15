from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from app.services import akshare_subprocess as nav_adapter
from app.services import trade_calendar_cache as calendar_adapter
from app.services.akshare_subprocess import (
    fund_nav_quality_adapter_policy_material,
)
from app.services.decision_quality_provider_policy import (
    CANDIDATE_CALENDAR_ADAPTER_POLICY_ID,
    CANDIDATE_NAV_ADAPTER_POLICY_ID,
    CandidateProviderAdapterPolicyError,
    verify_candidate_provider_adapter_policy,
)
from app.services.decision_quality_provider_receipts import (
    build_provider_origin_receipt,
)
from app.services.trade_calendar_cache import (
    trade_calendar_quality_adapter_policy_material,
)


_COMPLETED = datetime(2026, 1, 24, 8, 0, tzinfo=timezone.utc)
_STARTED = _COMPLETED - timedelta(seconds=1)
_CACHE_HOUR = int(_STARTED.timestamp() // 3600)


def _origin(
    material: dict[str, object],
    *,
    parsed_payload: object,
    normalized_payload: object,
    adapter_script: str | None = None,
    adapter_contract_version: str | None = None,
    cache_key_material: dict[str, object] | None = None,
    started: datetime = _STARTED,
    completed: datetime = _COMPLETED,
) -> dict:
    stdout = json.dumps(
        parsed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return build_provider_origin_receipt(
        provider_id=str(material["provider_id"]),
        operation=str(material["operation"]),
        request_parameters=dict(material["request_parameters"]),
        request_started_at=started,
        response_completed_at=completed,
        response_status="success",
        adapter_contract_version=(
            adapter_contract_version
            if adapter_contract_version is not None
            else str(material["adapter_contract_version"])
        ),
        adapter_script=(
            adapter_script
            if adapter_script is not None
            else str(material["adapter_script"])
        ),
        library_name="akshare",
        library_version="fixture-version",
        python_version="3.12.fixture",
        cache_policy=str(material["cache_policy"]),
        cache_key_material=(
            cache_key_material
            if cache_key_material is not None
            else dict(material["cache_key_material"])
        ),
        stdout_bytes=stdout,
        parsed_payload=parsed_payload,
        normalized_payload=normalized_payload,
        upstream_raw_unavailable_reason="adapter boundary only",
    )


def test_real_project_adapter_material_has_deterministic_policy_bindings() -> None:
    calendar_material = trade_calendar_quality_adapter_policy_material()
    calendar = _origin(
        calendar_material,
        parsed_payload=["2026-01-02", "2026-01-05"],
        normalized_payload={"dates": ["2026-01-02", "2026-01-05"]},
    )
    calendar_policy = verify_candidate_provider_adapter_policy(calendar)
    assert calendar_policy["adapter_policy_id"] == (
        CANDIDATE_CALENDAR_ADAPTER_POLICY_ID
    )
    assert calendar_policy == verify_candidate_provider_adapter_policy(calendar)

    nav_material = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=_CACHE_HOUR,
    )
    nav_payload = {
        "data": [{"date": "2026-01-02", "nav": 1.0, "daily_growth": 0.1}]
    }
    nav = _origin(
        nav_material,
        parsed_payload=nav_payload,
        normalized_payload=nav_payload,
    )
    nav_policy = verify_candidate_provider_adapter_policy(nav)
    assert nav_policy["adapter_policy_id"] == CANDIDATE_NAV_ADAPTER_POLICY_ID
    assert nav_policy["adapter_policy_hash"] != calendar_policy["adapter_policy_hash"]

    other_nav_material = fund_nav_quality_adapter_policy_material(
        fund_code="100002",
        trading_days=120,
        cache_hour=_CACHE_HOUR + 24,
    )
    other_nav = _origin(
        other_nav_material,
        parsed_payload=nav_payload,
        normalized_payload=nav_payload,
        started=_STARTED + timedelta(days=1),
        completed=_COMPLETED + timedelta(days=1),
    )
    other_nav_policy = verify_candidate_provider_adapter_policy(other_nav)
    assert other_nav_policy["adapter_policy_hash"] == nav_policy[
        "adapter_policy_hash"
    ]
    assert other_nav_policy["adapter_policy_script_sha256"] == nav_policy[
        "adapter_policy_script_sha256"
    ]
    assert other_nav_policy["adapter_script_sha256"] != nav_policy[
        "adapter_script_sha256"
    ]


def test_v1_historical_verifiers_survive_a_future_current_adapter_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar_material = trade_calendar_quality_adapter_policy_material(
        contract_version="decision_quality_trade_calendar_adapter.v1"
    )
    calendar = _origin(
        calendar_material,
        parsed_payload=["2026-01-02"],
        normalized_payload={"dates": ["2026-01-02"]},
    )
    nav_material = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=_CACHE_HOUR,
        contract_version="decision_quality_fund_nav_adapter.v1",
    )
    payload = {"data": [{"date": "2026-01-02", "nav": 1.0}]}
    nav = _origin(
        nav_material,
        parsed_payload=payload,
        normalized_payload=payload,
    )

    monkeypatch.setattr(
        calendar_adapter,
        "_QUALITY_ADAPTER_CONTRACT_VERSION",
        "decision_quality_trade_calendar_adapter.v2",
    )
    monkeypatch.setattr(
        calendar_adapter,
        "_TRADE_CALENDAR_SCRIPT",
        "print('future calendar v2')",
    )
    monkeypatch.setattr(
        nav_adapter,
        "_FUND_NAV_ADAPTER_CONTRACT_VERSION",
        "decision_quality_fund_nav_adapter.v2",
    )
    monkeypatch.setattr(
        nav_adapter,
        "_fund_nav_history_script",
        lambda *_args: "print('future NAV v2')",
    )

    assert verify_candidate_provider_adapter_policy(calendar)[
        "adapter_policy_id"
    ] == CANDIDATE_CALENDAR_ADAPTER_POLICY_ID
    assert verify_candidate_provider_adapter_policy(nav)[
        "adapter_policy_id"
    ] == CANDIDATE_NAV_ADAPTER_POLICY_ID


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("script", "print('self-consistent but not the project adapter')"),
        ("contract", "attacker_calendar_contract.v1"),
    ],
)
def test_self_consistent_arbitrary_calendar_adapter_is_rejected(
    override: str,
    value: str,
) -> None:
    material = trade_calendar_quality_adapter_policy_material()
    origin = _origin(
        material,
        parsed_payload=["2026-01-02"],
        normalized_payload={"dates": ["2026-01-02"]},
        adapter_script=value if override == "script" else None,
        adapter_contract_version=value if override == "contract" else None,
    )
    with pytest.raises(
        CandidateProviderAdapterPolicyError,
        match="production adapter policy|policy registry",
    ):
        verify_candidate_provider_adapter_policy(origin)


def test_nav_script_for_another_fund_cannot_substitute_for_request() -> None:
    requested = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=_CACHE_HOUR,
    )
    other_fund = fund_nav_quality_adapter_policy_material(
        fund_code="100002",
        trading_days=90,
        cache_hour=_CACHE_HOUR,
    )
    payload = {"data": [{"date": "2026-01-02", "nav": 1.0}]}
    origin = _origin(
        requested,
        parsed_payload=payload,
        normalized_payload=payload,
        adapter_script=str(other_fund["adapter_script"]),
    )
    with pytest.raises(CandidateProviderAdapterPolicyError):
        verify_candidate_provider_adapter_policy(origin)


def test_nav_cache_key_for_different_request_cannot_be_rehashed_into_policy() -> None:
    requested = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=90,
        cache_hour=_CACHE_HOUR,
    )
    other_request = fund_nav_quality_adapter_policy_material(
        fund_code="100001",
        trading_days=91,
        cache_hour=_CACHE_HOUR,
    )
    payload = {"data": [{"date": "2026-01-02", "nav": 1.0}]}
    origin = _origin(
        requested,
        parsed_payload=payload,
        normalized_payload=payload,
        cache_key_material=dict(other_request["cache_key_material"]),
    )
    with pytest.raises(CandidateProviderAdapterPolicyError):
        verify_candidate_provider_adapter_policy(origin)
