from app.services.theme_board_streak import attach_consecutive_up_days, consecutive_up_days


def test_consecutive_up_days_counts_latest_run() -> None:
    assert consecutive_up_days([("2026-08-12", 1.0), ("2026-08-13", 0.4), ("2026-08-14", 0.2)]) == 3
    assert consecutive_up_days([("2026-08-12", 1.0), ("2026-08-13", -0.1), ("2026-08-14", 0.8)]) == 1
    assert consecutive_up_days([("2026-08-12", -0.4), ("2026-08-13", -0.8), ("2026-08-14", -1.1)]) == -3
    assert consecutive_up_days([("2026-08-12", 1.0), ("2026-08-13", 0.4), ("2026-08-14", -0.2)]) == -1
    assert consecutive_up_days([("2026-08-13", 1.0), ("2026-08-14", 0.0)]) == 0
    assert consecutive_up_days([("2026-08-14", -1.2)]) == -1


def test_consecutive_up_days_empty_or_unknown_stays_none() -> None:
    assert consecutive_up_days([]) is None
    assert consecutive_up_days([("2026-08-14", None)]) is None


def test_consecutive_up_days_breaks_when_trading_days_are_missing() -> None:
    """账本漏记 8/10–8/13 时，不能把 8/7 和 8/14 两头的上涨连成 +7。"""
    assert (
        consecutive_up_days(
            [
                ("2026-08-03", 0.95),
                ("2026-08-04", 2.95),
                ("2026-08-05", 2.04),
                ("2026-08-06", 0.29),
                ("2026-08-07", 0.32),
                ("2026-08-14", 0.90),
            ]
        )
        == 1
    )


def test_consecutive_up_days_allows_weekend_between_friday_and_monday() -> None:
    assert consecutive_up_days([("2026-08-07", 0.32), ("2026-08-10", 0.50)]) == 2


def test_attach_uses_today_change_and_ledger(monkeypatch) -> None:
    store: dict[str, dict] = {
        "theme:daily_change:v1": {
            "boards": {
                "半导体": [
                    {"date": "2026-08-13", "change": 1.1},
                    {"date": "2026-08-14", "change": 0.6},
                ]
            }
        }
    }
    monkeypatch.setattr(
        "app.services.theme_board_streak.get_spot_snapshot_any_age",
        lambda key: store.get(key),
    )
    saved: dict[str, dict] = {}
    monkeypatch.setattr(
        "app.services.theme_board_streak.save_spot_snapshot",
        lambda key, payload: saved.update({key: payload}),
    )

    items = [{"sector_label": "半导体", "change_1d_percent": 0.3, "flow_source_code": None}]
    attach_consecutive_up_days(items, trade_date="2026-08-15", persist=True)

    assert items[0]["consecutive_up_days"] == 3
    assert saved["theme:daily_change:v1"]["boards"]["半导体"][-1] == {
        "date": "2026-08-15",
        "change": 0.3,
    }


def test_attach_breaks_streak_when_today_is_down(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.theme_board_streak.get_spot_snapshot_any_age",
        lambda _key: {
            "boards": {
                "半导体": [
                    {"date": "2026-08-13", "change": 1.1},
                    {"date": "2026-08-14", "change": 0.6},
                ]
            }
        },
    )
    items = [{"sector_label": "半导体", "change_1d_percent": -0.4}]
    attach_consecutive_up_days(items, trade_date="2026-08-15", persist=False)
    assert items[0]["consecutive_up_days"] == -1


def test_backfill_skips_index_theme_with_mixed_basket(monkeypatch) -> None:
    """指数主题（涨幅=中证指数、资金=东财 BK）冷启动不得用 BK 日线涨跌回填账本。

    账本按展示涨幅逐日记账；把另一个成分篮子的涨跌混进同一条连涨序列，会让
    「连涨天数」既不是指数的也不是板块的。宁可从当日起重新记。
    """
    monkeypatch.setattr(
        "app.services.theme_board_streak.get_spot_snapshot_any_age",
        lambda _key: {"boards": {}},
    )

    def _forbidden(_code):
        raise AssertionError("index theme must not backfill from BK flow history")

    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_board_flow_series_cache_only",
        _forbidden,
    )
    items = [
        {
            "sector_label": "医疗",
            "change_1d_percent": 0.9,
            "source_code": "399989",
            "flow_source_code": "BK0727",
        }
    ]
    attach_consecutive_up_days(items, trade_date="2026-08-15", persist=False)
    assert items[0]["consecutive_up_days"] == 1


def test_backfill_allows_board_theme_with_same_basket(monkeypatch) -> None:
    """概念/行业主题（source_code == flow_source_code）仍可用 BK 日线回填——天然同源。"""
    monkeypatch.setattr(
        "app.services.theme_board_streak.get_spot_snapshot_any_age",
        lambda _key: {"boards": {}},
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_board_flow_series_cache_only",
        lambda _code: [
            {"date": "2026-08-12", "change_percent": 0.5},
            {"date": "2026-08-13", "change_percent": 0.7},
            {"date": "2026-08-14", "change_percent": 0.2},
        ],
    )
    items = [
        {
            "sector_label": "CPO",
            "change_1d_percent": 0.3,
            "source_code": "BK1128",
            "flow_source_code": "BK1128",
        }
    ]
    attach_consecutive_up_days(items, trade_date="2026-08-15", persist=False)
    assert items[0]["consecutive_up_days"] == 4


def test_attach_backfills_gap_instead_of_joining_across_missing_days(monkeypatch) -> None:
    """历史已满 8 天不再冷启动回填时，缺交易日仍要用同源日线补洞。"""
    monkeypatch.setattr(
        "app.services.theme_board_streak.get_spot_snapshot_any_age",
        lambda _key: {
            "boards": {
                "CPO": [
                    {"date": "2026-07-30", "change": -1.0},
                    {"date": "2026-07-31", "change": 1.0},
                    {"date": "2026-08-03", "change": 1.0},
                    {"date": "2026-08-04", "change": 1.0},
                    {"date": "2026-08-05", "change": 1.0},
                    {"date": "2026-08-06", "change": 1.0},
                    {"date": "2026-08-07", "change": 1.0},
                    {"date": "2026-08-14", "change": 0.3},
                ]
            }
        },
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_board_flow_series_cache_only",
        lambda _code: [
            {"date": "2026-08-07", "change_percent": 1.0},
            {"date": "2026-08-10", "change_percent": -0.8},
            {"date": "2026-08-11", "change_percent": 0.4},
            {"date": "2026-08-12", "change_percent": 0.2},
            {"date": "2026-08-13", "change_percent": 0.1},
            {"date": "2026-08-14", "change_percent": 0.3},
        ],
    )
    items = [
        {
            "sector_label": "CPO",
            "change_1d_percent": 0.3,
            "source_code": "BK1128",
            "flow_source_code": "BK1128",
        }
    ]
    attach_consecutive_up_days(items, trade_date="2026-08-14", persist=False)
    assert items[0]["consecutive_up_days"] == 4
