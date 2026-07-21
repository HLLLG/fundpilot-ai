from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import akshare_subprocess
from app.services import fund_discovery_data_cache as cache_module


def test_partial_profile_batch_retries_only_unresolved_codes(monkeypatch):
    saved: dict = {}
    xq_calls: list[list[str]] = []

    monkeypatch.setattr(cache_module, "get_spot_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot_any_age",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cache_module,
        "save_spot_snapshot",
        lambda key, payload: saved.update({"key": key, "payload": payload}),
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda _codes, **_kwargs: [
            {
                "fund_code": "020356",
                "fund_scale_yi": 3.5,
                "profile_source": "sina",
            }
        ],
    )

    def fetch_xq(codes, **_kwargs):
        xq_calls.append(list(codes))
        if len(xq_calls) == 1:
            return []
        return [
            {
                "fund_code": "020356",
                "fund_shares_yi": 2.0,
                "fund_manager": "test manager",
                "established_date": "2024-01-23",
                "tracking_reference_text": "中证创新药产业指数",
                "benchmark_text": "中证创新药产业指数",
                "benchmark_text_kind": "tracking_target",
                "benchmark_text_source_kind": "xq_akshare_aggregator",
                "profile_source": "xq",
            }
        ]

    monkeypatch.setattr(akshare_subprocess, "fetch_fund_basic_profiles_xq", fetch_xq)

    result = cache_module.fetch_fund_research_profiles_cached(["020356"])

    assert len(xq_calls) == 2
    assert xq_calls[1] == ["020356"]
    assert result["020356"]["profile_status"] == "complete"
    assert result["020356"]["profile_missing_fields"] == []
    assert result["020356"]["fund_manager"] == "test manager"
    assert result["020356"]["tracking_reference_text"] == "中证创新药产业指数"
    assert result["020356"]["benchmark_text_kind"] == "tracking_target"
    assert saved["key"] == cache_module._PROFILE_CACHE_KEY


SNAPSHOT_AT = datetime.now(timezone.utc).isoformat()


def _universe_snapshot(code: str = "000001") -> dict:
    return {
        "schema_version": "fund_universe_snapshot.v1",
        "snapshot_available_at": SNAPSHOT_AT,
        "source": "pytest.fund_catalogue",
        "rows": [
            {
                "fund_code": code,
                "fund_name": "测试基金A",
                "fund_type": "gp",
                "return_6m_percent": 8.5,
            }
        ],
    }


def test_fresh_universe_snapshot_uses_one_frozen_availability(monkeypatch) -> None:
    payload = _universe_snapshot()
    payload["rows"][0]["membership_available_at"] = "2099-01-01T00:00:00+00:00"
    payload["rows"][0]["return_6m_percent_available_at"] = (
        "2099-01-01T00:00:00+00:00"
    )
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot_any_age",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale read")),
    )

    rows = cache_module.fetch_discovery_fund_universe_cached()

    assert rows[0]["membership_available_at"] == SNAPSHOT_AT
    assert rows[0]["return_6m_percent_available_at"] == SNAPSHOT_AT
    assert rows[0]["source"] == "pytest.fund_catalogue"


def test_expired_universe_is_pinned_while_refresh_is_scheduled(monkeypatch) -> None:
    expired_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    payload = {
        **_universe_snapshot("000002"),
        "snapshot_available_at": expired_at,
    }
    scheduled: list[int] = []
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot",
        # Simulate an expired DB snapshot promoted into process memory. Its
        # immutable capture time must win over the cache read time.
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot_any_age",
        lambda *_args: (_ for _ in ()).throw(AssertionError("duplicate stale read")),
    )
    monkeypatch.setattr(
        cache_module,
        "_schedule_discovery_universe_refresh",
        lambda *, limit: scheduled.append(limit),
    )

    rows = cache_module.fetch_discovery_fund_universe_cached(limit=20_000)

    assert [row["fund_code"] for row in rows] == ["000002"]
    assert rows[0]["snapshot_available_at"] == expired_at
    assert scheduled == [20_000]


def test_snapshot_freshness_uses_capture_time() -> None:
    now = datetime(2026, 7, 21, 8, tzinfo=timezone.utc)
    fresh = {
        **_universe_snapshot(),
        "snapshot_available_at": (now - timedelta(hours=23)).isoformat(),
    }
    expired = {
        **_universe_snapshot(),
        "snapshot_available_at": (now - timedelta(hours=25)).isoformat(),
    }

    assert cache_module._universe_snapshot_is_fresh(fresh, now=now) is True
    assert cache_module._universe_snapshot_is_fresh(expired, now=now) is False


def test_true_cold_start_fetches_before_returning(monkeypatch) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cache_module,
        "get_spot_snapshot_any_age",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_universe",
        lambda **_kwargs: _universe_snapshot("000003")["rows"],
    )
    monkeypatch.setattr(
        cache_module,
        "save_spot_snapshot",
        lambda _key, payload: saved.append(payload),
    )

    rows = cache_module.fetch_discovery_fund_universe_cached(limit=20_000)

    assert [row["fund_code"] for row in rows] == ["000003"]
    assert len(saved) == 1
    assert saved[0]["snapshot_available_at"]
    assert rows[0]["membership_available_at"] == saved[0]["snapshot_available_at"]


def test_empty_payload_is_not_a_valid_universe_snapshot() -> None:
    assert cache_module._valid_universe_snapshot(None) is False
    assert cache_module._valid_universe_snapshot({"rows": []}) is False
    assert cache_module._valid_universe_snapshot(_universe_snapshot()) is True
