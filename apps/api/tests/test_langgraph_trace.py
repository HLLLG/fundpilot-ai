from __future__ import annotations

from app.services.langgraph_trace import (
    LANGGRAPH_TRACE_SCHEMA_VERSION,
    compact_state_update,
    get_run,
    list_runs,
    redact_payload,
    start_run,
    append_event,
    finish_run,
)


def test_redact_payload_drops_prompt_and_holdings() -> None:
    redacted = redact_payload(
        {
            "kind": "status",
            "node": "tools",
            "owner": "code",
            "tool_name": "get_holdings",
            "messages": [{"role": "user", "content": "secret"}],
            "holdings": [{"fund_code": "012345", "holding_amount": 9999}],
            "prompt": "do not store",
        }
    )
    assert redacted["tool_name"] == "get_holdings"
    assert "messages" not in redacted
    assert "holdings" not in redacted
    assert "prompt" not in redacted


def test_compact_state_update_keeps_counts_not_messages() -> None:
    compact = compact_state_update(
        {
            "messages": [{"role": "user", "content": "why"}],
            "route": "tools",
            "last_message": {
                "content": "hello world",
                "tool_calls": [{"function": {"name": "lookup_fund"}}],
            },
        }
    )
    assert compact["message_count"] == 1
    assert compact["tool_names"] == ["lookup_fund"]
    assert compact["answer_chars"] == 11
    assert "messages" not in compact


def test_run_store_roundtrip() -> None:
    run_id = start_run("chat_followup")
    append_event(
        run_id,
        "node_end",
        node="tools",
        owner="code",
        payload={"tool_name": "get_holdings", "prompt": "nope"},
    )
    finish_run(run_id, "completed", summary={"node": "tools"})
    listed = list_runs(graph_name="chat_followup", limit=5)
    assert any(item["id"] == run_id for item in listed)
    detail = get_run(run_id)
    assert detail is not None
    assert detail["schema_version"] == LANGGRAPH_TRACE_SCHEMA_VERSION
    assert detail["status"] == "completed"
    assert detail["events"]
    stored = detail["events"][0]["payload"]
    assert stored.get("tool_name") == "get_holdings"
    assert "prompt" not in stored
