"""因子 IC 置信映射、共享快照读取与缓存测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
    result = fc.factor_confidence({}, "momentum")
    assert result == {"level": "不足", "basis": "无回测数据"}


def test_missing_factor_uses_caller_basis_but_size_keeps_specific_basis() -> None:
    missing = fc.factor_confidence({}, "momentum", missing_basis="IC 未参与")
    size = fc.factor_confidence({}, "size", missing_basis="IC 未参与")

    assert missing == {"level": "不足", "basis": "IC 未参与"}
    assert size == {"level": "不足", "basis": "规模因子未回测，仅供参考"}


def test_factor_reliability_covers_all_four() -> None:
    reliability = fc.factor_reliability(
        {"momentum": _ic(0.04, True)},
        missing_basis="IC 未参与",
    )
    assert set(reliability) == {"momentum", "risk_adjusted", "drawdown", "size"}
    assert reliability["momentum"]["level"] == "高"
    assert reliability["size"]["level"] == "不足"
    assert reliability["drawdown"]["level"] == "不足"
    assert reliability["drawdown"]["basis"] == "IC 未参与"
    assert reliability["size"]["basis"] == "规模因子未回测，仅供参考"


def test_load_ic_summary_reads_local_fallback(tmp_path, monkeypatch) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "available": True,
        "run_date": now.date().isoformat(),
        "generated_at": now.isoformat(),
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
    now = datetime.now(timezone.utc).replace(microsecond=0)
    generated = now - timedelta(hours=1)
    payload = valid_payload(generated.isoformat())
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
        {
            "state": "available",
            "status": {"available": True, "stale": False},
            "summary": {"factors": [{"factor": "momentum", "mean_ic": 0.01}]},
        },
        {
            "state": "available",
            "status": {"available": True, "stale": False},
            "summary": {"factors": [{"factor": "momentum", "mean_ic": 0.02}]},
        },
    ]
    times = iter([0.0, 100.0, 301.0])
    monkeypatch.setattr(fc.time, "time", lambda: next(times))
    monkeypatch.setattr(
        fc,
        "load_factor_ic_context",
        lambda **_kwargs: responses.pop(0),
        raising=False,
    )
    fc.clear_ic_summary_cache()

    first_context = fc.load_ic_context()
    cached_context = fc.load_ic_context()
    refreshed_context = fc.load_ic_context()
    first = first_context["factors"]
    cached = cached_context["factors"]
    refreshed = refreshed_context["factors"]

    assert first["momentum"]["mean_ic"] == 0.01
    assert cached_context is first_context
    assert cached is first
    assert refreshed["momentum"]["mean_ic"] == 0.02


def test_stale_ic_context_keeps_status_but_excludes_factors(monkeypatch) -> None:
    monkeypatch.setattr(
        fc,
        "load_factor_ic_context",
        lambda **_kwargs: {
            "state": "stale",
            "status": {"available": True, "stale": True, "age_days": 31},
            "summary": {
                "factors": [
                    {
                        "factor": "momentum",
                        "mean_ic": 0.08,
                        "significant": True,
                    }
                ]
            },
        },
    )
    fc.clear_ic_summary_cache()

    context = fc.load_ic_context()

    assert context["state"] == "stale"
    assert context["status"]["age_days"] == 31
    assert context["factors"] == {}
    assert fc.load_ic_summary() is context["factors"]


def test_available_ic_context_exposes_factor_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        fc,
        "load_factor_ic_context",
        lambda **_kwargs: {
            "state": "available",
            "status": {"available": True, "stale": False},
            "summary": {
                "factors": [
                    {
                        "factor": "momentum",
                        "mean_ic": 0.04,
                        "significant": True,
                    }
                ]
            },
        },
    )
    fc.clear_ic_summary_cache()

    context = fc.load_ic_context()

    assert context["factors"]["momentum"]["mean_ic"] == 0.04
    assert fc.load_ic_summary() is context["factors"]


def test_load_ic_summary_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fc, "SUMMARY_PATH", tmp_path / "missing.json")
    fc.clear_ic_summary_cache()
    assert fc.load_ic_summary() == {}
