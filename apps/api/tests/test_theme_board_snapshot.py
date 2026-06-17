from app.models import Holding
from app.services.theme_board_snapshot import (
    apply_holdings_overlay,
    build_linked_fund_counts,
    build_theme_board_payload,
    compute_consecutive_up_days,
    get_theme_board_snapshot,
    _build_theme_board_items,
    _merge_theme_board_rows,
)


def test_compute_consecutive_up_days_counts_from_trade_date():
    series = [
        {"date": "2026-06-13", "change_percent": 1.0},
        {"date": "2026-06-16", "change_percent": 0.5},
        {"date": "2026-06-17", "change_percent": 2.0},
    ]
    assert compute_consecutive_up_days(series, "2026-06-17") == 3


def test_compute_consecutive_up_days_stops_on_non_positive():
    series = [
        {"date": "2026-06-16", "change_percent": -1.0},
        {"date": "2026-06-17", "change_percent": 2.0},
    ]
    assert compute_consecutive_up_days(series, "2026-06-17") == 1


def test_compute_consecutive_up_days_zero_when_today_flat():
    series = [{"date": "2026-06-17", "change_percent": 0.0}]
    assert compute_consecutive_up_days(series, "2026-06-17") == 0


def test_compute_consecutive_up_days_null_when_missing_today_change():
    series = [{"date": "2026-06-17", "change_percent": None}]
    assert compute_consecutive_up_days(series, "2026-06-17") is None


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


def test_build_theme_board_payload_sort_change():
    items = [
        {"sector_label": "半导体", "change_1d_percent": 1.5, "consecutive_up_days": 2},
        {"sector_label": "商业航天", "change_1d_percent": 2.8, "consecutive_up_days": 5},
    ]
    payload = build_theme_board_payload(
        items,
        sort="change",
        snapshot_meta={
            "trade_date": "2026-06-17",
            "session_kind": "trading_day_intraday",
            "available": True,
            "from_cache": False,
            "stale": False,
            "message": None,
        },
        holdings=[],
    )
    assert payload["items"][0]["sector_label"] == "商业航天"
    assert payload["items"][0]["rank"] == 1


def test_build_theme_board_payload_sort_streak():
    items = [
        {"sector_label": "半导体", "change_1d_percent": 1.5, "consecutive_up_days": 2},
        {"sector_label": "商业航天", "change_1d_percent": 2.8, "consecutive_up_days": 5},
    ]
    payload = build_theme_board_payload(
        items,
        sort="streak",
        snapshot_meta={
            "trade_date": "2026-06-17",
            "session_kind": "trading_day_intraday",
            "available": True,
            "from_cache": False,
            "stale": False,
            "message": None,
        },
        holdings=[],
    )
    assert payload["items"][0]["sector_label"] == "商业航天"


def test_get_theme_board_snapshot_uses_cache(monkeypatch):
    stub_items = [
        {
            "sector_label": "商业航天",
            "change_1d_percent": 2.78,
            "consecutive_up_days": 5,
            "linked_fund_count": 2,
        }
    ]

    def fake_build_items(**_kwargs):
        return list(stub_items)

    monkeypatch.setattr(
        "app.services.theme_board_snapshot._build_theme_board_items",
        fake_build_items,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    saved: list[dict] = []
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.save_spot_snapshot",
        lambda _key, payload: saved.append(payload),
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.build_trading_session",
        lambda: {"effective_trade_date": "2026-06-17", "session_kind": "trading_day_intraday"},
    )

    first = get_theme_board_snapshot(force_refresh=True)
    assert first["available"] is True
    assert len(first["items"]) == 1
    assert saved

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: saved[0],
    )
    second = get_theme_board_snapshot(force_refresh=False)
    assert second["from_cache"] is True


def test_merge_theme_board_rows_fills_all_labels():
    merged = _merge_theme_board_rows(
        [{"sector_label": "半导体", "change_1d_percent": 1.0, "consecutive_up_days": 2, "linked_fund_count": 1}]
    )
    assert len(merged) == 19
    assert merged[0]["sector_label"] == "商业航天"
    by_label = {row["sector_label"]: row for row in merged}
    assert by_label["半导体"]["change_1d_percent"] == 1.0
    assert by_label["医药"]["change_1d_percent"] is None


def test_lookup_spot_change_fuzzy_match():
    from app.services.sector_canonical import get_canonical_sector
    from app.services.theme_board_snapshot import _lookup_spot_change

    canon = get_canonical_sector("医药")
    assert canon is not None
    spot = {"医药医疗": 1.23, "半导体": 2.0}
    assert _lookup_spot_change(label="医药", canon=canon, spot_changes=spot) == 1.23


def test_build_theme_board_items_respects_budget(monkeypatch):
    import time as time_module

    def slow_series(*_args, **_kwargs):
        time_module.sleep(30)
        return [{"date": "2026-06-17", "change_percent": 1.0}]

    monkeypatch.setattr(
        "app.services.theme_board_snapshot._BUDGET_SECONDS",
        0.2,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.list_discovery_sector_labels",
        lambda: ["半导体", "商业航天", "医药"],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.build_linked_fund_counts",
        lambda: {"半导体": 1, "商业航天": 1, "医药": 0},
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot._load_theme_spot_changes",
        lambda: {"半导体": 1.0, "商业航天": 2.0, "医药医疗": 0.5},
    )

    def slow_series(_canon):
        time_module.sleep(30)
        return [{"date": "2026-06-17", "change_percent": 9.0}]

    start = time_module.monotonic()
    items = _build_theme_board_items(
        trade_date="2026-06-17",
        fetch_series=slow_series,
        spot_changes={"半导体": 1.0, "商业航天": 2.0, "医药医疗": 0.5},
    )
    elapsed = time_module.monotonic() - start

    assert len(items) == 3
    assert items[0]["change_1d_percent"] is not None
    assert elapsed < 5
