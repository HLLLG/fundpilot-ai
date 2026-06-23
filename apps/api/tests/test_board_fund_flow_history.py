from app.services.board_fund_flow_history import (
    get_board_flow_history,
    get_cached_board_flow_series,
    parse_board_flow_kline,
    resolve_board_flow_code,
    _slice_range,
)


def test_parse_board_flow_kline():
    raw = "2026-06-22,-7374536704.0,3472195072.0,3897475072.0,-247009280.0,-7127527424.0,3.23,-1.29"
    parsed = parse_board_flow_kline(raw)
    assert parsed is not None
    assert parsed["date"] == "2026-06-22"
    assert parsed["main_force_net_yi"] == -73.75
    assert parsed["flow_tiers"]["super_large_net_yi"] == -71.28
    assert parsed["flow_tiers"]["small_net_yi"] == 34.72


def test_slice_range_week_and_month():
    points = [{"date": f"2026-06-{day:02d}", "main_force_net_yi": float(day)} for day in range(1, 26)]
    week = _slice_range(points, "week")
    month = _slice_range(points, "month")
    assert len(week) == 5
    assert week[-1]["date"] == "2026-06-25"
    assert len(month) == 20
    assert month[-1]["date"] == "2026-06-25"


def test_resolve_board_flow_code_by_label(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.list_theme_board_universe",
        lambda: [
            {"sector_label": "半导体", "flow_source_code": "BK1036"},
        ],
    )
    monkeypatch.setattr("app.services.board_fund_flow_history._LABEL_TO_FLOW_CODE", None)
    code, label = resolve_board_flow_code(sector_label="半导体")
    assert code == "BK1036"
    assert label == "半导体"


def test_get_board_flow_history_uses_cache(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.resolve_board_flow_code",
        lambda **kwargs: ("BK1036", "半导体"),
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_spot_snapshot",
        lambda cache_key, ttl_seconds: {
            "board_code": "BK1036",
            "series": [
                {"date": "2026-06-18", "main_force_net_yi": 1.0},
                {"date": "2026-06-19", "main_force_net_yi": -2.0},
                {"date": "2026-06-20", "main_force_net_yi": 3.0},
                {"date": "2026-06-21", "main_force_net_yi": -1.0},
                {"date": "2026-06-22", "main_force_net_yi": 4.0},
            ],
            "refreshed_at": "2026-06-23T00:00:00+00:00",
        },
    )

    payload = get_board_flow_history(sector_label="半导体", flow_range="week")
    assert payload["available"] is True
    assert payload["board_code"] == "BK1036"
    assert len(payload["points"]) == 5
    assert payload["cumulative_net_yi"] == 5.0


def test_resolve_board_flow_code_falls_back_to_canonical(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history._LABEL_TO_FLOW_CODE",
        {},
    )
    code, label = resolve_board_flow_code(sector_label="医药")
    assert code == "BK0465"
    assert label == "医药"


def test_get_cached_board_flow_series_uses_stale_when_fetch_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.fetch_board_flow_series",
        lambda _code: [],
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_spot_snapshot_any_age",
        lambda _key: {
            "series": [{"date": "2026-06-22", "main_force_net_yi": 1.0}],
        },
    )
    series = get_cached_board_flow_series("BK1036")
    assert len(series) == 1
    assert series[0]["main_force_net_yi"] == 1.0


def test_get_board_flow_history_unknown_sector(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history._build_label_to_flow_code_map",
        lambda: {},
    )
    payload = get_board_flow_history(sector_label="不存在的板块", flow_range="week")
    assert payload["available"] is False
    assert payload["points"] == []
