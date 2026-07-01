"""LLM 兜底分类：模拟 DeepSeek HTTP 响应，不发真实网络请求。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.fund_sector_llm_infer import infer_sector_via_llm


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


def _chat_response(content: dict) -> _FakeResponse:
    return _FakeResponse(
        {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}
    )


@pytest.fixture(autouse=True)
def _configured_settings(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "deepseek_api_key", "fake-key-for-tests-000000")
    monkeypatch.setattr(get_settings(), "fund_primary_sector_llm_infer_enabled", True)
    yield


def test_infer_sector_via_llm_returns_label_and_confidence(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _chat_response({"sector_name": "低空经济", "confidence": 0.65}),
    )

    result = infer_sector_via_llm("012345", "某某低空经济主题混合C")

    assert result == ("低空经济", 0.65)


def test_infer_sector_via_llm_rejects_generic_style_label(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _chat_response({"sector_name": "稳健回报", "confidence": 0.5}),
    )

    assert infer_sector_via_llm("012345", "某某稳健回报混合C") is None


def test_infer_sector_via_llm_returns_none_for_null_sector(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _chat_response({"sector_name": None, "confidence": 0.1}),
    )

    assert infer_sector_via_llm("012345", "某某灵活配置混合C") is None


def test_infer_sector_via_llm_clamps_confidence_range(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _chat_response({"sector_name": "机器人", "confidence": 0.99}),
    )

    result = infer_sector_via_llm("012345", "某某机器人主题混合C")

    assert result is not None
    label, confidence = result
    assert label == "机器人"
    assert confidence <= 0.7


def test_infer_sector_via_llm_swallows_network_errors(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr("app.services.fund_sector_llm_infer.httpx.post", _raise)

    assert infer_sector_via_llm("012345", "某某任意基金C") is None


def test_infer_sector_via_llm_swallows_malformed_json(monkeypatch):
    class _BadJsonResponse(_FakeResponse):
        def json(self) -> dict:
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _BadJsonResponse({}),
    )

    assert infer_sector_via_llm("012345", "某某任意基金C") is None


def test_infer_sector_via_llm_returns_none_without_fund_name():
    assert infer_sector_via_llm("012345", None) is None
    assert infer_sector_via_llm("012345", "   ") is None


def test_infer_sector_via_llm_uses_top_holdings_when_name_has_no_clue(monkeypatch):
    """基金名称本身没有主题线索时（如'中航机遇领航混合发起C'），应该把重仓股名称也
    发给模型，让它借助持仓判断真实主题（如光模块/CPO 相关重仓 -> 光通信）。"""
    captured_payload: dict = {}

    def _fake_post(_url, *, json, **_kwargs):
        captured_payload.update(json)
        return _chat_response({"sector_name": "光通信", "confidence": 0.7})

    monkeypatch.setattr("app.services.fund_sector_llm_infer.httpx.post", _fake_post)

    result = infer_sector_via_llm(
        "018957",
        "中航机遇领航混合发起C",
        top_holdings=["新易盛", "中际旭创", "天孚通信"],
    )

    assert result == ("光通信", 0.7)
    user_message = json.loads(captured_payload["messages"][1]["content"])
    assert user_message["top_holdings"] == ["新易盛", "中际旭创", "天孚通信"]


def test_infer_sector_via_llm_still_works_with_holdings_but_no_name(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: _chat_response({"sector_name": "半导体", "confidence": 0.5}),
    )

    result = infer_sector_via_llm("012345", None, top_holdings=["中芯国际", "北方华创"])

    assert result == ("半导体", 0.5)


def test_infer_sector_via_llm_returns_none_without_name_or_holdings():
    assert infer_sector_via_llm("012345", None, top_holdings=[]) is None
    assert infer_sector_via_llm("012345", "   ", top_holdings=None) is None


def test_infer_sector_via_llm_uses_generous_token_budget_for_reasoning_model():
    """deepseek_model_fast 是带思维链的推理模型，reasoning_content 会占用部分
    max_tokens；预算太小（如曾经的 128）会导致最终 JSON 被截断成空字符串。"""
    from app.services.fund_sector_llm_infer import _MAX_OUTPUT_TOKENS

    assert _MAX_OUTPUT_TOKENS >= 500


def test_infer_sector_via_llm_disabled_by_config(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "fund_primary_sector_llm_infer_enabled", False)
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not call network")),
    )

    assert infer_sector_via_llm("012345", "某某机器人主题混合C") is None


def test_infer_sector_via_llm_disabled_without_api_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "deepseek_api_key", None)
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not call network")),
    )

    assert infer_sector_via_llm("012345", "某某机器人主题混合C") is None
