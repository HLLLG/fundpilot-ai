from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from app.services.decision_repository import canonical_hash
from app.services.fund_benchmark_sector import parse_benchmark_index


BENCHMARK_MAPPING_SCHEMA_VERSION = "fund_benchmark_mapping.v1"

_BENCHMARK_SOURCES = {"benchmark_index", "precompute_benchmark"}
_WEIGHT_RE = re.compile(r"(?:[×xX*])\s*(\d+(?:\.\d+)?)\s*[%％]")
_SPLIT_RE = re.compile(r"[+＋]")
_CASH_TOKENS = ("存款", "活期", "货币市场工具")
_FORMAL_SOURCE_KINDS = {"live_fund_disclosure", "verified_fund_contract"}


def freeze_report_benchmark_specs(
    report: Mapping[str, Any],
    *,
    decision_kind: str,
    user_id: int,
    connection: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Attach only point-in-time benchmark evidence already known at decision time.

    This function deliberately performs no network lookup.  A cache row whose
    availability timestamp is later than the report is ignored, preventing a
    current benchmark from being projected backwards onto an older decision.
    """

    enriched = dict(report)
    facts_key = "analysis_facts" if decision_kind == "daily" else "discovery_facts"
    recommendation_key = (
        "fund_recommendations" if decision_kind == "daily" else "recommendations"
    )
    facts = dict(enriched.get(facts_key) or {})
    decision_at = _canonical_datetime(enriched.get("created_at"))
    specs: dict[str, dict[str, Any]] = {}
    mappings: list[dict[str, Any]] = []
    for code in _recommendation_codes(enriched.get(recommendation_key)):
        spec, mapping = freeze_fund_benchmark_spec(
            fund_code=code,
            decision_at=decision_at,
            user_id=user_id,
            connection=connection,
        )
        specs[code] = spec
        if mapping is not None:
            mappings.append(mapping)
    facts["benchmark_specs"] = specs
    facts["benchmark_contract"] = {
        "schema_version": BENCHMARK_MAPPING_SCHEMA_VERSION,
        "lookup_policy": "cached_point_in_time_only",
        "formal_excess_policy": "complete_fund_contract_only",
        "reference_policy": "tracked_index_and_proxy_never_formal",
        "frozen_count": len(mappings),
        "unavailable_count": sum(
            1 for spec in specs.values() if spec.get("tier") == "unavailable"
        ),
    }
    enriched[facts_key] = facts
    return enriched, mappings


def freeze_fund_benchmark_spec(
    *,
    fund_code: str,
    decision_at: str,
    user_id: int,
    connection: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    code = _fund_code(fund_code)
    if code is None:
        return _unavailable("invalid_fund_code"), None
    evidence = _cached_benchmark_evidence(
        connection,
        user_id=user_id,
        fund_code=code,
        decision_at=decision_at,
    )
    if evidence is None:
        return _unavailable("point_in_time_benchmark_mapping_unavailable"), None

    detail = _detail(evidence.get("detail"))
    benchmark_text = str(detail.get("benchmark_text") or "").strip()
    benchmark_text_kind = str(detail.get("benchmark_text_kind") or "unknown").strip()
    benchmark_source_kind = str(
        detail.get("benchmark_text_source_kind") or "unknown"
    ).strip()
    benchmark_text_truncated = detail.get("benchmark_text_truncated") is not False
    index_code = str(detail.get("index_code") or "").strip()
    index_name = str(
        detail.get("index_name")
        or evidence.get("intraday_index_name")
        or evidence.get("sector_name")
        or ""
    ).strip()
    available_at = str(evidence.get("available_at") or "")
    source_ref = str(evidence.get("source_ref") or "")

    if (
        benchmark_text
        and benchmark_text_kind == "performance_benchmark"
        and benchmark_source_kind in _FORMAL_SOURCE_KINDS
        and not benchmark_text_truncated
    ):
        components, structurally_complete = parse_fund_contract_components(
            benchmark_text,
            fallback_index_code=index_code or None,
            fallback_index_name=index_name or None,
        )
        completeness = "complete" if structurally_complete else "incomplete"
        mapping = _mapping(
            fund_code=code,
            benchmark_kind="official_contract",
            completeness=completeness,
            benchmark_name=benchmark_text[:500],
            benchmark_code=(
                str(components[0].get("benchmark_code"))
                if len(components) == 1 and components[0].get("benchmark_code")
                else None
            ),
            valid_from=available_at[:10],
            source=str(evidence.get("source") or "cached_benchmark"),
            source_ref=source_ref,
            available_at=available_at,
            confidence=evidence.get("confidence"),
            components=components,
            raw_text=benchmark_text,
        )
        spec = {
            **mapping,
            "tier": "fund_contract_exact",
            "status": completeness,
            "formal_excess_eligible": structurally_complete,
            "reason": (
                None if structurally_complete else "fund_contract_components_incomplete"
            ),
        }
        return spec, mapping

    if not index_code and benchmark_text:
        parsed_tracking = parse_benchmark_index(benchmark_text)
        if parsed_tracking is not None:
            index_code = parsed_tracking.index_code
            index_name = parsed_tracking.index_name or index_name

    if index_code:
        component = _index_component(
            index_code=index_code,
            index_name=index_name or index_code,
            weight_percent=100.0,
        )
        mapping = _mapping(
            fund_code=code,
            benchmark_kind="tracking_index",
            completeness="complete",
            benchmark_name=benchmark_text or index_name or index_code,
            benchmark_code=index_code,
            valid_from=available_at[:10],
            source=str(evidence.get("source") or "cached_benchmark"),
            source_ref=source_ref,
            available_at=available_at,
            confidence=evidence.get("confidence"),
            components=[component],
            raw_text=benchmark_text or None,
        )
        return {
            **mapping,
            "tier": "tracked_index_exact",
            "status": "complete",
            "formal_excess_eligible": False,
            "reason": "tracking_index_is_reference_only",
        }, mapping

    return _unavailable("cached_benchmark_detail_incomplete"), None


def parse_fund_contract_components(
    benchmark_text: str,
    *,
    fallback_index_code: str | None = None,
    fallback_index_name: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse a conservative subset of Chinese fund performance benchmarks.

    A component is considered complete only when its identity and weight are
    explicit and all weights total 100%.  Unknown legs are retained in the
    frozen contract so the evaluator can explain why formal excess is missing.
    """

    text = str(benchmark_text or "").strip()
    if not text:
        return [], False
    segments = [part.strip() for part in _SPLIT_RE.split(text) if part.strip()]
    if not segments:
        return [], False

    components: list[dict[str, Any]] = []
    recognized = True
    for index, segment in enumerate(segments):
        weight_match = _WEIGHT_RE.search(segment)
        if weight_match is not None:
            weight = float(weight_match.group(1))
        elif len(segments) == 1:
            weight = 100.0
        else:
            weight = None

        match = parse_benchmark_index(segment)
        code = match.index_code if match is not None else None
        name = match.index_name if match is not None else None
        if (
            code is None
            and index == 0
            and fallback_index_code
            and fallback_index_name
            and _normalized_name(fallback_index_name) in _normalized_name(segment)
        ):
            code = fallback_index_code
            name = fallback_index_name
        if code:
            components.append(
                _index_component(
                    index_code=code,
                    index_name=name or fallback_index_name or _component_name(segment),
                    weight_percent=weight,
                )
            )
            recognized = recognized and weight is not None
            continue

        if any(token in segment for token in _CASH_TOKENS):
            components.append(
                {
                    "component_id": f"cash-rate:{index}",
                    "component_type": "cash_rate",
                    "name": _component_name(segment),
                    "weight_percent": weight,
                    "source_symbol": None,
                    "max_lag_calendar_days": 7,
                }
            )
            recognized = recognized and weight is not None
            continue
        recognized = False
        components.append(
            {
                "component_id": f"unknown:{index}",
                "component_type": "unknown",
                "name": _component_name(segment),
                "weight_percent": weight,
                "source_symbol": None,
            }
        )

    weights = [component.get("weight_percent") for component in components]
    weight_complete = all(isinstance(value, (int, float)) for value in weights)
    weight_total = sum(float(value) for value in weights if value is not None)
    return components, bool(
        recognized and weight_complete and abs(weight_total - 100.0) <= 0.1
    )


def _mapping(
    *,
    fund_code: str,
    benchmark_kind: str,
    completeness: str,
    benchmark_name: str,
    benchmark_code: str | None,
    valid_from: str,
    source: str,
    source_ref: str,
    available_at: str,
    confidence: object,
    components: list[dict[str, Any]],
    raw_text: str | None,
) -> dict[str, Any]:
    material = {
        "schema_version": BENCHMARK_MAPPING_SCHEMA_VERSION,
        "fund_code": fund_code,
        "benchmark_kind": benchmark_kind,
        "completeness": completeness,
        "benchmark_name": benchmark_name,
        "benchmark_code": benchmark_code,
        "valid_from": valid_from,
        "valid_to": None,
        "source": source,
        "source_ref": source_ref,
        "available_at": available_at,
        "confidence": confidence,
        "components": components,
        "raw_contract_text": raw_text,
    }
    digest = canonical_hash(material)[:32]
    return {**material, "mapping_id": f"fbm_{digest}"}


def _cached_benchmark_evidence(
    connection: Any,
    *,
    user_id: int,
    fund_code: str,
    decision_at: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    local = connection.execute(
        "SELECT fund_code, sector_name, intraday_index_name, source, confidence, "
        "detail, updated_at FROM fund_primary_sectors "
        "WHERE userId = ? AND fund_code = ?",
        (user_id, fund_code),
    ).fetchone()
    if local is not None:
        row = _row(local)
        row["available_at"] = row.get("updated_at")
        row["source_ref"] = (
            f"fund_primary_sectors:{user_id}:{fund_code}:{row.get('updated_at') or ''}"
        )
        candidates.append(row)
    global_row = connection.execute(
        "SELECT fund_code, sector_name, intraday_index_name, source, confidence, "
        "detail, resolved_at FROM fund_primary_sectors_global WHERE fund_code = ?",
        (fund_code,),
    ).fetchone()
    if global_row is not None:
        row = _row(global_row)
        row["available_at"] = row.get("resolved_at")
        row["source_ref"] = (
            f"fund_primary_sectors_global:{fund_code}:{row.get('resolved_at') or ''}"
        )
        candidates.append(row)

    decision = _parse_datetime(decision_at)
    eligible: list[dict[str, Any]] = []
    for row in candidates:
        if str(row.get("source") or "") not in _BENCHMARK_SOURCES:
            continue
        available = _parse_datetime(row.get("available_at"))
        if available is None or available > decision:
            continue
        eligible.append(row)
    eligible.sort(
        key=lambda row: (
            1 if str(row.get("source")) == "benchmark_index" else 0,
            str(row.get("available_at") or ""),
        ),
        reverse=True,
    )
    return eligible[0] if eligible else None


def _index_component(
    *, index_code: str, index_name: str, weight_percent: float | None
) -> dict[str, Any]:
    return {
        "component_id": f"index:{index_code}",
        "component_type": "index",
        "name": index_name,
        "benchmark_code": index_code,
        "source_symbol": index_code,
        "weight_percent": weight_percent,
        "max_lag_calendar_days": 7,
    }


def _component_name(segment: str) -> str:
    cleaned = _WEIGHT_RE.sub("", segment).strip(" ()（）")
    return cleaned[:160] or "unknown"


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s（）()·\-—_]", "", str(value or "")).replace("收益率", "")


def _recommendation_codes(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = _fund_code(row.get("fund_code"))
        if code and code not in seen:
            seen.add(code)
            result.append(code)
    return result


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_MAPPING_SCHEMA_VERSION,
        "tier": "unavailable",
        "status": "unavailable",
        "formal_excess_eligible": False,
        "mapping_id": None,
        "reason": reason,
        "components": [],
    }


def _detail(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _row(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return dict(value)  # sqlite3.Row and mapping-compatible DB rows


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        return None
    code = text.zfill(6)
    return code if code != "000000" else None


def _canonical_datetime(value: object) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError("report created_at must be an ISO timestamp")
    return parsed.isoformat()


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        if len(text) >= 10:
            try:
                parsed = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "BENCHMARK_MAPPING_SCHEMA_VERSION",
    "freeze_fund_benchmark_spec",
    "freeze_report_benchmark_specs",
    "parse_fund_contract_components",
]
