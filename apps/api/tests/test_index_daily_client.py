from __future__ import annotations

import json

import requests

from app.services import index_daily_client


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_known_theme_index_uses_fast_eastmoney_history(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(
            {
                "data": {
                    "klines": [
                        "2026-07-16,2700.00,2765.25,2780.00,2690.00,1,2,3,4,5,6",
                        "2026-07-17,2754.12,2643.06,2759.84,2630.58,1,2,3,4,5,6",
                    ]
                }
            }
        )

    monkeypatch.setattr(index_daily_client.requests, "get", fake_get)

    result = index_daily_client._fetch_index_daily_history_impl("931994", 63)

    assert result == {
        "data": [
            {"date": "2026-07-16", "close": 2765.25},
            {"date": "2026-07-17", "close": 2643.06},
        ],
        "source": "eastmoney",
    }
    assert len(calls) == 1
    assert calls[0][0] == index_daily_client._EASTMONEY_URL
    assert calls[0][1]["params"]["secid"] == "2.931994"
    assert calls[0][1]["timeout"] == 6
    assert index_daily_client.index_display_name("931994") == "中证电网设备"


def test_theme_index_falls_back_to_sina_when_eastmoney_fails(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        if url == index_daily_client._EASTMONEY_URL:
            raise requests.RequestException("temporary failure")
        return _FakeResponse(
            [
                {"day": "2026-07-16", "close": "2765.25"},
                {"day": "2026-07-17", "close": "2643.06"},
            ]
        )

    monkeypatch.setattr(index_daily_client.requests, "get", fake_get)

    result = index_daily_client._fetch_index_daily_history_impl("931994", 63)

    assert result is not None
    assert result["source"] == "sina"
    assert len(calls) == 2
