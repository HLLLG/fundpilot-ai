from __future__ import annotations

from typing import Any

import pytest

from app.config import refresh_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_client import (
    REPORT_RESPONSE_FORMAT,
    DeepSeekClient,
    _build_chat_payload,
    build_analysis_prompt_provenance,
)
from app.services.discovery_client import DiscoveryClient, build_discovery_chat_messages
from app.services.deepseek_http import ProviderOutputError
from app.services.prompt_provenance import content_hash
from app.services.report_chat import _parse_stream_line


_FAKE_KEY = "sk-" + "x" * 32


def _runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        mode="deep",
        model="deepseek-v4-pro",
        news_enabled=True,
        news_max_topics=5,
        news_tool_max_rounds=0,
        news_tool_rounds_configured=2,
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="示例基金",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(),
        system_role_prompt="偏好低换手",
        analysis_mode="deep",
    )


def test_daily_retry_contract_uses_the_final_actual_messages(monkeypatch):
    client = DeepSeekClient()
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "content": (
                    '{"title":"t","summary":"retry","fund_recommendations":[],'
                    '"recommendations":[],"caveats":[]}'
                )
            },
            {
                "content": (
                    '{"title":"t","summary":"ok","fund_recommendations":['
                    '{"fund_code":"519674","fund_name":"示例基金","action":"观察"}'
                    '],"recommendations":[],"caveats":[]}'
                )
            },
        ]
    )

    def fake_chat_completion(**kwargs):
        calls.append(kwargs)
        client._last_chat_messages = kwargs["messages"]
        return next(responses)

    monkeypatch.setattr(client, "_chat_completion", fake_chat_completion)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": '{"today":"2026-07-14"}'},
    ]

    parsed = client._generate_report_json(messages, _runtime())
    contract = build_analysis_prompt_provenance(
        request=_request(),
        messages=client._last_report_messages,
        runtime=_runtime(),
        judge_meta={},
    )

    assert parsed["summary"] == "ok"
    assert len(calls) == 2
    assert client._last_report_messages == calls[-1]["messages"]
    assert len([m for m in calls[-1]["messages"] if m["role"] == "user"]) == 2
    assert contract["effective_messages_hash"] == content_hash(calls[-1]["messages"])
    assert contract["model"] == "deepseek-v4-pro"
    assert contract["temperature"] == 0.2
    assert contract["response_format"] == REPORT_RESPONSE_FORMAT


def test_daily_retry_failure_keeps_the_final_attempted_messages(monkeypatch):
    client = DeepSeekClient()
    calls: list[dict[str, Any]] = []

    def fake_chat_completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": '{"title":"incomplete"'}
        raise ProviderOutputError("invalid_json")

    monkeypatch.setattr(client, "_chat_completion", fake_chat_completion)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": '{"today":"2026-07-14"}'},
    ]

    with pytest.raises(ProviderOutputError, match="invalid_json"):
        client._generate_report_json(messages, _runtime())

    assert len(calls) == 2
    assert client._last_report_messages == calls[-1]["messages"]
    assert len([item for item in client._last_report_messages if item["role"] == "user"]) == 2


def test_daily_rejects_schema_invalid_json_after_retry(monkeypatch):
    client = DeepSeekClient()
    calls: list[dict[str, Any]] = []

    def fake_chat_completion(**kwargs):
        calls.append(kwargs)
        return {"content": "{}"}

    monkeypatch.setattr(client, "_chat_completion", fake_chat_completion)

    with pytest.raises(ProviderOutputError) as captured:
        client._generate_report_json(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "{}"},
            ],
            _runtime(),
        )

    assert captured.value.category == "invalid_json"
    assert len(calls) == 2


def test_daily_rejects_an_empty_recommendation_set_after_retry(monkeypatch):
    client = DeepSeekClient()
    response = {
        "content": (
            '{"title":"t","summary":"still incomplete",'
            '"fund_recommendations":[],"recommendations":[],"caveats":[]}'
        )
    }
    monkeypatch.setattr(client, "_chat_completion", lambda **_kwargs: response)

    with pytest.raises(ProviderOutputError) as captured:
        client._generate_report_json(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "{}"},
            ],
            _runtime(),
        )

    assert captured.value.category == "invalid_json"


def test_daily_internal_pipeline_error_is_not_disguised_as_provider_fallback(
    monkeypatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    client = DeepSeekClient()
    client.news_service.prefetch_for_holdings = lambda *_args, **_kwargs: []
    client.news_service.prefetch_fund_announcements = lambda *_args, **_kwargs: {
        "items": [],
        "requested": 0,
    }
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("internal-bug")),
    )

    with pytest.raises(RuntimeError, match="internal-bug"):
        client.generate_report(
            _request(),
            RiskAssessment(
                level="medium",
                weighted_return_percent=0,
                suggested_action="watch",
                alerts=[],
            ),
            [FundSnapshot(fund_code="519674", fund_name="示例基金", source="test")],
        )

    refresh_settings()


def test_discovery_sync_uses_the_shared_main_report_payload(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"title":"t","summary":"s",'
                                '"recommendations":[],"caveats":[]}'
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url, *, headers, json):
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.discovery_client.get_deepseek_http_client",
        lambda _settings: FakeClient(),
    )
    client = DiscoveryClient()
    user_payload = {"today": "2026-07-14", "discovery_facts": {}}

    client._call_model("system", user_payload, "deepseek-v4-pro")

    messages = build_discovery_chat_messages("system", user_payload)
    expected = _build_chat_payload(
        messages=messages,
        model="deepseek-v4-pro",
        max_tokens=client.settings.deepseek_max_tokens_report,
        tools=None,
        response_format=REPORT_RESPONSE_FORMAT,
    )

    assert captured["payload"] == expected
    assert captured["payload"]["temperature"] == 0.2
    assert captured["payload"]["max_tokens"] == client.settings.deepseek_max_tokens_report
    assert "Authorization" not in repr(captured["payload"])

    refresh_settings()


@pytest.mark.parametrize(
    "provider_payload",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": []}]},
        {"choices": [{"message": {"content": {}}}]},
        {"choices": [{"message": {"content": []}}]},
        {"choices": [{"message": {"content": "{}"}}]},
        {"choices": [{"message": {"content": '{"foo":"bar"}'}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"t","summary":"s",'
                            '"recommendations":[1],"caveats":[]}'
                        )
                    }
                }
            ]
        },
    ],
)
def test_discovery_sync_rejects_malformed_provider_envelopes(
    monkeypatch,
    provider_payload,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return provider_payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.discovery_client.get_deepseek_http_client",
        lambda _settings: FakeClient(),
    )

    with pytest.raises(ProviderOutputError) as captured:
        DiscoveryClient()._call_model("system", {"today": "2026-07-14"}, "test")

    assert captured.value.category == "invalid_json"
    refresh_settings()


@pytest.mark.parametrize(
    "line",
    [
        "data: null",
        "data: []",
        "data: {}",
        'data: {"choices": [null]}',
        'data: {"choices": [{"delta": []}]}',
    ],
)
def test_stream_parser_ignores_malformed_envelopes(line: str):
    assert _parse_stream_line(line) is None


def test_discovery_prompt_contract_hashes_the_messages_used_by_provider(
    monkeypatch,
):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_KEY)
    refresh_settings()
    client = DiscoveryClient()
    actual_messages = [
        {"role": "system", "content": "actual-provider-system"},
        {"role": "user", "content": '{"actual":true}'},
    ]

    def fake_call_model(_system_prompt, _user_payload, _model, **_kwargs):
        client._last_report_messages = actual_messages
        return {
            "title": "t",
            "summary": "s",
            "recommendations": [],
            "caveats": [],
        }

    monkeypatch.setattr(client, "_call_model", fake_call_model)
    monkeypatch.setattr(
        "app.services.discovery_client.judge_parsed_discovery_report",
        lambda parsed, **_kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        "app.services.discovery_client.build_discovery_report_from_parsed",
        lambda _parsed, **kwargs: kwargs["discovery_facts"],
    )
    facts = {
        "readonly": True,
        "session": {
            "calendar_date": "2026-07-14",
            "effective_trade_date": "2026-07-14",
        },
        "candidate_pool": [],
        "portfolio_gap": {"target_sectors": [], "holdings_slim": []},
    }

    result = client.generate_report(
        target_sectors=[],
        focus_sectors=[],
        candidate_pool=[],
        discovery_facts=facts,
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=0,
        sector_heat=[],
        market_news=[],
        topic_briefs=[],
        analysis_mode="fast",
    )

    contract = result["pipeline"]["prompt_contract"]
    assert contract["effective_messages_hash"] == content_hash(actual_messages)
    assert contract["effective_system_prompt_snapshot"] == "actual-provider-system"
    refresh_settings()
