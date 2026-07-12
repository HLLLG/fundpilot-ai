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
CURRENT_FACTOR_IC_SCHEMA_VERSION = 2
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
V2_EXPECTED_PARAMS = {
    "universe_size": 1500,
    "universe_mode": "stratified",
    "sample_pool_size": 25000,
    "nav_days": 1500,
    "rebalance_step": 10,
    "forward_days": 20,
    "factor_lookback": 250,
    "forward_horizons": [5, 20, 60],
}
MIN_EFFECTIVE_UNIVERSE = 240
V2_MIN_EFFECTIVE_UNIVERSE = 1200
MIN_VALID_PERIODS = 12
API_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY_PATH = API_ROOT / "var" / "factor_ic" / "summary.json"

FactorIcEvidenceState = Literal["unavailable", "stale", "available"]


class FactorIcNewerSnapshotExists(RuntimeError):
    pass


class FactorIcStorageUnavailable(RuntimeError):
    pass


class FactorIcParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_size: int
    universe_mode: Literal["top", "sampled", "stratified"]
    sample_pool_size: int
    nav_days: int
    rebalance_step: int
    forward_days: int
    factor_lookback: int
    forward_horizons: list[int] | None = None


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
    standard_error: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    oos_mean_ic: float | None = None
    oos_positive_ratio: float | None = None
    direction_stable: bool = False

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
        for name in (
            "ic_std", "icir", "t_stat", "positive_ratio", "standard_error",
            "ci_low", "ci_high", "oos_mean_ic", "oos_positive_ratio",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{self.factor} {name} 必须是有限数字")
        if self.positive_ratio is not None and not 0 <= self.positive_ratio <= 1:
            raise ValueError(f"{self.factor} positive_ratio 非法")
        if self.oos_positive_ratio is not None and not 0 <= self.oos_positive_ratio <= 1:
            raise ValueError(f"{self.factor} oos_positive_ratio 非法")
        if self.oos_mean_ic is not None and not -1 <= self.oos_mean_ic <= 1:
            raise ValueError(f"{self.factor} oos_mean_ic 非法")
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
    coverage: dict[str, Any] | None = None
    research_model: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_quality(self) -> "FactorIcSummary":
        if self.schema_version not in {
            FACTOR_IC_SCHEMA_VERSION,
            CURRENT_FACTOR_IC_SCHEMA_VERSION,
        }:
            raise ValueError("不支持的 factor IC schema_version")
        params = self.params.model_dump(exclude_none=True)
        expected_params = (
            V2_EXPECTED_PARAMS
            if self.schema_version == CURRENT_FACTOR_IC_SCHEMA_VERSION
            else EXPECTED_PARAMS
        )
        if params != expected_params:
            raise ValueError("回测参数不是固定生产口径")
        if not self.available:
            raise ValueError("回测结果不可用")
        minimum_universe = (
            V2_MIN_EFFECTIVE_UNIVERSE
            if self.schema_version == CURRENT_FACTOR_IC_SCHEMA_VERSION
            else MIN_EFFECTIVE_UNIVERSE
        )
        if self.universe_size < minimum_universe:
            raise ValueError(f"有效基金数不足 {minimum_universe}")
        if self.rebalance_count < MIN_VALID_PERIODS:
            raise ValueError(f"回测期数不足 {MIN_VALID_PERIODS}")
        names = [row.factor for row in self.factors]
        if len(names) != len(FACTOR_NAMES) or set(names) != FACTOR_NAMES:
            raise ValueError("四个因子必须齐全且不可重复")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at 必须包含时区")
        if self.run_date != self.generated_at.astimezone(timezone.utc).date():
            raise ValueError("run_date 必须等于 generated_at 的 UTC 日期")
        if self.schema_version == CURRENT_FACTOR_IC_SCHEMA_VERSION:
            self._validate_v2_research_model()
        return self

    def _validate_v2_research_model(self) -> None:
        coverage = self.coverage or {}
        if int(coverage.get("source_share_classes") or 0) < 5_000:
            raise ValueError("v2 全量基金目录覆盖不足")
        if int(coverage.get("unique_portfolios") or 0) < self.universe_size:
            raise ValueError("v2 去重基金组合数不足")
        if int(coverage.get("effective_nav_portfolios") or 0) < V2_MIN_EFFECTIVE_UNIVERSE:
            raise ValueError("v2 有效总收益序列不足")
        if float(coverage.get("total_return_preferred_rate") or 0) < 0.8:
            raise ValueError("v2 总收益口径覆盖率不足 80%")

        model = self.research_model or {}
        if model.get("version") != "factor_ic.v2":
            raise ValueError("v2 research_model 版本非法")
        if model.get("cohort_mode") != "current_survivors":
            raise ValueError("v2 cohort_mode 非法")
        segments = model.get("segments")
        peers = model.get("peer_distributions")
        classifications = model.get("fund_classifications")
        if not isinstance(segments, dict) or len(segments) < 4:
            raise ValueError("v2 分类 IC 覆盖不足")
        if not isinstance(peers, dict) or len(peers) < 4:
            raise ValueError("v2 同类分布覆盖不足")
        if not isinstance(classifications, dict) or len(classifications) < 5_000:
            raise ValueError("v2 基金分类映射覆盖不足")
        primary_horizon = str(model.get("primary_horizon") or 20)
        qualified_segments = 0
        for key, segment in segments.items():
            if not isinstance(segment, dict):
                continue
            horizon = (segment.get("horizons") or {}).get(primary_horizon) or {}
            qualified = horizon.get("qualified") or {}
            peer = peers.get(key) if isinstance(peers.get(key), dict) else {}
            if any(bool(value) for value in qualified.values()) and int(
                peer.get("eligible_count") or 0
            ) >= 20:
                qualified_segments += 1
        if qualified_segments < 4:
            raise ValueError("v2 主周期合格同类组不足 4 类")


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_factor_ic_status_from_loaded(
    raw: dict[str, Any] | None,
    source: str,
    metadata: dict[str, Any],
    *,
    threshold: int,
    current: datetime,
) -> dict[str, Any]:
    """Build status from one already-loaded summary without touching storage."""
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
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    research_model = (
        raw.get("research_model")
        if isinstance(raw.get("research_model"), dict)
        else {}
    )
    primary_horizon = str(research_model.get("primary_horizon") or 20)
    segment_status = {}
    for key, segment in (research_model.get("segments") or {}).items():
        if not isinstance(segment, dict):
            continue
        horizon = (segment.get("horizons") or {}).get(primary_horizon) or {}
        segment_status[str(key)] = {
            "label": segment.get("label"),
            "sampled_portfolios": segment.get("sampled_portfolios"),
            "universe_size": horizon.get("universe_size"),
            "rebalance_count": horizon.get("rebalance_count"),
            "qualified_factors": sorted(
                factor
                for factor, qualified in (horizon.get("qualified") or {}).items()
                if qualified
            ),
        }
    return {
        "available": True,
        "schema_version": raw.get("schema_version", 1),
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
        "forward_horizons": (params or {}).get("forward_horizons") or [raw.get("forward_days")],
        "coverage": {
            key: coverage.get(key)
            for key in (
                "source_share_classes",
                "unique_portfolios",
                "sampled_portfolios",
                "effective_nav_portfolios",
                "effective_nav_rate",
                "total_return_preferred_portfolios",
                "total_return_preferred_rate",
                "nav_return_source_counts",
                "sampled_by_type",
            )
            if key in coverage
        },
        "segments": segment_status,
        "cohort_mode": research_model.get("cohort_mode"),
        "source_commit": source_commit,
    }


def load_factor_ic_context(
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
    current = _as_utc(now or datetime.now(timezone.utc))
    raw, source, metadata = load_factor_ic_summary(
        local_path=local_path,
        connection_factory=connection_factory,
    )
    status = _build_factor_ic_status_from_loaded(
        raw,
        source,
        metadata,
        threshold=threshold,
        current=current,
    )
    state: FactorIcEvidenceState
    if not status.get("available"):
        state = "unavailable"
    elif status.get("stale"):
        state = "stale"
    else:
        state = "available"
    return {"state": state, "status": status, "summary": raw}


def build_factor_ic_status(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    return load_factor_ic_context(
        stale_after_days=stale_after_days,
        now=now,
        local_path=local_path,
        connection_factory=connection_factory,
    )["status"]
