from __future__ import annotations

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
