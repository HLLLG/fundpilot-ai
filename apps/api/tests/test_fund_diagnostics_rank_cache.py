"""基金诊断与开放式基金排行榜共享缓存。"""

from app.services.fund_diagnostics_cache import (
    _fetch_fund_diagnostics_via_akshare,
    diagnostics_cache_key,
    get_cached_fund_diagnostics,
    load_fund_diagnostics,
    save_cached_fund_diagnostics,
)
from app.services.fund_rank_cache import (
    fetch_open_fund_rank_cached,
    get_cached_open_fund_rank,
    rank_cache_key,
    save_cached_open_fund_rank,
)
from app.services.fund_data import _parse_overview_frame


def test_fund_diagnostics_cache_roundtrip():
    payload = {
        "fund_type": "混合型",
        "management_fee": 1.5,
        "return_1y_percent": 12.3,
    }
    save_cached_fund_diagnostics("519674", payload)
    cached = get_cached_fund_diagnostics("519674")
    assert cached == payload
    assert diagnostics_cache_key("519674") == "fund:diagnostics:v4:519674"


def test_overview_scale_keeps_source_and_point_in_time_date():
    import pandas as pd

    frame = pd.DataFrame(
        [{
            "基金类型": "混合型",
            "净资产规模": "88.80亿元（截止至：2026年06月30日）",
            "管理费率": "1.20%（每年）",
            "成立日期/规模": "2014年5月29日 / 2.00亿份",
        }]
    )

    result = _parse_overview_frame(frame)

    assert result["fund_scale_yi"] == 88.8
    assert result["fund_scale_source"] == "akshare.fund_overview_em"
    assert result["fund_scale_as_of"] == "2026-06-30"
    assert result["fund_type"] == "混合型"
    assert result["management_fee"] == "1.20%（每年）"


def test_overview_does_not_treat_share_or_inception_scale_as_aum():
    import pandas as pd

    frame = pd.DataFrame(
        [{
            "基金类型": "混合型",
            "成立日期/规模": "2014年5月29日 / 2.00亿份",
            "份额规模": "90.00亿份",
        }]
    )

    assert "fund_scale_yi" not in _parse_overview_frame(frame)


def test_diagnostics_adapter_parses_real_fund_overview_shape(monkeypatch):
    captured: dict[str, str] = {}

    def fake_run(script: str, *, label: str, timeout: int):
        captured["script"] = script
        return {
            "overview": {
                "columns": ["基金代码", "基金类型", "净资产规模", "管理费率"],
                "rows": [[
                    "519674",
                    "混合型-偏股",
                    "97.35亿元（截止至：2026年03月31日）",
                    "1.20%（每年）",
                ]],
            },
            "cumulative": {"columns": [], "rows": []},
        }

    monkeypatch.setattr(
        "app.services.fund_diagnostics_cache.run_akshare_json_script",
        fake_run,
    )

    result = _fetch_fund_diagnostics_via_akshare("519674")

    assert "fund_overview_em" in captured["script"]
    assert result == {
        "fund_type": "混合型-偏股",
        "management_fee": "1.20%（每年）",
        "fund_scale_yi": 97.35,
        "fund_scale_source": "akshare.fund_overview_em",
        "fund_scale_as_of": "2026-03-31",
    }


def test_load_fund_diagnostics_uses_cache(monkeypatch):
    save_cached_fund_diagnostics(
        "008586",
        {"fund_type": "股票型", "return_1y_percent": 5.0},
    )

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when cache warm")

    monkeypatch.setattr(
        "app.services.fund_diagnostics_cache._fetch_fund_diagnostics_via_akshare",
        _should_not_fetch,
    )
    result = load_fund_diagnostics("008586")
    assert result["fund_type"] == "股票型"


def test_fund_rank_cache_roundtrip():
    rows = [{"fund_code": "519674", "fund_name": "银河创新成长"}]
    save_cached_open_fund_rank(limit=300, rows=rows)
    cached = get_cached_open_fund_rank(limit=300)
    assert cached == rows
    assert rank_cache_key(300) == "fund:open_rank:v1:300"


def test_fetch_open_fund_rank_cached_skips_fetch(monkeypatch):
    rows = [{"fund_code": "008586", "fund_name": "测试"}]
    save_cached_open_fund_rank(limit=300, rows=rows)

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when cache warm")

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_rank",
        _should_not_fetch,
    )
    assert fetch_open_fund_rank_cached(limit=300) == rows
