from app.services.fund_code_resolver import lookup_fund_code_by_name, resolve_holding_fund_code


def test_lookup_fund_code_by_name_matches_known_funds(monkeypatch):
    table = [
        ("519674", "银河创新成长混合A"),
        ("008586", "华夏人工智能ETF联接C"),
        ("025856", "华夏中证电网设备主题ETF联接A"),
        ("015945", "易方达国防军工混合C"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    assert lookup_fund_code_by_name("银河创新成长混合A") == "519674"
    assert lookup_fund_code_by_name("华夏人工智能ETF联接C") == "008586"
    assert lookup_fund_code_by_name("华夏中证电网设备...") == "025856"
    assert lookup_fund_code_by_name("易方达国防军工混..") == "015945"


def test_resolve_holding_fund_code_prefers_existing_code():
    code, source = resolve_holding_fund_code("任意名称", existing_code="110020")
    assert code == "110020"
    assert source is None


def test_fund_name_table_uses_subprocess_payload(monkeypatch):
    from app.services.fund_code_resolver import _fund_name_table

    _fund_name_table.cache_clear()
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        lambda: [("519674", "银河创新成长混合A")],
    )

    assert _fund_name_table() == [("519674", "银河创新成长混合A")]
    _fund_name_table.cache_clear()
