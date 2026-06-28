"""全市场基金主关联板块（fund_primary_sectors_global）读写与 TTL。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import (
    get_fund_primary_sector_global,
    save_fund_primary_sector_global,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord

_BENCHMARK_SOURCES = frozenset({"benchmark_index", "precompute_benchmark"})
_HOLDINGS_SOURCES = frozenset({"holdings_infer", "precompute_holdings"})


def global_sector_enabled() -> bool:
    return bool(get_settings().fund_primary_sector_global_enabled)


def global_sector_ttl(source: str) -> timedelta:
    settings = get_settings()
    if source in _HOLDINGS_SOURCES:
        return timedelta(days=max(1, int(settings.fund_primary_sector_global_holdings_ttl_days)))
    return timedelta(days=max(1, int(settings.fund_primary_sector_global_benchmark_ttl_days)))


def is_global_sector_fresh(row: dict | None) -> bool:
    if not row or not global_sector_enabled():
        return False
    resolved_raw = row.get("resolved_at") or row.get("updated_at")
    if not resolved_raw:
        return False
    try:
        resolved_at = datetime.fromisoformat(str(resolved_raw))
    except ValueError:
        return False
    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
    source = str(row.get("source") or "")
    return datetime.now(timezone.utc) - resolved_at < global_sector_ttl(source)


def load_fresh_global_sector(fund_code: str) -> dict | None:
    if not global_sector_enabled():
        return None
    row = get_fund_primary_sector_global(fund_code)
    if row and is_global_sector_fresh(row):
        return row
    return None


def promote_record_to_global(record: PrimarySectorRecord) -> dict | None:
    """将解析结果写入全市场表（用户 OCR/手动仍走 per-user 表）。"""
    if not global_sector_enabled():
        return None
    return save_fund_primary_sector_global(
        fund_code=record.fund_code,
        sector_name=record.sector_name,
        intraday_index_name=record.intraday_index_name,
        source=record.source,
        confidence=record.confidence,
        detail=record.detail,
    )
