"""Infer a current primary-sector label from a PIT holdings disclosure.

The disclosure is selected centrally by ``fund_holdings_snapshot``.  Industry
labels and fine-board memberships are fetched only for a current association
request and stamped at first observation. Historical replay therefore fails
closed instead of attaching today's classifications to an old decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.fund_holdings_snapshot import CN_TZ
from app.services.fund_holdings_snapshot_repository import (
    resolve_fund_holdings_snapshot_at_decision,
)
from app.services.fund_industry_theme_map import map_industry_to_theme_label
from app.services.sector_labels import normalize_sector_label
from app.services.stock_classification_evidence import (
    fetch_current_board_constituent_evidence,
    fetch_current_stock_industry_evidence,
)

logger = logging.getLogger(__name__)

_MIN_SCORE_PERCENT = 8.0
# Quarterly reports disclose ten largest holdings. The lightweight concurrent
# client below can classify the complete disclosed set without the old
# AkShare-per-stock latency penalty.
_MAX_STOCKS_WITH_INDUSTRY = 10
_CURRENT_DECISION_SKEW_SECONDS = 30 * 60
_MIN_CLASSIFIED_NAV_PERCENT = 20.0
_MIN_CLASSIFIED_DISCLOSED_RATIO = 0.60
_MIN_THEME_DOMINANCE_RATIO = 0.60


@dataclass(frozen=True)
class _PortfolioThemeRefinementRule:
    """Map a broad provider industry to a supported investable sub-theme.

    The rule is portfolio-level rather than a one-stock alias: the sub-theme
    must explain a clear majority of the broad-industry holding mass.
    """

    target_theme: str
    parent_industries: tuple[str, ...]
    board_codes: tuple[str, ...]
    minimum_matched_stocks: int = 2
    minimum_matched_weight_ratio: float = 0.60


# CSI 931743 covers both semiconductor materials and semiconductor equipment.
# Eastmoney exposes those as two separate level-3 industry boards, so their
# union is the current free-source membership proxy for our canonical
# ``半导体材料`` (display source name: ``半导体材料设备``) theme.
_PORTFOLIO_THEME_REFINEMENT_RULES: tuple[_PortfolioThemeRefinementRule, ...] = (
    _PortfolioThemeRefinementRule(
        target_theme="半导体材料",
        parent_industries=("半导体",),
        board_codes=("BK1325", "BK1326"),
    ),
)


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
    theme: str | None = None
    theme_available_at: str | None = None
    theme_source: str | None = None
    theme_ref_id: str | None = None
    theme_pit_qualified: bool = False
    theme_detail: dict[str, Any] | None = None


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
    is_current_association_lookup = decision_at is None
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
    if (
        not is_current_association_lookup
        or decision is None
        or not _is_current_decision(decision)
    ):
        return _sector_payload(
            resolution=resolution,
            snapshot=snapshot,
            stocks=[],
            status="unavailable",
            reasons=["historical_industry_enrichment_disallowed"],
        )

    raw_holdings = snapshot.get("holdings")
    selected = _select_disclosed_holdings(raw_holdings)
    enriched = _fetch_current_industries(selected, force_refresh=force_refresh)
    association_evaluated_at = datetime.now(CN_TZ)
    metadata = _snapshot_metadata(snapshot)
    stocks: list[HoldingStockRow] = []
    for row in selected:
        code = str(row.get("security_code") or "").strip()
        industry_fields = _industry_evidence_fields(
            enriched.get(code),
            decision=association_evaluated_at,
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
        association_evaluated_at=association_evaluated_at,
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
        refined_theme = normalize_sector_label(row.theme)
        theme = refined_theme or map_industry_to_theme_label(row.industry)
        if not theme:
            continue
        classification_pit_qualified = bool(
            row.theme_pit_qualified if refined_theme else row.industry_pit_qualified
        )
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
                "research_only": not classification_pit_qualified,
                "industry_available_at": row.industry_available_at,
                "industry_source": row.industry_source,
                "industry_ref_id": row.industry_ref_id,
                "industry_pit_qualified": row.industry_pit_qualified,
                "refined_theme": refined_theme,
                "theme_available_at": row.theme_available_at,
                "theme_source": row.theme_source,
                "theme_ref_id": row.theme_ref_id,
                "theme_pit_qualified": row.theme_pit_qualified,
                "theme_detail": row.theme_detail,
                "classification_pit_qualified": classification_pit_qualified,
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
                "classification_pit_qualified": False,
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
        item.get("classification_pit_qualified") is True for item in evidence
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
            "classification_pit_qualified": pit_qualified,
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


def _fetch_current_industries(
    rows: list[dict[str, Any]],
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    targets = [
        {
            "security_code": str(row.get("security_code") or "").strip(),
            "security_name": str(row.get("security_name") or "").strip(),
        }
        for row in rows
        if str(row.get("security_code") or "").strip().isdigit()
        and len(str(row.get("security_code") or "").strip()) == 6
    ]
    if not targets:
        return {}
    try:
        broad_evidence = fetch_current_stock_industry_evidence(
            targets,
            force_refresh=force_refresh,
        )
        return _refine_current_portfolio_themes(
            rows,
            broad_evidence,
            force_refresh=force_refresh,
        )
    except Exception:  # noqa: BLE001 - the caller fails closed on missing evidence
        logger.exception("current stock classification enrichment failed")
        return {}


def _refine_current_portfolio_themes(
    rows: list[dict[str, Any]],
    broad_evidence: Mapping[str, Any],
    *,
    force_refresh: bool,
) -> dict[str, Any]:
    """Refine broad industries when a fine theme explains the portfolio.

    This is intentionally a portfolio-level gate. A stock appearing in one
    popular concept must not relabel an otherwise unrelated fund.
    """

    enriched = {
        str(code): dict(value) if isinstance(value, Mapping) else value
        for code, value in broad_evidence.items()
        if str(code).strip()
    }
    for rule in _PORTFOLIO_THEME_REFINEMENT_RULES:
        parent_labels = {
            normalized
            for raw in rule.parent_industries
            if (normalized := normalize_sector_label(raw))
        }
        candidates: list[tuple[str, float]] = []
        for row in rows:
            code = str(row.get("security_code") or "").strip()
            evidence = enriched.get(code)
            if not isinstance(evidence, Mapping):
                continue
            industry = normalize_sector_label(
                str(evidence.get("value") or evidence.get("industry") or "")
            )
            if industry not in parent_labels:
                continue
            try:
                weight = float(row.get("weight_percent"))
            except (TypeError, ValueError):
                continue
            if weight > 0:
                candidates.append((code, weight))
        if len(candidates) < rule.minimum_matched_stocks:
            continue

        board_evidence = fetch_current_board_constituent_evidence(
            rule.board_codes,
            force_refresh=force_refresh,
        )
        member_codes: set[str] = set()
        supporting_rows: list[dict[str, Any]] = []
        for board_code in rule.board_codes:
            raw = board_evidence.get(board_code)
            if not isinstance(raw, Mapping):
                continue
            codes = {
                str(code).strip()
                for code in raw.get("codes") or []
                if str(code).strip()
            }
            if not codes:
                continue
            member_codes.update(codes)
            supporting_rows.append(dict(raw))
        if not member_codes or not supporting_rows:
            continue

        matched = [(code, weight) for code, weight in candidates if code in member_codes]
        candidate_mass = sum(weight for _code, weight in candidates)
        matched_mass = sum(weight for _code, weight in matched)
        matched_ratio = matched_mass / candidate_mass if candidate_mass > 0 else 0.0
        if (
            len(matched) < rule.minimum_matched_stocks
            or matched_ratio < rule.minimum_matched_weight_ratio
        ):
            continue

        board_available = [
            available
            for raw in supporting_rows
            if (available := _aware_decision(raw.get("available_at"))) is not None
        ]
        if not board_available:
            continue
        theme_available_at = max(board_available).isoformat()
        supporting_refs = sorted(
            str(raw.get("ref_id") or "").strip()
            for raw in supporting_rows
            if str(raw.get("ref_id") or "").strip()
        )
        boards_pit_qualified = bool(supporting_refs) and all(
            raw.get("pit_qualified") is True for raw in supporting_rows
        )
        detail = {
            "method": "portfolio_board_membership",
            "target_theme": rule.target_theme,
            "parent_industries": list(rule.parent_industries),
            "board_codes": list(rule.board_codes),
            "matched_stock_count": len(matched),
            "candidate_stock_count": len(candidates),
            "matched_weight_percent": round(matched_mass, 8),
            "candidate_weight_percent": round(candidate_mass, 8),
            "matched_weight_ratio": round(matched_ratio, 8),
            "minimum_matched_stocks": rule.minimum_matched_stocks,
            "minimum_matched_weight_ratio": rule.minimum_matched_weight_ratio,
        }
        theme_ref_id = _classification_ref(
            {
                **detail,
                "supporting_refs": supporting_refs,
                "available_at": theme_available_at,
            }
        )
        matched_codes = {code for code, _weight in matched}
        for code in matched_codes:
            value = enriched.get(code)
            if not isinstance(value, Mapping):
                continue
            industry_available = _aware_decision(value.get("available_at"))
            effective_available = max(
                [*board_available, *([industry_available] if industry_available else [])]
            )
            enriched[code] = {
                **dict(value),
                "theme": rule.target_theme,
                "theme_available_at": effective_available.isoformat(),
                "theme_source": (
                    "eastmoney_portfolio_board_membership:"
                    + "+".join(rule.board_codes)
                ),
                "theme_ref_id": theme_ref_id,
                "theme_pit_qualified": bool(
                    boards_pit_qualified and value.get("pit_qualified") is True
                ),
                "theme_detail": detail,
            }
    return enriched


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
        theme = normalize_sector_label(str(value.get("theme") or ""))
        theme_available = _aware_decision(value.get("theme_available_at"))
        theme_source = str(value.get("theme_source") or "").strip() or None
        theme_ref_id = str(value.get("theme_ref_id") or "").strip() or None
        theme_pit_qualified = bool(
            value.get("theme_pit_qualified") is True
            and theme
            and theme_available is not None
            and theme_available <= decision
            and theme_source
            and theme_ref_id
            and pit_qualified
        )
        theme_detail = value.get("theme_detail")
        return {
            "industry": industry or None,
            "industry_available_at": available.isoformat() if available is not None else None,
            "industry_source": source,
            "industry_ref_id": ref_id,
            "industry_pit_qualified": pit_qualified,
            "theme": theme,
            "theme_available_at": (
                theme_available.isoformat() if theme_available is not None else None
            ),
            "theme_source": theme_source,
            "theme_ref_id": theme_ref_id,
            "theme_pit_qualified": theme_pit_qualified,
            "theme_detail": (
                dict(theme_detail) if isinstance(theme_detail, Mapping) else None
            ),
        }
    industry = str(value or "").strip()
    return {
        "industry": industry or None,
        "industry_available_at": None,
        "industry_source": None,
        "industry_ref_id": None,
        "industry_pit_qualified": False,
        "theme": None,
        "theme_available_at": None,
        "theme_source": None,
        "theme_ref_id": None,
        "theme_pit_qualified": False,
        "theme_detail": None,
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
    association_evaluated_at: datetime | None = None,
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
        "decision_at": (
            association_evaluated_at.isoformat()
            if association_evaluated_at is not None
            else resolution.get("decision_at")
        ),
        "holdings_decision_at": resolution.get("decision_at"),
        "association_evaluated_at": (
            association_evaluated_at.isoformat()
            if association_evaluated_at is not None
            else None
        ),
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
            "classification_pit_qualified": bool(
                clue_qualification.get("classification_pit_qualified") is True
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


def _classification_ref(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "HoldingStockRow",
    "assess_sector_from_portfolio_stocks",
    "fetch_portfolio_stocks_with_industry",
    "fetch_portfolio_stocks_with_industry_evidence",
    "infer_sector_from_portfolio_stocks",
]
