"""全市场 fund_code → 关联板块 离线预计算。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.database import count_fund_primary_sectors_global, get_fund_primary_sector_global
from app.services.fund_code_resolver import _fund_name_table
from app.services.fund_primary_sector_global import (
    global_sector_enabled,
    is_global_sector_fresh,
    promote_record_to_global,
)
from app.services.fund_primary_sector_service import (
    _resolve_from_benchmark_index,
    _resolve_from_holdings_infer,
)

logger = logging.getLogger(__name__)

PrecomputeMode = str  # "benchmark" | "holdings" | "auto"


@dataclass
class PrecomputeBatchResult:
    ok: int = 0
    skipped: int = 0
    miss: int = 0
    error: int = 0
    processed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "miss": self.miss,
            "error": self.error,
            "processed": self.processed,
            "errors": self.errors[:20],
        }


def _status_path() -> Path:
    root = get_settings().db_path.parent
    return root / "fund_primary_sector_precompute_status.json"


def load_precompute_status() -> dict:
    path = _status_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_precompute_status(payload: dict) -> None:
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_precompute_candidates(
    *,
    limit: int,
    force: bool = False,
    fund_codes: list[str] | None = None,
) -> list[str]:
    """优先：无全局记录 → TTL 过期 → 名称表顺序。"""
    if fund_codes:
        return [code.strip().zfill(6) for code in fund_codes if code.strip()][:limit]

    table = _fund_name_table()
    ordered = [code.zfill(6) for code, _name in table if code]
    if not ordered:
        return []

    missing: list[str] = []
    stale: list[str] = []
    fresh: list[str] = []
    for code in ordered:
        row = get_fund_primary_sector_global(code)
        if row is None:
            missing.append(code)
        elif force or not is_global_sector_fresh(row):
            stale.append(code)
        else:
            fresh.append(code)

    candidates = missing + stale + fresh
    return candidates[:limit]


def precompute_fund_sector(
    fund_code: str,
    *,
    mode: PrecomputeMode = "benchmark",
    force: bool = False,
) -> str:
    """返回 ok | skipped | miss | error。"""
    if not global_sector_enabled():
        return "skipped"

    code = fund_code.strip().zfill(6)
    if len(code) != 6:
        return "error"

    existing = get_fund_primary_sector_global(code)
    if existing and is_global_sector_fresh(existing) and not force:
        return "skipped"

    try:
        if mode in ("benchmark", "auto"):
            record = _resolve_from_benchmark_index(
                code,
                fetch=True,
                persist_user=False,
                promote_global=False,
            )
            if record is not None:
                promote_record_to_global(replace(record, source="precompute_benchmark"))
                return "ok"

        if mode in ("holdings", "auto"):
            record = _resolve_from_holdings_infer(code, persist=False)
            if record is not None:
                promote_record_to_global(replace(record, source="precompute_holdings"))
                return "ok"

        return "miss"
    except Exception as exc:
        logger.info("precompute failed for %s: %s", code, exc)
        return "error"


def run_precompute_batch(
    *,
    limit: int | None = None,
    mode: PrecomputeMode = "benchmark",
    force: bool = False,
    fund_codes: list[str] | None = None,
    sleep_seconds: float = 0.05,
) -> PrecomputeBatchResult:
    settings = get_settings()
    batch_limit = limit if limit is not None else int(settings.fund_primary_sector_precompute_batch_size)
    batch_limit = max(1, batch_limit)

    result = PrecomputeBatchResult()
    candidates = iter_precompute_candidates(limit=batch_limit, force=force, fund_codes=fund_codes)
    started = datetime.now(timezone.utc)

    for code in candidates:
        result.processed += 1
        status = precompute_fund_sector(code, mode=mode, force=force)
        if status == "ok":
            result.ok += 1
        elif status == "skipped":
            result.skipped += 1
        elif status == "miss":
            result.miss += 1
        else:
            result.error += 1
            if len(result.errors) < 20:
                result.errors.append(f"{code}:{status}")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    save_precompute_status(
        {
            "last_run_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "force": force,
            "global_count": count_fund_primary_sectors_global(),
            **result.to_dict(),
        }
    )
    logger.info(
        "fund primary sector precompute done mode=%s ok=%s skipped=%s miss=%s error=%s global=%s",
        mode,
        result.ok,
        result.skipped,
        result.miss,
        result.error,
        count_fund_primary_sectors_global(),
    )
    return result
