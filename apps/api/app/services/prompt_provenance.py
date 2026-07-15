from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence


PROMPT_CONTRACT_SCHEMA_VERSION = "prompt_contract.v1"


def canonical_json(value: object) -> str:
    """Serialize audit material deterministically without leaking repr details."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_hash(value: object) -> str:
    """Hash a JSON value (or a string value) using one canonical convention."""

    encoded = canonical_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_prompt_contract(
    *,
    template_version: str,
    template_snapshot: str,
    user_appendix_snapshot: str | None,
    messages: Sequence[Mapping[str, Any]],
    user_payload: Mapping[str, Any],
    provider_payload: Mapping[str, Any],
    analysis_mode: str,
    news_retrieval_policy: str,
    news_tool_rounds_configured: int,
    news_tool_rounds_executed: int,
    judge_mode: str,
    judge_meta: Mapping[str, Any] | None,
    decision_escalation_mode: str,
    policy_version: str,
) -> dict[str, Any]:
    """Freeze the exact main-generation prompt and provider runtime contract.

    ``messages`` and ``provider_payload`` must be the values used for the actual
    provider request.  Callers must not rebuild either from a report afterwards.
    Provider credentials and headers are intentionally not accepted.
    """

    frozen_messages = [deepcopy(dict(message)) for message in messages]
    frozen_payload = deepcopy(dict(user_payload))
    appendix = user_appendix_snapshot or ""
    system_prompt = _first_message_content(frozen_messages, "system")
    judge = dict(judge_meta or {})
    response_format = deepcopy(provider_payload.get("response_format"))

    contract = {
        "schema_version": PROMPT_CONTRACT_SCHEMA_VERSION,
        "template_version": str(template_version),
        "template_snapshot": str(template_snapshot),
        "template_hash": content_hash(str(template_snapshot)),
        "user_appendix_snapshot": appendix,
        "user_appendix_hash": content_hash(appendix),
        "effective_system_prompt_snapshot": system_prompt,
        "effective_system_prompt_hash": content_hash(system_prompt),
        "user_payload_hash": content_hash(frozen_payload),
        "effective_messages_hash": content_hash(frozen_messages),
        "analysis_mode": str(analysis_mode),
        "model": str(provider_payload.get("model") or ""),
        "temperature": provider_payload.get("temperature"),
        "max_tokens": provider_payload.get("max_tokens"),
        "response_format": response_format,
        "news_retrieval_policy": str(news_retrieval_policy),
        "news_tool_rounds_configured": max(0, int(news_tool_rounds_configured)),
        "news_tool_rounds_executed": max(0, int(news_tool_rounds_executed)),
        "judge_mode": str(judge_mode),
        "judge_attempted": bool(judge.get("llm_judge_attempted", False)),
        "judge_applied": bool(judge.get("llm_judge_applied", False)),
        "judge_timed_out": bool(judge.get("llm_judge_timeout", False)),
        "decision_escalation_mode": str(decision_escalation_mode),
        "policy_version": str(policy_version),
    }
    contract["contract_hash"] = content_hash(contract)
    return contract


def with_judge_result(
    contract: Mapping[str, Any],
    judge_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a finalized copy after the optional second-pass judge completes."""

    result = deepcopy(dict(contract))
    judge = dict(judge_meta or {})
    result["judge_attempted"] = bool(judge.get("llm_judge_attempted", False))
    result["judge_applied"] = bool(judge.get("llm_judge_applied", False))
    result["judge_timed_out"] = bool(judge.get("llm_judge_timeout", False))
    skipped = judge.get("llm_judge_skipped_reason")
    if skipped:
        result["judge_skipped_reason"] = str(skipped)
    else:
        result.pop("judge_skipped_reason", None)
    result.pop("contract_hash", None)
    result["contract_hash"] = content_hash(result)
    return result


def _first_message_content(messages: Sequence[Mapping[str, Any]], role: str) -> str:
    for message in messages:
        if message.get("role") == role:
            content = message.get("content")
            return content if isinstance(content, str) else canonical_json(content)
    return ""
