# Factor IC Refresh Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a quality-gated factor IC snapshot in GitHub Actions every week, publish it through a protected API to shared MySQL storage, and expose its freshness beside the portfolio factor panel.

**Architecture:** The existing offline runner remains the only computation entry point and writes a versioned summary in the GitHub runner's temporary directory. A shared contract/storage service validates and appends snapshots to `factor_ic_snapshots`; the API authenticates publication with a dedicated token, while normal factor reads prefer the latest database snapshot and fall back to the existing local file. The production API never runs the IC computation or a refresh daemon.

**Tech Stack:** Python 3.12, FastAPI 0.128, Pydantic 2.12, SQLite/MySQL through the existing `DbConnection`, pytest 9, GitHub Actions, Next.js 16, React 19, TypeScript 5.9, Vitest 3.

## Global Constraints

- Production generation parameters are exactly: `universe_mode=sampled`, `sample_pool_size=500`, `universe_size=300`, `nav_days=750`, `rebalance_step=21`, `forward_days=20`, `factor_lookback=250`, `max_workers=8`.
- Publication requires `available=true`, at least 240 effective funds, at least 12 rebalance periods, and all four factor rows with at least 12 valid periods and finite IC statistics.
- A statistically non-significant result is valid and must remain publishable.
- `FUND_AI_FACTOR_IC_PUBLISH_TOKEN` must never be logged, serialized into a response, written into Actions Summary, or passed as a command-line argument.
- When MySQL is configured, a fallback SQLite connection must never accept a production publication.
- Snapshot staleness is 30 days; stale snapshots remain readable until replaced by a newer valid snapshot.
- Do not add a refresh daemon, distributed lock, task queue, or `lifespan.py` thread.
- Unit and integration tests are offline; no test may call live AkShare, GitHub, CloudBase, or the production API.
- Preserve and do not stage the user's unrelated dirty files. Every commit in this plan stages only the files listed for that task.

---

## File Structure

### New files

- `.github/workflows/factor-ic-refresh.yml` — scheduled/manual generation and publication job.
- `apps/api/app/services/factor_ic_snapshot.py` — versioned contract, quality validation, persistence, local fallback, and status payload.
- `apps/api/scripts/publish_factor_ic.py` — thin HTTP publisher with bounded retries and Actions Summary output.
- `apps/api/tests/test_factor_ic_backtest.py` — restored IC engine/runner regression coverage.
- `apps/api/tests/test_factor_ic_snapshot.py` — contract, quality, storage, fallback, and status tests.
- `apps/api/tests/test_factor_ic_publish_endpoint.py` — publication authentication and endpoint behavior.
- `apps/api/tests/test_publish_factor_ic.py` — publisher retry and secret-handling behavior.
- `apps/api/tests/test_factor_ic_status_endpoint.py` — authenticated diagnostics endpoint.
- `apps/api/tests/test_factor_ic_workflow_contract.py` — static workflow contract checks.
- `apps/web/src/components/FactorIcStatusBadge.tsx` — self-contained freshness badge.
- `apps/web/src/components/FactorIcStatusBadge.test.tsx` — five UI states.

### Modified files

- `apps/api/scripts/run_factor_ic.py` — schema version and UTC generation timestamp.
- `apps/api/app/db_migrations.py` — SQLite schema v9 migration.
- `apps/api/app/mysql_bootstrap.py` — MySQL snapshot table.
- `apps/api/app/config.py` — publish token and stale threshold.
- `apps/api/app/services/factor_confidence.py` — database-first reader and 5-minute mapping cache.
- `apps/api/app/auth/middleware.py` — exact internal publication path bypasses user JWT only.
- `apps/api/app/main.py` — protected publication endpoint and authenticated status endpoint.
- `apps/web/src/lib/api.ts` — status contract and fetcher.
- `apps/web/src/components/PortfolioDashboard.tsx` — badge placement.
- `.env.example`, `apps/api/scripts/README.md`, `docs/deploy/cloudbase.md`, `docs/PROJECT_CONTEXT.md`, `docs/TODO_factor_ic_refresh.md` — operations and completion documentation.

---

### Task 1: Restore IC engine coverage and version the runner output

**Files:**
- Create: `apps/api/tests/test_factor_ic_backtest.py`
- Create: `apps/api/app/services/factor_ic_snapshot.py`
- Modify: `apps/api/scripts/run_factor_ic.py`

**Interfaces:**
- Produces: `FACTOR_IC_SCHEMA_VERSION: int = 1`.
- Produces: `FactorIcPublishRequest.model_validate(payload)` for later storage/API tasks.
- Produces: runner fields `schema_version: 1` and timezone-aware `generated_at: str`.

- [ ] **Step 1: Restore the core engine regression tests and add a failing runner metadata assertion**

Create `apps/api/tests/test_factor_ic_backtest.py` with the retained high-value tests from commit `6146bf6^`, including the runner assertion below:

```python
from __future__ import annotations

import json
import random
from datetime import datetime

from app.services.factor_ic_backtest import NavPoint, _spearman, compute_factor_ic


def test_spearman_perfect_directions_and_zero_variance() -> None:
    assert _spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert _spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert _spearman([1, 1, 1], [1, 2, 3]) is None


def test_planted_momentum_signal_detected() -> None:
    rng = random.Random(42)
    calendar = [f"D{i:04d}" for i in range(600)]
    panel: dict[str, list[NavPoint]] = {}
    for index in range(20):
        slope = 0.0005 * (index + 1)
        nav = 1.0
        points: list[NavPoint] = []
        for day in calendar:
            nav *= (1.0 + slope) * (1.0 + rng.uniform(-0.003, 0.003))
            points.append(NavPoint(day, nav))
        panel[f"{index:06d}"] = points
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    momentum = next(row for row in result.factors if row.factor == "momentum")
    assert momentum.mean_ic is not None and momentum.mean_ic > 0.7
    assert momentum.significant is True


def test_future_mutation_does_not_change_earlier_ic_periods() -> None:
    calendar = [f"D{i:04d}" for i in range(400)]
    base: dict[str, list[tuple[str, float]]] = {}
    for index in range(15):
        slope = 0.0004 * (index + 1)
        base[f"{index:06d}"] = [
            (day, (1.0 + slope) ** offset) for offset, day in enumerate(calendar)
        ]
    panel_a = {
        code: [NavPoint(day, value) for day, value in series]
        for code, series in base.items()
    }
    panel_b: dict[str, list[NavPoint]] = {}
    for code, series in base.items():
        points = [NavPoint(day, value) for day, value in series]
        for offset in range(len(points) - 30, len(points)):
            points[offset] = NavPoint(points[offset].date, points[offset].nav * 1.5)
        panel_b[code] = points
    result_a = compute_factor_ic(nav_panel=panel_a, calendar=calendar)
    result_b = compute_factor_ic(nav_panel=panel_b, calendar=calendar)
    momentum_a = next(row for row in result_a.factors if row.factor == "momentum")
    momentum_b = next(row for row in result_b.factors if row.factor == "momentum")
    stable_count = min(len(momentum_a.ic_series), len(momentum_b.ic_series)) - 2
    assert stable_count > 0
    assert momentum_a.ic_series[:stable_count] == momentum_b.ic_series[:stable_count]


def test_runner_writes_versioned_utc_summary(tmp_path) -> None:
    from scripts.run_factor_ic import build_ic_report

    calendar = [f"D{i:04d}" for i in range(400)]

    def fetch_rank(_limit: int) -> list[dict]:
        return [
            {"fund_code": f"{index:06d}", "fund_name": f"基金{index}"}
            for index in range(15)
        ]

    def fetch_nav(code: str, _name: str, _days: int) -> list[NavPoint]:
        index = int(code)
        return [
            NavPoint(day, (1.0 + 0.0003 * (index + 1)) ** offset)
            for offset, day in enumerate(calendar)
        ]

    build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_size=15,
        nav_days=400,
    )
    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(payload["generated_at"])
    assert payload["schema_version"] == 1
    assert generated_at.tzinfo is not None
    assert payload["run_date"] == generated_at.date().isoformat()
```

- [ ] **Step 2: Run the focused test and confirm the metadata assertion fails**

Run:

```bash
cd apps/api
python -m pytest tests/test_factor_ic_backtest.py::test_runner_writes_versioned_utc_summary -q
```

Expected: FAIL with missing `generated_at` or `schema_version`.

- [ ] **Step 3: Add the versioned Pydantic contract and quality validator**

Create `apps/api/app/services/factor_ic_snapshot.py` with these public types and constants:

```python
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FACTOR_IC_SCHEMA_VERSION = 1
FACTOR_NAMES = frozenset({"momentum", "risk_adjusted", "drawdown", "composite"})
EXPECTED_PARAMS = {
    "universe_size": 300,
    "universe_mode": "sampled",
    "sample_pool_size": 500,
    "nav_days": 750,
    "rebalance_step": 21,
    "forward_days": 20,
    "factor_lookback": 250,
}
MIN_EFFECTIVE_UNIVERSE = 240
MIN_VALID_PERIODS = 12


class FactorIcParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_size: int
    universe_mode: Literal["top", "sampled"]
    sample_pool_size: int
    nav_days: int
    rebalance_step: int
    forward_days: int
    factor_lookback: int


class FactorIcFactorStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    factor: Literal["momentum", "risk_adjusted", "drawdown", "composite"]
    n_periods: int
    mean_ic: float | None
    ic_std: float | None = None
    icir: float | None = None
    t_stat: float | None = None
    positive_ratio: float | None = None
    significant: bool

    @model_validator(mode="after")
    def validate_statistics(self) -> "FactorIcFactorStats":
        if self.n_periods < MIN_VALID_PERIODS:
            raise ValueError(f"{self.factor} 有效期数不足 {MIN_VALID_PERIODS}")
        if self.mean_ic is None or not math.isfinite(self.mean_ic) or not -1 <= self.mean_ic <= 1:
            raise ValueError(f"{self.factor} mean_ic 非法")
        for name in ("ic_std", "icir", "t_stat", "positive_ratio"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{self.factor} {name} 必须是有限数字")
        if self.positive_ratio is not None and not 0 <= self.positive_ratio <= 1:
            raise ValueError(f"{self.factor} positive_ratio 非法")
        return self


class FactorIcSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int
    run_date: date
    generated_at: datetime
    params: FactorIcParams
    available: bool
    universe_size: int
    rebalance_count: int
    forward_days: int
    factors: list[FactorIcFactorStats]

    @model_validator(mode="after")
    def validate_quality(self) -> "FactorIcSummary":
        if self.schema_version != FACTOR_IC_SCHEMA_VERSION:
            raise ValueError("不支持的 factor IC schema_version")
        if self.params.model_dump() != EXPECTED_PARAMS:
            raise ValueError("回测参数不是固定生产口径")
        if not self.available:
            raise ValueError("回测结果不可用")
        if self.universe_size < MIN_EFFECTIVE_UNIVERSE:
            raise ValueError(f"有效基金数不足 {MIN_EFFECTIVE_UNIVERSE}")
        if self.rebalance_count < MIN_VALID_PERIODS:
            raise ValueError(f"回测期数不足 {MIN_VALID_PERIODS}")
        names = [row.factor for row in self.factors]
        if len(names) != len(FACTOR_NAMES) or set(names) != FACTOR_NAMES:
            raise ValueError("四个因子必须齐全且不可重复")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at 必须包含时区")
        if self.run_date != self.generated_at.astimezone(timezone.utc).date():
            raise ValueError("run_date 必须等于 generated_at 的 UTC 日期")
        return self


class FactorIcPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: FactorIcSummary
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    source_run_id: str = Field(min_length=1, max_length=64)


def validate_publish_request(
    payload: dict[str, Any], *, now: datetime | None = None
) -> FactorIcPublishRequest:
    request = FactorIcPublishRequest.model_validate(payload)
    current = now or datetime.now(timezone.utc)
    generated = request.summary.generated_at.astimezone(timezone.utc)
    if generated > current + timedelta(minutes=5):
        raise ValueError("generated_at 不能来自未来")
    if generated < current - timedelta(hours=24):
        raise ValueError("generated_at 已超过 24 小时")
    return request
```

- [ ] **Step 4: Add `schema_version` and a single UTC timestamp to the runner**

Modify `apps/api/scripts/run_factor_ic.py` so one timestamp drives both fields:

```python
from app.services.factor_ic_snapshot import FACTOR_IC_SCHEMA_VERSION

generated_at = datetime.now(timezone.utc)
run_date = generated_at.date().isoformat()

summary = {
    "schema_version": FACTOR_IC_SCHEMA_VERSION,
    "run_date": run_date,
    "generated_at": generated_at.isoformat(),
    "params": {
        "universe_size": universe_size,
        "universe_mode": universe_mode,
        "sample_pool_size": sample_pool_size,
        "nav_days": nav_days,
        "rebalance_step": rebalance_step,
        "forward_days": forward_days,
        "factor_lookback": factor_lookback,
    },
    "available": result.available,
    "message": result.message,
    "universe_size": result.universe_size,
    "rebalance_count": result.rebalance_count,
    "forward_days": result.forward_days,
    "caveats": _CAVEATS,
    "factors": [
        {key: value for key, value in asdict(stats).items() if key != "ic_series"}
        for stats in result.factors
    ],
}
```

- [ ] **Step 5: Run the restored suite**

Run:

```bash
cd apps/api
python -m pytest tests/test_factor_ic_backtest.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add apps/api/app/services/factor_ic_snapshot.py apps/api/scripts/run_factor_ic.py apps/api/tests/test_factor_ic_backtest.py
git commit -m "feat: version factor IC snapshot output"
```

---

### Task 2: Add append-only SQLite/MySQL snapshot persistence

**Files:**
- Modify: `apps/api/app/services/factor_ic_snapshot.py`
- Modify: `apps/api/app/db_migrations.py`
- Modify: `apps/api/app/mysql_bootstrap.py`
- Modify: `apps/api/app/config.py`
- Create: `apps/api/tests/test_factor_ic_snapshot.py`
- Modify: `apps/api/tests/test_db_migrations.py`

**Interfaces:**
- Produces: `publish_factor_ic_snapshot(request, connection_factory=None, now=None) -> dict`.
- Produces: `read_latest_database_snapshot(connection_factory=None) -> dict | None`.
- Produces: `FactorIcNewerSnapshotExists` and `FactorIcStorageUnavailable`.

- [ ] **Step 1: Write failing migration and persistence tests**

Create `apps/api/tests/test_factor_ic_snapshot.py` with a reusable valid request and these assertions:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.factor_ic_snapshot import (
    FactorIcNewerSnapshotExists,
    FactorIcStorageUnavailable,
    publish_factor_ic_snapshot,
    read_latest_database_snapshot,
    validate_publish_request,
)


def valid_payload(generated_at: str | None = None) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    factors = [
        {
            "factor": name,
            "n_periods": 34,
            "mean_ic": 0.01,
            "ic_std": 0.2,
            "icir": 0.05,
            "t_stat": 0.3,
            "positive_ratio": 0.5,
            "significant": False,
        }
        for name in ("momentum", "risk_adjusted", "drawdown", "composite")
    ]
    return {
        "summary": {
            "schema_version": 1,
            "run_date": generated_at[:10],
            "generated_at": generated_at,
            "params": {
                "universe_size": 300,
                "universe_mode": "sampled",
                "sample_pool_size": 500,
                "nav_days": 750,
                "rebalance_step": 21,
                "forward_days": 20,
                "factor_lookback": 250,
            },
            "available": True,
            "universe_size": 300,
            "rebalance_count": 35,
            "forward_days": 20,
            "factors": factors,
        },
        "source_commit": "a" * 40,
        "source_run_id": "12345",
    }


def test_publish_is_append_only_idempotent_and_reads_latest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "factor-ic.db"))
    from app.config import refresh_settings

    refresh_settings()
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    first = validate_publish_request(valid_payload("2026-07-10T08:00:00+00:00"), now=now)
    created = publish_factor_ic_snapshot(first, now=now)
    duplicate = publish_factor_ic_snapshot(first, now=now)
    latest = read_latest_database_snapshot()
    assert created["created"] is True
    assert duplicate["created"] is False
    assert latest is not None
    assert latest["summary"]["universe_size"] == 300


def test_older_snapshot_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "factor-ic-old.db"))
    from app.config import refresh_settings

    refresh_settings()
    now = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    newer = validate_publish_request(valid_payload("2026-07-10T09:00:00+00:00"), now=now)
    older = validate_publish_request(valid_payload("2026-07-10T08:00:00+00:00"), now=now)
    publish_factor_ic_snapshot(newer, now=now)
    with pytest.raises(FactorIcNewerSnapshotExists):
        publish_factor_ic_snapshot(older, now=now)


def test_non_significant_result_is_valid_but_small_universe_is_rejected() -> None:
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    assert all(not row["significant"] for row in payload["summary"]["factors"])
    validate_publish_request(payload, now=now)
    payload["summary"]["universe_size"] = 239
    with pytest.raises(ValueError, match="有效基金数不足"):
        validate_publish_request(payload, now=now)


def test_mysql_configuration_rejects_fallback_sqlite(monkeypatch) -> None:
    from app.config import refresh_settings

    class FallbackConnection:
        dialect = "sqlite"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://user:password@db.example.test:3306/fundpilot",
    )
    refresh_settings()
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    request = validate_publish_request(
        valid_payload("2026-07-10T08:00:00+00:00"),
        now=now,
    )
    with pytest.raises(FactorIcStorageUnavailable):
        publish_factor_ic_snapshot(
            request,
            connection_factory=FallbackConnection,
            now=now,
        )
```

Add contract boundary cases by mutating `valid_payload()` for effective universe 239/240, periods 11/12, duplicate factors, NaN, and `significant=False`.

Add this current-version migration regression to `apps/api/tests/test_db_migrations.py`:

```python
def test_current_schema_still_ensures_factor_ic_snapshot_table() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )
    run_migrations(connection)
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='factor_ic_snapshots'"
    ).fetchone()
    assert table is not None
```

- [ ] **Step 2: Run the persistence tests and verify missing interfaces fail**

Run:

```bash
cd apps/api
python -m pytest tests/test_factor_ic_snapshot.py -q
```

Expected: collection FAIL because persistence interfaces do not yet exist.

- [ ] **Step 3: Add the SQLite v9 and MySQL schemas**

In `apps/api/app/db_migrations.py`, set `SCHEMA_VERSION = 9`, add the function below, and invoke it both before the `version >= SCHEMA_VERSION` early return and in the normal migration path:

```python
def _migrate_factor_ic_snapshots(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_ic_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            run_date TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source_commit TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_factor_ic_generated
        ON factor_ic_snapshots (generated_at DESC)
        """
    )
```

In `apps/api/app/mysql_bootstrap.py`, append this statement to `statements`:

```sql
CREATE TABLE IF NOT EXISTS factor_ic_snapshots (
    snapshot_id VARCHAR(64) PRIMARY KEY,
    schema_version INT NOT NULL,
    run_date VARCHAR(16) NOT NULL,
    generated_at VARCHAR(64) NOT NULL,
    published_at VARCHAR(64) NOT NULL,
    source_commit VARCHAR(64) NOT NULL,
    source_run_id VARCHAR(64) NOT NULL,
    payload LONGTEXT NOT NULL,
    INDEX idx_factor_ic_generated (generated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

- [ ] **Step 4: Add configuration and persistence implementation**

Add to `Settings` in `apps/api/app/config.py`:

```python
factor_ic_publish_token: str | None = None
factor_ic_stale_after_days: int = 30
```

Extend `factor_ic_snapshot.py` with the exact public behavior:

```python
import hashlib
import json
from collections.abc import Callable


class FactorIcNewerSnapshotExists(RuntimeError):
    pass


class FactorIcStorageUnavailable(RuntimeError):
    pass


def _canonical_summary(request: FactorIcPublishRequest) -> tuple[dict, str, str]:
    summary = request.summary.model_dump(mode="json")
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot_id = hashlib.sha256(
        f"{request.source_commit}\n{encoded}".encode("utf-8")
    ).hexdigest()
    return summary, encoded, snapshot_id


def read_latest_database_snapshot(
    connection_factory: Callable | None = None,
) -> dict | None:
    from app.database import _connect

    factory = connection_factory or _connect
    with factory() as connection:
        row = connection.execute(
            """
            SELECT snapshot_id, generated_at, published_at,
                   source_commit, source_run_id, payload
            FROM factor_ic_snapshots
            ORDER BY generated_at DESC, published_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    return {
        "snapshot_id": data["snapshot_id"],
        "generated_at": data["generated_at"],
        "published_at": data["published_at"],
        "source_commit": data["source_commit"],
        "source_run_id": data["source_run_id"],
        "summary": json.loads(data["payload"]),
    }


def publish_factor_ic_snapshot(
    request: FactorIcPublishRequest,
    *,
    connection_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict:
    from app.config import get_settings
    from app.database import _connect

    factory = connection_factory or _connect
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary, encoded, snapshot_id = _canonical_summary(request)
    try:
        with factory() as connection:
            if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
                raise FactorIcStorageUnavailable("MySQL 不可用，拒绝回落到本地 SQLite 发布")
            existing = connection.execute(
                """
                SELECT snapshot_id, generated_at
                FROM factor_ic_snapshots
                ORDER BY generated_at DESC, published_at DESC
                LIMIT 1
                """
            ).fetchone()
            if existing is not None:
                latest = dict(existing)
                if latest["snapshot_id"] == snapshot_id:
                    return {"created": False, "snapshot_id": snapshot_id}
                latest_generated = datetime.fromisoformat(str(latest["generated_at"]))
                if request.summary.generated_at <= latest_generated:
                    raise FactorIcNewerSnapshotExists("数据库已有更新的 factor IC 快照")
            connection.execute(
                """
                INSERT OR IGNORE INTO factor_ic_snapshots (
                    snapshot_id, schema_version, run_date, generated_at,
                    published_at, source_commit, source_run_id, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    request.summary.schema_version,
                    request.summary.run_date.isoformat(),
                    request.summary.generated_at.isoformat(),
                    current.isoformat(),
                    request.source_commit,
                    request.source_run_id,
                    encoded,
                ),
            )
            connection.commit()
    except (FactorIcNewerSnapshotExists, FactorIcStorageUnavailable):
        raise
    except Exception as exc:
        raise FactorIcStorageUnavailable("factor IC 快照数据库写入失败") from exc
    return {"created": True, "snapshot_id": snapshot_id}
```

- [ ] **Step 5: Run snapshot and migration tests**

Run:

```bash
cd apps/api
python -m pytest tests/test_factor_ic_snapshot.py tests/test_db_migrations.py tests/test_db_connect.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add apps/api/app/services/factor_ic_snapshot.py apps/api/app/db_migrations.py apps/api/app/mysql_bootstrap.py apps/api/app/config.py apps/api/tests/test_factor_ic_snapshot.py apps/api/tests/test_db_migrations.py
git commit -m "feat: persist factor IC snapshots"
```

---

### Task 3: Read shared snapshots and build freshness diagnostics

**Files:**
- Modify: `apps/api/app/services/factor_ic_snapshot.py`
- Modify: `apps/api/app/services/factor_confidence.py`
- Modify: `apps/api/tests/test_factor_ic_snapshot.py`
- Create: `apps/api/tests/test_factor_confidence.py`

**Interfaces:**
- Produces: `load_factor_ic_summary(local_path=None, connection_factory=None) -> tuple[dict | None, str, dict]`.
- Produces: `build_factor_ic_status(stale_after_days=None, now=None, local_path=None, connection_factory=None) -> dict`.
- Preserves: `factor_confidence.load_ic_summary() -> dict[str, dict]`.

- [ ] **Step 1: Write failing database-priority, fallback, cache, and 30-day boundary tests**

Add to `test_factor_ic_snapshot.py`:

```python
def test_status_uses_database_before_local_file(tmp_path, monkeypatch) -> None:
    from app.config import refresh_settings
    from app.services.factor_ic_snapshot import build_factor_ic_status

    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "status.db"))
    refresh_settings()
    now = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    request = validate_publish_request(valid_payload(), now=now)
    publish_factor_ic_snapshot(request, now=now)
    missing_local = tmp_path / "missing-summary.json"
    status = build_factor_ic_status(now=now, local_path=missing_local)
    assert status["source"] == "database"
    assert status["stale"] is False
    assert status["universe_size"] == 300


@pytest.mark.parametrize(
    ("age_days", "expected_stale"),
    [(29, False), (30, True), (31, True)],
)
def test_status_staleness_boundary(
    age_days: int, expected_stale: bool, tmp_path, monkeypatch
) -> None:
    from datetime import timedelta
    from app.config import refresh_settings
    from app.services.factor_ic_snapshot import build_factor_ic_status

    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / f"status-{age_days}.db"))
    refresh_settings()
    now = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    generated = now - timedelta(days=age_days)
    payload = valid_payload(generated.isoformat())
    payload["summary"]["run_date"] = generated.date().isoformat()
    request = validate_publish_request(payload, now=generated)
    publish_factor_ic_snapshot(request, now=generated)
    status = build_factor_ic_status(now=now, local_path=tmp_path / "none.json")
    assert status["age_days"] == age_days
    assert status["stale"] is expected_stale
```

Create `test_factor_confidence.py` by restoring the pure mapping tests from `6146bf6^`, then add a database-first `load_ic_summary()` assertion.

- [ ] **Step 2: Run focused tests and confirm missing reader/status functions fail**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py -q
```

Expected: FAIL because shared reader/status functions are missing and `factor_confidence` is file-only.

- [ ] **Step 3: Implement best-effort database-first raw loading and status payload**

Add to `factor_ic_snapshot.py`:

```python
import json
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY_PATH = API_ROOT / "var" / "factor_ic" / "summary.json"


def load_factor_ic_summary(
    *,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> tuple[dict | None, str, dict]:
    try:
        database_row = read_latest_database_snapshot(connection_factory)
    except Exception:
        database_row = None
    if database_row is not None:
        metadata = {
            "published_at": database_row["published_at"],
            "source_commit": database_row["source_commit"],
            "source_run_id": database_row["source_run_id"],
        }
        return database_row["summary"], "database", metadata
    path = local_path or DEFAULT_SUMMARY_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None, "unavailable", {}
    return raw, "local_file", {}


def build_factor_ic_status(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict:
    from app.config import get_settings

    threshold = (
        stale_after_days
        if stale_after_days is not None
        else get_settings().factor_ic_stale_after_days
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw, source, metadata = load_factor_ic_summary(
        local_path=local_path,
        connection_factory=connection_factory,
    )
    if not raw or not raw.get("run_date"):
        return {
            "available": False,
            "stale_after_days": threshold,
            "source": "unavailable",
        }
    generated_at = raw.get("generated_at") or f"{raw['run_date']}T00:00:00+00:00"
    generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    age_days = max(0, (current.date() - generated.astimezone(timezone.utc).date()).days)
    factors = raw.get("factors") or []
    return {
        "available": True,
        "run_date": raw["run_date"],
        "generated_at": generated.isoformat(),
        "published_at": metadata.get("published_at"),
        "age_days": age_days,
        "stale": age_days >= threshold,
        "stale_after_days": threshold,
        "source": source,
        "target_universe_size": (raw.get("params") or {}).get("universe_size"),
        "universe_size": raw.get("universe_size"),
        "universe_mode": (raw.get("params") or {}).get("universe_mode"),
        "rebalance_count": raw.get("rebalance_count"),
        "factor_periods": {
            str(row.get("factor")): row.get("n_periods")
            for row in factors
            if row.get("factor")
        },
        "source_commit": (metadata.get("source_commit") or "")[:7] or None,
    }
```

- [ ] **Step 4: Update `factor_confidence` without changing its public mapping API**

Replace the file reader in `factor_confidence.py` with:

```python
from app.services.factor_ic_snapshot import DEFAULT_SUMMARY_PATH, load_factor_ic_summary

SUMMARY_PATH = DEFAULT_SUMMARY_PATH
SUMMARY_TTL_SECONDS = 300


def clear_ic_summary_cache() -> None:
    _SUMMARY_CACHE.clear()


def load_ic_summary() -> dict[str, dict]:
    now = time.time()
    cached = _SUMMARY_CACHE.get("default")
    if cached and now - cached[0] < SUMMARY_TTL_SECONDS:
        return cached[1]
    raw, _source, _metadata = load_factor_ic_summary(local_path=Path(SUMMARY_PATH))
    result: dict[str, dict] = {}
    for stats in (raw or {}).get("factors") or []:
        key = stats.get("factor")
        if key:
            result[str(key)] = stats
    _SUMMARY_CACHE["default"] = (now, result)
    return result
```

- [ ] **Step 5: Run focused tests**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add apps/api/app/services/factor_ic_snapshot.py apps/api/app/services/factor_confidence.py apps/api/tests/test_factor_ic_snapshot.py apps/api/tests/test_factor_confidence.py
git commit -m "feat: read shared factor IC snapshots"
```

---

### Task 4: Add protected publication and authenticated diagnostics endpoints

**Files:**
- Modify: `apps/api/app/auth/middleware.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_factor_ic_publish_endpoint.py`
- Create: `apps/api/tests/test_factor_ic_status_endpoint.py`

**Interfaces:**
- Produces: `POST /api/internal/factor-ic-snapshots` with `X-Factor-IC-Publish-Token`.
- Produces: `GET /api/diagnostics/factor-ic-status` with normal JWT auth.

- [ ] **Step 1: Write failing authentication and endpoint tests**

Create endpoint tests with `TestClient(app)` and `valid_payload()` imported from `test_factor_ic_snapshot.py`:

```python
def test_publish_endpoint_rejects_missing_server_token(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from app.config import refresh_settings
    from app.main import app

    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "missing-token.db"))
    monkeypatch.delenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", raising=False)
    refresh_settings()
    response = TestClient(app).post(
        "/api/internal/factor-ic-snapshots",
        json=valid_payload(),
    )
    assert response.status_code == 503


def test_publish_endpoint_accepts_valid_token(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from app.config import refresh_settings
    from app.main import app

    token = "factor-ic-test-token-at-least-32-characters"
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "valid-token.db"))
    monkeypatch.setenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", token)
    refresh_settings()
    response = TestClient(app).post(
        "/api/internal/factor-ic-snapshots",
        headers={"X-Factor-IC-Publish-Token": token},
        json=valid_payload(),
    )
    assert response.status_code == 200
    assert response.json()["created"] is True
```

Also assert wrong token returns 401, invalid quality returns 422, newer existing snapshot returns 409, and the diagnostics endpoint requires JWT.

- [ ] **Step 2: Run endpoint tests and verify 401/404 failures**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_publish_endpoint.py tests/test_factor_ic_status_endpoint.py -q
```

Expected: FAIL because the internal path is still intercepted by JWT middleware and routes do not exist.

- [ ] **Step 3: Exempt only the exact internal route from user JWT**

Add to `_PUBLIC_EXACT` in `app/auth/middleware.py`:

```python
"/api/internal/factor-ic-snapshots",
```

Do not add `/api/internal` as a prefix.

- [ ] **Step 4: Add the constant-time token dependency and both routes**

In `main.py`, add `Depends` and `Header` imports and these functions:

```python
import secrets
from typing import Annotated

from fastapi import Depends, Header
from pydantic import ValidationError

from app.services.factor_ic_snapshot import (
    FactorIcNewerSnapshotExists,
    FactorIcStorageUnavailable,
    build_factor_ic_status,
    publish_factor_ic_snapshot,
    validate_publish_request,
)
from app.services.factor_confidence import clear_ic_summary_cache


def _require_factor_ic_publish_token(
    supplied: Annotated[str | None, Header(alias="X-Factor-IC-Publish-Token")] = None,
) -> None:
    expected = (get_settings().factor_ic_publish_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="因子 IC 发布未配置")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="因子 IC 发布凭证无效")


@app.post("/api/internal/factor-ic-snapshots", include_in_schema=False)
def publish_factor_ic(
    body: dict,
    _authorized: None = Depends(_require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_publish_request(body)
        result = publish_factor_ic_snapshot(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcNewerSnapshotExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    clear_ic_summary_cache()
    return result


@app.get("/api/diagnostics/factor-ic-status")
def factor_ic_status() -> dict:
    return build_factor_ic_status()
```

- [ ] **Step 5: Run endpoint and middleware regression tests**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_publish_endpoint.py tests/test_factor_ic_status_endpoint.py tests/test_cors.py tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add apps/api/app/auth/middleware.py apps/api/app/main.py apps/api/tests/test_factor_ic_publish_endpoint.py apps/api/tests/test_factor_ic_status_endpoint.py
git commit -m "feat: publish factor IC snapshots securely"
```

---

### Task 5: Add the retrying publisher CLI

**Files:**
- Create: `apps/api/scripts/publish_factor_ic.py`
- Create: `apps/api/tests/test_publish_factor_ic.py`
- Modify: `apps/api/scripts/README.md`

**Interfaces:**
- Produces: `publish_summary(summary_path, url, token, source_commit, source_run_id, client, sleep) -> str` returning `created`, `duplicate`, or `newer_exists`.
- Consumes: `FactorIcPublishRequest` and `validate_publish_request` from Task 1.

- [ ] **Step 1: Write failing retry/secret tests with an injected fake client**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from scripts.publish_factor_ic import publish_summary
from tests.test_factor_ic_snapshot import valid_payload


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(self.status_code, request=request, text=self.text)
        response.raise_for_status()


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_retries_5xx_and_never_places_token_in_body(tmp_path) -> None:
    raw = valid_payload("2026-07-10T08:00:00+00:00")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(raw["summary"]), encoding="utf-8")
    client = FakeClient([FakeResponse(503), FakeResponse(200, {"created": True})])
    sleeps: list[float] = []
    result = publish_summary(
        summary_path=summary_path,
        url="https://example.test/api/internal/factor-ic-snapshots",
        token="secret-token-value",
        source_commit="a" * 40,
        source_run_id="12345",
        client=client,
        sleep=sleeps.append,
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
    )
    assert result == "created"
    assert sleeps == [5]
    assert "secret-token-value" not in json.dumps(client.calls[0]["json"])
    assert client.calls[0]["headers"]["X-Factor-IC-Publish-Token"] == "secret-token-value"
```

Add tests for four total attempts with delays `[5, 15, 45]`, no retry on 401/422, 409 returning `newer_exists`, and Actions Summary excluding the token.

- [ ] **Step 2: Run and verify the script import fails**

```bash
cd apps/api
python -m pytest tests/test_publish_factor_ic.py -q
```

Expected: collection FAIL because `scripts/publish_factor_ic.py` does not exist.

- [ ] **Step 3: Implement the thin publisher with dependency injection**

The script imports its runtime dependencies and makes `app` importable when executed as a file:

```python
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.factor_ic_snapshot import validate_publish_request  # noqa: E402
```

Its retrying core is:

```python
RETRY_DELAYS = (5, 15, 45)


def publish_summary(
    *,
    summary_path: Path,
    url: str,
    token: str,
    source_commit: str,
    source_run_id: str,
    client,
    sleep=time.sleep,
    now: datetime | None = None,
) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    request = validate_publish_request(
        {
            "summary": summary,
            "source_commit": source_commit,
            "source_run_id": source_run_id,
        },
        now=now,
    )
    body = request.model_dump(mode="json")
    headers = {"X-Factor-IC-Publish-Token": token}
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = client.post(url, json=body, headers=headers, timeout=30.0)
        except httpx.RequestError:
            if attempt == len(RETRY_DELAYS):
                raise
            sleep(RETRY_DELAYS[attempt])
            continue
        if response.status_code == 409:
            return "newer_exists"
        if 500 <= response.status_code < 600:
            if attempt == len(RETRY_DELAYS):
                response.raise_for_status()
            sleep(RETRY_DELAYS[attempt])
            continue
        response.raise_for_status()
        return "created" if response.json().get("created") else "duplicate"
    raise RuntimeError("factor IC 发布重试状态异常")
```

`main()` reads and validates environment variables, publishes, and writes only non-secret metrics:

```python
def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def _write_actions_summary(summary: dict, result: str) -> None:
    target = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not target:
        return
    factor_lines = [
        f"- {row['factor']}: IC {row['mean_ic']:+.4f}, n={row['n_periods']}"
        for row in summary.get("factors") or []
    ]
    text = "\n".join(
        [
            "## Factor IC Refresh",
            "",
            f"- result: {result}",
            f"- generated_at: {summary['generated_at']}",
            f"- universe_size: {summary['universe_size']}",
            f"- rebalance_count: {summary['rebalance_count']}",
            *factor_lines,
            "",
        ]
    )
    with Path(target).open("a", encoding="utf-8") as stream:
        stream.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="发布已校验的 factor IC summary")
    parser.add_argument("summary_path", type=Path)
    args = parser.parse_args()
    url = _required_env("FACTOR_IC_PUBLISH_URL")
    token = _required_env("FACTOR_IC_PUBLISH_TOKEN")
    source_commit = _required_env("GITHUB_SHA")
    source_run_id = _required_env("GITHUB_RUN_ID")
    summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
    with httpx.Client() as client:
        result = publish_summary(
            summary_path=args.summary_path,
            url=url,
            token=token,
            source_commit=source_commit,
            source_run_id=source_run_id,
            client=client,
        )
    _write_actions_summary(summary, result)
    print(f"factor IC publish result: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Document local generation and explicit publication**

In `apps/api/scripts/README.md`, retain the normal local command and add a separate publication section using environment variables. State that production publication is normally run by GitHub Actions and that `run_factor_ic.py` alone never writes production data.

- [ ] **Step 5: Run publisher tests**

```bash
cd apps/api
python -m pytest tests/test_publish_factor_ic.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add apps/api/scripts/publish_factor_ic.py apps/api/scripts/README.md apps/api/tests/test_publish_factor_ic.py
git commit -m "feat: add factor IC snapshot publisher"
```

---

### Task 6: Add the weekly GitHub Actions workflow

**Files:**
- Create: `.github/workflows/factor-ic-refresh.yml`
- Create: `apps/api/tests/test_factor_ic_workflow_contract.py`

**Interfaces:**
- Consumes: GitHub Secret `FACTOR_IC_PUBLISH_TOKEN`.
- Produces: weekly and manual execution of the exact production parameters.

- [ ] **Step 1: Write a failing static workflow contract test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "factor-ic-refresh.yml"


def test_factor_ic_workflow_is_read_only_and_uses_fixed_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert 'cron: "23 3 * * 0"' in text
    assert 'timezone: "Asia/Shanghai"' in text
    assert "contents: read" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 45" in text
    assert "--universe-mode sampled" in text
    assert "--sample-pool-size 500" in text
    assert "--universe-size 300" in text
    assert "--nav-days 750" in text
    assert "--max-workers 8" in text
    assert "secrets.FACTOR_IC_PUBLISH_TOKEN" in text
    assert "git push" not in text
```

- [ ] **Step 2: Run and confirm the missing workflow fails**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_workflow_contract.py -q
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the workflow**

```yaml
name: Factor IC Refresh

on:
  schedule:
    - cron: "23 3 * * 0"
      timezone: "Asia/Shanghai"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: factor-ic-refresh
  cancel-in-progress: false

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    defaults:
      run:
        working-directory: apps/api
    env:
      PYTHONUTF8: "1"
      FACTOR_IC_PUBLISH_URL: "https://fundpilot-api-269544-5-1392809852.sh.run.tcloudbase.com/api/internal/factor-ic-snapshots"
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: apps/api/requirements.txt
      - name: Install backend dependencies
        run: |
          python -m pip install --upgrade pip setuptools wheel
          pip install --retries 10 --default-timeout=120 -r requirements.txt
      - name: Generate factor IC summary
        run: |
          python scripts/run_factor_ic.py \
            --universe-mode sampled \
            --sample-pool-size 500 \
            --universe-size 300 \
            --nav-days 750 \
            --rebalance-step 21 \
            --forward-days 20 \
            --factor-lookback 250 \
            --max-workers 8 \
            --out-dir "$RUNNER_TEMP/factor-ic"
      - name: Publish validated summary
        env:
          FACTOR_IC_PUBLISH_TOKEN: ${{ secrets.FACTOR_IC_PUBLISH_TOKEN }}
        run: python scripts/publish_factor_ic.py "$RUNNER_TEMP/factor-ic/summary.json"
```

- [ ] **Step 4: Run static and focused contract tests**

```bash
cd apps/api
python -m pytest tests/test_factor_ic_workflow_contract.py tests/test_publish_factor_ic.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add .github/workflows/factor-ic-refresh.yml apps/api/tests/test_factor_ic_workflow_contract.py
git commit -m "ci: schedule factor IC refresh"
```

---

### Task 7: Show factor IC freshness in the portfolio panel

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/FactorIcStatusBadge.tsx`
- Create: `apps/web/src/components/FactorIcStatusBadge.test.tsx`
- Modify: `apps/web/src/components/PortfolioDashboard.tsx`

**Interfaces:**
- Consumes: `GET /api/diagnostics/factor-ic-status`.
- Produces: `FactorIcStatus` and `fetchFactorIcStatus()`.
- Produces: `<FactorIcStatusBadge />` with loading/fresh/stale/unavailable/error states.

- [ ] **Step 1: Write failing component tests for all five states**

```tsx
// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { FactorIcStatusBadge } from "@/components/FactorIcStatusBadge";
import { fetchFactorIcStatus } from "@/lib/api";

vi.mock("@/lib/api", () => ({ fetchFactorIcStatus: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("FactorIcStatusBadge", () => {
  it("renders a fresh snapshot", async () => {
    vi.mocked(fetchFactorIcStatus).mockResolvedValue({
      available: true,
      run_date: "2026-07-10",
      age_days: 0,
      stale: false,
      stale_after_days: 30,
      source: "database",
      universe_size: 300,
    });
    render(<FactorIcStatusBadge />);
    expect(screen.getByText(/加载中/)).toBeInTheDocument();
    expect(await screen.findByText("IC 回测：7月10日 · 300只基金")).toBeInTheDocument();
  });

  it("distinguishes stale, unavailable, and request errors", async () => {
    vi.mocked(fetchFactorIcStatus).mockResolvedValueOnce({
      available: true,
      run_date: "2026-05-01",
      age_days: 70,
      stale: true,
      stale_after_days: 30,
      source: "database",
      universe_size: 300,
    });
    const { unmount } = render(<FactorIcStatusBadge />);
    expect(await screen.findByText(/已超过30天/)).toBeInTheDocument();
    unmount();

    vi.mocked(fetchFactorIcStatus).mockResolvedValueOnce({
      available: false,
      stale_after_days: 30,
      source: "unavailable",
    });
    render(<FactorIcStatusBadge />);
    expect(await screen.findByText("IC 回测暂未生成")).toBeInTheDocument();
    cleanup();

    vi.mocked(fetchFactorIcStatus).mockRejectedValueOnce(new Error("offline"));
    render(<FactorIcStatusBadge />);
    await waitFor(() => expect(screen.getByText("IC 状态暂不可用")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run the component test and verify missing module/component failures**

```bash
cd apps/web
npx vitest run src/components/FactorIcStatusBadge.test.tsx
```

Expected: FAIL because status types, fetcher, and component do not exist.

- [ ] **Step 3: Add the API contract and fetcher**

Append to `apps/web/src/lib/api.ts`:

```typescript
export type FactorIcStatus = {
  available: boolean;
  run_date?: string;
  generated_at?: string;
  published_at?: string | null;
  age_days?: number;
  stale?: boolean;
  stale_after_days: number;
  source: "database" | "local_file" | "unavailable";
  target_universe_size?: number | null;
  universe_size?: number | null;
  universe_mode?: string | null;
  rebalance_count?: number | null;
  factor_periods?: Record<string, number | null>;
  source_commit?: string | null;
};

export async function fetchFactorIcStatus(): Promise<FactorIcStatus> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/factor-ic-status`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
```

- [ ] **Step 4: Implement the accessible responsive badge**

Create `FactorIcStatusBadge.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { fetchFactorIcStatus, type FactorIcStatus } from "@/lib/api";

function shortDate(value?: string) {
  const parts = (value ?? "").split("-");
  if (parts.length !== 3) return value ?? "";
  return `${Number(parts[1])}月${Number(parts[2])}日`;
}

export function FactorIcStatusBadge() {
  const [status, setStatus] = useState<FactorIcStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchFactorIcStatus()
      .then((result) => {
        if (!cancelled) setStatus(result);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <span role="status" className="text-xs text-rose-600">IC 状态暂不可用</span>;
  }
  if (!status) {
    return <span role="status" className="animate-pulse text-xs text-slate-400">IC 回测加载中…</span>;
  }
  if (!status.available) {
    return <span role="status" className="text-xs text-slate-500">IC 回测暂未生成</span>;
  }
  if (status.stale) {
    return (
      <span role="status" className="text-xs text-amber-700">
        IC 回测已超过{status.stale_after_days}天，系统将继续自动重试
      </span>
    );
  }
  return (
    <span role="status" className="text-xs text-slate-500">
      IC 回测：{shortDate(status.run_date)} · {status.universe_size ?? "—"}只基金
    </span>
  );
}
```

- [ ] **Step 5: Mount the badge without crowding mobile layout**

In `PortfolioDashboard.tsx`, import the component and replace the factor panel title node with:

```tsx
<div className="min-w-0 space-y-1">
  <div className="pl-panel-title">持仓因子体检</div>
  <FactorIcStatusBadge />
</div>
```

Keep the existing expand/collapse button as the second child of `pl-panel-head`.

- [ ] **Step 6: Run focused and full frontend verification**

```bash
cd apps/web
npx vitest run src/components/FactorIcStatusBadge.test.tsx
npm test
npm run typecheck
npm run lint
npm run build
```

Expected: all commands PASS with zero lint warnings.

- [ ] **Step 7: Commit Task 7**

```bash
git add apps/web/src/lib/api.ts apps/web/src/components/FactorIcStatusBadge.tsx apps/web/src/components/FactorIcStatusBadge.test.tsx apps/web/src/components/PortfolioDashboard.tsx
git commit -m "feat: show factor IC freshness"
```

---

### Task 8: Document operations, run full verification, and prepare acceptance

**Files:**
- Modify: `.env.example`
- Modify: `docs/deploy/cloudbase.md`
- Modify: `docs/PROJECT_CONTEXT.md`
- Modify: `docs/TODO_factor_ic_refresh.md`
- Modify: `docs/superpowers/specs/2026-07-04-factor-ic-refresh-automation-design.md` only if implementation details differ from the approved design.

**Interfaces:**
- Produces: exact one-time secret configuration and production acceptance instructions.
- Produces: resolved TODO linked to the implementation and verification evidence.

- [ ] **Step 1: Update environment and deployment documentation**

Add these entries to `.env.example` without a real token:

```dotenv
# 因子 IC 快照发布：生产 CloudBase 与 GitHub Secret 使用同一个随机 Token
FUND_AI_FACTOR_IC_PUBLISH_TOKEN=
FUND_AI_FACTOR_IC_STALE_AFTER_DAYS=30
```

In `docs/deploy/cloudbase.md`, document this one-time sequence:

```text
python -c "import secrets; print(secrets.token_urlsafe(48))"
CloudBase: FUND_AI_FACTOR_IC_PUBLISH_TOKEN=<生成值>
GitHub Actions Secret: FACTOR_IC_PUBLISH_TOKEN=<同一值>
Actions → Factor IC Refresh → Run workflow
```

State that the token is a publication-only secret and must not reuse the JWT or DeepSeek secret.

- [ ] **Step 2: Update project context and resolve the TODO**

Add a 2026-07-10 changelog item to `docs/PROJECT_CONTEXT.md` covering the external workflow, fixed sampled universe, 240/12 quality gate, append-only table, protected endpoint, 30-day status, front-end badge, and verification commands. Add the two environment variables and both endpoints to their existing tables.

Rewrite `docs/TODO_factor_ic_refresh.md` status to “已解决”, link the approved design and workflow, retain the original background for audit, and state that old C3 was rejected because the production service uses 2～5 auto-scaling instances.

- [ ] **Step 3: Run all backend tests**

```bash
cd apps/api
python -m pytest tests -q -n auto --dist loadscope
```

Expected: PASS with no live network access.

- [ ] **Step 4: Run all frontend checks**

```bash
cd apps/web
npm test
npm run typecheck
npm run lint
npm run build
```

Expected: PASS with zero lint warnings.

- [ ] **Step 5: Run the local generation → publish → read integration scenario**

Use a temporary directory and SQLite database, generate a production-parameter summary, publish it through a local `TestClient`/test server with a test-only token, then assert:

```text
POST first copy       → 200 created=true
POST identical copy   → 200 created=false
POST older copy       → 409
GET status with JWT   → available=true, source=database, stale=false
load_ic_summary       → four factors from the database snapshot
POST low-quality copy → 422 and previous snapshot unchanged
```

The generated production-parameter result must report at least 240 funds and at least 12 valid periods per factor. Do not use the production API or production token.

- [ ] **Step 6: Run final diff and secret scans**

```bash
git diff --check
git status --short
git grep -nE "FUND_AI_FACTOR_IC_PUBLISH_TOKEN=[A-Za-z0-9_-]{32,}" -- ':!docs/superpowers/*'
```

Expected: no whitespace errors, no unexpected files, and the secret scan returns no matches.

- [ ] **Step 7: Commit documentation and completion state**

```bash
git add .env.example docs/deploy/cloudbase.md docs/PROJECT_CONTEXT.md docs/TODO_factor_ic_refresh.md docs/superpowers/specs/2026-07-04-factor-ic-refresh-automation-design.md
git commit -m "docs: document factor IC refresh operations"
```

- [ ] **Step 8: Request production acceptance instead of mutating external secrets automatically**

Provide the user with:

1. The generated one-time configuration command.
2. Exact CloudBase environment variable and GitHub Secret names.
3. The manual workflow trigger path.
4. Expected workflow summary fields.
5. Expected Web badge text and diagnostics response.

Do not set CloudBase/GitHub secrets, push, or trigger production without explicit user authorization.
