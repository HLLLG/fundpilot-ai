from __future__ import annotations

import json

from app.services import fund_search_suggestions as service


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def test_public_suggestions_keep_provider_order_and_fund_metadata(monkeypatch) -> None:
    service._cache.clear()  # noqa: SLF001 - reset the bounded process cache for isolation
    payload = {
        "Datas": [
            {
                "CODE": "025857",
                "NAME": "华夏中证电网设备主题ETF发起式联接C",
                "CATEGORYDESC": "基金",
                "FundBaseInfo": {"FTYPE": "指数型-股票"},
            },
            {
                "CODE": "ignored",
                "NAME": "不是基金",
                "CATEGORYDESC": "基金经理",
            },
            {
                "CODE": "008888",
                "NAME": "华夏国证半导体芯片ETF联接C",
                "CATEGORYDESC": "基金",
                "FundBaseInfo": {"FTYPE": "指数型-股票"},
            },
        ]
    }
    calls: list[float] = []

    def fake_urlopen(_request, *, timeout: float):
        calls.append(timeout)
        return _Response(payload)

    monkeypatch.setattr(service, "urlopen", fake_urlopen)

    rows = service.fetch_ranked_fund_suggestions("华夏")

    assert [row["fund_code"] for row in rows] == ["025857", "008888"]
    assert [row["provider_rank"] for row in rows] == [1, 2]
    assert rows[0]["fund_type"] == "指数型-股票"
    assert len(calls) == 1

    assert service.fetch_ranked_fund_suggestions("华夏") == rows
    assert len(calls) == 1


def test_public_suggestion_failure_degrades_to_empty_result(monkeypatch) -> None:
    service._cache.clear()  # noqa: SLF001 - reset the bounded process cache for isolation

    def unavailable(*_args, **_kwargs):
        raise OSError("provider unavailable")

    monkeypatch.setattr(service, "urlopen", unavailable)

    assert service.fetch_ranked_fund_suggestions("华夏") == []
