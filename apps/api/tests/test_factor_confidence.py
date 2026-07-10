"""因子 IC 置信映射、共享快照读取与缓存测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services import factor_confidence as fc


def _ic(mean_ic: float, significant: bool) -> dict:
    return {"mean_ic": mean_ic, "significant": significant}


def test_significant_strong_positive_high() -> None:
    result = fc.factor_confidence({"momentum": _ic(0.041, True)}, "momentum")
    assert result["level"] == "高"
    assert "IC" in result["basis"]


def test_significant_weak_positive_medium() -> None:
    result = fc.factor_confidence({"momentum": _ic(0.018, True)}, "momentum")
    assert result["level"] == "中"


def test_significant_negative_low() -> None:
    result = fc.factor_confidence({"drawdown": _ic(-0.05, True)}, "drawdown")
    assert result["level"] == "低"
    assert "反向" in result["basis"]


def test_not_significant_low() -> None:
    result = fc.factor_confidence(
        {"risk_adjusted": _ic(0.06, False)},
        "risk_adjusted",
    )
    assert result["level"] == "低"
    assert "不显著" in result["basis"]


def test_size_always_insufficient() -> None:
    result = fc.factor_confidence({"momentum": _ic(0.04, True)}, "size")
    assert result["level"] == "不足"
    assert "未回测" in result["basis"]


def test_missing_factor_insufficient() -> None:
    assert fc.factor_confidence({}, "momentum")["level"] == "不足"


def test_factor_reliability_covers_all_four() -> None:
    reliability = fc.factor_reliability({"momentum": _ic(0.04, True)})
    assert set(reliability) == {"momentum", "risk_adjusted", "drawdown", "size"}
    assert reliability["momentum"]["level"] == "高"
    assert reliability["size"]["level"] == "不足"
    assert reliability["drawdown"]["level"] == "不足"


def test_load_ic_summary_reads_local_fallback(tmp_path, monkeypatch) -> None:
    payload = {
        "factors": [
            {"factor": "momentum", "mean_ic": 0.04, "significant": True},
            {"factor": "drawdown", "mean_ic": -0.01, "significant": False},
        ]
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(fc, "SUMMARY_PATH", path)
    fc.clear_ic_summary_cache()

    result = fc.load_ic_summary()

    assert result["momentum"]["mean_ic"] == 0.04
    assert result["momentum"]["significant"] is True
    assert "drawdown" in result


def test_load_ic_summary_prefers_database(tmp_path, monkeypatch) -> None:
    from app.config import refresh_settings
    from app.services.factor_ic_snapshot import (
        publish_factor_ic_snapshot,
        validate_publish_request,
    )
    from tests.test_factor_ic_snapshot import valid_payload

    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "confidence.db"))
    refresh_settings()
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    payload["summary"]["factors"][0]["mean_ic"] = 0.04
    request = validate_publish_request(payload, now=now)
    publish_factor_ic_snapshot(request, now=now)

    local_path = tmp_path / "summary.json"
    local_path.write_text(
        json.dumps(
            {
                "factors": [
                    {
                        "factor": "momentum",
                        "mean_ic": -0.5,
                        "significant": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fc, "SUMMARY_PATH", local_path)
    fc.clear_ic_summary_cache()

    result = fc.load_ic_summary()

    assert result["momentum"]["mean_ic"] == 0.04


def test_load_ic_summary_cache_expires_after_five_minutes(monkeypatch) -> None:
    responses = [
        ({"factors": [{"factor": "momentum", "mean_ic": 0.01}]}, "database", {}),
        ({"factors": [{"factor": "momentum", "mean_ic": 0.02}]}, "database", {}),
    ]
    times = iter([0.0, 100.0, 301.0])
    monkeypatch.setattr(fc.time, "time", lambda: next(times))
    monkeypatch.setattr(
        fc,
        "load_factor_ic_summary",
        lambda **_kwargs: responses.pop(0),
        raising=False,
    )
    fc.clear_ic_summary_cache()

    first = fc.load_ic_summary()
    cached = fc.load_ic_summary()
    refreshed = fc.load_ic_summary()

    assert first["momentum"]["mean_ic"] == 0.01
    assert cached is first
    assert refreshed["momentum"]["mean_ic"] == 0.02


def test_load_ic_summary_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fc, "SUMMARY_PATH", tmp_path / "missing.json")
    fc.clear_ic_summary_cache()
    assert fc.load_ic_summary() == {}
