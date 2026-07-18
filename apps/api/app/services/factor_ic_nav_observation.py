"""Append-only first-observation ledger for Factor IC NAV evidence.

The public provider does not expose a trustworthy publication timestamp for
each historical NAV revision.  This store therefore records only what the
collector actually saw and when it first saw that exact value.  Historical
rows fetched today remain observed today; they are never relabelled as if they
had been available on their NAV date.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


NAV_OBSERVATION_SCHEMA_VERSION = "factor_ic_nav_observation.v1"
NAV_OBSERVATION_BATCH_SCHEMA_VERSION = "factor_ic_nav_observation_batch.v1"
NAV_OBSERVATION_HISTORY_SCHEMA_VERSION = "factor_ic_nav_observation_history.v1"
NAV_OBSERVATION_STATUS_SCHEMA_VERSION = "factor_ic_nav_observation_status.v1"
AVAILABILITY_BASIS = "collector_first_observed_at"
REVISION_POLICY = "first_observed_value"
MAX_BATCH_OBSERVATIONS = 5_000
MAX_QUERY_CODES = 100
MAX_QUERY_DAYS = 1_800
MAX_QUERY_ROWS = 200_000


class FactorIcNavObservationConflict(RuntimeError):
    """An immutable observation identity no longer matches stored evidence."""


class FactorIcNavObservationStorageUnavailable(RuntimeError):
    """The authoritative observation store cannot be safely read or written."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _row_dict(row: object) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)  # type: ignore[arg-type]


def _finite_optional(value: Any, *, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} 必须是有限数字")
    return parsed


class FactorIcNavObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: str = Field(pattern=r"^\d{6}$")
    nav_date: date
    source: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")
    unit_nav: float
    cumulative_nav: float | None = None
    daily_growth_percent: float | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "FactorIcNavObservationInput":
        if not math.isfinite(self.unit_nav) or self.unit_nav <= 0:
            raise ValueError("unit_nav 必须是正有限数")
        if self.cumulative_nav is not None and (
            not math.isfinite(self.cumulative_nav) or self.cumulative_nav <= 0
        ):
            raise ValueError("cumulative_nav 必须是正有限数")
        if self.daily_growth_percent is not None and (
            not math.isfinite(self.daily_growth_percent)
            or not -99.9 < self.daily_growth_percent < 1_000
        ):
            raise ValueError("daily_growth_percent 超出安全范围")
        return self


class FactorIcNavObservationPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["factor_ic_nav_observation_batch.v1"] = (
        NAV_OBSERVATION_BATCH_SCHEMA_VERSION
    )
    observed_at: datetime
    availability_basis: Literal["collector_first_observed_at"] = AVAILABILITY_BASIS
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    source_run_id: str = Field(min_length=1, max_length=64)
    source_member_count: int = Field(ge=1, le=25_000)
    missing_observation_count: int = Field(ge=0, le=25_000)
    observations: list[FactorIcNavObservationInput] = Field(
        min_length=1,
        max_length=MAX_BATCH_OBSERVATIONS,
    )

    @model_validator(mode="after")
    def validate_batch(self) -> "FactorIcNavObservationPublishRequest":
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at 必须包含时区")
        if len(self.observations) + self.missing_observation_count != self.source_member_count:
            raise ValueError("NAV observation 覆盖计数不守恒")
        if len(self.observations) / self.source_member_count < 0.80:
            raise ValueError("NAV observation 当批覆盖率不足 80%")
        identities = {(item.fund_code, item.nav_date) for item in self.observations}
        if len(identities) != len(self.observations):
            raise ValueError("同一批次 fund_code + nav_date 不可重复")
        observed_date = _utc(self.observed_at).date()
        if any(item.nav_date > observed_date for item in self.observations):
            raise ValueError("nav_date 不能晚于实际 observed_at")
        return self


class FactorIcNavObservationHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_codes: list[str] = Field(min_length=1, max_length=MAX_QUERY_CODES)
    start_date: date
    end_date: date
    as_of: datetime | None = None

    @model_validator(mode="after")
    def validate_query(self) -> "FactorIcNavObservationHistoryQuery":
        normalized = sorted({str(code).strip() for code in self.fund_codes})
        if len(normalized) != len(self.fund_codes):
            raise ValueError("fund_codes 不可重复")
        if any(not re.fullmatch(r"\d{6}", code) for code in normalized):
            raise ValueError("fund_codes 必须是 6 位数字")
        if self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if (self.end_date - self.start_date).days + 1 > MAX_QUERY_DAYS:
            raise ValueError(f"NAV observation 查询跨度不能超过 {MAX_QUERY_DAYS} 天")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("as_of 必须包含时区")
        self.fund_codes = normalized
        return self


def validate_nav_observation_publish_request(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> FactorIcNavObservationPublishRequest:
    request = FactorIcNavObservationPublishRequest.model_validate(payload)
    current = _utc(now or datetime.now(timezone.utc))
    observed = _utc(request.observed_at)
    if observed > current + timedelta(minutes=5):
        raise ValueError("NAV observed_at 不能来自未来")
    if observed < current - timedelta(hours=24):
        raise ValueError("NAV observation 批次已超过 24 小时，拒绝伪装历史观察")
    return request


def build_nav_observation_batch_from_universe(
    universe_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse one directory capture to collect latest NAVs without N×HTTP calls."""

    snapshot = universe_payload.get("snapshot")
    members = universe_payload.get("members")
    if not isinstance(snapshot, Mapping) or not isinstance(members, list):
        raise ValueError("PIT universe payload 缺少 snapshot/members")
    observed_at = snapshot.get("captured_at")
    if not isinstance(observed_at, (str, datetime)):
        raise ValueError("PIT universe captured_at 缺失")
    observations: list[dict[str, Any]] = []
    missing = 0
    for member in members:
        if not isinstance(member, Mapping):
            raise ValueError("PIT universe member 非法")
        metadata = member.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        nav_date = str(metadata_map.get("nav_date") or "")[:10]
        unit_nav = _finite_optional(metadata_map.get("latest_nav"), field="latest_nav")
        if not nav_date or unit_nav is None:
            missing += 1
            continue
        observations.append(
            {
                "fund_code": str(member.get("fund_code") or ""),
                "nav_date": nav_date,
                "source": "eastmoney.open_fund_rankhandler",
                "unit_nav": unit_nav,
                "cumulative_nav": None,
                "daily_growth_percent": _finite_optional(
                    metadata_map.get("daily_growth_percent"),
                    field="daily_growth_percent",
                ),
            }
        )
    request = FactorIcNavObservationPublishRequest.model_validate(
        {
            "schema_version": NAV_OBSERVATION_BATCH_SCHEMA_VERSION,
            "observed_at": observed_at,
            "availability_basis": AVAILABILITY_BASIS,
            "source_commit": universe_payload.get("source_commit"),
            "source_run_id": universe_payload.get("source_run_id"),
            "source_member_count": len(members),
            "missing_observation_count": missing,
            "observations": observations,
        }
    )
    return request.model_dump(mode="json")


def _identity_material(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": NAV_OBSERVATION_SCHEMA_VERSION,
        "fund_code": str(item["fund_code"]),
        "nav_date": str(item["nav_date"]),
        "source": str(item["source"]),
        "unit_nav": float(item["unit_nav"]),
        "cumulative_nav": (
            float(item["cumulative_nav"])
            if item.get("cumulative_nav") is not None
            else None
        ),
        "daily_growth_percent": (
            float(item["daily_growth_percent"])
            if item.get("daily_growth_percent") is not None
            else None
        ),
    }


def _stored_payload(
    item: Mapping[str, Any],
    *,
    observed_at: str,
    source_commit: str,
    source_run_id: str,
) -> tuple[str, dict[str, Any], str]:
    identity = _identity_material(item)
    observation_id = "fnav_" + _hash(identity)
    payload = {
        **identity,
        "observation_id": observation_id,
        "first_observed_at": observed_at,
        "available_at": observed_at,
        "availability_basis": AVAILABILITY_BASIS,
        "revision_policy": REVISION_POLICY,
        "source_commit": source_commit,
        "source_run_id": source_run_id,
    }
    return observation_id, payload, _hash(payload)


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _validate_stored_row(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(row["payload"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation payload 无法解析"
        ) from exc
    if not isinstance(payload, dict):
        raise FactorIcNavObservationStorageUnavailable("NAV observation payload 非对象")
    if (
        payload.get("schema_version") != NAV_OBSERVATION_SCHEMA_VERSION
        or payload.get("availability_basis") != AVAILABILITY_BASIS
        or payload.get("revision_policy") != REVISION_POLICY
    ):
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 版本或可得性契约冲突"
        )
    try:
        first_observed = _utc(
            datetime.fromisoformat(
                str(payload.get("first_observed_at") or "").replace("Z", "+00:00")
            )
        )
        available = _utc(
            datetime.fromisoformat(
                str(payload.get("available_at") or "").replace("Z", "+00:00")
            )
        )
        nav_day = date.fromisoformat(str(payload.get("nav_date") or ""))
    except (TypeError, ValueError) as exc:
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 时间契约非法"
        ) from exc
    if first_observed != available or nav_day > first_observed.date():
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 首次可得时间契约冲突"
        )
    content_hash = _hash(payload)
    if content_hash != str(row.get("content_hash") or ""):
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 内容哈希不一致"
        )
    identity = _identity_material(payload)
    expected_id = "fnav_" + _hash(identity)
    if expected_id != str(row.get("observation_id") or ""):
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 身份哈希不一致"
        )
    for key in (
        "schema_version",
        "fund_code",
        "nav_date",
        "source",
        "first_observed_at",
        "available_at",
        "availability_basis",
        "source_commit",
        "source_run_id",
    ):
        if str(payload.get(key) or "") != str(row.get(key) or ""):
            raise FactorIcNavObservationStorageUnavailable(
                f"NAV observation 索引字段冲突: {key}"
            )
    for key in ("unit_nav", "cumulative_nav", "daily_growth_percent"):
        payload_value = payload.get(key)
        row_value = row.get(key)
        if payload_value is None or row_value is None:
            if payload_value is not None or row_value is not None:
                raise FactorIcNavObservationStorageUnavailable(
                    f"NAV observation 数值索引字段冲突: {key}"
                )
        elif float(payload_value) != float(row_value):
            raise FactorIcNavObservationStorageUnavailable(
                f"NAV observation 数值索引字段冲突: {key}"
            )
    return payload


def publish_nav_observation_batch(
    request: FactorIcNavObservationPublishRequest,
    *,
    connection_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    current = _utc(now or datetime.now(timezone.utc))
    observed_at = _utc(request.observed_at).isoformat()
    records = [
        _stored_payload(
            item.model_dump(mode="json"),
            observed_at=observed_at,
            source_commit=request.source_commit,
            source_run_id=request.source_run_id,
        )
        for item in request.observations
    ]
    factory = connection_factory or _connect
    created_at = current.isoformat()
    created = 0
    duplicate = 0
    try:
        with factory() as connection:
            dialect = getattr(connection, "dialect", None)
            if get_settings().uses_mysql and dialect != "mysql":
                raise FactorIcNavObservationStorageUnavailable(
                    "MySQL 不可用，拒绝回落 SQLite 发布 NAV observation"
                )
            if dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            for batch in _chunks(records, 100):
                ids = [record[0] for record in batch]
                placeholders = ", ".join("?" for _ in ids)
                existing_rows = connection.execute(
                    f"""
                    SELECT observation_id, schema_version, content_hash, payload,
                           fund_code, nav_date, source, first_observed_at,
                           available_at, availability_basis, unit_nav,
                           cumulative_nav, daily_growth_percent,
                           source_commit, source_run_id
                    FROM factor_ic_nav_observations
                    WHERE observation_id IN ({placeholders})
                    """,
                    tuple(ids),
                ).fetchall()
                existing = {
                    str(_row_dict(row)["observation_id"]): _row_dict(row)
                    for row in existing_rows
                }
                for observation_id, payload, content_hash in batch:
                    stored = existing.get(observation_id)
                    if stored is not None:
                        stored_payload = _validate_stored_row(stored)
                        if _identity_material(stored_payload) != _identity_material(payload):
                            raise FactorIcNavObservationConflict(
                                "NAV observation 不可变身份冲突"
                            )
                        duplicate += 1
                        continue
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO factor_ic_nav_observations (
                            observation_id, schema_version, fund_code, nav_date,
                            source, first_observed_at, available_at,
                            availability_basis, unit_nav, cumulative_nav,
                            daily_growth_percent, content_hash, payload,
                            source_commit, source_run_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observation_id,
                            NAV_OBSERVATION_SCHEMA_VERSION,
                            payload["fund_code"],
                            payload["nav_date"],
                            payload["source"],
                            payload["first_observed_at"],
                            payload["available_at"],
                            payload["availability_basis"],
                            payload["unit_nav"],
                            payload.get("cumulative_nav"),
                            payload.get("daily_growth_percent"),
                            content_hash,
                            _canonical_json(payload),
                            request.source_commit,
                            request.source_run_id,
                            created_at,
                        ),
                    )
                    if int(getattr(cursor, "rowcount", 1) or 0) > 0:
                        created += 1
                    else:
                        duplicate += 1
            ids = [record[0] for record in records]
            stored_count = 0
            for batch in _chunks(ids, 500):
                placeholders = ", ".join("?" for _ in batch)
                stored_rows = connection.execute(
                    f"""
                    SELECT observation_id, schema_version, content_hash, payload,
                           fund_code, nav_date, source, first_observed_at,
                           available_at, availability_basis, unit_nav,
                           cumulative_nav, daily_growth_percent,
                           source_commit, source_run_id
                    FROM factor_ic_nav_observations
                    WHERE observation_id IN ({placeholders})
                    """,
                    tuple(batch),
                ).fetchall()
                stored_count += len(stored_rows)
                for stored_row in stored_rows:
                    _validate_stored_row(_row_dict(stored_row))
            if stored_count != len(ids):
                raise FactorIcNavObservationStorageUnavailable(
                    "NAV observation 批次写入不完整"
                )
    except (FactorIcNavObservationConflict, FactorIcNavObservationStorageUnavailable):
        raise
    except Exception as exc:
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 数据库写入失败"
        ) from exc
    return {
        "schema_version": "factor_ic_nav_observation_publish_result.v1",
        "created_count": created,
        "duplicate_count": duplicate,
        "observation_count": len(records),
        "missing_observation_count": request.missing_observation_count,
        "source_run_id": request.source_run_id,
    }


def read_nav_observation_history(
    *,
    fund_codes: Sequence[str],
    start_date: date,
    end_date: date,
    as_of: datetime | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    codes = sorted({str(code).strip() for code in fund_codes})
    if not codes or len(codes) > MAX_QUERY_CODES:
        raise ValueError(f"fund_codes 数量必须在 1~{MAX_QUERY_CODES}")
    if any(not re.fullmatch(r"\d{6}", code) for code in codes):
        raise ValueError("fund_codes 必须是 6 位数字")
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")
    if (end_date - start_date).days + 1 > MAX_QUERY_DAYS:
        raise ValueError(f"NAV observation 查询跨度不能超过 {MAX_QUERY_DAYS} 天")
    cutoff = _utc(as_of or datetime.now(timezone.utc))
    if cutoff > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("as_of 不能来自未来")
    placeholders = ", ".join("?" for _ in codes)
    factory = connection_factory or _connect
    try:
        with factory() as connection:
            if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
                raise FactorIcNavObservationStorageUnavailable(
                    "MySQL 不可用，拒绝回落 SQLite 读取 NAV observation"
                )
            rows = connection.execute(
                f"""
                SELECT observation_id, schema_version, content_hash, payload,
                       fund_code, nav_date, source, first_observed_at,
                       available_at, availability_basis, unit_nav,
                       cumulative_nav, daily_growth_percent,
                       source_commit, source_run_id
                FROM factor_ic_nav_observations
                WHERE fund_code IN ({placeholders})
                  AND nav_date >= ? AND nav_date <= ?
                  AND first_observed_at <= ?
                ORDER BY fund_code, nav_date, first_observed_at, observation_id
                LIMIT ?
                """,
                (
                    *codes,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    cutoff.isoformat(),
                    MAX_QUERY_ROWS + 1,
                ),
            ).fetchall()
    except FactorIcNavObservationStorageUnavailable:
        raise
    except Exception as exc:
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 历史读取失败"
        ) from exc
    if len(rows) > MAX_QUERY_ROWS:
        raise ValueError("NAV observation 查询结果过大，请缩小基金或日期范围")

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    revision_rows_excluded = 0
    for raw in rows:
        row = _row_dict(raw)
        payload = _validate_stored_row(row)
        key = (str(payload["fund_code"]), str(payload["nav_date"]))
        if key in seen:
            revision_rows_excluded += 1
            continue
        seen.add(key)
        selected.append(
            {
                "fund_code": payload["fund_code"],
                "nav_date": payload["nav_date"],
                "unit_nav": payload["unit_nav"],
                "cumulative_nav": payload.get("cumulative_nav"),
                "daily_growth_percent": payload.get("daily_growth_percent"),
                "first_observed_at": payload["first_observed_at"],
                "available_at": payload["available_at"],
                "source": payload["source"],
                "observation_id": payload["observation_id"],
                "source_commit": payload["source_commit"],
                "source_run_id": payload["source_run_id"],
            }
        )
    return {
        "schema_version": NAV_OBSERVATION_HISTORY_SCHEMA_VERSION,
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
        "availability_basis": AVAILABILITY_BASIS,
        "revision_policy": REVISION_POLICY,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "as_of": cutoff.isoformat(),
        "fund_code_count": len(codes),
        "observation_count": len(selected),
        "revision_rows_excluded": revision_rows_excluded,
        "content_hash": _hash(selected),
        "observations": selected,
    }


def read_nav_observation_status(
    *,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    factory = connection_factory or _connect
    try:
        with factory() as connection:
            if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
                raise FactorIcNavObservationStorageUnavailable(
                    "MySQL 不可用，拒绝回落 SQLite 读取 NAV observation 状态"
                )
            summary_row = connection.execute(
                """
                SELECT COUNT(*) AS observation_count,
                       COUNT(DISTINCT fund_code) AS fund_count,
                       COUNT(DISTINCT source_run_id) AS capture_run_count,
                       MIN(first_observed_at) AS first_observed_at,
                       MAX(first_observed_at) AS latest_observed_at,
                       MIN(nav_date) AS first_nav_date,
                       MAX(nav_date) AS latest_nav_date
                FROM factor_ic_nav_observations
                """
            ).fetchone()
            revision_row = connection.execute(
                """
                SELECT COALESCE(SUM(row_count - 1), 0) AS revision_count
                FROM (
                    SELECT COUNT(*) AS row_count
                    FROM factor_ic_nav_observations
                    GROUP BY fund_code, nav_date
                    HAVING COUNT(*) > 1
                ) revisions
                """
            ).fetchone()
            latest_run_row = connection.execute(
                """
                SELECT source_run_id
                FROM factor_ic_nav_observations
                ORDER BY first_observed_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
            latest_run_id = (
                str(_row_dict(latest_run_row)["source_run_id"])
                if latest_run_row is not None
                else None
            )
            latest_run_count = 0
            if latest_run_id is not None:
                latest_count_row = connection.execute(
                    """
                    SELECT COUNT(*) AS row_count,
                           COUNT(DISTINCT fund_code) AS fund_count
                    FROM factor_ic_nav_observations
                    WHERE source_run_id = ?
                    """,
                    (latest_run_id,),
                ).fetchone()
                latest_run_count = int(_row_dict(latest_count_row)["fund_count"])
    except FactorIcNavObservationStorageUnavailable:
        raise
    except Exception as exc:
        raise FactorIcNavObservationStorageUnavailable(
            "NAV observation 状态读取失败"
        ) from exc
    summary = _row_dict(summary_row)
    count = int(summary.get("observation_count") or 0)
    return {
        "schema_version": NAV_OBSERVATION_STATUS_SCHEMA_VERSION,
        "status": "collecting" if count else "not_started",
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
        "availability_basis": AVAILABILITY_BASIS,
        "revision_policy": REVISION_POLICY,
        "observation_count": count,
        "fund_count": int(summary.get("fund_count") or 0),
        "capture_run_count": int(summary.get("capture_run_count") or 0),
        "revision_count": int(_row_dict(revision_row).get("revision_count") or 0),
        "first_observed_at": summary.get("first_observed_at"),
        "latest_observed_at": summary.get("latest_observed_at"),
        "first_nav_date": summary.get("first_nav_date"),
        "latest_nav_date": summary.get("latest_nav_date"),
        "latest_capture_fund_count": latest_run_count,
        "minimum_feature_history_points": 250,
        "full_model_ready": False,
        "automatic_promotion_allowed": False,
    }


__all__ = [
    "AVAILABILITY_BASIS",
    "NAV_OBSERVATION_BATCH_SCHEMA_VERSION",
    "NAV_OBSERVATION_HISTORY_SCHEMA_VERSION",
    "NAV_OBSERVATION_SCHEMA_VERSION",
    "REVISION_POLICY",
    "FactorIcNavObservationConflict",
    "FactorIcNavObservationHistoryQuery",
    "FactorIcNavObservationPublishRequest",
    "FactorIcNavObservationStorageUnavailable",
    "build_nav_observation_batch_from_universe",
    "publish_nav_observation_batch",
    "read_nav_observation_history",
    "read_nav_observation_status",
    "validate_nav_observation_publish_request",
]
