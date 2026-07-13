from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.services import akshare_subprocess, fund_discovery_data_cache as cache


def _checked_at(*, hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _install_cache(
    monkeypatch,
    *,
    fresh: dict | None,
    stale: dict | None,
) -> list[dict]:
    saved: list[dict] = []
    monkeypatch.setattr(cache, "get_spot_snapshot", lambda *_args, **_kwargs: fresh)
    monkeypatch.setattr(cache, "get_spot_snapshot_any_age", lambda *_args, **_kwargs: stale)
    monkeypatch.setattr(
        cache,
        "save_spot_snapshot",
        lambda _key, payload: saved.append(payload),
    )
    return saved


def test_stale_complete_profile_is_refreshed_and_primary_source_wins(monkeypatch) -> None:
    stale = {
        "rows": [
            {
                "fund_code": "100055",
                "fund_scale_yi": 1.0,
                "fund_manager": "旧经理",
                "established_date": "2011-07-13",
                "profile_checked_at": _checked_at(hours_ago=60),
                "profile_status": "complete",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ]
    }
    saved = _install_cache(monkeypatch, fresh=None, stale=stale)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "100055",
                "fund_scale_yi": 20.3397,
                "fund_manager": "赵年珅",
                "established_date": "2011-07-13",
                "fund_scale_basis": "nav_times_latest_shares",
                "profile_updated_at": "2026-07-09",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "100055",
                "fund_shares_yi": 12.46,
                "fund_manager": "赵年珅",
                "established_date": "2011-07-13",
                "fund_shares_basis": "xq_latest_reported_shares",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ],
    )

    row = cache.fetch_fund_research_profiles_cached(["100055"])["100055"]

    assert row["fund_scale_yi"] == 20.3397
    assert row["fund_manager"] == "赵年珅"
    assert row["fund_scale_basis"] == "nav_times_latest_shares"
    assert row["profile_status"] == "complete"
    assert saved


def test_fresh_partial_profile_uses_xq_only_to_fill_missing_fields(monkeypatch) -> None:
    fresh = {
        "rows": [
            {
                "fund_code": "001188",
                "fund_scale_yi": 3.6465,
                "fund_scale_basis": "nav_times_latest_shares",
                "fund_manager": None,
                "established_date": "2015-04-28",
                "profile_checked_at": _checked_at(hours_ago=1),
                "profile_status": "partial",
                "profile_source": "sina.fund_scale_open_sina",
                "profile_sources": ["sina.fund_scale_open_sina"],
            }
        ]
    }
    _install_cache(monkeypatch, fresh=fresh, stale=fresh)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_shares_yi": 2.26,
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "fund_shares_basis": "xq_latest_reported_shares",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ],
    )

    row = cache.fetch_fund_research_profiles_cached(["001188"])["001188"]

    assert row["fund_scale_yi"] == 3.6465
    assert row["fund_scale_basis"] == "nav_times_latest_shares"
    assert row["fund_manager"] == "王璐"
    assert row["profile_status"] == "complete"
    assert row["profile_sources"] == [
        "sina.fund_scale_open_sina",
        "xq.fund_individual_basic_info_xq",
    ]


def test_fresh_xq_shares_replace_old_shares_when_partial_profile_becomes_complete(
    monkeypatch,
) -> None:
    fresh = {
        "rows": [
            {
                "fund_code": "001188",
                "fund_shares_yi": 2.0,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": None,
                "established_date": "2015-04-28",
                "profile_checked_at": _checked_at(hours_ago=1),
                "profile_status": "partial",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=fresh, stale=fresh)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_shares_yi": 3.0,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ],
    )

    row = cache.fetch_fund_research_profiles_cached(["001188"])["001188"]

    assert row["fund_shares_yi"] == 3.0
    assert row["fund_manager"] == "王璐"
    assert row["profile_status"] == "complete"
    assert row.get("profile_stale_fields") is None


def test_fresh_sina_fields_replace_existing_values_on_partial_row(monkeypatch) -> None:
    fresh = {
        "rows": [
            {
                "fund_code": "001188",
                "fund_scale_yi": 1.0,
                "fund_manager": None,
                "established_date": "2015-04-28",
                "profile_checked_at": _checked_at(hours_ago=1),
                "profile_status": "partial",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=fresh, stale=fresh)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_scale_yi": 3.6465,
                "fund_manager": None,
                "established_date": "2015-04-28",
                "fund_scale_basis": "nav_times_latest_shares",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_shares_yi": 2.26,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ],
    )

    row = cache.fetch_fund_research_profiles_cached(["001188"])["001188"]

    assert row["fund_scale_yi"] == 3.6465
    assert row["fund_manager"] == "王璐"
    assert row["profile_status"] == "complete"


def test_xq_adapter_keeps_totshare_as_shares_not_aum(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run(script, **_kwargs):
        captured["script"] = script
        return {"data": []}

    monkeypatch.setattr(akshare_subprocess, "run_akshare_json_script", fake_run)

    assert akshare_subprocess.fetch_fund_basic_profiles_xq(["100055"]) == []
    assert '"fund_shares_yi"' in captured["script"]
    assert '"fund_scale_yi"' not in captured["script"]
    assert "latest_reported_aum" not in captured["script"]


def test_per_row_checked_at_prevents_global_cache_ttl_starvation(monkeypatch) -> None:
    fresh = {
        "rows": [
            {
                "fund_code": "021033",
                "fund_scale_yi": 1.1,
                "fund_manager": "旧经理",
                "established_date": "2024-04-23",
                "profile_checked_at": _checked_at(hours_ago=40),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=fresh, stale=fresh)
    calls: list[str] = []

    def primary(*_args, **_kwargs):
        calls.append("sina")
        return [
            {
                "fund_code": "021033",
                "fund_scale_yi": 2.76,
                "fund_manager": "李栩",
                "established_date": "2024-04-23",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ]

    monkeypatch.setattr(akshare_subprocess, "fetch_open_fund_research_profiles", primary)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [],
    )

    row = cache.fetch_fund_research_profiles_cached(["021033"])["021033"]

    assert calls == ["sina"]
    assert row["fund_scale_yi"] == 2.76
    assert row["fund_manager"] == "李栩"


def test_provider_outage_keeps_stale_values_but_marks_them_non_actionable(monkeypatch) -> None:
    stale = {
        "rows": [
            {
                "fund_code": "009896",
                "fund_scale_yi": 14.2,
                "fund_manager": "冯剑峰",
                "established_date": "2020-09-10",
                "profile_checked_at": _checked_at(hours_ago=60),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=None, stale=stale)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [],
    )

    row = cache.fetch_fund_research_profiles_cached(["009896"])["009896"]

    assert row["fund_scale_yi"] == 14.2
    assert row["profile_status"] == "stale_fallback"
    assert row["profile_missing_fields"] == []


def test_xq_shares_replace_stale_scale_input_when_sina_scale_is_empty(monkeypatch) -> None:
    stale = {
        "rows": [
            {
                "fund_code": "001188",
                "fund_scale_yi": 1.0,
                "fund_manager": "旧经理",
                "established_date": "2015-04-28",
                "profile_checked_at": _checked_at(hours_ago=60),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=None, stale=stale)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_scale_yi": None,
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_shares_yi": 2.26,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "profile_source": "xq.fund_individual_basic_info_xq",
            }
        ],
    )

    row = cache.fetch_fund_research_profiles_cached(["001188"])["001188"]

    assert row.get("fund_scale_yi") is None
    assert row["fund_shares_yi"] == 2.26
    assert row["fund_shares_basis"] == "xq_latest_reported_shares"
    assert row["profile_status"] == "complete"
    assert row["profile_sources"] == [
        "sina.fund_scale_open_sina",
        "xq.fund_individual_basic_info_xq",
    ]


def test_stale_xq_shares_are_not_reclassified_as_fresh_when_xq_refresh_fails(
    monkeypatch,
) -> None:
    stale = {
        "rows": [
            {
                "fund_code": "001188",
                "fund_shares_yi": 2.26,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": "旧经理",
                "established_date": "2015-04-28",
                "profile_checked_at": _checked_at(hours_ago=60),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=None, stale=stale)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [
            {
                "fund_code": "001188",
                "fund_manager": "王璐",
                "established_date": "2015-04-28",
                "profile_source": "sina.fund_scale_open_sina",
            }
        ],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [],
    )

    row = cache.fetch_fund_research_profiles_cached(["001188"])["001188"]

    assert row["fund_shares_yi"] == 2.26
    assert row["fund_manager"] == "王璐"
    assert row["profile_status"] == "partial"
    assert row["profile_stale_fields"] == ["fund_scale_yi"]


def test_empty_provider_shell_does_not_mark_stale_profile_complete(monkeypatch) -> None:
    stale = {
        "rows": [
            {
                "fund_code": "009896",
                "fund_scale_yi": 14.2,
                "fund_manager": "冯剑峰",
                "established_date": "2020-09-10",
                "profile_checked_at": _checked_at(hours_ago=60),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=None, stale=stale)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: [{"fund_code": "009896", "fund_name": "广发港股"}],
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [{"fund_code": "009896"}],
    )

    row = cache.fetch_fund_research_profiles_cached(["009896"])["009896"]

    assert row["profile_status"] == "stale_fallback"


def test_warm_complete_profile_does_not_call_providers(monkeypatch) -> None:
    fresh = {
        "rows": [
            {
                "fund_code": "021033",
                "fund_scale_yi": 2.76,
                "fund_manager": "李栩",
                "established_date": "2024-04-23",
                "profile_checked_at": _checked_at(hours_ago=1),
                "profile_status": "complete",
            }
        ]
    }
    _install_cache(monkeypatch, fresh=fresh, stale=fresh)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_open_fund_research_profiles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected Sina call")),
    )
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected XQ call")),
    )

    row = cache.fetch_fund_research_profiles_cached(["021033"])["021033"]

    assert row["profile_status"] == "complete"


def test_concurrent_batches_keep_union_in_shared_profile_cache(monkeypatch) -> None:
    state: dict[str, dict] = {"payload": {"rows": []}}
    monkeypatch.setattr(
        cache,
        "get_spot_snapshot",
        lambda *_args, **_kwargs: state["payload"],
    )
    monkeypatch.setattr(
        cache,
        "get_spot_snapshot_any_age",
        lambda *_args, **_kwargs: state["payload"],
    )
    monkeypatch.setattr(
        cache,
        "save_spot_snapshot",
        lambda _key, payload: state.__setitem__("payload", payload),
    )

    def primary(codes, **_kwargs):
        return [
            {
                "fund_code": code,
                "fund_scale_yi": 2.0,
                "fund_manager": f"经理{code}",
                "established_date": "2020-01-01",
                "profile_source": "sina.fund_scale_open_sina",
            }
            for code in codes
        ]

    monkeypatch.setattr(akshare_subprocess, "fetch_open_fund_research_profiles", primary)
    monkeypatch.setattr(
        akshare_subprocess,
        "fetch_fund_basic_profiles_xq",
        lambda *_args, **_kwargs: [],
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                cache.fetch_fund_research_profiles_cached,
                (["000001"], ["000002"]),
            )
        )

    codes = {row["fund_code"] for row in state["payload"]["rows"]}
    assert codes == {"000001", "000002"}
