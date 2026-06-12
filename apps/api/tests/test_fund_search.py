from app.services.fund_code_resolver import search_funds_by_keyword


def test_search_funds_by_keyword(monkeypatch):
    table = [
        ("025856", "华夏中证电网设备主题ETF发起式联接A"),
        ("015945", "易方达国防军工混合C"),
        ("001475", "易方达国防军工混合A"),
    ]
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fund_name_table",
        lambda: table,
    )

    by_code = search_funds_by_keyword("025856")
    assert len(by_code) == 1
    assert by_code[0]["fund_code"] == "025856"

    by_name = search_funds_by_keyword("国防军工")
    codes = {item["fund_code"] for item in by_name}
    assert "015945" in codes
    assert "001475" in codes
