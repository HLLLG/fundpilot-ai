"""Property-based tests for `app.services.chat_aggregate.aggregate_chat_stream`.

Feature: miniprogram-web-parity, Property 14: 追问流聚合（BC1）

Property 14 (design.md):
    For any 追问 event sequence `[token, token, ..., done]`, the aggregated
    full text equals the in-order concatenation of every `token` event's
    `content`, and the final assistant message is the one carried by the
    `done` event; if the sequence contains an `error` event, a `ValueError`
    carrying its text is raised.

Validates: Requirements 14.3, 16.5, 17.5

Uses pytest + Hypothesis with >=100 examples.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.chat_aggregate import AggregatedChat, aggregate_chat_stream

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Token content: arbitrary text including empty strings, whitespace, unicode.
_token_text = st.text(max_size=40)

# A non-trivially-empty error message so `_error_text` returns it (a blank
# message falls back to the default "追问失败").
_error_text_strategy = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")

# The assistant message object carried by the `done` event.
_message_strategy = st.fixed_dictionaries(
    {
        "role": st.just("assistant"),
        "content": st.text(max_size=40),
        "id": st.integers(min_value=1, max_value=10_000),
    }
)

# The user message object carried by the `user_message` event.
_user_message_strategy = st.fixed_dictionaries(
    {
        "role": st.just("user"),
        "content": st.text(max_size=40),
    }
)

_chat_mode_strategy = st.sampled_from(["report", "discovery", "briefing"])
_model_strategy = st.sampled_from(["deepseek-chat", "deepseek-reasoner"])


def _token_event(content: str) -> dict:
    return {"type": "token", "content": content}


def _done_event(message: dict, chat_mode: str, model: str) -> dict:
    return {
        "type": "done",
        "message": message,
        "chat_mode": chat_mode,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Property 14 — happy path: [user_message?, status*, token*, done]
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    tokens=st.lists(_token_text, max_size=20),
    message=_message_strategy,
    user_message=_user_message_strategy,
    chat_mode=_chat_mode_strategy,
    model=_model_strategy,
    include_user=st.booleans(),
    status_noise=st.lists(st.text(max_size=10), max_size=5),
)
def test_aggregate_concatenates_tokens_and_keeps_done_message(
    tokens,
    message,
    user_message,
    chat_mode,
    model,
    include_user,
    status_noise,
):
    """Aggregated content == in-order concat of token contents; final message
    == the `done` event's message; ignored `status` events do not interfere."""

    events: list[dict] = []
    if include_user:
        events.append({"type": "user_message", "message": user_message})
    # Interleave ignored status events with tokens to prove they're skipped.
    for index, token in enumerate(tokens):
        if index < len(status_noise):
            events.append({"type": "status", "content": status_noise[index]})
        events.append(_token_event(token))
    events.append(_done_event(message, chat_mode, model))

    result = aggregate_chat_stream(events)

    assert isinstance(result, AggregatedChat)
    # Core property: full text is the ordered concatenation of token contents.
    assert result.content == "".join(tokens)
    # Final assistant message is the one carried by the `done` event.
    assert result.message == message
    assert result.chat_mode == chat_mode
    assert result.model == model
    if include_user:
        assert result.user_message == user_message
    else:
        assert result.user_message is None


# ---------------------------------------------------------------------------
# Property 14 — error path: any sequence containing an `error` event raises
# a ValueError carrying its text.
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    pre_tokens=st.lists(_token_text, max_size=10),
    post_tokens=st.lists(_token_text, max_size=10),
    error_message=_error_text_strategy,
    message=_message_strategy,
    include_done=st.booleans(),
)
def test_aggregate_raises_value_error_on_error_event(
    pre_tokens,
    post_tokens,
    error_message,
    message,
    include_done,
):
    """An `error` event anywhere in the stream raises ValueError(error_text)."""

    events: list[dict] = [_token_event(t) for t in pre_tokens]
    events.append({"type": "error", "message": error_message})
    events.extend(_token_event(t) for t in post_tokens)
    if include_done:
        events.append(_done_event(message, "report", "deepseek-chat"))

    with pytest.raises(ValueError) as exc_info:
        aggregate_chat_stream(events)

    assert str(exc_info.value) == error_message


# ---------------------------------------------------------------------------
# Property 14 — JSON/SSE string payloads aggregate identically to dicts.
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    tokens=st.lists(_token_text, max_size=15),
    message=_message_strategy,
)
def test_aggregate_handles_json_string_payloads(tokens, message):
    """String JSON payloads (as the SSE generators yield) aggregate the same
    way as dict events, with `data:` prefixes tolerated."""

    import json

    events: list[str] = [
        f"data: {json.dumps(_token_event(t))}" for t in tokens
    ]
    events.append(json.dumps(_done_event(message, "report", "deepseek-chat")))

    result = aggregate_chat_stream(events)

    assert result.content == "".join(tokens)
    assert result.message == message
