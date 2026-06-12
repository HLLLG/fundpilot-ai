from app.services.fund_code_resolver import lookup_fund_code_by_name, resolve_holding_fund_code


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

    assert lookup_fund_code_by_name("银河创新成长混合A") == "519674"
    assert lookup_fund_code_by_name("华夏人工智能ETF联接C") == "008586"
    assert lookup_fund_code_by_name("华夏中证电网设备...") == "025856"
    assert lookup_fund_code_by_name("华夏中证电网设备主题ETF联接A") == "025856"
    assert lookup_fund_code_by_name("易方达国防军工混..") == "015945"
    polluted = "投资锦囊北美云厂商持续加大资本支出华夏人工智能ETF联C"
    assert lookup_fund_code_by_name(polluted) == "008586"
    assert lookup_fund_code_by_name("托易方达国防军工混合C") == "015945"
    assert lookup_fund_code_by_name("托易方达国防军工混合") == "015945"


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


def test_resolve_holding_fund_code_keeps_profile_when_lookup_fails():
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


def test_fund_name_table_uses_subprocess_payload(monkeypatch):
    from app.services.fund_code_resolver import _fund_name_table

    _fund_name_table.cache_clear()
    monkeypatch.setattr(
        "app.services.fund_code_resolver._fetch_fund_name_table_subprocess",
        lambda: [("519674", "银河创新成长混合A")],
    )

    assert _fund_name_table() == [("519674", "银河创新成长混合A")]
    _fund_name_table.cache_clear()
