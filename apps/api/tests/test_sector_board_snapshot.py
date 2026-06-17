from app.services.sector_board_snapshot import (
    build_list_payload,
    build_widget_payload,
    get_sector_board_snapshot,
)

_STUB_INDUSTRY = [
    {"name": "建筑材料", "code": "BK0425", "change_percent": 4.02, "main_force_net_yi": 12.5},
    {"name": "电子", "code": "BK0459", "change_percent": 2.77, "main_force_net_yi": 141.17},
    {"name": "钢铁", "code": "BK0479", "change_percent": -2.01, "main_force_net_yi": -8.3},
    {"name": "传媒", "code": "BK0486", "change_percent": -1.79, "main_force_net_yi": -5.1},
]

_STUB_CONCEPT = [
    {"name": "半导体材料", "code": "BK0976", "change_percent": 2.51, "main_force_net_yi": 33.14},
    {"name": "新能源", "code": "BK0493", "change_percent": -1.2, "main_force_net_yi": -57.67},
]


def _stub_snapshot() -> dict:
    return {
        "trade_date": "2026-06-17",
        "session_kind": "trading_day_intraday",
        "available": True,
        "from_cache": False,
        "message": None,
        "industry": list(_STUB_INDUSTRY),
        "concept": list(_STUB_CONCEPT),
    }


def test_build_widget_payload_top_bottom():
    widget = build_widget_payload(_stub_snapshot())
    assert widget["available"] is True
    assert len(widget["top_gainers"]) == 3
    assert widget["top_gainers"][0]["name"] == "建筑材料"
    assert widget["top_losers"][0]["name"] == "钢铁"
    assert widget["top_inflow"][0]["main_force_net_yi"] == 141.17
    assert widget["top_outflow"][0]["main_force_net_yi"] == -57.67


def test_build_list_payload_sort_change():
    payload = build_list_payload(_stub_snapshot(), board_type="industry", sort="change")
    assert payload["board_type"] == "industry"
    assert payload["items"][0]["name"] == "建筑材料"
    assert payload["items"][0]["rank"] == 1


def test_build_list_payload_sort_inflow():
    payload = build_list_payload(_stub_snapshot(), board_type="concept", sort="inflow")
    assert payload["items"][0]["name"] == "半导体材料"


def test_build_list_payload_dedupes_duplicate_codes():
    snapshot = _stub_snapshot()
    snapshot["concept"] = [
        {"name": "A板块", "code": "BK1365", "change_percent": 3.0, "main_force_net_yi": 10.0},
        {"name": "A板块重复", "code": "BK1365", "change_percent": 1.0, "main_force_net_yi": 5.0},
        {"name": "B板块", "code": "BK1366", "change_percent": 2.0, "main_force_net_yi": 8.0},
    ]
    payload = build_list_payload(snapshot, board_type="concept", sort="change")
    codes = [item["code"] for item in payload["items"]]
    assert codes.count("BK1365") == 1
    assert payload["items"][0]["name"] == "A板块"


def test_combined_board_rows_prefers_industry():
    from app.services.sector_board_snapshot import _combined_board_rows

    industry = [{"name": "电子", "change_percent": 2.0}]
    concept = [{"name": "电子", "change_percent": 9.0}, {"name": "半导体材料", "change_percent": 2.5}]
    merged = _combined_board_rows(industry, concept)
    by_name = {row["name"]: row["change_percent"] for row in merged}
    assert by_name["电子"] == 2.0
    assert by_name["半导体材料"] == 2.5


def test_get_sector_board_snapshot_falls_back_to_stale_on_fetch_failure(monkeypatch):
    stale = _stub_snapshot()

    monkeypatch.setattr(
        "app.services.sector_board_snapshot._fetch_all_board_records_parallel",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        "app.services.sector_board_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_board_snapshot.get_spot_snapshot_any_age",
        lambda *_args, **_kwargs: stale,
    )
    monkeypatch.setattr(
        "app.services.sector_board_snapshot.build_trading_session",
        lambda: {"effective_trade_date": "2026-06-17", "session_kind": "trading_day_intraday"},
    )

    result = get_sector_board_snapshot(force_refresh=True)
    assert result["available"] is True
    assert result["stale"] is True
    assert result["industry"][0]["name"] == "建筑材料"
    widget = build_widget_payload(result)
    assert len(widget["top_gainers"]) == 3


def test_fetch_board_records_falls_back_to_akshare(monkeypatch):
    from app.services.sector_board_snapshot import _fetch_board_records

    monkeypatch.setattr(
        "app.services.sector_board_snapshot.fetch_eastmoney_board_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("httpx down")),
    )
    monkeypatch.setattr(
        "app.services.sector_board_snapshot.fetch_akshare_board_records",
        lambda board_type: [
            {
                "name": "建筑材料",
                "code": "BK0425",
                "change_percent": 4.0,
                "main_force_net_yi": None,
            }
        ]
        if board_type == "industry"
        else [],
    )
    rows = _fetch_board_records("industry")
    assert rows[0]["name"] == "建筑材料"
