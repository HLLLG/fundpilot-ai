from __future__ import annotations

from app.services.chat_agent_loop import iter_chat_agent_events
from app.services.chat_agent_tools import ChatAgentContext


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self._provider_deadline = None

    def _chat_completion(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {
                            "name": "explain_holding_decision",
                            "arguments": '{"fund_code":"012345"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "这份日报对 012345 给出减仓。"}


def test_agent_loop_executes_tool_then_answers(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(
        "app.services.chat_agent_loop.DeepSeekClient",
        lambda: fake,
    )
    report = {
        "fund_recommendations": [
            {
                "fund_code": "012345",
                "fund_name": "测试行业基金",
                "action": "减仓",
                "suggested_position_change_percent": -25,
            }
        ]
    }
    events = list(
        iter_chat_agent_events(
            messages=[{"role": "user", "content": "为什么减仓？"}],
            tools=[{"type": "function", "function": {"name": "explain_holding_decision"}}],
            context=ChatAgentContext(surface="report", report=report),
            model="test-model",
            max_rounds=2,
        )
    )
    types = [event["type"] for event in events]
    assert "status" in types
    tokens = "".join(
        event["content"] for event in events if event["type"] == "token"
    )
    assert "减仓" in tokens
    assert fake.calls == 2


def test_agent_loop_without_tools_streams(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.deepseek_streaming.stream_chat_completion",
        lambda **_kwargs: iter(["只", "用报告"]),
    )
    events = list(
        iter_chat_agent_events(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            context=ChatAgentContext(surface="report", report={}),
            model="test-model",
            max_rounds=0,
        )
    )
    assert [event["content"] for event in events if event.get("type") == "token"] == [
        "只",
        "用报告",
    ]
