from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

SelectionStrategy = Literal["balanced", "with_new_issue"]

_NEW_ISSUE_MAX_AGE_DAYS = 180
_NEW_ISSUE_SLOTS = 2
_PER_SECTOR = 5


def balanced_score(row: dict) -> float:
    """Score higher for recent strength without extreme 1y chasing."""
    r1y = _num(row.get("return_1y_percent")) or 0.0
    r6m = _num(row.get("return_6m_percent"))
    r3m = _num(row.get("return_3m_percent"))
    if r6m is None:
        r6m = r1y
    if r3m is None:
        r3m = r6m

    recent_strength = r3m * 0.45 + r6m * 0.35
    annualized_recent = (r3m * 4 + r6m * 2) / 6
    momentum_gap = max(0.0, annualized_recent - r1y * 0.25)
    chase_penalty = max(0.0, r1y - 70.0) * 0.4

    nav_trend = row.get("nav_trend") or {}
    dist_high = _num(nav_trend.get("distance_from_high_percent"))
    room_bonus = 0.0
    if dist_high is not None and dist_high < 0:
        room_bonus = min(12.0, abs(dist_high) * 0.25)

    return recent_strength + momentum_gap - chase_penalty + room_bonus


def rank_candidates_balanced(candidates: list[dict]) -> list[dict]:
    scored = [(balanced_score(item), item) for item in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def pick_sector_candidates(
    *,
    sector_label: str,
    fixed_entries: list[dict],
    ranked_entries: list[dict],
    new_issue_rows: list[dict],
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    selection_strategy: SelectionStrategy,
    name_matches_sector,
    matches_fund_type,
    as_of_date: date | None = None,
) -> list[dict]:
    """Pick up to _PER_SECTOR entries for one sector."""
    results = list(fixed_entries)
    remaining = max(_PER_SECTOR - len(results), 0)
    if remaining <= 0:
        return results[:_PER_SECTOR]

    if selection_strategy == "with_new_issue":
        new_picks = _pick_new_issue_for_sector(
            new_issue_rows,
            sector_label=sector_label,
            keywords=keywords,
            excluded=excluded,
            seen_codes=seen_codes,
            fund_type_preference=fund_type_preference,
            limit=min(_NEW_ISSUE_SLOTS, remaining),
            name_matches_sector=name_matches_sector,
            matches_fund_type=matches_fund_type,
            as_of_date=as_of_date,
        )
        results.extend(new_picks)
        remaining = max(_PER_SECTOR - len(results), 0)

    if remaining <= 0:
        return results[:_PER_SECTOR]

    ranked = rank_candidates_balanced(ranked_entries)
    for entry in ranked:
        code = str(entry.get("fund_code", "")).zfill(6)
        if code in seen_codes:
            continue
        results.append(entry)
        seen_codes.add(code)
        remaining -= 1
        if remaining <= 0:
            break

    return results[:_PER_SECTOR]


def _pick_new_issue_for_sector(
    rows: list[dict],
    *,
    sector_label: str,
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    limit: int,
    name_matches_sector,
    matches_fund_type,
    as_of_date: date | None = None,
) -> list[dict]:
    if limit <= 0:
        return []

    cutoff = (as_of_date or date.today()) - timedelta(days=_NEW_ISSUE_MAX_AGE_DAYS)
    picks: list[dict] = []
    for row in rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if not code.isdigit() or len(code) != 6:
            continue
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name", ""))
        if not name_matches_sector(name, keywords):
            continue
        if not matches_fund_type(name, fund_type_preference):
            continue
        established = _parse_date(row.get("established_date"))
        if established is not None and established < cutoff:
            continue
        entry = {
            "fund_code": code,
            "fund_name": name,
            "sector_label": sector_label,
            "selection_reason": "新发观察",
            "is_new_issue": True,
            "established_date": established.isoformat() if established else row.get("established_date"),
            "return_since_issue_percent": row.get("return_since_issue_percent"),
        }
        picks.append(entry)
        seen_codes.add(code)
        if len(picks) >= limit:
            break
    return picks


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    if not text:
        return None
    normalized = text.replace("/", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
