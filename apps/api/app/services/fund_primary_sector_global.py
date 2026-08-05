"""全市场基金主关联板块（fund_primary_sectors_global）读写与 TTL。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import (
    get_fund_primary_sector_global,
    save_fund_primary_sector_global,
)
from app.services.fund_sector_identity import materialize_primary_sector_record
from app.services.fund_primary_sector_types import PrimarySectorRecord

_BENCHMARK_SOURCES = frozenset({"benchmark_index", "precompute_benchmark"})
_HOLDINGS_SOURCES = frozenset({"holdings_infer", "precompute_holdings"})
_VERIFIED_SOURCES = frozenset(
    {
        "ocr_detail",
        "manual",
        "holdings_infer",
        "precompute_holdings",
        "benchmark_index",
        "precompute_benchmark",
    }
)
_SOURCE_PRIORITY = {
    "ocr_detail": 100,
    "manual": 90,
    "holdings_infer": 70,
    "precompute_holdings": 70,
    "benchmark_index": 65,
    "precompute_benchmark": 65,
    "benchmark_freeform": 55,
    "alipay_overview": 50,
    "semantic_name": 40,
    "llm_infer": 30,
    "precompute_llm": 30,
    "semantic_name_freeform": 25,
    "name_infer": 10,
}


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
    existing = get_fund_primary_sector_global(record.fund_code)
    if existing and not _should_replace_global(existing, record):
        return existing
    saved = save_fund_primary_sector_global(
        fund_code=record.fund_code,
        sector_name=record.sector_name,
        intraday_index_name=record.intraday_index_name,
        source=record.source,
        confidence=record.confidence,
        detail=record.detail,
    )
    materialize_primary_sector_record(record)
    return saved


def _should_replace_global(existing: dict, record: PrimarySectorRecord) -> bool:
    old_source = str(existing.get("source") or "")
    new_source = str(record.source or "")
    old_priority = _SOURCE_PRIORITY.get(old_source, 0)
    new_priority = _SOURCE_PRIORITY.get(new_source, 0)
    if new_priority >= old_priority:
        return True
    # Stale verified evidence may be refreshed from another independently
    # observed source, but never from a name or LLM inference.
    return bool(
        not is_global_sector_fresh(existing)
        and new_source in _VERIFIED_SOURCES
    )
