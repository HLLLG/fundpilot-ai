from __future__ import annotations

import inspect

from app.services import (
    analysis_payload,
    deepseek_client,
    discovery_client,
    discovery_offline,
    discovery_payload,
    discovery_pipeline,
    discovery_streaming,
)


def test_legacy_daily_lookthrough_facts_are_removed_from_llm_payload() -> None:
    facts = {
        "readonly": True,
        "session": {},
        "holdings": [],
        "portfolio": {},
        "fund_lookthrough": {"status": "qualified", "private": "sentinel"},
        "fund_lookthrough_claim_audit": {"status": "legacy"},
    }

    trimmed = analysis_payload.trim_analysis_facts_for_llm(facts)

    assert "fund_lookthrough" not in trimmed
    assert "fund_lookthrough_claim_audit" not in trimmed
    assert facts["fund_lookthrough"]["private"] == "sentinel"


def test_new_daily_and_discovery_paths_do_not_build_lookthrough_context() -> None:
    sources = {
        "analysis_payload": inspect.getsource(analysis_payload),
        "discovery_pipeline": inspect.getsource(discovery_pipeline),
        "discovery_streaming": inspect.getsource(discovery_streaming),
    }

    for source in sources.values():
        assert "build_fund_lookthrough_context" not in source

    assert 'facts["fund_lookthrough"]' not in sources["analysis_payload"]
    assert 'discovery_facts["fund_lookthrough"]' not in sources["discovery_pipeline"]
    assert 'discovery_facts["fund_lookthrough"]' not in sources["discovery_streaming"]


def test_model_and_report_paths_no_longer_activate_lookthrough_claim_handling() -> None:
    sources = [
        inspect.getsource(deepseek_client),
        inspect.getsource(discovery_client),
        inspect.getsource(discovery_offline),
        inspect.getsource(discovery_payload),
    ]

    for source in sources:
        assert "validate_fund_lookthrough_claims" not in source
        assert "HOLDINGS_LOOKTHROUGH_REQUIREMENT" not in source
