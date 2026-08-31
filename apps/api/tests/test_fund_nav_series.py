from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.database import (
    get_fund_nav_series_meta,
    list_fund_nav_series_by_codes,
    list_fund_risk_metrics_by_codes,
    replace_fund_daily_catalogue,
    upsert_fund_nav_series,
)
from app.services.fund_nav_series import (
    NAV_SERIES_SOURCE_DAILY,
    NAV_SERIES_SOURCE_HISTORY,
    backfill_fund_nav_series,
    daily_nav_series_already_ran_today,
    expand_daily_snapshot_to_points,
    purge_expired_fund_nav_series,
    retention_cutoff_date,
    run_daily_nav_series_and_risk,
    schedule_daily_nav_series_sync,
    schedule_nav_series_backfill,
    sync_daily_fund_nav_series,
)
from app.mysql_bootstrap import ensure_mysql_schema
from app.services.fund_risk_metrics import refresh_fund_risk_metrics_from_nav_series
from app.services.fund_sharpe import SHARPE_SCHEMA_VERSION


def _history_points(start: date, count: int, *, start_nav: float = 1.0) -> list[dict]:
    points = []
    nav = start_nav
    for offset in range(count):
        # 用简单日递推，避免测试依赖交易日历。
        day = date.fromordinal(start.toordinal() + offset)
        growth = 0.2 if offset % 7 else -0.4
        nav = round(nav * (1 + growth / 100), 4)
        points.append(
            {
                "date": day.isoformat(),
                "nav": nav,
                "daily_growth": growth,
            }
        )
    return points


def test_mysql_bootstrap_declares_nav_series_and_3y_drawdown() -> None:
    statements: list[str] = []

    class Cursor:
        def execute(self, statement, params=()):  # noqa: ANN001, ANN202
            statements.append(str(statement))

    class Connection:
        def cursor(self):  # noqa: ANN202
            return Cursor()

        def commit(self) -> None:
            return None

    ensure_mysql_schema(Connection())
    joined = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS fund_nav_series" in joined
    assert "idx_fund_nav_series_date" in joined
    assert "max_drawdown_3y_percent" in joined


def test_retention_cutoff_is_three_calendar_years() -> None:
    assert retention_cutoff_date(date(2026, 8, 31)) == date(2023, 8, 31)


def test_expand_daily_snapshot_writes_latest_and_prior_days() -> None:
    points = expand_daily_snapshot_to_points(
        {
            "latest_date": "2026-08-28",
            "prior_date": "2026-08-27",
            "rows": [
                {
                    "fund_code": "000001",
                    "latest_nav": 1.318,
                    "prior_nav": 1.34,
                    "daily_growth_percent": -1.64,
                }
            ],
        },
        available_at="2026-08-28T16:00:00+00:00",
    )

    assert [(item["nav_date"], item["unit_nav"], item["daily_growth_percent"]) for item in points] == [
        ("2026-08-28", 1.318, -1.64),
        ("2026-08-27", 1.34, None),
    ]


def test_upsert_and_purge_keeps_only_three_years() -> None:
    written = upsert_fund_nav_series(
        [
            {
                "fund_code": "000001",
                "nav_date": "2022-08-30",
                "unit_nav": 1.01,
            },
            {
                "fund_code": "000001",
                "nav_date": "2023-08-31",
                "unit_nav": 1.05,
            },
            {
                "fund_code": "000001",
                "nav_date": "2026-08-31",
                "unit_nav": 1.2,
                "daily_growth_percent": 0.5,
            },
        ],
        snapshot_available_at="2026-08-31T16:00:00+00:00",
        source=NAV_SERIES_SOURCE_HISTORY,
    )
    purged = purge_expired_fund_nav_series(today=date(2026, 8, 31))

    assert written == 3
    assert purged == 1
    stored = list_fund_nav_series_by_codes(["000001"])["000001"]
    assert [row["nav_date"] for row in stored] == ["2023-08-31", "2026-08-31"]
    meta = get_fund_nav_series_meta()
    assert meta is not None
    assert meta["fund_count"] == 1
    assert meta["row_count"] == 2


def test_daily_sync_uses_market_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_daily_nav_snapshot",
        lambda: {
            "latest_date": "2026-08-28",
            "prior_date": "2026-08-27",
            "rows": [
                {
                    "fund_code": "110022",
                    "latest_nav": 2.5,
                    "prior_nav": 2.4,
                    "daily_growth_percent": 4.17,
                }
            ],
        },
    )

    summary = sync_daily_fund_nav_series()
    stored = list_fund_nav_series_by_codes(["110022"])["110022"]

    assert summary["written"] == 2
    assert summary["latest_date"] == "2026-08-28"
    assert stored[-1]["unit_nav"] == 2.5
    assert stored[-1]["source"] == NAV_SERIES_SOURCE_DAILY


def test_backfill_skips_filled_codes_and_writes_history(monkeypatch) -> None:
    replace_fund_daily_catalogue(
        [
            {"fund_code": "000011", "fund_name": "测试A", "fund_type": "gp"},
            {"fund_code": "000012", "fund_name": "测试B", "fund_type": "gp"},
        ],
        snapshot_available_at="2026-08-31T16:00:00+00:00",
        source="test",
    )
    fetched: list[str] = []

    def _fetch(code: str, trading_days: int = 800):
        fetched.append(code)
        start = date(2025, 1, 1)
        return {"data": _history_points(start, 5)}

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_nav_history",
        _fetch,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_series._BACKFILL_SLEEP_SECONDS",
        0,
    )

    first = backfill_fund_nav_series(limit=1)
    second = backfill_fund_nav_series()

    assert first["fetched"] == 1
    assert fetched == ["000011", "000012"]
    assert second["fetched"] == 1
    assert second["remaining"] == 0
    assert len(list_fund_nav_series_by_codes(["000011"])["000011"]) == 5


def test_refresh_risk_from_nav_series_computes_1y_and_3y() -> None:
    start = date(2023, 8, 31)
    points = _history_points(start, 520)
    upsert_fund_nav_series(
        [
            {
                "fund_code": "000021",
                "nav_date": item["date"],
                "unit_nav": item["nav"],
                "daily_growth_percent": item["daily_growth"],
            }
            for item in points
        ],
        snapshot_available_at="2026-08-31T16:00:00+00:00",
        source=NAV_SERIES_SOURCE_HISTORY,
    )

    written = refresh_fund_risk_metrics_from_nav_series(fund_codes=["000021"])
    row = list_fund_risk_metrics_by_codes(["000021"])["000021"]

    assert written == 1
    assert row["schema_version"] == SHARPE_SCHEMA_VERSION
    assert row["source"] == "computed_nav"
    assert row["max_drawdown_1y_percent"] is not None
    assert row["max_drawdown_3y_percent"] is not None
    assert row["sharpe_1y"] is not None
    assert row["sharpe_3y"] is not None
    assert -100.0 <= row["max_drawdown_1y_percent"] <= 0.0
    assert -100.0 <= row["max_drawdown_3y_percent"] <= 0.0


def test_daily_maintenance_recomputes_risk(monkeypatch) -> None:
    start = date(2025, 8, 1)
    history = _history_points(start, 200)

    def _snapshot():
        latest = history[-1]
        prior = history[-2]
        return {
            "latest_date": latest["date"],
            "prior_date": prior["date"],
            "rows": [
                {
                    "fund_code": "000031",
                    "latest_nav": latest["nav"],
                    "prior_nav": prior["nav"],
                    "daily_growth_percent": latest["daily_growth"],
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_daily_nav_snapshot",
        _snapshot,
    )
    upsert_fund_nav_series(
        [
            {
                "fund_code": "000031",
                "nav_date": item["date"],
                "unit_nav": item["nav"],
                "daily_growth_percent": item["daily_growth"],
            }
            for item in history[:-1]
        ],
        snapshot_available_at="2026-08-30T16:00:00+00:00",
        source=NAV_SERIES_SOURCE_HISTORY,
    )

    summary = run_daily_nav_series_and_risk()
    row = list_fund_risk_metrics_by_codes(["000031"])["000031"]

    assert summary["risk_written"] == 1
    assert row["max_drawdown_1y_percent"] is not None
    assert row["sharpe_1y"] is not None


def test_daily_already_ran_today_uses_shanghai_date() -> None:
    from app.services.fund_nav_series import _save_status

    _save_status({"daily_updated_at": "2026-08-31T16:05:00+08:00"})
    assert daily_nav_series_already_ran_today(today=date(2026, 8, 31)) is True
    assert daily_nav_series_already_ran_today(today=date(2026, 9, 1)) is False


def test_schedule_daily_skips_when_already_ran_unless_forced(monkeypatch) -> None:
    from app.services import fund_nav_series as nav_mod

    nav_mod._reset_nav_series_memory_for_tests()
    started: list[str] = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            started.append(str(kwargs.get("name") or ""))

        def start(self) -> None:
            return None

    monkeypatch.setattr(nav_mod, "daily_nav_series_already_ran_today", lambda: True)
    monkeypatch.setattr(nav_mod, "Thread", _FakeThread)
    schedule_daily_nav_series_sync()
    assert started == []
    schedule_daily_nav_series_sync(force=True)
    assert started == ["fund-nav-series-daily"]
    nav_mod._reset_nav_series_memory_for_tests()


def test_schedule_backfill_skips_when_disabled(monkeypatch) -> None:
    from app.services import fund_nav_series as nav_mod

    nav_mod._reset_nav_series_memory_for_tests()
    started: list[str] = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            started.append(str(kwargs.get("name") or ""))

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        nav_mod,
        "get_settings",
        lambda: SimpleNamespace(resolved_fund_nav_series_backfill_enabled=False),
    )
    monkeypatch.setattr(nav_mod, "Thread", _FakeThread)
    schedule_nav_series_backfill()
    assert started == []
    nav_mod._reset_nav_series_memory_for_tests()


def test_daily_workflow_calls_sync_script() -> None:
    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github"
        / "workflows"
        / "fund-nav-series-daily.yml"
    )
    text = workflow.read_text(encoding="utf-8")
    assert "scripts/sync_fund_nav_series.py --daily" in text
    assert "10 22 * * 1-5" in text
