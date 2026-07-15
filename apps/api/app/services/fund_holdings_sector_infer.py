"""Infer a research-only sector label from a PIT holdings disclosure.

The disclosure is selected centrally by ``fund_holdings_snapshot``.  Industry
labels are fetched only for a current request because the provider exposes no
historical publication clock for those labels; historical replay therefore
fails closed instead of attaching today's industries to an old decision.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.fund_holdings_snapshot import CN_TZ
from app.services.fund_holdings_snapshot_repository import (
    resolve_fund_holdings_snapshot_at_decision,
)
from app.services.fund_industry_theme_map import map_industry_to_theme_label

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 45
_MIN_SCORE_PERCENT = 8.0
_MAX_STOCKS_WITH_INDUSTRY = 8
_CURRENT_DECISION_SKEW_SECONDS = 30 * 60
_MIN_CLASSIFIED_NAV_PERCENT = 20.0
_MIN_CLASSIFIED_DISCLOSED_RATIO = 0.60
_MIN_THEME_DOMINANCE_RATIO = 0.60


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@dataclass(frozen=True)
class HoldingStockRow:
    name: str
    weight: float
    industry: str | None = None
    stock_code: str | None = None
    snapshot_hash: str | None = None
    report_period: str | None = None
    as_of: str | None = None
    available_at: str | None = None
    coverage: dict[str, Any] | None = None
    industry_available_at: str | None = None
    industry_source: str | None = None
    industry_ref_id: str | None = None
    industry_pit_qualified: bool = False


def fetch_portfolio_stocks_with_industry(
    fund_code: str,
    *,
    decision_at: str | datetime | None = None,
    force_refresh: bool = False,
) -> list[HoldingStockRow]:
    """Compatibility list API backed by the central PIT snapshot."""

    result = fetch_portfolio_stocks_with_industry_evidence(
        fund_code,
        decision_at=decision_at,
        force_refresh=force_refresh,
    )
    rows = result.get("stocks")
    return list(rows) if isinstance(rows, list) else []


def fetch_portfolio_stocks_with_industry_evidence(
    fund_code: str,
    *,
    decision_at: str | datetime | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return selected stocks plus disclosure lineage and eligibility."""

    resolution = resolve_fund_holdings_snapshot_at_decision(
        fund_code,
        decision_at=decision_at,
        force_refresh=force_refresh,
    )
    decision = _aware_decision(resolution.get("decision_at"))
    snapshot = resolution.get("snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("qualified") is not True:
        return _sector_payload(
            resolution=resolution,
            snapshot=snapshot if isinstance(snapshot, Mapping) else None,
            stocks=[],
            status=str(resolution.get("status") or "unavailable"),
            reasons=list(resolution.get("reason_codes") or []),
        )

    freshness = snapshot.get("freshness")
    freshness_label = (
        str(freshness.get("label")) if isinstance(freshness, Mapping) else "unknown"
    )
    if freshness_label == "stale":
        return _sector_payload(
            resolution=resolution,
            snapshot=snapshot,
            stocks=[],
            status="stale",
            reasons=["holdings_snapshot_stale"],
        )
    if decision is None or not _is_current_decision(decision):
        return _sector_payload(
            resolution=resolution,
            snapshot=snapshot,
            stocks=[],
            status="unavailable",
            reasons=["historical_industry_enrichment_disallowed"],
        )

    raw_holdings = snapshot.get("holdings")
    selected = _select_disclosed_holdings(raw_holdings)
    enriched = _fetch_current_industries(selected)
    metadata = _snapshot_metadata(snapshot)
    stocks: list[HoldingStockRow] = []
    for row in selected:
        code = str(row.get("security_code") or "").strip()
        industry_fields = _industry_evidence_fields(
            enriched.get(code),
            decision=decision,
        )
        stocks.append(
            HoldingStockRow(
                name=str(row.get("security_name") or "").strip(),
                weight=float(row["weight_percent"]),
                stock_code=code or None,
                **industry_fields,
                **metadata,
            )
        )
    has_industry = any(row.industry for row in stocks)
    return _sector_payload(
        resolution=resolution,
        snapshot=snapshot,
        stocks=stocks,
        status="qualified" if has_industry else "unavailable",
        reasons=[] if has_industry else ["current_industry_unavailable"],
    )


def infer_sector_from_portfolio_stocks(
    fund_code: str,
    stocks: list[HoldingStockRow],
) -> tuple[str, dict[str, float], list[dict[str, Any]]] | None:
    """Weight mapped industries and retain PIT lineage in every evidence row."""

    del fund_code
    assessment = assess_sector_from_portfolio_stocks(stocks)
    coverage = assessment.get("coverage")
    dominant_mass = (
        coverage.get("dominant_theme_mass_percent")
        if isinstance(coverage, Mapping)
        else None
    )
    if assessment.get("sector_name") is None or not isinstance(
        dominant_mass, (int, float)
    ) or dominant_mass < _MIN_SCORE_PERCENT:
        return None
    return (
        str(assessment["sector_name"]),
        dict(assessment.get("scores") or {}),
        list(assessment.get("evidence") or []),
    )


def assess_sector_from_portfolio_stocks(
    stocks: list[HoldingStockRow],
) -> dict[str, Any]:
    """Build a research clue and a separate primary-sector eligibility gate."""

    scores: dict[str, float] = {}
    evidence: list[dict[str, Any]] = []
    disclosed_mass_candidates: list[float] = []
    for row in stocks:
        if row.weight <= 0:
            continue
        if isinstance(row.coverage, Mapping):
            raw_coverage = row.coverage.get("portfolio_weight_coverage_percent")
            if raw_coverage is None:
                raw_coverage = row.coverage.get("weight_sum_percent")
            try:
                coverage_value = float(raw_coverage)
            except (TypeError, ValueError):
                coverage_value = 0.0
            if 0 < coverage_value <= 100.01:
                disclosed_mass_candidates.append(coverage_value)
        theme = map_industry_to_theme_label(row.industry)
        if not theme:
            continue
        scores[theme] = scores.get(theme, 0.0) + row.weight
        evidence.append(
            {
                "stock": row.name,
                "stock_code": row.stock_code,
                "weight": row.weight,
                "industry": row.industry,
                "theme": theme,
                "snapshot_hash": row.snapshot_hash,
                "report_period": row.report_period,
                "as_of": row.as_of,
                "available_at": row.available_at,
                "coverage": row.coverage,
                "research_only": True,
                "industry_available_at": row.industry_available_at,
                "industry_source": row.industry_source,
                "industry_ref_id": row.industry_ref_id,
                "industry_pit_qualified": row.industry_pit_qualified,
            }
        )

    disclosed_mass = max(disclosed_mass_candidates, default=0.0)
    classified_mass = round(sum(scores.values()), 8)
    if not scores:
        return {
            "status": "unavailable",
            "sector_name": None,
            "scores": {},
            "evidence": [],
            "coverage": {
                "disclosed_mass_percent": disclosed_mass or None,
                "classified_mass_percent": 0.0,
                "classified_disclosed_ratio": None,
                "dominant_theme_mass_percent": 0.0,
                "dominant_theme_ratio": None,
            },
            "qualification": {
                "research_clue_available": False,
                "sector_inference_eligible": False,
                "industry_pit_qualified": False,
                "coverage_qualified": False,
                "dominance_qualified": False,
                "research_only": True,
                "reason_codes": ["mapped_industry_evidence_missing"],
            },
        }
    sector_name = max(scores, key=lambda key: scores[key])
    dominant_mass = scores[sector_name]
    classified_ratio = (
        classified_mass / disclosed_mass if disclosed_mass > 0 else None
    )
    dominance_ratio = dominant_mass / classified_mass if classified_mass > 0 else None
    pit_qualified = bool(evidence) and all(
        item.get("industry_pit_qualified") is True for item in evidence
    )
    coverage_qualified = bool(
        disclosed_mass > 0
        and classified_mass >= _MIN_CLASSIFIED_NAV_PERCENT
        and classified_ratio is not None
        and classified_ratio >= _MIN_CLASSIFIED_DISCLOSED_RATIO
    )
    dominance_qualified = bool(
        dominant_mass >= _MIN_SCORE_PERCENT
        and dominance_ratio is not None
        and dominance_ratio >= _MIN_THEME_DOMINANCE_RATIO
    )
    reasons: list[str] = []
    if not pit_qualified:
        reasons.append("industry_evidence_not_pit_qualified")
    if not coverage_qualified:
        reasons.append("industry_classification_coverage_insufficient")
    if not dominance_qualified:
        reasons.append("industry_theme_dominance_insufficient")
    eligible = not reasons
    return {
        "status": "qualified" if eligible else "research_only",
        "sector_name": sector_name,
        "scores": dict(sorted(scores.items())),
        "evidence": evidence,
        "coverage": {
            "disclosed_mass_percent": disclosed_mass or None,
            "classified_mass_percent": round(classified_mass, 8),
            "classified_disclosed_ratio": (
                round(classified_ratio, 8) if classified_ratio is not None else None
            ),
            "dominant_theme_mass_percent": round(dominant_mass, 8),
            "dominant_theme_ratio": (
                round(dominance_ratio, 8) if dominance_ratio is not None else None
            ),
            "minimum_classified_nav_percent": _MIN_CLASSIFIED_NAV_PERCENT,
            "minimum_classified_disclosed_ratio": _MIN_CLASSIFIED_DISCLOSED_RATIO,
            "minimum_theme_dominance_ratio": _MIN_THEME_DOMINANCE_RATIO,
        },
        "qualification": {
            "research_clue_available": True,
            "sector_inference_eligible": eligible,
            "industry_pit_qualified": pit_qualified,
            "coverage_qualified": coverage_qualified,
            "dominance_qualified": dominance_qualified,
            "research_only": not eligible,
            "reason_codes": reasons,
        },
    }


def _select_disclosed_holdings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            weight = float(raw.get("weight_percent"))
        except (TypeError, ValueError):
            continue
        code = str(raw.get("security_code") or "").strip()
        if not code or weight <= 0:
            continue
        rows.append({**dict(raw), "weight_percent": weight})
    rows.sort(
        key=lambda row: (
            -float(row["weight_percent"]),
            int(row["rank"]) if isinstance(row.get("rank"), int) else 10**9,
            str(row.get("security_code") or ""),
        )
    )
    return rows[:_MAX_STOCKS_WITH_INDUSTRY]


def _fetch_current_industries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    targets = [
        {
            "security_code": str(row.get("security_code") or ""),
            "security_name": str(row.get("security_name") or ""),
        }
        for row in rows
        if str(row.get("security_code") or "").isdigit()
        and len(str(row.get("security_code") or "")) == 6
    ]
    if not targets:
        return {}
    script = r'''
import json
import sys

import akshare as ak

targets = json.loads(sys.argv[1])
output = {}
for row in targets:
    code = str(row.get("security_code") or "").strip()
    if not code:
        continue
    try:
        frame = ak.stock_individual_info_em(symbol=code)
    except Exception:
        continue
    if frame is None or frame.empty:
        continue
    for _, info_row in frame.iterrows():
        item = str(info_row.get("item", "")).strip()
        if item in ("行业", "所属行业"):
            value = str(info_row.get("value") or "").strip()
            if value:
                output[code] = value
            break
print(json.dumps(output, ensure_ascii=False))
'''
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, json.dumps(targets, ensure_ascii=False)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            env=_subprocess_env(),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return {}
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, Mapping):
            return {}
        return {
            str(code): str(industry).strip()
            for code, industry in payload.items()
            if str(code).strip() and str(industry).strip()
        }
    except Exception:
        logger.exception("current stock industry enrichment failed")
        return {}


def _industry_evidence_fields(
    value: object,
    *,
    decision: datetime,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        industry = str(
            value.get("value") or value.get("industry") or value.get("name") or ""
        ).strip()
        available = _aware_decision(value.get("available_at"))
        source = str(value.get("source") or "").strip() or None
        ref_id = str(value.get("ref_id") or value.get("snapshot_hash") or "").strip() or None
        pit_qualified = bool(
            value.get("pit_qualified") is True
            and industry
            and available is not None
            and available <= decision
            and source
            and ref_id
        )
        return {
            "industry": industry or None,
            "industry_available_at": available.isoformat() if available is not None else None,
            "industry_source": source,
            "industry_ref_id": ref_id,
            "industry_pit_qualified": pit_qualified,
        }
    industry = str(value or "").strip()
    return {
        "industry": industry or None,
        "industry_available_at": None,
        "industry_source": None,
        "industry_ref_id": None,
        "industry_pit_qualified": False,
    }


def _snapshot_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    coverage = snapshot.get("coverage")
    return {
        "snapshot_hash": str(snapshot.get("snapshot_hash") or "") or None,
        "report_period": str(snapshot.get("report_period") or "") or None,
        "as_of": str(snapshot.get("as_of_date") or "") or None,
        "available_at": str(snapshot.get("available_at") or "") or None,
        "coverage": dict(coverage) if isinstance(coverage, Mapping) else None,
    }


def _sector_payload(
    *,
    resolution: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    stocks: list[HoldingStockRow],
    status: str,
    reasons: list[str],
) -> dict[str, Any]:
    sector_clue = assess_sector_from_portfolio_stocks(stocks)
    clue_qualification = sector_clue.get("qualification")
    clue_qualification = (
        dict(clue_qualification) if isinstance(clue_qualification, Mapping) else {}
    )
    metadata = _snapshot_metadata(snapshot) if snapshot is not None else {
        "snapshot_hash": None,
        "report_period": None,
        "as_of": None,
        "available_at": None,
        "coverage": None,
    }
    return {
        "status": status,
        "qualified": status == "qualified",
        "reason_codes": list(dict.fromkeys(reasons)),
        "decision_at": resolution.get("decision_at"),
        "source": resolution.get("source"),
        **metadata,
        "stocks": stocks,
        "sector_clue": sector_clue,
        "qualification": {
            "sector_inference_eligible": bool(
                status == "qualified"
                and clue_qualification.get("sector_inference_eligible") is True
            ),
            "research_clue_available": bool(
                clue_qualification.get("research_clue_available") is True
            ),
            "research_only": clue_qualification.get("research_only") is not False,
            "industry_pit_qualified": bool(
                clue_qualification.get("industry_pit_qualified") is True
            ),
            "coverage_qualified": bool(
                clue_qualification.get("coverage_qualified") is True
            ),
            "dominance_qualified": bool(
                clue_qualification.get("dominance_qualified") is True
            ),
            "historical_inference_allowed": False,
            "reason_codes": list(
                dict.fromkeys(
                    [*reasons, *list(clue_qualification.get("reason_codes") or [])]
                )
            ),
        },
    }


def _aware_decision(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(CN_TZ)


def _is_current_decision(value: datetime) -> bool:
    skew = (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()
    return -300 <= skew <= _CURRENT_DECISION_SKEW_SECONDS


__all__ = [
    "HoldingStockRow",
    "assess_sector_from_portfolio_stocks",
    "fetch_portfolio_stocks_with_industry",
    "fetch_portfolio_stocks_with_industry_evidence",
    "infer_sector_from_portfolio_stocks",
]
