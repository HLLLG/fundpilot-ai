from app.models import Holding
from app.services.theme_board_snapshot import (
    apply_holdings_overlay,
    build_linked_fund_counts,
    build_theme_board_payload,
    compute_consecutive_up_days,
    _lookup_spot_change,
    _merge_theme_board_rows,
    _theme_streak_unavailable_hint,
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


def test_build_linked_fund_counts_includes_seeds(monkeypatch):
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.list_fund_primary_sectors",
        lambda: [],
    )
    counts = build_linked_fund_counts()
    assert counts["半导体"] >= 1
    assert counts["商业航天"] >= 1


def test_apply_holdings_overlay():
    items = [{"sector_label": "半导体", "change_1d_percent": 1.0}]
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
    assert overlaid[0]["held_fund_count"] == 1
    assert overlaid[0]["in_portfolio"] is True


def test_build_theme_board_payload_sort():
    items = [
        {"sector_label": "半导体", "change_1d_percent": 1.5, "consecutive_up_days": 2},
        {"sector_label": "商业航天", "change_1d_percent": 2.8, "consecutive_up_days": 5},
    ]
    meta = {
        "trade_date": "2026-06-17",
        "session_kind": "trading_day_intraday",
        "available": True,
        "from_cache": False,
        "stale": False,
        "message": None,
    }
    by_change = build_theme_board_payload(items, sort="change", snapshot_meta=meta, holdings=[])
    assert by_change["items"][0]["sector_label"] == "商业航天"
    assert by_change["items"][0]["rank"] == 1

    by_streak = build_theme_board_payload(items, sort="streak", snapshot_meta=meta, holdings=[])
    assert by_streak["items"][0]["sector_label"] == "商业航天"


def test_merge_theme_board_rows_fills_all_labels():
    merged = _merge_theme_board_rows(
        [{"sector_label": "半导体", "change_1d_percent": 1.0, "consecutive_up_days": 2, "linked_fund_count": 1}]
    )
    assert len(merged) == 21
    by_label = {row["sector_label"]: row for row in merged}
    assert by_label["半导体"]["change_1d_percent"] == 1.0
    assert by_label["医药"]["change_1d_percent"] is None


def test_enrich_theme_board_prefers_kline_over_spot(monkeypatch):
    from app.services.sector_canonical import get_canonical_sector

    canon = get_canonical_sector("半导体")
    assert canon is not None
    row = {
        "sector_label": "半导体",
        "change_1d_percent": 4.13,
        "consecutive_up_days": None,
        "linked_fund_count": 1,
        "_canon": canon,
    }

    def fake_series(_canon):
        return [
            {"date": "2026-06-16", "change_percent": 1.0},
            {"date": "2026-06-17", "change_percent": 6.91},
        ]

    monkeypatch.setattr(
        "app.services.theme_board_snapshot._load_theme_spot_changes",
        lambda: {"半导体": 4.13},
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_kline_close_percent",
        lambda *args, **kwargs: 6.91,
    )
    from app.services.theme_board_snapshot import (
        _enrich_theme_board_daily_change,
        _enrich_theme_board_streak,
    )

    _enrich_theme_board_daily_change(row, "2026-06-17")
    _enrich_theme_board_streak(row, "2026-06-17", fake_series)
    assert row["change_1d_percent"] == 6.91
    assert row["consecutive_up_days"] == 2


def test_lookup_spot_change_fuzzy_match():
    from app.services.sector_canonical import get_canonical_sector

    canon = get_canonical_sector("医药")
    assert canon is not None
    spot = {"医药医疗": 1.23, "半导体": 2.0}
    assert _lookup_spot_change(label="医药", canon=canon, spot_changes=spot) == 1.23


def test_theme_streak_unavailable_hint():
    assert _theme_streak_unavailable_hint([{"change_1d_percent": 1.0}]) is not None
    assert _theme_streak_unavailable_hint(
        [{"change_1d_percent": 1.0, "consecutive_up_days": 2}]
    ) is None
    assert _theme_streak_unavailable_hint([{"change_1d_percent": None}]) is None
