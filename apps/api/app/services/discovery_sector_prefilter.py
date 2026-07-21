from __future__ import annotations

"""Bounded full-market prefilter for discovery sector evidence.

The previous pipeline fetched expensive flow/position evidence almost entirely
for the hottest sectors.  That made an early setup structurally invisible until
after it had already rallied.  This module keeps the request bounded while
reserving evidence slots for three distinct price stages: momentum, quiet setup
and pullback acceptance.
"""

from math import isfinite
from typing import Any, Iterable


DEFAULT_EVIDENCE_LABEL_LIMIT = 24


def select_opportunity_evidence_labels(
    sector_heat: list[dict],
    target_sectors: list[str],
    focus_sectors: list[str],
    *,
    max_labels: int = DEFAULT_EVIDENCE_LABEL_LIMIT,
) -> list[str]:
    limit = max(1, int(max_labels))
    result: list[str] = []
    seen: set[str] = set()

    def append_labels(values: Iterable[str]) -> None:
        for raw in values:
            label = str(raw or "").strip()
            if not label or label in seen or len(result) >= limit:
                continue
            seen.add(label)
            result.append(label)

    append_labels([*target_sectors, *focus_sectors])
    rows = [row for row in sector_heat if _label(row)]

    momentum = sorted(
        rows,
        key=lambda row: (
            _num(row.get("heat_score")) or -999.0,
            _num(row.get("change_5d_percent")) or -999.0,
        ),
        reverse=True,
    )
    append_labels(_label(row) for row in momentum[:8])

    quiet_setups = sorted(rows, key=_quiet_setup_score, reverse=True)
    append_labels(_label(row) for row in quiet_setups[:10])

    pullbacks = sorted(
        [row for row in rows if _is_pullback_candidate(row)],
        key=_pullback_score,
        reverse=True,
    )
    append_labels(_label(row) for row in pullbacks[:8])

    # Fill any remaining budget deterministically.  This also covers providers
    # that temporarily omit one of the change windows.
    append_labels(_label(row) for row in momentum)
    return result[:limit]


def _quiet_setup_score(row: dict[str, Any]) -> float:
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    breadth = _num(row.get("advancing_ratio_percent"))
    if change_1d is None and change_5d is None:
        return -999.0
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    score = 0.0
    if -2.5 <= c1 <= 1.5:
        score += 45.0
    else:
        score -= abs(c1) * 4.0
    if -4.0 <= c5 <= 3.0:
        score += 35.0
    else:
        score -= abs(c5) * 2.0
    if breadth is not None:
        score += max(0.0, 20.0 - abs(breadth - 55.0) * 0.4)
    return score


def _is_pullback_candidate(row: dict[str, Any]) -> bool:
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    return bool(
        change_1d is not None
        and change_5d is not None
        and -3.0 <= change_1d <= 1.5
        and 2.0 <= change_5d <= 12.0
    )


def _pullback_score(row: dict[str, Any]) -> float:
    change_1d = _num(row.get("change_1d_percent")) or 0.0
    change_5d = _num(row.get("change_5d_percent")) or 0.0
    return change_5d * 3.0 - abs(change_1d) * 2.0


def _label(row: dict[str, Any]) -> str:
    return str(row.get("sector_label") or "").strip()


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


__all__ = ["DEFAULT_EVIDENCE_LABEL_LIMIT", "select_opportunity_evidence_labels"]
