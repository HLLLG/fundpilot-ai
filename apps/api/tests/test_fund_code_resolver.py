from app.services.fund_code_resolver import lookup_fund_code_by_name, resolve_holding_fund_code


def _lookup_code(name: str) -> str | None:
    code, _ = lookup_fund_code_by_name(name)
    return code


def test_lookup_fund_code_by_name_matches_known_funds(monkeypatch):
    table = [
        ("519674", "银河创新成长混合A"),
        ("008586", "华夏人工智能ETF联接C"),
        ("025856", "华夏中证电网设备主题ETF发起式联接A"),
        ("015945", "易方达国防军工混合C"),
        ("001475", "易方达国防军工混合A"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    assert _lookup_code("银河创新成长混合A") == "519674"
    assert _lookup_code("华夏人工智能ETF联接C") == "008586"
    assert _lookup_code("华夏中证电网设备...") == "025856"
    assert _lookup_code("华夏中证电网设备主题ETF联接A") == "025856"
    assert _lookup_code("易方达国防军工混..") == "015945"
    polluted = "投资锦囊北美云厂商持续加大资本支出华夏人工智能ETF联C"
    assert _lookup_code(polluted) == "008586"
    assert _lookup_code("托易方达国防军工混合C") == "015945"
    assert _lookup_code("托易方达国防军工混合") == "015945"


def test_lookup_fund_code_by_name_matches_index_and_avic_funds(monkeypatch):
    table = [
        ("021492", "中航机遇领航混合发起C"),
        ("025857", "华夏中证电网设备主题ETF联接C"),
        ("026790", "中欧上证科创板人工智能指数C"),
        ("027575", "天弘上证科创板芯片设计主题ETF发起联接C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    assert _lookup_code("中航机遇领航混合C") == "021492"
    assert _lookup_code("华夏中证电网设备主题ETF联接C") == "025857"
    assert _lookup_code("中欧上证科创板人工智能指数C") == "026790"
    # 支付宝 OCR 简称 ↔ 东财全称（发起联接 / 主题 / 上证科创板）
    assert _lookup_code("天弘科创芯片设计ETF联接C") == "027575"


def test_lookup_fund_code_by_name_uses_fuzzy_for_ocr_typo(monkeypatch):
    table = [
        ("026790", "中欧上证科创板人工智能指数C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )
    code, source = lookup_fund_code_by_name("中欧科创板人工智能指数C")
    assert code == "026790"
    assert source == "fuzzy"


def test_resolve_holding_fund_code_prefers_name_lookup_over_stale_profile_code(monkeypatch):
    table = [("008586", "华夏人工智能ETF联接C")]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    code, source = resolve_holding_fund_code(
        "华夏人工智能ETF联接C",
        existing_code="996882",
    )
    assert code == "008586"
    assert source == "akshare"


def test_resolve_holding_fund_code_prefers_saved_profile_when_share_class_ambiguous(monkeypatch):
    table = [
        ("025856", "华夏中证电网设备主题ETF发起式联接A"),
        ("025857", "华夏中证电网设备主题ETF联接C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    assert _lookup_code("华夏中证电网设备...") == "025857"

    code, source = resolve_holding_fund_code(
        "华夏中证电网设备...",
        existing_code="025856",
    )
    assert code == "025856"
    assert source == "profile"


def test_resolve_holding_fund_code_uses_lookup_when_ocr_specifies_different_share_class(monkeypatch):
    table = [
        ("025856", "华夏中证电网设备主题ETF发起式联接A"),
        ("025857", "华夏中证电网设备主题ETF联接C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    code, source = resolve_holding_fund_code(
        "华夏中证电网设备主题ETF联接C",
        existing_code="025856",
    )
    assert code == "025857"
    assert source == "akshare"


def test_resolve_holding_fund_code_keeps_profile_when_lookup_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: [],
    )
    code, source = resolve_holding_fund_code("任意名称", existing_code="110020")
    assert code == "110020"
    assert source == "profile"


def test_reconcile_holding_fund_codes_fixes_provisional_codes(monkeypatch):
    from app.models import Holding
    from app.services.fund_code_resolver import reconcile_holding_fund_codes

    table = [
        ("025856", "华夏中证电网设备主题ETF发起式联接A"),
        ("015945", "易方达国防军工混合C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )
    holdings = reconcile_holding_fund_codes(
        [
            Holding(
                fund_code="996882",
                fund_name="华夏中证电网设备主题ETF联接A",
                holding_amount=100,
                return_percent=1,
            ),
            Holding(
                fund_code="915548",
                fund_name="托易方达国防军工混合",
                holding_amount=200,
                return_percent=2,
            ),
        ]
    )
    assert holdings[0].fund_code == "025856"
    assert holdings[1].fund_code == "015945"
    assert holdings[1].fund_name == "易方达国防军工混合"


def test_fund_name_table_rejects_mojibake_payload(monkeypatch):
    from app.services.fund_code_resolver import (
        _fund_name_table,
        clear_fund_name_table_cache,
    )

    clear_fund_name_table_cache()
    good = [("000001", "华夏成长混合")] * 1000 + [("026790", "中欧上证科创板人工智能指数C")]
    bad = [("000001", "鍗庡忔垚闀挎贩鍚")] * 1000 + [("026790", "ŷ֤ƴ˹ָC")]
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        _fetch,
    )

    table = _fund_name_table()
    assert calls["n"] == 2
    assert table[-1] == ("026790", "中欧上证科创板人工智能指数C")
    clear_fund_name_table_cache()


def test_fund_name_table_uses_subprocess_payload(monkeypatch):
    from app.services.fund_code_resolver import _fund_name_table, clear_fund_name_table_cache

    clear_fund_name_table_cache()
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        lambda: [("519674", "银河创新成长混合A")],
    )

    assert _fund_name_table() == [("519674", "银河创新成长混合A")]
    clear_fund_name_table_cache()


def test_search_by_code_reloads_when_cached_name_is_mojibake(monkeypatch):
    from app.services.fund_code_resolver import clear_fund_name_table_cache, search_funds_by_keyword

    clear_fund_name_table_cache()
    good = [("000001", "华夏成长混合")] * 1000 + [("026790", "中欧上证科创板人工智能指数C")]
    bad = [("000001", "鍗庡忔垚闀挎贩鍚")] * 1000 + [("026790", "ŷ֤ƴ˹ָC")]
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        _fetch,
    )

    items = search_funds_by_keyword("026790")
    assert calls["n"] >= 2
    assert items == [{"fund_code": "026790", "fund_name": "中欧上证科创板人工智能指数C"}]
    clear_fund_name_table_cache()
