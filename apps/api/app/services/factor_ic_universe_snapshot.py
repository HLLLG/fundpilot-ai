"""Point-in-time fund-universe capture, validation and append-only storage.

This store does not manufacture historical membership.  A snapshot may only be
dated on the UTC day when its source became available, so a current catalogue
cannot be relabelled as an older universe and leak survivor information into a
backtest.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.fund_universe_sampler import (
    canonical_portfolio_name,
    dedupe_share_classes,
    stratified_sample_universe,
    universe_coverage,
)


FACTOR_IC_UNIVERSE_SCHEMA_VERSION = 1
FACTOR_IC_UNIVERSE_SAMPLE_TARGET = 1_500
MIN_SOURCE_SHARE_CLASSES = 5_000
MIN_DEDUPED_FUNDS = 1_500
MIN_SAMPLED_FUNDS = 1_200
MIN_FUND_TYPES = 4
MAX_HISTORY_SNAPSHOTS = 260
MAX_HISTORY_DAYS = 3_650
MAX_HISTORY_STRIDE_DAYS = 365
_MAX_HEADER_SCAN = 4_000
_KNOWN_FUND_TYPES = frozenset({"gp", "hh", "zq", "zs", "qdii", "fof", "unknown"})
_SHARE_CLASS = re.compile(r"(?:([A-Z])类|([A-Z]))$", re.IGNORECASE)


class FactorIcUniverseConflict(RuntimeError):
    """An immutable snapshot identity was reused for different evidence."""


class FactorIcUniverseStorageUnavailable(RuntimeError):
    """The authoritative universe store cannot safely complete an operation."""


class FactorIcUniverseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = FACTOR_IC_UNIVERSE_SCHEMA_VERSION
    snapshot_date: date
    available_at: datetime
    captured_at: datetime
    source: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_share_count: int
    deduped_fund_count: int
    sampled_fund_count: int
    sample_target: Literal[1500] = FACTOR_IC_UNIVERSE_SAMPLE_TARGET
    fund_type_count: int
    source_by_type: dict[str, int]
    deduped_by_type: dict[str, int]
    sampled_by_type: dict[str, int]

    @model_validator(mode="after")
    def validate_quality_and_time(self) -> "FactorIcUniverseSnapshot":
        if self.available_at.tzinfo is None or self.captured_at.tzinfo is None:
            raise ValueError("available_at/captured_at 必须包含时区")
        available = self.available_at.astimezone(timezone.utc)
        captured = self.captured_at.astimezone(timezone.utc)
        if self.snapshot_date != available.date():
            raise ValueError("snapshot_date 必须等于数据实际 available_at 的 UTC 日期")
        if available > captured:
            raise ValueError("available_at 不能晚于 captured_at")
        if self.source_share_count < MIN_SOURCE_SHARE_CLASSES:
            raise ValueError(f"源份额覆盖不足 {MIN_SOURCE_SHARE_CLASSES}")
        if self.deduped_fund_count < MIN_DEDUPED_FUNDS:
            raise ValueError(f"去重基金组合不足 {MIN_DEDUPED_FUNDS}")
        if not MIN_SAMPLED_FUNDS <= self.sampled_fund_count <= self.sample_target:
            raise ValueError(
                f"抽样基金数必须在 {MIN_SAMPLED_FUNDS}~{self.sample_target} 之间"
            )
        if self.sampled_fund_count > self.deduped_fund_count:
            raise ValueError("抽样基金数不能超过去重组合数")
        if self.deduped_fund_count > self.source_share_count:
            raise ValueError("去重组合数不能超过源份额数")
        if self.fund_type_count < MIN_FUND_TYPES:
            raise ValueError(f"抽样基金类型不足 {MIN_FUND_TYPES} 类")
        distributions = (
            ("source_by_type", self.source_by_type, self.source_share_count),
            ("deduped_by_type", self.deduped_by_type, self.deduped_fund_count),
            ("sampled_by_type", self.sampled_by_type, self.sampled_fund_count),
        )
        for name, distribution, expected in distributions:
            unknown_types = set(distribution) - _KNOWN_FUND_TYPES
            if unknown_types:
                raise ValueError(f"{name} 含未知基金类型")
            if not distribution or any(int(value) < 0 for value in distribution.values()):
                raise ValueError(f"{name} 缺失或含负数")
            if sum(int(value) for value in distribution.values()) != expected:
                raise ValueError(f"{name} 合计与快照总数不一致")
        return self


class FactorIcUniverseMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: str = Field(pattern=r"^\d{6}$")
    fund_name: str = Field(min_length=1, max_length=255)
    fund_type: str = Field(min_length=1, max_length=32)
    share_class: str | None = Field(default=None, max_length=16)
    canonical_fund_code: str = Field(pattern=r"^\d{6}$")
    canonical_portfolio_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    inception_date: date | None = None
    available_at: datetime
    source_rank: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_member(self) -> "FactorIcUniverseMember":
        if self.available_at.tzinfo is None:
            raise ValueError("成员 available_at 必须包含时区")
        normalized_type = self.fund_type.strip().lower()
        if normalized_type not in _KNOWN_FUND_TYPES:
            raise ValueError(f"未知基金类型: {self.fund_type}")
        self.fund_type = normalized_type
        self.fund_name = self.fund_name.strip()
        if not self.fund_name:
            raise ValueError("fund_name 不可为空白")
        if self.canonical_fund_code != self.fund_code:
            raise ValueError("当前去重代表份额 canonical_fund_code 必须等于 fund_code")
        expected_key = hashlib.sha256(
            f"{normalized_type}\n{canonical_portfolio_name(self.fund_name)}".encode(
                "utf-8"
            )
        ).hexdigest()
        if self.canonical_portfolio_key != expected_key:
            raise ValueError("canonical_portfolio_key 与基金名称/类型不一致")
        nav_date_raw = self.metadata.get("nav_date")
        latest_nav_raw = self.metadata.get("latest_nav")
        has_nav_date = nav_date_raw not in (None, "")
        has_latest_nav = latest_nav_raw not in (None, "")
        if has_nav_date != has_latest_nav:
            raise ValueError("nav_date/latest_nav 必须成对出现")
        if has_nav_date:
            try:
                parsed_nav_date = date.fromisoformat(str(nav_date_raw).strip())
            except ValueError as exc:
                raise ValueError("nav_date 非法") from exc
            if parsed_nav_date > self.available_at.astimezone(timezone.utc).date():
                raise ValueError("nav_date 穿越成员 available_at")
            if isinstance(latest_nav_raw, bool):
                raise ValueError("latest_nav 必须是有限正数")
            try:
                latest_nav = float(latest_nav_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("latest_nav 必须是有限正数") from exc
            if not math.isfinite(latest_nav) or latest_nav <= 0:
                raise ValueError("latest_nav 必须是有限正数")
            self.metadata["nav_date"] = parsed_nav_date.isoformat()
            self.metadata["latest_nav"] = latest_nav
        growth_raw = self.metadata.get("daily_growth_percent")
        if growth_raw not in (None, ""):
            if not has_latest_nav:
                raise ValueError("daily_growth_percent 缺少配对净值观察")
            if isinstance(growth_raw, bool):
                raise ValueError("daily_growth_percent 必须是有限数字")
            try:
                growth = float(growth_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("daily_growth_percent 必须是有限数字") from exc
            if not math.isfinite(growth):
                raise ValueError("daily_growth_percent 必须是有限数字")
            self.metadata["daily_growth_percent"] = growth
        return self


class FactorIcUniversePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: FactorIcUniverseSnapshot
    members: list[FactorIcUniverseMember] = Field(
        min_length=MIN_SAMPLED_FUNDS,
        max_length=FACTOR_IC_UNIVERSE_SAMPLE_TARGET,
    )
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    source_run_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_membership(self) -> "FactorIcUniversePublishRequest":
        snapshot = self.snapshot
        if len(self.members) != snapshot.sampled_fund_count:
            raise ValueError("members 数量与 sampled_fund_count 不一致")
        codes = [member.fund_code for member in self.members]
        if len(codes) != len(set(codes)):
            raise ValueError("同一快照内 fund_code 必须唯一")
        portfolio_keys = [member.canonical_portfolio_key for member in self.members]
        if len(portfolio_keys) != len(set(portfolio_keys)):
            raise ValueError("同一快照内去重组合键必须唯一")
        ranks = [member.source_rank for member in self.members if member.source_rank is not None]
        if len(ranks) != len(set(ranks)):
            raise ValueError("同一快照内 source_rank 必须唯一")
        if ranks and max(ranks) > snapshot.source_share_count:
            raise ValueError("source_rank 不能超过源份额数")
        snapshot_available = snapshot.available_at.astimezone(timezone.utc)
        fund_types: set[str] = set()
        for member in self.members:
            member_available = member.available_at.astimezone(timezone.utc)
            if member_available > snapshot_available:
                raise ValueError(f"{member.fund_code} available_at 穿越快照时点")
            if member.inception_date and member.inception_date > snapshot.snapshot_date:
                raise ValueError(f"{member.fund_code} 成立日晚于快照日期")
            if member.inception_date and member.inception_date > member_available.date():
                raise ValueError(f"{member.fund_code} 成立日晚于成员 available_at")
            nav_date = member.metadata.get("nav_date")
            if nav_date not in (None, ""):
                try:
                    parsed_nav_date = date.fromisoformat(str(nav_date)[:10])
                except ValueError as exc:
                    raise ValueError(f"{member.fund_code} nav_date 非法") from exc
                if parsed_nav_date > snapshot.snapshot_date:
                    raise ValueError(f"{member.fund_code} nav_date 穿越快照日期")
            observed_at_raw = member.metadata.get("snapshot_available_at")
            if observed_at_raw in (None, ""):
                raise ValueError(f"{member.fund_code} 缺少 snapshot_available_at")
            try:
                observed_at = datetime.fromisoformat(
                    str(observed_at_raw).replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(
                    f"{member.fund_code} snapshot_available_at 非法"
                ) from exc
            if observed_at.tzinfo is None:
                raise ValueError(
                    f"{member.fund_code} snapshot_available_at 必须包含时区"
                )
            observed_utc = observed_at.astimezone(timezone.utc)
            if observed_utc != snapshot_available or observed_utc != member_available:
                raise ValueError(
                    f"{member.fund_code} snapshot_available_at 与快照时点不一致"
                )
            member.metadata["snapshot_available_at"] = observed_utc.isoformat()
            if member.fund_type != "unknown":
                fund_types.add(member.fund_type)
        if len(fund_types) != snapshot.fund_type_count:
            raise ValueError("fund_type_count 与成员类型不一致")
        if len(fund_types) < MIN_FUND_TYPES:
            raise ValueError(f"抽样基金类型不足 {MIN_FUND_TYPES} 类")
        sampled_by_type: dict[str, int] = {}
        for member in self.members:
            sampled_by_type[member.fund_type] = sampled_by_type.get(member.fund_type, 0) + 1
        if snapshot.sampled_by_type != sampled_by_type:
            raise ValueError("sampled_by_type 与成员分布不一致")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _share_class(name: str) -> str | None:
    match = _SHARE_CLASS.search(re.sub(r"\s+", "", name))
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").upper() or None


def _parse_inception_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text in {"--", "None", "null"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"成立日期格式非法: {text}") from exc


def _safe_metadata(
    row: dict[str, Any], *, snapshot_available_at: datetime
) -> dict[str, Any]:
    keys = (
        "nav_date",
        "latest_nav",
        "daily_growth_percent",
        "return_1y_percent",
        "return_6m_percent",
        "return_3m_percent",
        "fund_scale_yi",
    )
    metadata = {
        key: row.get(key)
        for key in keys
        if row.get(key) not in (None, "")
    }
    metadata["snapshot_available_at"] = _utc(snapshot_available_at).isoformat()
    return metadata


def build_factor_ic_universe_payload(
    rank_rows: list[dict[str, Any]],
    *,
    source_commit: str,
    source_run_id: str,
    captured_at: datetime | None = None,
    source: str = "eastmoney_open_fund_universe",
    sample_target: int = FACTOR_IC_UNIVERSE_SAMPLE_TARGET,
) -> dict[str, Any]:
    """Build a production payload from one current, fully fetched catalogue."""
    if sample_target != FACTOR_IC_UNIVERSE_SAMPLE_TARGET:
        raise ValueError(f"生产抽样目标固定为 {FACTOR_IC_UNIVERSE_SAMPLE_TARGET}")
    captured = _utc(captured_at or datetime.now(timezone.utc))
    usable_rows = [
        dict(row)
        for row in rank_rows
        if re.fullmatch(r"\d{6}", str(row.get("fund_code") or "").strip())
        and str(row.get("fund_name") or "").strip()
    ]
    if len(usable_rows) != len(rank_rows):
        raise ValueError("源目录含无效基金代码或名称，拒绝静默丢弃")
    deduped = dedupe_share_classes(usable_rows)
    sampled = stratified_sample_universe(usable_rows, sample_target)
    coverage = universe_coverage(usable_rows, sampled)
    source_rank = {
        str(row["fund_code"]): index
        for index, row in enumerate(usable_rows, start=1)
    }
    members: list[dict[str, Any]] = []
    for row in sampled:
        code = str(row["fund_code"]).strip()
        name = str(row["fund_name"]).strip()
        fund_type = str(row.get("fund_type") or "unknown").strip().lower()
        canonical_name = canonical_portfolio_name(name)
        portfolio_key = hashlib.sha256(
            f"{fund_type}\n{canonical_name}".encode("utf-8")
        ).hexdigest()
        members.append(
            {
                "fund_code": code,
                "fund_name": name,
                "fund_type": fund_type,
                "share_class": _share_class(name),
                "canonical_fund_code": code,
                "canonical_portfolio_key": portfolio_key,
                "inception_date": _parse_inception_date(row.get("established_date")),
                "available_at": captured,
                "source_rank": source_rank.get(code),
                "metadata": _safe_metadata(
                    row,
                    snapshot_available_at=captured,
                ),
            }
        )
    sampled_types = {
        member["fund_type"] for member in members if member["fund_type"] != "unknown"
    }
    snapshot = {
        "schema_version": FACTOR_IC_UNIVERSE_SCHEMA_VERSION,
        "snapshot_date": captured.date(),
        "available_at": captured,
        "captured_at": captured,
        "source": source,
        "source_share_count": int(coverage["source_share_classes"]),
        "deduped_fund_count": len(deduped),
        "sampled_fund_count": len(members),
        "sample_target": sample_target,
        "fund_type_count": len(sampled_types),
        "source_by_type": coverage["source_by_type"],
        "deduped_by_type": coverage["unique_by_type"],
        "sampled_by_type": coverage["sampled_by_type"],
    }
    request = FactorIcUniversePublishRequest.model_validate(
        {
            "snapshot": snapshot,
            "members": members,
            "source_commit": source_commit,
            "source_run_id": source_run_id,
        }
    )
    return request.model_dump(mode="json")


def validate_factor_ic_universe_publish_request(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> FactorIcUniversePublishRequest:
    request = FactorIcUniversePublishRequest.model_validate(payload)
    current = _utc(now or datetime.now(timezone.utc))
    available = _utc(request.snapshot.available_at)
    captured = _utc(request.snapshot.captured_at)
    if available > current + timedelta(minutes=5) or captured > current + timedelta(minutes=5):
        raise ValueError("快照时间不能来自未来")
    if available < current - timedelta(hours=24) or captured < current - timedelta(hours=24):
        raise ValueError("当前目录快照已超过 24 小时，禁止伪装成历史 PIT 基金池")
    return request


def _canonical_request(
    request: FactorIcUniversePublishRequest,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    snapshot = request.snapshot.model_dump(mode="json")
    snapshot["available_at"] = _utc(request.snapshot.available_at).isoformat()
    snapshot["captured_at"] = _utc(request.snapshot.captured_at).isoformat()
    members = []
    for item in sorted(request.members, key=lambda member: member.fund_code):
        member = item.model_dump(mode="json")
        member["available_at"] = _utc(item.available_at).isoformat()
        members.append(member)
    evidence = {"snapshot": snapshot, "members": members}
    content_hash = _sha256(evidence)
    return snapshot, members, content_hash, content_hash


def _row_dict(row: object) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)  # type: ignore[arg-type]


def _member_insert_values(
    *,
    snapshot_id: str,
    member: dict[str, Any],
    created_at: str,
) -> tuple[Any, ...]:
    encoded = _canonical_json(member)
    return (
        snapshot_id,
        member["fund_code"],
        member["fund_name"],
        member["fund_type"],
        member.get("share_class"),
        member["canonical_portfolio_key"],
        member.get("inception_date"),
        member["available_at"],
        member.get("source_rank"),
        hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        encoded,
        created_at,
    )


def _stored_member_hashes(connection: Any, snapshot_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT fund_code, content_hash
        FROM factor_ic_universe_members
        WHERE snapshot_id = ?
        ORDER BY fund_code
        """,
        (snapshot_id,),
    ).fetchall()
    return {
        str(_row_dict(row)["fund_code"]): str(_row_dict(row)["content_hash"])
        for row in rows
    }


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def publish_factor_ic_universe_snapshot(
    request: FactorIcUniversePublishRequest,
    *,
    connection_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    factory = connection_factory or _connect
    current = _utc(now or datetime.now(timezone.utc))
    snapshot, members, snapshot_id, content_hash = _canonical_request(request)
    published_at = current.isoformat()
    try:
        with factory() as connection:
            dialect = getattr(connection, "dialect", None)
            if get_settings().uses_mysql and dialect != "mysql":
                raise FactorIcUniverseStorageUnavailable(
                    "MySQL 不可用，拒绝回落到本地 SQLite 发布 PIT 基金池"
                )
            if dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT content_hash, sampled_fund_count
                FROM factor_ic_universe_snapshots
                WHERE snapshot_id = ?
                LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            if existing is not None:
                stored = _row_dict(existing)
                if str(stored["content_hash"]) != content_hash:
                    raise FactorIcUniverseConflict("PIT 基金池快照标识发生不可变冲突")
                expected_hashes = {
                    str(member["fund_code"]): hashlib.sha256(
                        _canonical_json(member).encode("utf-8")
                    ).hexdigest()
                    for member in members
                }
                stored_hashes = _stored_member_hashes(connection, snapshot_id)
                if len(stored_hashes) != int(stored["sampled_fund_count"]):
                    raise FactorIcUniverseStorageUnavailable("PIT 基金池快照成员不完整")
                if stored_hashes != expected_hashes:
                    raise FactorIcUniverseConflict("PIT 基金池成员发生不可变冲突")
                return {"created": False, "snapshot_id": snapshot_id}

            insert_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO factor_ic_universe_snapshots (
                    snapshot_id, schema_version, snapshot_date, available_at,
                    captured_at, published_at, source, source_share_count,
                    deduped_fund_count, sampled_fund_count, sample_target,
                    fund_type_count, source_commit, source_run_id, content_hash,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    snapshot["schema_version"],
                    snapshot["snapshot_date"],
                    snapshot["available_at"],
                    snapshot["captured_at"],
                    published_at,
                    snapshot["source"],
                    snapshot["source_share_count"],
                    snapshot["deduped_fund_count"],
                    snapshot["sampled_fund_count"],
                    snapshot["sample_target"],
                    snapshot["fund_type_count"],
                    request.source_commit,
                    request.source_run_id,
                    content_hash,
                    _canonical_json(snapshot),
                ),
            )
            values = [
                _member_insert_values(
                    snapshot_id=snapshot_id,
                    member=member,
                    created_at=published_at,
                )
                for member in members
            ]
            columns = (
                "snapshot_id, fund_code, fund_name, fund_type, share_class, "
                "canonical_portfolio_key, inception_date, available_at, source_rank, "
                "content_hash, payload, created_at"
            )
            # 50 rows keep SQLite below its conservative bind-variable limit and
            # avoid one remote MySQL round trip per fund.
            for batch in _chunks(values, 50):
                row_placeholders = "(" + ", ".join("?" for _ in range(12)) + ")"
                sql = (
                    f"INSERT OR IGNORE INTO factor_ic_universe_members ({columns}) VALUES "
                    + ", ".join(row_placeholders for _ in batch)
                )
                flattened = tuple(value for row in batch for value in row)
                connection.execute(sql, flattened)
            stored_hashes = _stored_member_hashes(connection, snapshot_id)
            expected_hashes = {str(row[1]): str(row[9]) for row in values}
            if len(stored_hashes) != len(members):
                raise FactorIcUniverseStorageUnavailable("PIT 基金池成员写入不完整")
            if stored_hashes != expected_hashes:
                raise FactorIcUniverseConflict("PIT 基金池成员发生不可变冲突")
            created = bool(getattr(insert_cursor, "rowcount", 1))
    except (FactorIcUniverseConflict, FactorIcUniverseStorageUnavailable):
        raise
    except Exception as exc:
        raise FactorIcUniverseStorageUnavailable("PIT 基金池数据库写入失败") from exc
    return {"created": created, "snapshot_id": snapshot_id}


def _normalize_history_bounds(
    *,
    start_date: date | None,
    end_date: date | None,
    days: int,
    max_snapshots: int,
    stride_days: int,
    now: datetime | None,
) -> tuple[date, date]:
    if not 1 <= days <= MAX_HISTORY_DAYS:
        raise ValueError(f"days 必须在 1~{MAX_HISTORY_DAYS} 之间")
    if not 1 <= max_snapshots <= MAX_HISTORY_SNAPSHOTS:
        raise ValueError(f"max_snapshots 必须在 1~{MAX_HISTORY_SNAPSHOTS} 之间")
    if not 1 <= stride_days <= MAX_HISTORY_STRIDE_DAYS:
        raise ValueError(f"stride_days 必须在 1~{MAX_HISTORY_STRIDE_DAYS} 之间")
    resolved_end = end_date or _utc(now or datetime.now(timezone.utc)).date()
    resolved_start = start_date or (resolved_end - timedelta(days=days - 1))
    if resolved_start > resolved_end:
        raise ValueError("start_date 不能晚于 end_date")
    if (resolved_end - resolved_start).days + 1 > MAX_HISTORY_DAYS:
        raise ValueError(f"历史查询跨度不能超过 {MAX_HISTORY_DAYS} 天")
    return resolved_start, resolved_end


def read_factor_ic_universe_history(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = 365,
    max_snapshots: int = 60,
    stride_days: int = 7,
    include_members: bool = True,
    connection_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read bounded PIT history, selecting the newest snapshot per stride."""
    from app.database import _connect

    resolved_start, resolved_end = _normalize_history_bounds(
        start_date=start_date,
        end_date=end_date,
        days=days,
        max_snapshots=max_snapshots,
        stride_days=stride_days,
        now=now,
    )
    factory = connection_factory or _connect
    scan_limit = min(
        _MAX_HEADER_SCAN,
        max(max_snapshots * max(2, stride_days), (resolved_end - resolved_start).days + 1),
    )
    try:
        with factory() as connection:
            from app.config import get_settings

            if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
                raise FactorIcUniverseStorageUnavailable(
                    "MySQL 不可用，拒绝回落到本地 SQLite 读取 PIT 基金池"
                )
            rows = connection.execute(
                """
                SELECT snapshot_id, snapshot_date, available_at, captured_at,
                       published_at, source, source_share_count,
                       deduped_fund_count, sampled_fund_count, sample_target,
                       fund_type_count, source_commit, source_run_id, content_hash,
                       payload
                FROM factor_ic_universe_snapshots
                WHERE snapshot_date >= ? AND snapshot_date <= ?
                ORDER BY snapshot_date DESC, available_at DESC, published_at DESC
                LIMIT ?
                """,
                (resolved_start.isoformat(), resolved_end.isoformat(), scan_limit),
            ).fetchall()
            selected: list[dict[str, Any]] = []
            last_selected_date: date | None = None
            seen_dates: set[date] = set()
            for row in rows:
                item = _row_dict(row)
                item_date = date.fromisoformat(str(item["snapshot_date"]))
                if item_date in seen_dates:
                    continue
                seen_dates.add(item_date)
                if last_selected_date is not None and (
                    last_selected_date - item_date
                ).days < stride_days:
                    continue
                selected.append(item)
                last_selected_date = item_date
                if len(selected) >= max_snapshots:
                    break
            selected.reverse()
            members_by_snapshot: dict[str, list[dict[str, Any]]] = {
                str(item["snapshot_id"]): [] for item in selected
            }
            if include_members and selected:
                ids = list(members_by_snapshot)
                for id_batch in _chunks(ids, 50):
                    placeholders = ", ".join("?" for _ in id_batch)
                    member_rows = connection.execute(
                        f"""
                        SELECT snapshot_id, fund_code, fund_name, fund_type,
                               share_class, canonical_portfolio_key, inception_date,
                               available_at, source_rank, content_hash, payload
                        FROM factor_ic_universe_members
                        WHERE snapshot_id IN ({placeholders})
                        ORDER BY snapshot_id, fund_code
                        """,
                        tuple(id_batch),
                    ).fetchall()
                    for member_row in member_rows:
                        stored = _row_dict(member_row)
                        encoded_payload = str(stored["payload"])
                        if hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest() != str(
                            stored["content_hash"]
                        ):
                            raise FactorIcUniverseStorageUnavailable(
                                "PIT 基金池成员内容哈希不一致"
                            )
                        payload = json.loads(encoded_payload)
                        # Preserve the normalized public contract even if future
                        # payload versions carry extra metadata.
                        payload.update(
                            {
                                "fund_code": stored["fund_code"],
                                "fund_name": stored["fund_name"],
                                "fund_type": stored["fund_type"],
                                "share_class": stored["share_class"],
                                "canonical_fund_code": payload.get("canonical_fund_code")
                                or stored["fund_code"],
                                "canonical_portfolio_key": stored["canonical_portfolio_key"],
                                "inception_date": stored["inception_date"],
                                "available_at": stored["available_at"],
                                "source_rank": stored["source_rank"],
                            }
                        )
                        members_by_snapshot[str(stored["snapshot_id"])].append(payload)
                for item in selected:
                    snapshot_id = str(item["snapshot_id"])
                    snapshot_members = members_by_snapshot[snapshot_id]
                    if len(snapshot_members) != int(item["sampled_fund_count"]):
                        raise FactorIcUniverseStorageUnavailable(
                            f"PIT 基金池快照 {snapshot_id} 成员不完整"
                        )
                    snapshot_payload = json.loads(str(item["payload"]))
                    if _sha256(
                        {"snapshot": snapshot_payload, "members": snapshot_members}
                    ) != str(item["content_hash"]):
                        raise FactorIcUniverseStorageUnavailable(
                            f"PIT 基金池快照 {snapshot_id} 内容哈希不一致"
                        )
    except FactorIcUniverseStorageUnavailable:
        raise
    except Exception as exc:
        raise FactorIcUniverseStorageUnavailable("PIT 基金池历史读取失败") from exc

    snapshots: list[dict[str, Any]] = []
    for item in selected:
        snapshot = json.loads(str(item["payload"]))
        snapshot.update(
            {
                "snapshot_id": item["snapshot_id"],
                "snapshot_date": item["snapshot_date"],
                "available_at": item["available_at"],
                "captured_at": item["captured_at"],
                "published_at": item["published_at"],
                "source": item["source"],
                "source_commit": item["source_commit"],
                "source_run_id": item["source_run_id"],
                "content_hash": item["content_hash"],
            }
        )
        if include_members:
            snapshot["members"] = members_by_snapshot[str(item["snapshot_id"])]
        snapshots.append(snapshot)
    return {
        "schema_version": "factor_ic_universe_history.v1",
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "stride_days": stride_days,
        "max_snapshots": max_snapshots,
        "include_members": include_members,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }


# Short alias used by research runners while keeping the explicit public name.
read_universe_history = read_factor_ic_universe_history


__all__ = [
    "FACTOR_IC_UNIVERSE_SAMPLE_TARGET",
    "FACTOR_IC_UNIVERSE_SCHEMA_VERSION",
    "FactorIcUniverseConflict",
    "FactorIcUniversePublishRequest",
    "FactorIcUniverseStorageUnavailable",
    "MAX_HISTORY_SNAPSHOTS",
    "build_factor_ic_universe_payload",
    "publish_factor_ic_universe_snapshot",
    "read_factor_ic_universe_history",
    "read_universe_history",
    "validate_factor_ic_universe_publish_request",
]
