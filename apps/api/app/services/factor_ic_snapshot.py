"""因子 IC 快照的版本化发布契约与质量门槛。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
API_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY_PATH = API_ROOT / "var" / "factor_ic" / "summary.json"


class FactorIcNewerSnapshotExists(RuntimeError):
    pass


class FactorIcStorageUnavailable(RuntimeError):
    pass


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
        if (
            self.mean_ic is None
            or not math.isfinite(self.mean_ic)
            or not -1 <= self.mean_ic <= 1
        ):
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
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> FactorIcPublishRequest:
    request = FactorIcPublishRequest.model_validate(payload)
    current = now or datetime.now(timezone.utc)
    generated = request.summary.generated_at.astimezone(timezone.utc)
    if generated > current + timedelta(minutes=5):
        raise ValueError("generated_at 不能来自未来")
    if generated < current - timedelta(hours=24):
        raise ValueError("generated_at 已超过 24 小时")
    return request


def _canonical_summary(
    request: FactorIcPublishRequest,
) -> tuple[dict[str, Any], str, str]:
    summary = request.summary.model_dump(mode="json")
    summary["generated_at"] = request.summary.generated_at.astimezone(
        timezone.utc
    ).isoformat()
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot_id = hashlib.sha256(
        f"{request.source_commit}\n{encoded}".encode("utf-8")
    ).hexdigest()
    return summary, encoded, snapshot_id


def _row_dict(row: object) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)  # type: ignore[arg-type]


def read_latest_database_snapshot(
    connection_factory: Callable | None = None,
) -> dict[str, Any] | None:
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
    data = _row_dict(row)
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
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    factory = connection_factory or _connect
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary, encoded, snapshot_id = _canonical_summary(request)
    generated_at = request.summary.generated_at.astimezone(timezone.utc)

    try:
        with factory() as connection:
            dialect = getattr(connection, "dialect", None)
            if get_settings().uses_mysql and dialect != "mysql":
                raise FactorIcStorageUnavailable(
                    "MySQL 不可用，拒绝回落到本地 SQLite 发布"
                )
            if dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")

            duplicate = connection.execute(
                """
                SELECT snapshot_id
                FROM factor_ic_snapshots
                WHERE snapshot_id = ?
                LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            if duplicate is not None:
                return {"created": False, "snapshot_id": snapshot_id}

            latest_query = """
                SELECT snapshot_id, generated_at
                FROM factor_ic_snapshots
                ORDER BY generated_at DESC, published_at DESC
                LIMIT 1
            """
            if dialect == "mysql":
                latest_query += " FOR UPDATE"
            existing = connection.execute(latest_query).fetchone()
            if existing is not None:
                latest = _row_dict(existing)
                latest_generated = datetime.fromisoformat(
                    str(latest["generated_at"])
                ).astimezone(timezone.utc)
                if generated_at <= latest_generated:
                    raise FactorIcNewerSnapshotExists(
                        "数据库已有更新的 factor IC 快照"
                    )

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
                    str(summary["generated_at"]),
                    current.isoformat(),
                    request.source_commit,
                    request.source_run_id,
                    encoded,
                ),
            )
    except (FactorIcNewerSnapshotExists, FactorIcStorageUnavailable):
        raise
    except Exception as exc:
        raise FactorIcStorageUnavailable("factor IC 快照数据库写入失败") from exc
    return {"created": True, "snapshot_id": snapshot_id}


def load_factor_ic_summary(
    *,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
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
    if not isinstance(raw, dict):
        return None, "unavailable", {}
    return raw, "local_file", {}


def _unavailable_status(threshold: int) -> dict[str, Any]:
    return {
        "available": False,
        "stale_after_days": threshold,
        "source": "unavailable",
    }


def build_factor_ic_status(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
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
    if not raw or not raw.get("run_date") or raw.get("available") is False:
        return _unavailable_status(threshold)
    params = raw.get("params")
    if params is not None and not isinstance(params, dict):
        return _unavailable_status(threshold)
    factors = raw.get("factors")
    if factors is not None and not isinstance(factors, list):
        return _unavailable_status(threshold)

    generated_at = raw.get("generated_at") or f"{raw['run_date']}T00:00:00+00:00"
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return _unavailable_status(threshold)

    generated_utc = generated.astimezone(timezone.utc)
    age_days = max(0, (current.date() - generated_utc.date()).days)
    factors = factors or []
    factor_periods = {
        str(row.get("factor")): row.get("n_periods")
        for row in factors
        if isinstance(row, dict) and row.get("factor")
    }
    source_commit = str(metadata.get("source_commit") or "")[:7] or None
    return {
        "available": True,
        "run_date": str(raw["run_date"]),
        "generated_at": generated.isoformat(),
        "published_at": metadata.get("published_at"),
        "age_days": age_days,
        "stale": age_days >= threshold,
        "stale_after_days": threshold,
        "source": source,
        "target_universe_size": (params or {}).get("universe_size"),
        "universe_size": raw.get("universe_size"),
        "universe_mode": (params or {}).get("universe_mode"),
        "rebalance_count": raw.get("rebalance_count"),
        "factor_periods": factor_periods,
        "source_commit": source_commit,
    }
