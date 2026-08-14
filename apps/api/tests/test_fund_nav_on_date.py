from __future__ import annotations

import pandas as pd
import pytest

from app.services import fund_nav_service as nav


@pytest.fixture(autouse=True)
def _isolate_nav_cache(monkeypatch: pytest.MonkeyPatch):
    nav._NAV_CACHE.clear()
    nav._UNIT_NAV_CACHE.clear()
    store: dict[str, dict] = {}
    monkeypatch.setattr(nav, "save_spot_snapshot", lambda key, payload: store.__setitem__(key, dict(payload)))
    monkeypatch.setattr(
        nav,
        "get_spot_snapshot",
        lambda key, ttl_seconds=0: dict(store[key]) if key in store else None,
    )
    yield
    nav._NAV_CACHE.clear()
    nav._UNIT_NAV_CACHE.clear()


def test_unit_nav_on_date_uses_official_daily_table_without_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nav,
        "_fetch_nav_df",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch history")),
    )
    nav._cache_nav_return("011036", "2026-08-14", 2.44, nav.TTL_HIT)
    nav._cache_unit_nav("011036", 1.2345, as_of="2026-08-14")

    assert nav.get_unit_nav_on_date("011036", "2026-08-14") == 1.2345


def test_unit_nav_on_date_uses_legacy_latest_nav_when_official_return_is_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nav,
        "_fetch_nav_df",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch history")),
    )
    nav._cache_nav_return("017787", "2026-08-14", 2.31, nav.TTL_HIT)
    nav._cache_unit_nav("017787", 2.0)

    assert nav.get_unit_nav_on_date("017787", "2026-08-14") == 2.0


def test_unit_nav_on_date_does_not_reuse_other_day_latest_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nav,
        "_fetch_nav_df",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "净值日期": ["2026-08-13"],
                "单位净值": [1.11],
                "日增长率": [0.1],
            }
        ),
    )
    nav._cache_nav_return("011036", "2026-08-14", 2.44, nav.TTL_HIT)
    nav._cache_unit_nav("011036", 1.11, as_of="2026-08-13")

    assert nav.get_unit_nav_on_date("011036", "2026-08-14") is None


def test_unit_nav_on_date_still_reads_history_when_official_return_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nav,
        "_fetch_nav_df",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "净值日期": ["2026-08-13", "2026-08-14"],
                "单位净值": [1.0, 1.5],
                "日增长率": [0.0, 1.0],
            }
        ),
    )

    assert nav.get_unit_nav_on_date("002610", "2026-08-14") == 1.5
