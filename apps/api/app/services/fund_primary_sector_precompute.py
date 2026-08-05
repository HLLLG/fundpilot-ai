"""全市场 fund_code → 关联板块 离线预计算。"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.database import (
    count_fresh_verified_fund_sector_current,
    count_fund_primary_sectors_global,
    get_fund_sector_current_primary_by_codes,
    get_fund_primary_sectors_global_by_codes,
    list_fund_sector_resolution_statuses,
    save_fund_sector_resolution_statuses,
)
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock
from app.services.discovery_sector_identity import candidate_sector_identity_is_executable
from app.services.fund_code_resolver import _fund_name_table
from app.services.fund_primary_sector_global import (
    global_sector_enabled,
    is_global_sector_fresh,
    promote_record_to_global,
)
from app.services.fund_universe_sampler import canonical_portfolio_name
from app.services.fund_primary_sector_service import (
    _is_passive_index_fund_name,
    _resolve_from_benchmark_index,
    _resolve_from_holdings_infer,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.fund_sector_identity import (
    FUND_SECTOR_IDENTITY_VERSION,
    is_current_identity_row_executable,
)

logger = logging.getLogger(__name__)

PrecomputeMode = str  # "benchmark" | "holdings" | "llm" | "auto"
_PRIORITY_QUEUE_SCHEMA_VERSION = "fund_primary_sector_precompute_priority.v1"
_PRIORITY_QUEUE_MAX_CODES = 512
_PRIORITY_BATCH_SIZE = 32
_PRIORITY_LOCK_RESOURCE = "fund-primary-sector-precompute-priority"
_BULK_PROFILE_STAGE = "bulk_benchmark_profile"
_HOLDINGS_RESOLUTION_STAGE = "holdings_resolution"
_NON_SECTOR_CATEGORY_TOKENS = ("债券", "货币", "FOF", "理财", "固收")
_EQUITY_CATEGORY_TOKENS = ("股票", "混合", "指数", "QDII")


def _lookup_fund_name(fund_code: str) -> str | None:
    """按代码查名称。`_fund_name_table()` 自身已做进程内缓存，这里无需再缓存一层。"""
    for code, name in _fund_name_table():
        if code and code.strip().zfill(6) == fund_code and name:
            return name
    return None


@dataclass
class PrecomputeBatchResult:
    ok: int = 0
    skipped: int = 0
    miss: int = 0
    error: int = 0
    processed: int = 0
    queued: int = 0
    research_only: int = 0
    pending: int = 0
    unmapped: int = 0
    unavailable: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "miss": self.miss,
            "error": self.error,
            "processed": self.processed,
            "queued": self.queued,
            "research_only": self.research_only,
            "pending": self.pending,
            "unmapped": self.unmapped,
            "unavailable": self.unavailable,
            "errors": self.errors[:20],
        }


@dataclass(frozen=True)
class _HoldingsResolutionEvaluation:
    record: PrimarySectorRecord | None
    resolution_status: str
    reason_code: str
    detail: dict[str, object]


def _status_path() -> Path:
    root = get_settings().db_path.parent
    return root / "fund_primary_sector_precompute_status.json"


def _priority_queue_path() -> Path:
    return get_settings().db_path.parent / "fund_primary_sector_precompute_priority.json"


def _normalized_codes(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text.isdigit() or len(text) > 6:
            continue
        code = text.zfill(6)
        if code == "000000" or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _load_priority_queue_unlocked() -> list[str]:
    path = _priority_queue_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, Mapping):
        return []
    values = payload.get("fund_codes")
    return _normalized_codes(values if isinstance(values, list) else [])


def _save_priority_queue_unlocked(fund_codes: Sequence[object]) -> None:
    path = _priority_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _PRIORITY_QUEUE_SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fund_codes": _normalized_codes(fund_codes)[:_PRIORITY_QUEUE_MAX_CODES],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def enqueue_priority_precompute_codes(fund_codes: Sequence[object]) -> int:
    """Persist current near-miss candidates ahead of the all-market cursor.

    The discovery request only writes a tiny queue; holdings retrieval stays in
    the background worker and therefore cannot lengthen the report request.
    """

    settings = get_settings()
    if not (
        settings.fund_primary_sector_global_enabled
        and settings.fund_primary_sector_precompute_enabled
    ):
        return 0
    incoming = _normalized_codes(fund_codes)
    if not incoming:
        return 0
    try:
        with cross_process_lock(_PRIORITY_LOCK_RESOURCE, timeout_seconds=1.0):
            existing = _load_priority_queue_unlocked()
            _save_priority_queue_unlocked([*existing, *incoming])
    except (CrossProcessLockError, OSError) as exc:
        logger.info("fund primary sector priority enqueue skipped: %s", exc)
        return 0
    return len(incoming)


def enqueue_candidate_sector_precompute(
    candidate_pool: Sequence[Mapping[str, object]],
) -> int:
    """Queue only candidates whose otherwise-eligible path lacks identity."""

    codes: list[object] = []
    for item in candidate_pool:
        quality_gate = (
            item.get("quality_gate")
            if isinstance(item.get("quality_gate"), Mapping)
            else {}
        )
        if str(quality_gate.get("status") or "") != "eligible":
            continue
        if str(item.get("vehicle_quality_status") or "") != "eligible":
            continue
        if candidate_sector_identity_is_executable(item):
            continue
        codes.append(item.get("fund_code"))
    return enqueue_priority_precompute_codes(codes)


def run_priority_precompute_batch(
    *,
    limit: int = _PRIORITY_BATCH_SIZE,
) -> PrecomputeBatchResult:
    """Drain one candidate-first holdings batch without weakening identity gates."""

    try:
        with cross_process_lock(_PRIORITY_LOCK_RESOURCE, timeout_seconds=1.0):
            codes = _load_priority_queue_unlocked()[: max(1, limit)]
    except CrossProcessLockError as exc:
        logger.info("fund primary sector priority load skipped: %s", exc)
        return PrecomputeBatchResult()
    if not codes:
        return PrecomputeBatchResult()

    # A fresh verified identity is already usable.  A recent research-only or
    # unavailable attempt is deferred to its checkpoint instead of being
    # hammered every time the same discovery candidate is recalled.
    current_rows = get_fund_sector_current_primary_by_codes(set(codes))
    resolution_rows = list_fund_sector_resolution_statuses()
    now = datetime.now(timezone.utc)
    already_resolved = [
        code
        for code in codes
        if is_current_identity_row_executable(current_rows.get(code))
    ]
    already_resolved_set = set(already_resolved)
    retry_deferred: list[str] = []
    for code in codes:
        if code in already_resolved_set:
            continue
        row = resolution_rows.get(code) or {}
        if str(row.get("resolution_status") or "") not in {
            "research_only",
            "unavailable",
        }:
            continue
        retry_at = _parse_utc(row.get("next_retry_at"))
        if retry_at is not None and retry_at > now:
            retry_deferred.append(code)
    resolved_codes = set([*already_resolved, *retry_deferred])
    missing_codes = [code for code in codes if code not in resolved_codes]
    result = (
        run_precompute_batch(
            limit=len(missing_codes),
            mode="holdings",
            force=False,
            fund_codes=missing_codes,
        )
        if missing_codes
        else PrecomputeBatchResult()
    )
    processed_missing = missing_codes[: result.processed]
    processed_codes = set([*already_resolved, *retry_deferred, *processed_missing])
    skipped_count = len(already_resolved) + len(retry_deferred)
    result.processed += skipped_count
    result.skipped += skipped_count
    if not processed_codes:
        return result
    try:
        with cross_process_lock(_PRIORITY_LOCK_RESOURCE, timeout_seconds=1.0):
            remaining = [
                code
                for code in _load_priority_queue_unlocked()
                if code not in processed_codes
            ]
            _save_priority_queue_unlocked(remaining)
    except (CrossProcessLockError, OSError) as exc:
        logger.info("fund primary sector priority dequeue skipped: %s", exc)
    return result


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


def resolution_coverage() -> dict[str, object]:
    universe_codes = {
        str(code).strip().zfill(6)
        for code, _name in _fund_name_table()
        if str(code or "").strip()
    }
    universe_size = len(universe_codes)
    rows = list_fund_sector_resolution_statuses()
    relevant = [row for code, row in rows.items() if code in universe_codes]
    counts = Counter(
        str(row.get("resolution_status") or "unknown") for row in relevant
    )
    stats = dict(sorted(counts.items()))
    covered = len(relevant)
    stats["total"] = covered
    return {
        "candidate_universe_size": universe_size,
        "resolution_status_count": covered,
        "resolution_coverage_percent": (
            round(covered * 100.0 / universe_size, 4) if universe_size else 100.0
        ),
        "initial_backfill_complete": bool(universe_size and covered >= universe_size),
        "resolution_stats": stats,
    }


def initial_resolution_backfill_pending() -> bool:
    coverage = resolution_coverage()
    return not bool(coverage["initial_backfill_complete"])


def _parse_utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retry_at_for_resolution(status: str, *, checked_at: datetime) -> datetime:
    settings = get_settings()
    if status == "verified":
        return checked_at + timedelta(
            days=max(1, int(settings.fund_primary_sector_global_benchmark_ttl_days))
        )
    if status == "unavailable":
        return checked_at + timedelta(
            hours=max(
                1,
                int(settings.fund_primary_sector_precompute_unavailable_retry_hours),
            )
        )
    if status == "queued":
        # ``queued`` means the profile pass has finished and the fund is ready
        # for the stronger holdings pass.  It must be immediately selectable.
        return checked_at
    if status == "research_only":
        return checked_at + timedelta(
            days=max(
                1,
                int(
                    settings.fund_primary_sector_precompute_research_retry_days
                ),
            )
        )
    if status == "pending":
        return checked_at + timedelta(
            days=max(1, int(settings.fund_primary_sector_precompute_pending_retry_days))
        )
    return checked_at + timedelta(
        days=max(1, int(settings.fund_primary_sector_precompute_unmapped_retry_days))
    )


def _resolution_status_row(
    *,
    fund_code: str,
    fund_name: str | None,
    status: str,
    reason_code: str,
    detail: Mapping[str, object] | None,
    previous: Mapping[str, object] | None,
    checked_at: datetime,
    stage: str = _BULK_PROFILE_STAGE,
    next_retry_at: datetime | None = None,
) -> dict[str, object]:
    try:
        previous_attempts = int((previous or {}).get("attempt_count") or 0)
    except (TypeError, ValueError):
        previous_attempts = 0
    return {
        "fund_code": fund_code.strip().zfill(6),
        "resolution_status": status,
        "stage": stage,
        "reason_code": reason_code,
        "fund_name": fund_name,
        "checked_at": checked_at.isoformat(),
        "next_retry_at": (
            next_retry_at or _retry_at_for_resolution(status, checked_at=checked_at)
        ).isoformat(),
        "attempt_count": previous_attempts + 1,
        "mapping_version": FUND_SECTOR_IDENTITY_VERSION,
        "detail": dict(detail or {}),
    }


def _bulk_resolution_candidates(
    *,
    limit: int,
    force: bool,
    fund_codes: list[str] | None,
    statuses: Mapping[str, Mapping[str, object]],
) -> list[str]:
    ordered = _normalized_codes(
        fund_codes
        if fund_codes is not None
        else [code for code, _name in _fund_name_table()]
    )
    if force:
        return ordered[:limit]
    now = datetime.now(timezone.utc)
    missing: list[str] = []
    due: dict[str, list[str]] = {
        "unavailable": [],
        "queued": [],
        "research_only": [],
        "pending": [],
        "unmapped": [],
        "verified": [],
    }
    for code in ordered:
        row = statuses.get(code)
        if row is None:
            missing.append(code)
            continue
        next_retry = _parse_utc(row.get("next_retry_at"))
        if next_retry is not None and next_retry > now:
            continue
        status = str(row.get("resolution_status") or "unavailable")
        due.setdefault(status, []).append(code)
    candidates = [
        *missing,
        *due.get("unavailable", []),
        *due.get("pending", []),
        *due.get("research_only", []),
        *due.get("unmapped", []),
        *due.get("verified", []),
    ]
    return candidates[:limit]


def _holdings_worker_count(settings: object) -> int:
    configured = max(
        1,
        int(
            getattr(
                settings,
                "fund_primary_sector_precompute_holdings_workers",
                1,
            )
        ),
    )
    pool_size = max(0, int(getattr(settings, "akshare_worker_pool_size", 0)))
    # Holdings discovery enters the shared AkShare subprocess pool first.  Do
    # not admit more outer tasks than the pool can serve without timing out.
    return min(configured, pool_size) if pool_size > 0 else configured


def _decoded_status_detail(row: Mapping[str, object] | None) -> dict[str, object]:
    if not row:
        return {}
    raw = row.get("detail")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _holdings_resolution_candidates(
    *,
    limit: int,
    force: bool,
    fund_codes: list[str] | None,
    statuses: Mapping[str, Mapping[str, object]],
) -> list[str]:
    """Select only funds that have a reason to spend holdings-provider budget.

    The profile pass explicitly marks equity/theme candidates as ``queued``.
    Failed/research-only holdings rows are retried only when their checkpoint is
    due.  Missing profile rows are not blindly sent through the expensive path.
    """

    if fund_codes is not None:
        return _normalized_codes(fund_codes)[:limit]

    now = datetime.now(timezone.utc)
    candidates: list[tuple[tuple[object, ...], str]] = []
    for code, row in statuses.items():
        status = str(row.get("resolution_status") or "")
        stage = str(row.get("stage") or "")
        initial_queue = status in {"queued", "pending"}
        holdings_retry = bool(
            stage == _HOLDINGS_RESOLUTION_STAGE
            and status in {"research_only", "unavailable"}
        )
        if not initial_queue and not holdings_retry:
            continue
        retry_at = _parse_utc(row.get("next_retry_at"))
        if not force and retry_at is not None and retry_at > now:
            continue
        detail = _decoded_status_detail(row)
        category = str(detail.get("fund_category") or "")
        semantic_hint = bool(detail.get("semantic_recall_sector"))
        try:
            attempts = int(row.get("attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        # Prefer explicit theme recall and index/equity categories: these have
        # the highest chance of producing a verified identity per provider call.
        priority = (
            0 if initial_queue else 1,
            0 if semantic_hint else 1,
            0 if "指数" in category else 1 if "股票" in category else 2,
            attempts,
            code,
        )
        candidates.append((priority, code))
    candidates.sort(key=lambda item: item[0])
    selected: list[str] = []
    selected_families: set[tuple[str, str]] = set()
    for _priority, code in candidates:
        row = statuses[code]
        detail = _decoded_status_detail(row)
        fund_name = str(row.get("fund_name") or "").strip()
        category = str(detail.get("fund_category") or "").strip()
        portfolio_name = canonical_portfolio_name(fund_name)
        # This is scheduling-only de-duplication.  Evidence is never copied to
        # a sibling code: later batches still verify each share class itself.
        family = (category.casefold(), portfolio_name.casefold())
        if portfolio_name and family in selected_families:
            continue
        if portfolio_name:
            selected_families.add(family)
        selected.append(code)
        if len(selected) >= limit:
            break
    return selected


def _fetch_holdings_evidence(fund_code: str) -> dict[str, object]:
    from app.services.fund_holdings_sector_infer import (
        fetch_portfolio_stocks_with_industry_evidence,
    )

    try:
        return dict(fetch_portfolio_stocks_with_industry_evidence(fund_code))
    except Exception as exc:  # noqa: BLE001 - one provider miss must not stop a batch
        logger.info("holdings evidence fetch failed for %s: %s", fund_code, exc)
        return {
            "status": "unavailable",
            "reason_codes": ["holdings_evidence_fetch_error"],
            "stocks": [],
            "error_type": type(exc).__name__,
        }


def _fetch_holdings_evidence_batch(
    fund_codes: Sequence[str],
    *,
    workers: int,
) -> dict[str, dict[str, object]]:
    codes = list(fund_codes)
    if len(codes) <= 1 or workers <= 1:
        return {code: _fetch_holdings_evidence(code) for code in codes}
    results: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(
        max_workers=min(max(1, workers), len(codes)),
        thread_name_prefix="fund-sector-holdings",
    ) as executor:
        future_by_code = {
            executor.submit(_fetch_holdings_evidence, code): code for code in codes
        }
        for future in as_completed(future_by_code):
            code = future_by_code[future]
            try:
                results[code] = future.result()
            except Exception as exc:  # defensive: worker wrapper is already closed
                results[code] = {
                    "status": "unavailable",
                    "reason_codes": ["holdings_evidence_worker_error"],
                    "stocks": [],
                    "error_type": type(exc).__name__,
                }
    return results


def _evaluate_holdings_resolution(
    fund_code: str,
    evidence_payload: Mapping[str, object],
) -> _HoldingsResolutionEvaluation:
    from app.services.fund_holdings_sector_infer import (
        assess_sector_from_portfolio_stocks,
    )

    raw_stocks = evidence_payload.get("stocks")
    stocks = list(raw_stocks) if isinstance(raw_stocks, list) else []
    raw_provider_reasons = evidence_payload.get("reason_codes")
    provider_reason_items = (
        list(raw_provider_reasons)
        if isinstance(raw_provider_reasons, (list, tuple, set))
        else [raw_provider_reasons]
        if raw_provider_reasons
        else []
    )
    provider_reasons = [
        str(item)
        for item in provider_reason_items
        if str(item or "").strip()
    ]
    base_detail: dict[str, object] = {
        "provider_status": evidence_payload.get("status"),
        "provider_reason_codes": provider_reasons,
        "snapshot_hash": evidence_payload.get("snapshot_hash"),
        "report_period": evidence_payload.get("report_period"),
        "as_of_date": evidence_payload.get("as_of"),
        "available_at": evidence_payload.get("available_at"),
    }
    if evidence_payload.get("error_type"):
        base_detail["error_type"] = evidence_payload.get("error_type")
    if not stocks:
        return _HoldingsResolutionEvaluation(
            record=None,
            resolution_status="unavailable",
            reason_code=(
                provider_reasons[0]
                if provider_reasons
                else "holdings_snapshot_unavailable"
            ),
            detail=base_detail,
        )

    raw_clue = evidence_payload.get("sector_clue")
    sector_clue = (
        dict(raw_clue)
        if isinstance(raw_clue, Mapping)
        else assess_sector_from_portfolio_stocks(stocks)
    )
    qualification = (
        dict(sector_clue.get("qualification"))
        if isinstance(sector_clue.get("qualification"), Mapping)
        else {}
    )
    coverage = (
        dict(sector_clue.get("coverage"))
        if isinstance(sector_clue.get("coverage"), Mapping)
        else {}
    )
    detail = {
        **base_detail,
        "sector_name": sector_clue.get("sector_name"),
        "qualification": qualification,
        "coverage": coverage,
    }
    record = _resolve_from_holdings_infer(
        fund_code,
        persist=False,
        stocks=stocks,
        evidence_payload=evidence_payload,
        materialize_research=True,
        materialization_source="precompute_holdings",
    )
    if record is not None:
        return _HoldingsResolutionEvaluation(
            record=record,
            resolution_status="verified",
            reason_code="holdings_identity_verified",
            detail=detail,
        )
    if qualification.get("research_clue_available") is True:
        return _HoldingsResolutionEvaluation(
            record=None,
            resolution_status="research_only",
            reason_code="holdings_evidence_research_only",
            detail=detail,
        )
    return _HoldingsResolutionEvaluation(
        record=None,
        resolution_status="unavailable",
        reason_code="holdings_classification_unavailable",
        detail=detail,
    )


def _profile_sector_resolution(
    *,
    fund_code: str,
    fallback_name: str | None,
    profile: Mapping[str, object],
) -> tuple[PrimarySectorRecord | None, str, str, dict[str, object]]:
    from app.services.fund_benchmark_sector import resolve_sector_from_benchmark
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    fund_name = str(profile.get("fund_name") or fallback_name or "").strip()
    category = str(profile.get("fund_category") or "").strip()
    benchmark_text = str(
        profile.get("tracking_reference_text") or profile.get("benchmark_text") or ""
    ).strip()
    benchmark_kind = str(profile.get("benchmark_text_kind") or "unknown").strip()
    source_kind = str(
        profile.get("benchmark_text_source_kind") or "xq_akshare_aggregator"
    ).strip()
    semantic = infer_semantic_sector_from_fund_name(fund_name) if fund_name else None
    semantic_hint = bool(semantic is not None and semantic.source == "semantic_name")
    qdii_category = "QDII" in category.upper()
    passive_category = any(
        marker in category for marker in ("标准指数", "被动指数")
    )
    passive_name = _is_passive_index_fund_name(fund_name)
    passive = bool(
        benchmark_kind == "tracking_target"
        or passive_category
        or passive_name
    )
    resolved = resolve_sector_from_benchmark(benchmark_text) if benchmark_text else None
    detail: dict[str, object] = {
        "profile_source": profile.get("profile_source"),
        "fund_category": category or None,
        "benchmark_text": benchmark_text or None,
        "benchmark_text_kind": benchmark_kind,
        "benchmark_text_source_kind": source_kind,
        "passive_index_name_gate": passive_name,
        "passive_index_category_gate": passive_category,
        "tracking_target_gate": benchmark_kind == "tracking_target",
        "semantic_recall_sector": semantic.sector_name if semantic_hint else None,
        "holdings_classifier_scope": (
            "overseas_unsupported" if qdii_category else "cn_equity_supported"
        ),
    }

    # Category exclusion is evaluated before benchmark text.  An active bond,
    # money-market or FOF fund often has a performance benchmark, but that
    # benchmark is not evidence that the fund owns one equity sector.
    if any(token in category for token in _NON_SECTOR_CATEGORY_TOKENS):
        return None, "unmapped", "non_sector_fund_category", detail

    if resolved is not None and passive:
        sector_name, intraday_index_name, match = resolved
        record = PrimarySectorRecord(
            fund_code=fund_code,
            sector_name=sector_name,
            intraday_index_name=intraday_index_name,
            source="precompute_benchmark",
            confidence=0.68,
            detail={
                "index_code": match.index_code,
                "index_name": match.index_name,
                "benchmark_text": match.benchmark_text,
                "relation_kind": "tracking_reference",
                "price_proxy_eligible": True,
                "benchmark_text_kind": benchmark_kind,
                "benchmark_text_source_kind": source_kind,
                "benchmark_text_length": len(benchmark_text),
                "benchmark_text_truncated": False,
                "fund_category": category or None,
            },
        )
        return record, "verified", "exact_benchmark_verified", detail

    if benchmark_text and not passive:
        if qdii_category:
            return (
                None,
                "research_only",
                "overseas_holdings_classifier_unavailable",
                detail,
            )
        if semantic_hint or any(
            token in category for token in _EQUITY_CATEGORY_TOKENS
        ):
            return (
                None,
                "queued",
                "active_fund_holdings_verification_queued",
                detail,
            )
        return (
            None,
            "research_only",
            "active_performance_benchmark_research_only",
            detail,
        )
    if benchmark_text and passive:
        return (
            None,
            "research_only" if semantic_hint else "unmapped",
            (
                "tracking_index_sector_catalog_research_only"
                if semantic_hint
                else "broad_or_non_sector_tracking_index"
            ),
            detail,
        )
    if qdii_category:
        return (
            None,
            "research_only",
            "overseas_holdings_classifier_unavailable",
            detail,
        )
    if semantic_hint or any(token in category for token in _EQUITY_CATEGORY_TOKENS):
        return None, "queued", "holdings_verification_queued", detail
    return None, "unmapped", "no_sector_identity_expected", detail


def reclassify_stored_profile_resolutions(
    *,
    reason_codes: set[str] | None = None,
    limit: int | None = None,
) -> PrecomputeBatchResult:
    """Re-run mapping rules against checkpointed profile evidence, without I/O.

    Index catalog changes should not refetch tens of thousands of upstream
    profiles.  Resolution rows retain the exact benchmark text and provenance,
    so an auditable local reclassification can promote only newly exact matches.
    """

    statuses = list_fund_sector_resolution_statuses()
    selected = [
        (code, row)
        for code, row in sorted(statuses.items())
        if str(row.get("stage") or "") == _BULK_PROFILE_STAGE
        and (
            reason_codes is None
            or str(row.get("reason_code") or "") in reason_codes
        )
    ]
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    result = PrecomputeBatchResult()
    # Promotion performs its own exact-code lookup.  Avoid one enormous IN
    # query when migrating a legacy database with tens of thousands of rows.
    global_rows_by_code: dict[str, dict] = {}
    checkpoint_rows: list[dict[str, object]] = []
    checked_at = datetime.now(timezone.utc)

    for code, previous in selected:
        raw_detail = previous.get("detail")
        if isinstance(raw_detail, str):
            try:
                decoded = json.loads(raw_detail)
            except json.JSONDecodeError:
                decoded = None
            detail = decoded if isinstance(decoded, Mapping) else {}
        else:
            detail = raw_detail if isinstance(raw_detail, Mapping) else {}
        benchmark_text = str(detail.get("benchmark_text") or "").strip()
        category = str(detail.get("fund_category") or "").strip()
        if not benchmark_text and not category:
            result.skipped += 1
            continue
        profile = {
            "fund_code": code,
            "fund_name": previous.get("fund_name"),
            "fund_category": category or None,
            "tracking_reference_text": (
                benchmark_text
                if str(detail.get("benchmark_text_kind") or "")
                == "tracking_target"
                else None
            ),
            "benchmark_text": benchmark_text or None,
            "benchmark_text_kind": detail.get("benchmark_text_kind"),
            "benchmark_text_source_kind": detail.get(
                "benchmark_text_source_kind"
            ),
            "profile_source": detail.get("profile_source"),
        }
        record, status, reason_code, refreshed_detail = _profile_sector_resolution(
            fund_code=code,
            fallback_name=str(previous.get("fund_name") or "") or None,
            profile=profile,
        )
        result.processed += 1
        if record is not None:
            _promote_and_remember(
                record,
                source="precompute_benchmark",
                global_rows_by_code=global_rows_by_code,
            )
            result.ok += 1
        elif status == "queued":
            result.queued += 1
            result.miss += 1
        elif status == "research_only":
            result.research_only += 1
            result.miss += 1
        elif status == "pending":
            result.pending += 1
            result.miss += 1
        else:
            result.unmapped += 1
            result.miss += 1
        checkpoint_rows.append(
            _resolution_status_row(
                fund_code=code,
                fund_name=str(previous.get("fund_name") or "") or None,
                status=status,
                reason_code=reason_code,
                detail={
                    **refreshed_detail,
                    "reclassified_from_reason_code": previous.get("reason_code"),
                },
                previous=previous,
                checked_at=checked_at,
            )
        )
        if len(checkpoint_rows) >= 500:
            save_fund_sector_resolution_statuses(checkpoint_rows)
            checkpoint_rows = []

    if checkpoint_rows:
        save_fund_sector_resolution_statuses(checkpoint_rows)
    return result


def migrate_legacy_pending_profile_resolutions() -> PrecomputeBatchResult:
    """One-time/idempotent migration from the overloaded legacy pending state."""

    return reclassify_stored_profile_resolutions(
        reason_codes={
            "active_performance_benchmark_not_identity",
            "tracking_index_sector_catalog_pending",
            "independent_identity_evidence_missing",
        }
    )


def run_bulk_profile_precompute_batch(
    *,
    limit: int | None = None,
    force: bool = False,
    fund_codes: list[str] | None = None,
    sleep_seconds: float = 0.25,
) -> PrecomputeBatchResult:
    """Resolve a checkpointed all-market batch through bounded XQ concurrency."""

    from app.services.akshare_subprocess import fetch_fund_basic_profiles_xq

    settings = get_settings()
    batch_limit = max(
        1,
        int(
            limit
            if limit is not None
            else settings.fund_primary_sector_precompute_batch_size
        ),
    )
    chunk_size = max(
        1,
        min(80, int(settings.fund_primary_sector_precompute_profile_chunk_size)),
    )
    name_by_code = {
        str(code).strip().zfill(6): str(name or "").strip()
        for code, name in _fund_name_table()
        if str(code or "").strip()
    }
    statuses = list_fund_sector_resolution_statuses()
    candidates = _bulk_resolution_candidates(
        limit=batch_limit,
        force=force,
        fund_codes=fund_codes,
        statuses=statuses,
    )
    result = PrecomputeBatchResult()
    started = datetime.now(timezone.utc)
    global_rows_by_code = get_fund_primary_sectors_global_by_codes(set(candidates))

    for start in range(0, len(candidates), chunk_size):
        chunk = candidates[start : start + chunk_size]
        provider_failed = False
        try:
            profile_rows = fetch_fund_basic_profiles_xq(
                chunk,
                timeout_seconds=max(45, int(len(chunk) * 0.75)),
            )
            if profile_rows is None:
                provider_failed = True
                profile_rows = []
        except Exception as exc:  # noqa: BLE001 - provider batch is fail-closed
            provider_failed = True
            profile_rows = []
            if len(result.errors) < 20:
                result.errors.append(f"profile_batch:{type(exc).__name__}")
        by_code = {
            str(row.get("fund_code") or "").strip().zfill(6): row
            for row in profile_rows
            if isinstance(row, Mapping) and row.get("fund_code")
        }
        checkpoint_rows: list[dict[str, object]] = []
        checked_at = datetime.now(timezone.utc)
        for code in chunk:
            result.processed += 1
            profile = by_code.get(code)
            name = name_by_code.get(code) or None
            if profile is None:
                result.unavailable += 1
                result.miss += 1
                checkpoint_rows.append(
                    _resolution_status_row(
                        fund_code=code,
                        fund_name=name,
                        status="unavailable",
                        reason_code=(
                            "profile_provider_batch_unavailable"
                            if provider_failed
                            else "profile_row_unavailable"
                        ),
                        detail={"provider_failed": provider_failed},
                        previous=statuses.get(code),
                        checked_at=checked_at,
                    )
                )
                continue
            record, status, reason_code, detail = _profile_sector_resolution(
                fund_code=code,
                fallback_name=name,
                profile=profile,
            )
            if record is not None:
                _promote_and_remember(
                    record,
                    source="precompute_benchmark",
                    global_rows_by_code=global_rows_by_code,
                )
                result.ok += 1
            elif status == "queued":
                result.queued += 1
                result.miss += 1
            elif status == "research_only":
                result.research_only += 1
                result.miss += 1
            elif status == "pending":
                result.pending += 1
                result.miss += 1
            else:
                result.unmapped += 1
                result.miss += 1
            checkpoint_rows.append(
                _resolution_status_row(
                    fund_code=code,
                    fund_name=str(profile.get("fund_name") or name or "") or None,
                    status=status,
                    reason_code=reason_code,
                    detail=detail,
                    previous=statuses.get(code),
                    checked_at=checked_at,
                )
            )
        save_fund_sector_resolution_statuses(checkpoint_rows)
        for row in checkpoint_rows:
            statuses[str(row["fund_code"])] = row
        if sleep_seconds > 0 and start + chunk_size < len(candidates):
            time.sleep(sleep_seconds)

    coverage = resolution_coverage()
    status_payload = {
        **load_precompute_status(),
        "last_run_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "mode": "benchmark_bulk",
        "force": force,
        "global_count": count_fund_primary_sectors_global(),
        "verified_current_count": count_fresh_verified_fund_sector_current(),
        "profile_chunk_size": chunk_size,
        **coverage,
        **result.to_dict(),
    }
    save_precompute_status(status_payload)
    return result


def _promote_and_remember(
    record: PrimarySectorRecord,
    *,
    source: str,
    global_rows_by_code: dict[str, dict],
) -> None:
    promoted_record = replace(record, source=source)
    saved = promote_record_to_global(promoted_record)
    if saved is None:
        resolved_at = datetime.now(timezone.utc).isoformat()
        saved = {
            "fund_code": promoted_record.fund_code,
            "sector_name": promoted_record.sector_name,
            "intraday_index_name": promoted_record.intraday_index_name,
            "source": promoted_record.source,
            "confidence": promoted_record.confidence,
            "detail": promoted_record.detail,
            "resolved_at": resolved_at,
            "updated_at": resolved_at,
        }
    global_rows_by_code[promoted_record.fund_code] = saved


def iter_precompute_candidates(
    *,
    limit: int,
    force: bool = False,
    fund_codes: list[str] | None = None,
    global_rows_by_code: dict[str, dict] | None = None,
    current_rows_by_code: dict[str, dict] | None = None,
) -> list[str]:
    """优先：无全局记录 → TTL 过期 → 名称表顺序。"""
    if fund_codes:
        return [code.strip().zfill(6) for code in fund_codes if code.strip()][:limit]

    table = _fund_name_table()
    ordered = [code.zfill(6) for code, _name in table if code]
    if not ordered:
        return []

    if global_rows_by_code is None:
        global_rows_by_code = get_fund_primary_sectors_global_by_codes(set(ordered))
    if current_rows_by_code is None:
        current_rows_by_code = get_fund_sector_current_primary_by_codes(set(ordered))

    missing: list[str] = []
    stale: list[str] = []
    fresh: list[str] = []
    for code in ordered:
        current = current_rows_by_code.get(code)
        if current is None:
            missing.append(code)
        elif force or not is_current_identity_row_executable(current):
            stale.append(code)
        else:
            fresh.append(code)

    candidates = missing + stale + fresh
    if not candidates:
        return []
    status = load_precompute_status()
    try:
        status_universe_size = int(status.get("candidate_universe_size") or 0)
        cursor = (
            int(status.get("candidate_cursor") or 0) % len(candidates)
            if status_universe_size == len(ordered)
            else 0
        )
    except (TypeError, ValueError):
        cursor = 0
    rotated = candidates[cursor:] + candidates[:cursor]
    return rotated[:limit]


def precompute_fund_sector(
    fund_code: str,
    *,
    mode: PrecomputeMode = "benchmark",
    force: bool = False,
    global_rows_by_code: dict[str, dict] | None = None,
    current_rows_by_code: dict[str, dict] | None = None,
) -> str:
    """返回 ok | skipped | miss | error。"""
    if not global_sector_enabled():
        return "skipped"

    code = fund_code.strip().zfill(6)
    if len(code) != 6:
        return "error"

    if global_rows_by_code is None:
        global_rows_by_code = get_fund_primary_sectors_global_by_codes([code])
    if current_rows_by_code is None:
        current_rows_by_code = get_fund_sector_current_primary_by_codes([code])
    existing = global_rows_by_code.get(code)
    existing_is_fresh = bool(existing and is_global_sector_fresh(existing))
    current = current_rows_by_code.get(code)
    if is_current_identity_row_executable(current) and not force:
        return "skipped"

    try:
        if mode in ("benchmark", "auto"):
            record = _resolve_from_benchmark_index(
                code,
                fund_name=_lookup_fund_name(code),
                fetch=True,
                persist_user=False,
                promote_global=False,
                preloaded_global_row=existing if existing_is_fresh else None,
            )
            if record is not None:
                _promote_and_remember(
                    record,
                    source="precompute_benchmark",
                    global_rows_by_code=global_rows_by_code,
                )
                return "ok"

        if mode in ("holdings", "auto"):
            record = _resolve_from_holdings_infer(
                code,
                persist=False,
                materialize_research=True,
                materialization_source="precompute_holdings",
            )
            if record is not None:
                _promote_and_remember(
                    record,
                    source="precompute_holdings",
                    global_rows_by_code=global_rows_by_code,
                )
                return "ok"

        if mode in ("llm", "auto") and get_settings().fund_primary_sector_llm_infer_enabled:
            from app.services.fund_sector_llm_infer import infer_sector_via_llm

            fund_name = _lookup_fund_name(code)
            llm_result = infer_sector_via_llm(code, fund_name) if fund_name else None
            if llm_result is not None:
                sector_name, confidence = llm_result
                _promote_and_remember(
                    PrimarySectorRecord(
                        fund_code=code,
                        sector_name=sector_name,
                        intraday_index_name=None,
                        source="precompute_llm",
                        confidence=confidence,
                        detail={"fund_name": fund_name},
                    ),
                    source="precompute_llm",
                    global_rows_by_code=global_rows_by_code,
                )
                return "ok"

        return "miss"
    except Exception as exc:
        logger.info("precompute failed for %s: %s", code, exc)
        return "error"


def run_holdings_precompute_batch(
    *,
    limit: int | None = None,
    force: bool = False,
    fund_codes: list[str] | None = None,
    sleep_seconds: float = 0.0,
) -> PrecomputeBatchResult:
    """Resolve one bounded holdings queue batch with explicit final states."""

    settings = get_settings()
    batch_limit = max(
        1,
        int(
            limit
            if limit is not None
            else settings.fund_primary_sector_precompute_holdings_batch_size
        ),
    )
    resolution_rows = list_fund_sector_resolution_statuses()
    candidates = _holdings_resolution_candidates(
        limit=batch_limit,
        force=force,
        fund_codes=fund_codes,
        statuses=resolution_rows,
    )
    result = PrecomputeBatchResult()
    started = datetime.now(timezone.utc)
    if not candidates:
        return result

    current_rows = get_fund_sector_current_primary_by_codes(set(candidates))
    global_rows = get_fund_primary_sectors_global_by_codes(set(candidates))
    fetch_codes = [
        code
        for code in candidates
        if not is_current_identity_row_executable(current_rows.get(code))
    ]
    holdings_workers = _holdings_worker_count(settings)
    evidence_by_code = _fetch_holdings_evidence_batch(
        fetch_codes,
        workers=holdings_workers,
    )
    name_by_code = {
        str(code).strip().zfill(6): str(name or "").strip()
        for code, name in _fund_name_table()
        if str(code or "").strip()
    }
    checkpoints: list[dict[str, object]] = []

    for code in candidates:
        result.processed += 1
        checked_at = datetime.now(timezone.utc)
        current = current_rows.get(code)
        if is_current_identity_row_executable(current):
            result.skipped += 1
            checkpoints.append(
                _resolution_status_row(
                    fund_code=code,
                    fund_name=name_by_code.get(code) or None,
                    status="verified",
                    reason_code="identity_already_verified",
                    detail={"source": (current or {}).get("source")},
                    previous=resolution_rows.get(code),
                    checked_at=checked_at,
                    stage=_HOLDINGS_RESOLUTION_STAGE,
                    next_retry_at=_parse_utc((current or {}).get("expires_at")),
                )
            )
            continue

        try:
            evaluation = _evaluate_holdings_resolution(
                code,
                evidence_by_code.get(
                    code,
                    {
                        "status": "unavailable",
                        "reason_codes": ["holdings_evidence_result_missing"],
                        "stocks": [],
                    },
                ),
            )
        except Exception as exc:  # noqa: BLE001 - checkpoint and continue
            logger.info("holdings resolution failed for %s: %s", code, exc)
            result.error += 1
            if len(result.errors) < 20:
                result.errors.append(f"{code}:{type(exc).__name__}")
            evaluation = _HoldingsResolutionEvaluation(
                record=None,
                resolution_status="unavailable",
                reason_code="holdings_resolution_error",
                detail={"error_type": type(exc).__name__},
            )

        retry_at: datetime | None = None
        if evaluation.record is not None:
            _promote_and_remember(
                evaluation.record,
                source="precompute_holdings",
                global_rows_by_code=global_rows,
            )
            result.ok += 1
            retry_at = checked_at + timedelta(
                days=max(
                    1,
                    int(settings.fund_primary_sector_global_holdings_ttl_days),
                )
            )
        elif evaluation.resolution_status == "research_only":
            result.research_only += 1
            result.miss += 1
        else:
            result.unavailable += 1
            result.miss += 1
        checkpoints.append(
            _resolution_status_row(
                fund_code=code,
                fund_name=name_by_code.get(code) or None,
                status=evaluation.resolution_status,
                reason_code=evaluation.reason_code,
                detail=evaluation.detail,
                previous=resolution_rows.get(code),
                checked_at=checked_at,
                stage=_HOLDINGS_RESOLUTION_STAGE,
                next_retry_at=retry_at,
            )
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    save_fund_sector_resolution_statuses(checkpoints)
    save_precompute_status(
        {
            **load_precompute_status(),
            "last_holdings_run_at": started.isoformat(),
            "holdings_finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "holdings",
            "force": force,
            "holdings_workers": holdings_workers,
            "global_count": count_fund_primary_sectors_global(),
            "verified_current_count": count_fresh_verified_fund_sector_current(),
            **resolution_coverage(),
            **result.to_dict(),
        }
    )
    return result


def run_precompute_batch(
    *,
    limit: int | None = None,
    mode: PrecomputeMode = "benchmark",
    force: bool = False,
    fund_codes: list[str] | None = None,
    sleep_seconds: float = 0.05,
) -> PrecomputeBatchResult:
    if mode == "benchmark":
        return run_bulk_profile_precompute_batch(
            limit=limit,
            force=force,
            fund_codes=fund_codes,
            sleep_seconds=max(0.0, sleep_seconds),
        )
    if mode == "holdings":
        return run_holdings_precompute_batch(
            limit=limit,
            force=force,
            fund_codes=fund_codes,
            sleep_seconds=max(0.0, sleep_seconds),
        )

    settings = get_settings()
    batch_limit = limit if limit is not None else int(settings.fund_primary_sector_precompute_batch_size)
    batch_limit = max(1, batch_limit)

    result = PrecomputeBatchResult()
    previous_status = load_precompute_status()
    try:
        previous_cursor = int(previous_status.get("candidate_cursor") or 0)
    except (TypeError, ValueError):
        previous_cursor = 0
    preload_codes = (
        [code.strip().zfill(6) for code in fund_codes if code.strip()][:batch_limit]
        if fund_codes
        else [code.zfill(6) for code, _name in _fund_name_table() if code]
    )
    global_rows_by_code = get_fund_primary_sectors_global_by_codes(set(preload_codes))
    current_rows_by_code = get_fund_sector_current_primary_by_codes(set(preload_codes))
    candidates = iter_precompute_candidates(
        limit=batch_limit,
        force=force,
        fund_codes=fund_codes,
        global_rows_by_code=global_rows_by_code,
        current_rows_by_code=current_rows_by_code,
    )
    started = datetime.now(timezone.utc)
    outcomes: dict[str, str] = {}

    for code in candidates:
        result.processed += 1
        status = precompute_fund_sector(
            code,
            mode=mode,
            force=force,
            global_rows_by_code=global_rows_by_code,
            current_rows_by_code=current_rows_by_code,
        )
        outcomes[code] = status
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

    if candidates:
        resolution_rows = list_fund_sector_resolution_statuses()
        refreshed_current = get_fund_sector_current_primary_by_codes(set(candidates))
        checked_at = datetime.now(timezone.utc)
        names = {
            str(code).strip().zfill(6): str(name or "").strip()
            for code, name in _fund_name_table()
            if str(code or "").strip()
        }
        checkpoints: list[dict[str, object]] = []
        for code in candidates:
            current = refreshed_current.get(code)
            if is_current_identity_row_executable(current):
                retry_at = _parse_utc((current or {}).get("expires_at"))
                checkpoints.append(
                    _resolution_status_row(
                        fund_code=code,
                        fund_name=names.get(code) or None,
                        status="verified",
                        reason_code=f"{mode}_identity_verified",
                        detail={"source": (current or {}).get("source")},
                        previous=resolution_rows.get(code),
                        checked_at=checked_at,
                        stage=f"{mode}_resolution",
                        next_retry_at=retry_at,
                    )
                )
                continue
            outcome = outcomes.get(code, "error")
            status = "research_only" if outcome == "ok" else "unavailable"
            if status == "research_only":
                result.research_only += 1
            else:
                result.unavailable += 1
            checkpoints.append(
                _resolution_status_row(
                    fund_code=code,
                    fund_name=names.get(code) or None,
                    status=status,
                    reason_code=(
                        f"{mode}_evidence_research_only"
                        if status == "research_only"
                        else f"{mode}_evidence_unavailable"
                    ),
                    detail={"batch_outcome": outcome},
                    previous=resolution_rows.get(code),
                    checked_at=checked_at,
                    stage=f"{mode}_resolution",
                )
            )
        save_fund_sector_resolution_statuses(checkpoints)

    universe_size = len(_fund_name_table())
    next_cursor = (
        (previous_cursor + result.processed) % universe_size
        if not fund_codes and universe_size > 0
        else previous_cursor
    )
    global_count = count_fund_primary_sectors_global()
    verified_current_count = count_fresh_verified_fund_sector_current()
    save_precompute_status(
        {
            **load_precompute_status(),
            "last_run_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "force": force,
            "global_count": global_count,
            "verified_current_count": verified_current_count,
            "candidate_cursor": next_cursor,
            "candidate_universe_size": universe_size,
            **resolution_coverage(),
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
        global_count,
    )
    return result
