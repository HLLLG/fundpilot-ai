from app.models import Holding
from app.services import theme_board_snapshot as mod
from app.services.theme_board_snapshot import (
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
    # canonical 命中
    assert "半导体" in by_label
    assert by_label["半导体"]["board_kind"] == "concept"
    # 别名命中（软件 -> 软件开发 90.BK0737）
    assert "软件" in by_label
    assert by_label["软件"]["secid"] == "90.BK0737"
    assert by_label["软件"]["board_kind"] == "industry"
    # 需东财概念/行业名表才能解析的（空表时跳过），不应出现细分行业
    assert "稀土" not in by_label
    # secid 唯一
    secids = [e["secid"] for e in universe]
    assert len(secids) == len(set(secids))


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
    assert by_label["稀土"]["secid"] == "90.BK1625"
    assert by_label["稀土"]["board_kind"] == "concept"
    assert by_label["稀土"]["change_hint"] == 2.86


def test_refresh_theme_board_snapshot_computes_change_and_streak(monkeypatch):
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

    def fake_series(secid, source_code=None, *, canon=None, timeout=8.0):
        return [
            {"date": "2026-06-16", "change_percent": 1.0},
            {"date": "2026-06-17", "change_percent": 0.5},
            {"date": "2026-06-18", "change_percent": 2.0},
        ]

    monkeypatch.setattr(mod, "_fetch_universe_series", fake_series)
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 2.0
    assert item["consecutive_up_days"] == 3
    assert item["board_kind"] == "concept"
    assert "linked_fund_count" not in item
    assert snapshot["refreshed_at"]


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
    monkeypatch.setattr(mod, "_load_theme_spot_changes", lambda: {"电子": 3.21})
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 3.21
    assert item["consecutive_up_days"] is None


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
        {"sector_label": "半导体", "secid": "90.BK1036", "change_1d_percent": 1.5, "consecutive_up_days": 2, "_canon": "x"},
        {"sector_label": "商业航天", "secid": "90.BK0963", "change_1d_percent": 2.8, "consecutive_up_days": 5},
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
    assert by_streak["items"][0]["sector_label"] == "商业航天"


def test_get_theme_board_snapshot_reads_cache_and_overlays(monkeypatch):
    from app.services.sector_canonical import get_quote_canonical_sector

    semi_secid = get_quote_canonical_sector("半导体").eastmoney_secid
    cached = {
        "items": [
            {"sector_label": "半导体", "board_kind": "concept", "secid": semi_secid,
             "change_1d_percent": 2.0, "consecutive_up_days": 3},
            {"sector_label": "电子", "board_kind": "industry", "secid": "90.BK0447",
             "change_1d_percent": 1.0, "consecutive_up_days": 1},
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
    assert semi["board_kind"] == "concept"
    assert "linked_fund_count" not in semi
    assert payload["items"][0]["change_1d_percent"] >= payload["items"][1]["change_1d_percent"]
