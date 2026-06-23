from app.services.fund_name_fuzzy import best_fuzzy_fund_match, fuzzy_name_match_score
from app.services.fund_name_table_store import (
    clear_persisted_fund_name_table_cache,
    load_cached_fund_name_table,
    save_fund_name_table_cache,
)


def test_fund_name_table_cache_roundtrip(tmp_path, monkeypatch):
    cache_file = tmp_path / "fund_name_table_cache.json"
    monkeypatch.setenv("FUND_AI_FUND_NAME_CACHE_PATH", str(cache_file))
    clear_persisted_fund_name_table_cache()

    rows = [("026790", "中欧上证科创板人工智能指数C")]
    save_fund_name_table_cache(rows)
    loaded = load_cached_fund_name_table()
    assert loaded == rows

    clear_persisted_fund_name_table_cache()
    assert load_cached_fund_name_table() is None


def test_fund_name_table_cache_expires(tmp_path, monkeypatch):
    import json

    cache_file = tmp_path / "fund_name_table_cache.json"
    monkeypatch.setenv("FUND_AI_FUND_NAME_CACHE_PATH", str(cache_file))
    monkeypatch.setenv("FUND_AI_FUND_NAME_TABLE_TTL_SECONDS", "60")
    clear_persisted_fund_name_table_cache()

    cache_file.write_text(
        json.dumps(
            {
                "version": 1,
                "fetched_at": "2000-01-01T00:00:00+00:00",
                "rows": [["000001", "华夏成长混合"]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert load_cached_fund_name_table() is None


def test_fuzzy_match_handles_ocr_typo_and_theme_abbreviation():
    table = [
        ("026790", "中欧上证科创板人工智能指数C"),
        ("027575", "天弘上证科创板芯片设计主题ETF发起联接C"),
    ]
    match = best_fuzzy_fund_match("中欧科创板人工智能指数C", table)
    assert match is not None
    assert match[0] == "026790"
    assert fuzzy_name_match_score(
        "天弘科创芯片设计ETF联接C",
        "天弘上证科创板芯片设计主题ETF发起联接C",
    ) >= 0.86
