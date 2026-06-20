from app.models import Holding
from app.services import theme_board_snapshot as mod
from app.services.theme_board_snapshot import (
    apply_flow_to_items,
    apply_holdings_overlay,
    build_theme_board_payload,
    compute_consecutive_up_days,
    list_theme_board_universe,
    refresh_theme_board_snapshot,
    get_theme_board_snapshot,
)


def test_compute_consecutive_up_days():
    assert compute_consecutive_up_days(
        [
            {"date": "2026-06-13", "change_percent": 1.0},
            {"date": "2026-06-16", "change_percent": 0.5},
            {"date": "2026-06-17", "change_percent": 2.0},
        ],
        "2026-06-17",
    ) == 3
    assert compute_consecutive_up_days(
        [
            {"date": "2026-06-16", "change_percent": -1.0},
            {"date": "2026-06-17", "change_percent": 2.0},
        ],
        "2026-06-17",
    ) == 1
    assert compute_consecutive_up_days([{"date": "2026-06-17", "change_percent": 0.0}], "2026-06-17") == 0
    assert compute_consecutive_up_days([{"date": "2026-06-17", "change_percent": None}], "2026-06-17") is None


def test_list_theme_board_universe_resolves_via_canonical_and_alias():
    # conftest stubs board records -> empty；仅 canonical + 别名能解析
    universe = list_theme_board_universe()
    by_label = {e["sector_label"]: e for e in universe}
    # canonical 命中（半导体→中证半导体指数，对标小倍）
    assert "半导体" in by_label
    assert by_label["半导体"]["secid"] == "2.H30184"
    assert by_label["半导体"]["board_kind"] == "index"
    # 小倍式中证主题指数
    assert by_label["5G"]["secid"] == "2.931079"
    assert by_label["5G"]["board_kind"] == "index"
    assert by_label["消费电子"]["secid"] == "2.931494"
    assert by_label["稀土"]["secid"] == "2.930598"
    assert by_label["稀土"]["board_kind"] == "index"
    # 小倍式中证主题指数（软件不再走别名 BK0737）
    assert "软件" in by_label
    assert by_label["软件"]["secid"] == "2.H30202"
    assert by_label["软件"]["board_kind"] == "index"
    assert by_label["创新药"]["secid"] == "2.931152"
    # 双轨：指数涨跌幅 + 东财 BK 资金流
    assert by_label["半导体"]["flow_source_code"] == "BK1036"
    assert by_label["软件"]["flow_source_code"] == "BK0737"
    # 标签唯一（允许光伏/新能源等同 secid 不同名）
    labels = [e["sector_label"] for e in universe]
    assert len(labels) == len(set(labels))


def test_list_theme_board_universe_resolves_via_eastmoney_name(monkeypatch):
    def fake(board_type):
        if board_type == "concept":
            return [
                {"name": "稀土", "code": "BK1625", "change_percent": 2.86},
                {"name": "创新药", "code": "BK0731", "change_percent": 2.67},
            ]
        return []

    monkeypatch.setattr(mod, "fetch_eastmoney_board_records", fake)
    universe = list_theme_board_universe()
    by_label = {e["sector_label"]: e for e in universe}
    # 稀土已固化中证指数，不受东财名表影响
    assert by_label["稀土"]["secid"] == "2.930598"
    assert by_label["创新药"]["secid"] == "2.931152"


def test_refresh_theme_board_snapshot_computes_change(monkeypatch):
    monkeypatch.setattr(
        mod,
        "list_theme_board_universe",
        lambda: [
            {
                "sector_label": "半导体",
                "secid": "90.BK1036",
                "source_code": "BK1036",
                "board_kind": "concept",
                "_canon": None,
            }
        ],
    )
    monkeypatch.setattr(
        mod,
        "fetch_eastmoney_kline_close_percent",
        lambda secid, **kwargs: 2.0,
    )
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 2.0
    assert "consecutive_up_days" not in item
    assert item["board_kind"] == "concept"
    assert "linked_fund_count" not in item
    assert snapshot["refreshed_at"]


def test_refresh_theme_board_snapshot_trends2_fallback(monkeypatch):
    monkeypatch.setattr(
        mod,
        "list_theme_board_universe",
        lambda: [
            {
                "sector_label": "人工智能",
                "secid": "2.930713",
                "source_code": "930713",
                "board_kind": "index",
                "_canon": None,
            }
        ],
    )
    monkeypatch.setattr(mod, "_fetch_universe_series", lambda *a, **k: [])
    monkeypatch.setattr(
        mod,
        "fetch_eastmoney_kline_close_percent",
        lambda secid, **kwargs: 4.76 if secid == "2.930713" else None,
    )
    monkeypatch.setattr(mod, "_load_theme_spot_changes", lambda: {"人工智能": 0.37})
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 4.76
    assert "consecutive_up_days" not in item


def test_refresh_theme_board_snapshot_spot_fallback(monkeypatch):
    monkeypatch.setattr(
        mod,
        "list_theme_board_universe",
        lambda: [
            {
                "sector_label": "电子",
                "secid": "90.BK0447",
                "source_code": "BK0447",
                "board_kind": "industry",
                "_canon": None,
            }
        ],
    )
    monkeypatch.setattr(mod, "_fetch_universe_series", lambda *a, **k: [])
    monkeypatch.setattr(mod, "fetch_eastmoney_kline_close_percent", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_load_theme_spot_changes", lambda: {"电子": 3.21})
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 3.21
    assert "consecutive_up_days" not in item


def test_apply_holdings_overlay_matches_by_secid():
    from app.services.sector_canonical import get_quote_canonical_sector

    semi_secid = get_quote_canonical_sector("半导体").eastmoney_secid
    items = [
        {"sector_label": "半导体", "secid": semi_secid, "change_1d_percent": 1.0},
        {"sector_label": "电子", "secid": "90.BK0447", "change_1d_percent": 0.5},
    ]
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            holding_amount=1000,
            return_percent=1,
            sector_name="半导体",
        )
    ]
    overlaid = apply_holdings_overlay(items, holdings)
    semi = next(i for i in overlaid if i["sector_label"] == "半导体")
    other = next(i for i in overlaid if i["sector_label"] == "电子")
    assert semi["held_fund_count"] == 1
    assert semi["in_portfolio"] is True
    assert other["in_portfolio"] is False


def test_build_theme_board_payload_sort_and_strips_internal():
    items = [
        {"sector_label": "半导体", "secid": "90.BK1036", "change_1d_percent": 1.5, "_canon": "x"},
        {"sector_label": "商业航天", "secid": "90.BK0963", "change_1d_percent": 2.8},
    ]
    meta = {
        "trade_date": "2026-06-18",
        "session_kind": "trading_day_intraday",
        "available": True,
        "from_cache": True,
        "stale": False,
        "refreshed_at": "2026-06-18T06:00:00+00:00",
        "message": None,
    }
    by_change = build_theme_board_payload(items, sort="change", snapshot_meta=meta, holdings=[])
    assert by_change["items"][0]["sector_label"] == "商业航天"
    assert by_change["items"][0]["rank"] == 1
    assert by_change["refreshed_at"] == "2026-06-18T06:00:00+00:00"
    assert "_canon" not in by_change["items"][0]

    by_streak = build_theme_board_payload(items, sort="streak", snapshot_meta=meta, holdings=[])
    assert len(by_streak["items"]) == 2


def test_get_theme_board_snapshot_reads_cache_and_overlays(monkeypatch):
    from app.services.sector_canonical import get_quote_canonical_sector

    semi_secid = get_quote_canonical_sector("半导体").eastmoney_secid
    cached = {
        "items": [
            {"sector_label": "半导体", "board_kind": "index", "secid": semi_secid,
             "change_1d_percent": 2.0},
            {"sector_label": "电子", "board_kind": "industry", "secid": "90.BK0447",
             "change_1d_percent": 1.0},
        ],
        "trade_date": "2026-06-18",
        "session_kind": "trading_day_intraday",
        "refreshed_at": "2026-06-18T06:00:00+00:00",
    }
    monkeypatch.setattr(mod, "get_spot_snapshot_any_age", lambda *a, **k: cached)
    holding = Holding(
        fund_code="519674",
        fund_name="半导体基金",
        holding_amount=1000,
        return_percent=1,
        sector_name="半导体",
    )
    payload = get_theme_board_snapshot(holdings=[holding], sort="change")
    assert payload["from_cache"] is True
    assert payload["refreshed_at"] == "2026-06-18T06:00:00+00:00"
    semi = next(i for i in payload["items"] if i["sector_label"] == "半导体")
    assert semi["in_portfolio"] is True
    assert semi["board_kind"] == "index"
    assert "linked_fund_count" not in semi
    assert payload["items"][0]["change_1d_percent"] >= payload["items"][1]["change_1d_percent"]


def test_apply_flow_to_items_merges_sector_snapshot(monkeypatch):
    monkeypatch.setattr(
        mod,
        "get_sector_board_snapshot",
        lambda **_kwargs: {
            "industry": [
                {
                    "name": "电子",
                    "code": "BK0447",
                    "change_percent": 1.0,
                    "main_force_net_yi": 12.5,
                    "super_large_net_yi": 20.0,
                    "large_net_yi": -7.5,
                    "medium_net_yi": -3.0,
                    "small_net_yi": -9.5,
                }
            ],
            "concept": [
                {
                    "name": "半导体",
                    "code": "BK1036",
                    "main_force_net_yi": 12.5,
                    "super_large_net_yi": 20.0,
                    "large_net_yi": -7.5,
                    "medium_net_yi": -3.0,
                    "small_net_yi": -9.5,
                }
            ],
        },
    )
    items = [
        {
            "sector_label": "电子",
            "board_kind": "industry",
            "secid": "90.BK0447",
            "flow_source_code": "BK0447",
            "change_1d_percent": 1.0,
        },
        {
            "sector_label": "半导体",
            "board_kind": "index",
            "secid": "2.H30184",
            "flow_source_code": "BK1036",
            "change_1d_percent": 2.0,
        },
    ]
    enriched = apply_flow_to_items(items)
    electronic = enriched[0]
    assert electronic["main_force_net_yi"] == 12.5
    assert electronic["flow_tiers"]["super_large_net_yi"] == 20.0
    assert electronic["flow_tiers"]["small_net_yi"] == -9.5
    semi = enriched[1]
    assert semi["main_force_net_yi"] == 12.5
    assert semi["flow_tiers"]["super_large_net_yi"] == 20.0


def test_build_theme_board_payload_sort_by_inflow(monkeypatch):
    monkeypatch.setattr(
        mod,
        "get_sector_board_snapshot",
        lambda **_kwargs: {
            "industry": [
                {"code": "BK0447", "main_force_net_yi": 5.0},
                {"code": "BK1036", "main_force_net_yi": 20.0},
            ],
            "concept": [],
        },
    )
    items = [
        {
            "sector_label": "电子",
            "board_kind": "industry",
            "secid": "90.BK0447",
            "flow_source_code": "BK0447",
            "change_1d_percent": 3.0,
        },
        {
            "sector_label": "半导体",
            "board_kind": "index",
            "secid": "2.H30184",
            "flow_source_code": "BK1036",
            "change_1d_percent": 1.0,
        },
    ]
    meta = {
        "trade_date": "2026-06-18",
        "session_kind": "trading_day_intraday",
        "available": True,
        "from_cache": True,
        "stale": False,
        "refreshed_at": "2026-06-18T06:00:00+00:00",
        "message": None,
    }
    payload = build_theme_board_payload(items, sort="inflow", snapshot_meta=meta, holdings=[])
    assert payload["sort"] == "inflow"
    assert payload["items"][0]["sector_label"] == "半导体"
    assert payload["items"][0]["main_force_net_yi"] == 20.0
