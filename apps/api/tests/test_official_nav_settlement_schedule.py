from __future__ import annotations

import threading
import time

from app.request_context import get_request_user_id
from app.services import ocr_pipeline
from app.services import official_nav_settlement as settlement


def test_schedule_official_nav_settlement_runs_with_user_context_and_caches(
    monkeypatch,
) -> None:
    done = threading.Event()
    seen: dict = {}

    def fake_settle() -> dict:
        seen["user_id"] = get_request_user_id()
        return {
            "ok": True,
            "skipped": False,
            "holdings": [{"fund_code": "010236"}],
        }

    def fake_save(payload: dict, **_kwargs) -> bool:
        seen["payload"] = payload
        done.set()
        return True

    monkeypatch.setattr(settlement, "settle_official_nav_for_portfolio", fake_settle)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_cache.save_cached_holdings_response",
        fake_save,
    )

    settlement.schedule_official_nav_settlement(user_id=2)

    assert done.wait(timeout=2.0)
    assert seen["user_id"] == 2
    assert seen["payload"]["holdings"][0]["fund_code"] == "010236"


def test_schedule_official_nav_settlement_skips_duplicate_in_flight(monkeypatch) -> None:
    from app.services.portfolio_refresh_gate import nav_work_in_flight

    release = threading.Event()
    calls = {"n": 0}

    def fake_settle() -> dict:
        calls["n"] += 1
        release.wait(timeout=2.0)
        return {"ok": True, "skipped": True, "holdings": []}

    monkeypatch.setattr(settlement, "settle_official_nav_for_portfolio", fake_settle)

    settlement.schedule_official_nav_settlement(user_id=7)
    settlement.schedule_official_nav_settlement(user_id=7)
    release.set()

    for _ in range(40):
        if not nav_work_in_flight(7):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("background official NAV settlement did not finish")

    assert calls["n"] == 1


def test_schedule_official_nav_settlement_noop_without_user(monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(settlement, "try_get_request_user_id", lambda: None)
    monkeypatch.setattr(
        settlement,
        "settle_official_nav_for_portfolio",
        lambda: called.__setitem__("n", called["n"] + 1),
    )

    settlement.schedule_official_nav_settlement()

    assert called["n"] == 0


def test_apply_confirmed_holdings_schedules_background_nav_settlement(monkeypatch) -> None:
    scheduled = {"nav": 0, "sector": 0}
    monkeypatch.setattr(
        ocr_pipeline,
        "_apply_confirmed_holdings_unlocked",
        lambda holdings: {"holdings": holdings},
    )
    monkeypatch.setattr(
        "app.services.official_nav_settlement.schedule_official_nav_settlement",
        lambda **_kwargs: scheduled.__setitem__("nav", scheduled["nav"] + 1),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.schedule_missing_sector_infer",
        lambda **_kwargs: scheduled.__setitem__("sector", scheduled["sector"] + 1),
    )

    result = ocr_pipeline.apply_confirmed_holdings([{"fund_code": "000001"}])

    assert result["holdings"] == [{"fund_code": "000001"}]
    assert scheduled["nav"] == 1
    assert scheduled["sector"] == 1
