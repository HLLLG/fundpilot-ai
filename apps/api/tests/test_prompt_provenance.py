from __future__ import annotations

from app.services.prompt_provenance import (
    PROMPT_CONTRACT_SCHEMA_VERSION,
    build_prompt_contract,
    content_hash,
    with_judge_result,
)


def _contract(*, appendix: str = "偏好低换手", temperature: float = 0.2) -> dict:
    user_payload = {"today": "2026-07-14", "facts": {"score": 88}}
    messages = [
        {"role": "system", "content": f"SYSTEM\n{appendix}"},
        {"role": "user", "content": '{"today":"2026-07-14","facts":{"score":88}}'},
    ]
    provider_payload = {
        "model": "deepseek-reasoner",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    return build_prompt_contract(
        template_version="analysis_prompt.2026-07.v4",
        template_snapshot="SYSTEM",
        user_appendix_snapshot=appendix,
        messages=messages,
        user_payload=user_payload,
        provider_payload=provider_payload,
        analysis_mode="deep",
        news_retrieval_policy="bounded_prefetch.v1",
        news_tool_rounds_configured=2,
        news_tool_rounds_executed=0,
        judge_mode="optional_second_pass",
        judge_meta={},
        decision_escalation_mode="shadow",
        policy_version="decision_policy.2026-07.v4",
    )


def test_prompt_contract_freezes_exact_messages_and_runtime_without_credentials():
    contract = _contract()

    assert contract["schema_version"] == PROMPT_CONTRACT_SCHEMA_VERSION
    assert contract["template_snapshot"] == "SYSTEM"
    assert contract["template_hash"] == content_hash("SYSTEM")
    assert contract["user_appendix_snapshot"] == "偏好低换手"
    assert contract["effective_system_prompt_snapshot"] == "SYSTEM\n偏好低换手"
    assert contract["model"] == "deepseek-reasoner"
    assert contract["temperature"] == 0.2
    assert contract["max_tokens"] == 4096
    assert contract["response_format"] == {"type": "json_object"}
    assert contract["news_tool_rounds_configured"] == 2
    assert contract["news_tool_rounds_executed"] == 0
    serialized = repr(contract).lower()
    assert "authorization" not in serialized
    assert "api_key" not in serialized


def test_prompt_contract_is_stable_and_component_changes_are_visible():
    first = _contract()
    second = _contract()
    appendix_changed = _contract(appendix="偏好更低换手")
    temperature_changed = _contract(temperature=0.1)

    assert first == second
    assert first["effective_messages_hash"] != appendix_changed["effective_messages_hash"]
    assert first["user_appendix_hash"] != appendix_changed["user_appendix_hash"]
    assert first["temperature"] != temperature_changed["temperature"]
    assert first["effective_messages_hash"] == temperature_changed["effective_messages_hash"]


def test_prompt_contract_judge_result_is_finalized_without_mutating_seed():
    seed = _contract()
    finalized = with_judge_result(
        seed,
        {
            "llm_judge_attempted": True,
            "llm_judge_applied": False,
            "llm_judge_timeout": True,
        },
    )

    assert seed["judge_attempted"] is False
    assert finalized["judge_attempted"] is True
    assert finalized["judge_applied"] is False
    assert finalized["judge_timed_out"] is True
