"""Materialized fund-to-sector identity with point-in-time exposure evidence.

Fund codes are lookup keys, not sector evidence.  This module turns independently
observed tracking-index or holdings evidence into a fast current view while
retaining every multi-sector assessment as an immutable-style snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.database import (
    get_fund_sector_current_primary_by_codes,
    replace_fund_sector_current,
    save_fund_sector_exposure_snapshot,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord

FUND_SECTOR_IDENTITY_VERSION = "fund_sector_identity.2026-08.v1"

_HOLDINGS_SOURCES = frozenset({"holdings_infer", "precompute_holdings"})
_BENCHMARK_SOURCES = frozenset({"benchmark_index", "precompute_benchmark"})
_DIRECT_SOURCES = frozenset({"ocr_detail", "manual"})
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


def is_current_identity_row_fresh(
    row: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if not row:
        return False
    expires_at = _parse_datetime(row.get("expires_at"))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expires_at > current.astimezone(timezone.utc)


def is_current_identity_row_executable(
    row: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    return bool(
        row
        and row.get("identity_status") == "verified"
        and bool(row.get("is_primary"))
        and is_current_identity_row_fresh(row, now=now)
    )


def is_current_identity_row_reproducibly_verified(
    row: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a fresh verified row still satisfies today's evidence contract.

    Older migrations could mark an active fund's broad performance benchmark as
    verified sector identity.  The public execution gate remains backward
    compatible, while repair queues use this stricter check so those rows are
    revisited with holdings evidence instead of being skipped forever.
    """

    if not is_current_identity_row_executable(row, now=now) or row is None:
        return False
    source = str(row.get("source") or "").strip()
    if source in _DIRECT_SOURCES:
        return True

    raw_detail = row.get("detail")
    if isinstance(raw_detail, str):
        try:
            decoded = json.loads(raw_detail)
        except (TypeError, ValueError):
            decoded = None
        detail = decoded if isinstance(decoded, Mapping) else {}
    else:
        detail = raw_detail if isinstance(raw_detail, Mapping) else {}

    if source in _HOLDINGS_SOURCES:
        qualification = detail.get("qualification")
        return bool(
            isinstance(qualification, Mapping)
            and qualification.get("sector_inference_eligible") is True
            and qualification.get("research_only") is False
        )
    if source in _BENCHMARK_SOURCES:
        fund_code = str(row.get("fund_code") or "").strip().zfill(6)
        sector_name = str(row.get("sector_name") or "").strip()
        if not fund_code or not sector_name:
            return False
        return _benchmark_identity_matches(
            PrimarySectorRecord(
                fund_code=fund_code,
                sector_name=sector_name,
                intraday_index_name=None,
                source=source,
                confidence=_float_or_none(row.get("confidence")),
                detail=dict(detail),
            ),
            detail,
        )
    return False


def materialize_primary_sector_record(
    record: PrimarySectorRecord,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    detail = dict(record.detail or {})
    evaluated = _aware_utc(evaluated_at or datetime.now(timezone.utc))
    status = _identity_status(record, detail)
    metadata = _evidence_metadata(detail, evaluated=evaluated)
    score_rows = _sector_scores(record, detail)
    source_ref = _source_ref(record, detail, metadata)
    snapshot_id = _snapshot_id(
        record=record,
        status=status,
        score_rows=score_rows,
        metadata=metadata,
        source_ref=source_ref,
    )
    expires_at = evaluated + _ttl_for_source(record.source)

    snapshot_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    for sector_name, exposure_percent in score_rows:
        is_primary = sector_name == record.sector_name
        row_detail = _row_detail(
            detail,
            sector_name=sector_name,
            is_primary=is_primary,
        )
        confidence = _sector_confidence(
            record.confidence,
            exposure_percent=exposure_percent,
            is_primary=is_primary,
        )
        common = {
            "fund_code": record.fund_code,
            "sector_name": sector_name,
            "exposure_percent": exposure_percent,
            "is_primary": is_primary,
            "identity_status": status,
            "source": record.source,
            "confidence": confidence,
            "source_ref": source_ref,
            "report_period": metadata["report_period"],
            "as_of_date": metadata["as_of_date"],
            "available_at": metadata["available_at"],
            "mapping_version": FUND_SECTOR_IDENTITY_VERSION,
            "detail": row_detail,
        }
        snapshot_rows.append(
            {
                **common,
                "evaluated_at": evaluated.isoformat(),
            }
        )
        current_rows.append(
            {
                **common,
                "evidence_snapshot_id": snapshot_id,
                "resolved_at": evaluated.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        )

    save_fund_sector_exposure_snapshot(
        snapshot_id=snapshot_id,
        rows=snapshot_rows,
    )
    current_replaced = _replace_current_if_stronger(record.fund_code, current_rows)
    return {
        "snapshot_id": snapshot_id,
        "identity_status": status,
        "current_replaced": current_replaced,
        "rows": current_rows,
    }


def materialize_holdings_sector_assessment(
    *,
    fund_code: str,
    sector_clue: Mapping[str, Any],
    evidence_payload: Mapping[str, Any] | None,
    source: str = "holdings_infer",
    evaluated_at: datetime | None = None,
) -> dict[str, Any] | None:
    scores = sector_clue.get("scores")
    if not isinstance(scores, Mapping) or not scores:
        return None
    primary_sector = str(sector_clue.get("sector_name") or "").strip()
    if not primary_sector:
        numeric_scores = {
            str(key): _float_or_none(value)
            for key, value in scores.items()
            if str(key).strip()
        }
        numeric_scores = {key: value for key, value in numeric_scores.items() if value is not None}
        if not numeric_scores:
            return None
        primary_sector = max(numeric_scores, key=lambda key: numeric_scores[key])

    payload = evidence_payload if isinstance(evidence_payload, Mapping) else {}
    qualification = (
        dict(sector_clue.get("qualification"))
        if isinstance(sector_clue.get("qualification"), Mapping)
        else {}
    )
    detail = {
        "scores": {str(key): value for key, value in scores.items()},
        "evidence": list(sector_clue.get("evidence") or [])[:16],
        "coverage": dict(sector_clue.get("coverage") or {}),
        "qualification": qualification,
        "snapshot_hash": payload.get("snapshot_hash"),
        "report_period": payload.get("report_period"),
        "as_of_date": payload.get("as_of"),
        "available_at": payload.get("available_at"),
        "association_evaluated_at": payload.get("association_evaluated_at"),
        "holdings_decision_at": payload.get("holdings_decision_at"),
    }
    dominant = _float_or_none(scores.get(primary_sector)) or 0.0
    confidence = min(0.92, round(dominant / 100.0 + 0.5, 2))
    return materialize_primary_sector_record(
        PrimarySectorRecord(
            fund_code=fund_code.strip().zfill(6),
            sector_name=primary_sector,
            intraday_index_name=None,
            source=source,
            confidence=confidence,
            detail=detail,
        ),
        evaluated_at=evaluated_at,
    )


def current_identity_rows_for_api(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        detail = row.get("detail")
        if isinstance(detail, str):
            try:
                decoded = json.loads(detail)
            except (TypeError, ValueError):
                decoded = None
            row["detail"] = decoded if isinstance(decoded, dict) else None
        row["fresh"] = is_current_identity_row_fresh(row, now=now)
        row["effective_identity_status"] = (
            row.get("identity_status") if row["fresh"] else "stale"
        )
        result.append(row)
    return result


def _replace_current_if_stronger(
    fund_code: str,
    rows: list[dict[str, Any]],
) -> bool:
    primary = next((row for row in rows if row.get("is_primary")), None)
    if primary is None:
        return False
    existing = get_fund_sector_current_primary_by_codes([fund_code]).get(
        fund_code.strip().zfill(6)
    )
    if not _should_replace_current(existing, primary):
        return False
    replace_fund_sector_current(fund_code=fund_code, rows=rows)
    return True


def _should_replace_current(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> bool:
    if existing is None:
        return True
    old_verified = existing.get("identity_status") == "verified"
    new_verified = candidate.get("identity_status") == "verified"
    if old_verified and not new_verified:
        return False
    if new_verified and not old_verified:
        return True
    if not is_current_identity_row_fresh(existing):
        return new_verified or not old_verified
    old_priority = _SOURCE_PRIORITY.get(str(existing.get("source") or ""), 0)
    new_priority = _SOURCE_PRIORITY.get(str(candidate.get("source") or ""), 0)
    return new_priority >= old_priority


def _identity_status(record: PrimarySectorRecord, detail: Mapping[str, Any]) -> str:
    if record.source in _DIRECT_SOURCES:
        return "verified"
    if record.source in _HOLDINGS_SOURCES:
        qualification = detail.get("qualification")
        return (
            "verified"
            if isinstance(qualification, Mapping)
            and qualification.get("sector_inference_eligible") is True
            and qualification.get("research_only") is False
            else "pending"
        )
    if record.source in _BENCHMARK_SOURCES:
        return "verified" if _benchmark_identity_matches(record, detail) else "pending"
    return "pending"


def _benchmark_identity_matches(
    record: PrimarySectorRecord,
    detail: Mapping[str, Any],
) -> bool:
    if detail.get("price_proxy_eligible") is not True:
        return False
    benchmark_text = str(detail.get("benchmark_text") or "").strip()
    index_code = str(detail.get("index_code") or "").strip().upper()
    if not benchmark_text or not index_code:
        return False
    from app.services.fund_benchmark_sector import resolve_sector_from_benchmark

    resolved = resolve_sector_from_benchmark(benchmark_text)
    if resolved is None:
        return False
    sector_name, _intraday_name, match = resolved
    return bool(
        sector_name == record.sector_name
        and str(match.index_code or "").strip().upper() == index_code
    )


def _sector_scores(
    record: PrimarySectorRecord,
    detail: Mapping[str, Any],
) -> list[tuple[str, float | None]]:
    raw_scores = detail.get("scores")
    scores: dict[str, float] = {}
    if isinstance(raw_scores, Mapping):
        for raw_sector, raw_value in raw_scores.items():
            sector = str(raw_sector or "").strip()
            value = _float_or_none(raw_value)
            if sector and value is not None and value > 0:
                scores[sector] = value
    if record.sector_name not in scores:
        scores[record.sector_name] = 0.0
    ordered = sorted(
        scores.items(),
        key=lambda item: (
            item[0] != record.sector_name,
            -item[1],
            item[0],
        ),
    )
    return [(sector, value if value > 0 else None) for sector, value in ordered]


def _evidence_metadata(
    detail: Mapping[str, Any],
    *,
    evaluated: datetime,
) -> dict[str, str | None]:
    evidence = detail.get("evidence")
    first = evidence[0] if isinstance(evidence, list) and evidence and isinstance(evidence[0], Mapping) else {}
    return {
        "report_period": _text(detail.get("report_period") or first.get("report_period")),
        "as_of_date": _text(
            detail.get("as_of_date") or detail.get("as_of") or first.get("as_of")
        ),
        "available_at": _text(
            detail.get("available_at")
            or first.get("available_at")
            or evaluated.isoformat()
        ),
        "snapshot_hash": _text(
            detail.get("snapshot_hash") or first.get("snapshot_hash")
        ),
    }


def _source_ref(
    record: PrimarySectorRecord,
    detail: Mapping[str, Any],
    metadata: Mapping[str, str | None],
) -> str | None:
    direct = _text(metadata.get("snapshot_hash") or detail.get("index_code"))
    if direct:
        return direct
    evidence = detail.get("evidence")
    refs: list[str] = []
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            for key in ("theme_ref_id", "industry_ref_id", "snapshot_hash"):
                value = _text(item.get(key))
                if value:
                    refs.append(value)
    if not refs:
        return None
    encoded = "|".join(sorted(set(refs)))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_id(
    *,
    record: PrimarySectorRecord,
    status: str,
    score_rows: list[tuple[str, float | None]],
    metadata: Mapping[str, str | None],
    source_ref: str | None,
) -> str:
    payload = {
        "fund_code": record.fund_code,
        "source": record.source,
        "status": status,
        "scores": score_rows,
        "source_ref": source_ref,
        "report_period": metadata.get("report_period"),
        "as_of_date": metadata.get("as_of_date"),
        "available_at": metadata.get("available_at"),
        "mapping_version": FUND_SECTOR_IDENTITY_VERSION,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row_detail(
    detail: Mapping[str, Any],
    *,
    sector_name: str,
    is_primary: bool,
) -> dict[str, Any]:
    evidence = detail.get("evidence")
    sector_evidence = [
        dict(item)
        for item in evidence
        if isinstance(item, Mapping) and str(item.get("theme") or "") == sector_name
    ] if isinstance(evidence, list) else []
    return {
        "is_primary": is_primary,
        "coverage": dict(detail.get("coverage") or {}),
        "qualification": dict(detail.get("qualification") or {}),
        "evidence": sector_evidence[:8],
        "index_code": detail.get("index_code"),
        "index_name": detail.get("index_name"),
        "benchmark_text": detail.get("benchmark_text"),
        "benchmark_text_kind": detail.get("benchmark_text_kind"),
        "price_proxy_eligible": detail.get("price_proxy_eligible"),
    }


def _sector_confidence(
    base: float | None,
    *,
    exposure_percent: float | None,
    is_primary: bool,
) -> float | None:
    if is_primary or base is None or exposure_percent is None:
        return base
    return round(min(float(base), 0.5 + max(0.0, exposure_percent) / 100.0), 4)


def _ttl_for_source(source: str) -> timedelta:
    settings = get_settings()
    if source in _HOLDINGS_SOURCES:
        days = max(1, int(settings.fund_primary_sector_global_holdings_ttl_days))
    elif source in _DIRECT_SOURCES:
        days = max(365, int(settings.fund_primary_sector_global_holdings_ttl_days))
    else:
        days = max(1, int(settings.fund_primary_sector_global_benchmark_ttl_days))
    return timedelta(days=days)


def _parse_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "FUND_SECTOR_IDENTITY_VERSION",
    "current_identity_rows_for_api",
    "is_current_identity_row_executable",
    "is_current_identity_row_fresh",
    "is_current_identity_row_reproducibly_verified",
    "materialize_holdings_sector_assessment",
    "materialize_primary_sector_record",
]
