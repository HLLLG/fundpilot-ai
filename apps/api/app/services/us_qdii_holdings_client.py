"""QDII holdings compatibility adapter over the central PIT snapshot store."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.fund_holdings_snapshot_repository import (
    resolve_fund_holdings_snapshot_at_decision,
)

_US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,31}$")


def classify_holding_market(code: str) -> str:
    """Infer the quote market without changing the disclosed identifier."""

    raw = str(code or "").strip().upper()
    if not raw:
        return "unknown"
    if _US_TICKER_RE.fullmatch(raw):
        return "us"
    if raw.isdigit() and len(raw) == 5:
        return "hk"
    if raw.isdigit() and len(raw) == 6:
        return "cn"
    return "unknown"


def normalize_holding_code(code: str, market: str) -> str:
    """Normalize only for downstream quote lookup, not snapshot identity."""

    raw = str(code or "").strip().upper()
    if market == "us":
        return raw
    digits = re.sub(r"\D", "", raw)
    if market == "hk":
        return digits.zfill(5)[-5:]
    if market == "cn":
        return digits.zfill(6)[-6:]
    return raw


def get_fund_holdings(
    fund_code: str,
    *,
    force_refresh: bool = False,
    decision_at: str | datetime | None = None,
) -> dict[str, Any] | None:
    """Read one PIT disclosure and expose the legacy QDII holdings shape.

    A current store miss may use the bounded central live resolver. Historical
    calls are store-only.  A stale disclosure is returned as explicit
    ``status=stale`` / ``qualified=false`` evidence rather than a silent cache
    fallback.
    """

    resolution = resolve_fund_holdings_snapshot_at_decision(
        fund_code,
        decision_at=decision_at,
        force_refresh=force_refresh,
    )
    snapshot = resolution.get("snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("qualified") is not True:
        return None
    return _adapt_snapshot(snapshot, resolution=resolution)


def load_qdii_holdings_batch(
    fund_codes: list[str],
    *,
    force_refresh: bool = False,
    decision_at: str | datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Load QDII disclosures while preserving the legacy code-to-payload API."""

    out: dict[str, dict[str, Any]] = {}
    for raw_code in fund_codes:
        code = str(raw_code).strip().zfill(6)
        payload = get_fund_holdings(
            code,
            force_refresh=force_refresh,
            decision_at=decision_at,
        )
        if payload is not None:
            out[code] = payload
    return out


def _adapt_snapshot(
    snapshot: Mapping[str, Any],
    *,
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    raw_holdings = snapshot.get("holdings")
    holdings: list[dict[str, Any]] = []
    if isinstance(raw_holdings, list):
        for raw in raw_holdings:
            if not isinstance(raw, Mapping):
                continue
            code = str(raw.get("security_code") or "").strip().upper()
            market = classify_holding_market(code)
            if market == "unknown":
                continue
            try:
                weight = float(raw.get("weight_percent"))
            except (TypeError, ValueError):
                continue
            if weight <= 0:
                continue
            holdings.append(
                {
                    "code": normalize_holding_code(code, market),
                    "name": str(raw.get("security_name") or "").strip(),
                    "weight": weight,
                    "market": market,
                    "rank": raw.get("rank"),
                }
            )

    freshness = snapshot.get("freshness")
    freshness_label = (
        str(freshness.get("label")) if isinstance(freshness, Mapping) else "unknown"
    )
    stale = freshness_label == "stale"
    coverage = snapshot.get("coverage")
    coverage_known = _coverage_percent(coverage) is not None
    reason_codes = (
        ["holdings_snapshot_stale"]
        if stale
        else [] if coverage_known else ["holdings_coverage_unknown"]
    )
    snapshot_qualification = snapshot.get("qualification")
    nowcast_eligible = bool(
        isinstance(snapshot_qualification, Mapping)
        and snapshot_qualification.get("nowcast_eligible") is True
        and not stale
        and coverage_known
    )
    adapter_qualified = not stale and coverage_known
    return {
        "fund_code": str(snapshot.get("fund_code") or ""),
        "holdings": holdings,
        # Backward-compatible field plus explicit unambiguous fields.
        "report_date": snapshot.get("as_of_date"),
        "report_period": snapshot.get("report_period"),
        "as_of": snapshot.get("as_of_date"),
        "available_at": snapshot.get("available_at"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "coverage": coverage if isinstance(coverage, Mapping) else None,
        "freshness": freshness,
        "fetched_at": _aware_fetched_at(resolution, snapshot),
        "status": "stale" if stale else "qualified" if adapter_qualified else "unavailable",
        "qualified": adapter_qualified,
        "reason_codes": reason_codes,
        "source": resolution.get("source"),
        "qualification": {
            "snapshot_qualified": True,
            "pit_eligible": True,
            "stale": stale,
            "nowcast_eligible": nowcast_eligible,
            "full_fund_reference_eligible": nowcast_eligible,
            "coverage_known": coverage_known,
            "disclosed_contribution_research_eligible": bool(holdings)
            and adapter_qualified,
            "reason_codes": reason_codes,
        },
    }


def _aware_fetched_at(
    resolution: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> str:
    candidates: list[object] = []
    record = resolution.get("record")
    if isinstance(record, Mapping):
        candidates.append(record.get("first_observed_at"))
    audit = snapshot.get("audit")
    if isinstance(audit, Mapping):
        repository = audit.get("snapshot_repository")
        if isinstance(repository, Mapping):
            candidates.append(repository.get("first_observed_at"))
        candidates.append(audit.get("fetched_at"))
    for value in candidates:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.isoformat()
    # This is adapter observation metadata only; it never substitutes for the
    # disclosure's available_at and does not participate in snapshot identity.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coverage_percent(value: object) -> float | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("portfolio_weight_coverage_percent")
    if raw is None:
        raw = value.get("weight_sum_percent")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= 100.01 else None


__all__ = [
    "classify_holding_market",
    "get_fund_holdings",
    "load_qdii_holdings_batch",
    "normalize_holding_code",
]
