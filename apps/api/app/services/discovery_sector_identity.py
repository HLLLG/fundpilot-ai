"""Executable sector-identity contract for discovery candidates.

Sector-fit scores describe ranking strength; they are not identity evidence.
New candidate rows therefore carry an explicit status derived from provenance.
The score fallback exists only so historical persisted reports without
provenance fields remain readable under their original contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any


SECTOR_IDENTITY_VERIFIED = "verified"
SECTOR_IDENTITY_PENDING = "pending"

_VERIFIED_MATCH_KINDS = frozenset({"primary", "tracking_exact"})
_PENDING_MATCH_KINDS = frozenset({"fallback", "name", "new_issue"})


def annotate_candidate_sector_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a candidate carrying an explicit, provenance-based identity gate."""

    row = dict(candidate)
    kind = _match_kind(row)
    verified = kind in _VERIFIED_MATCH_KINDS
    row["sector_identity_status"] = (
        SECTOR_IDENTITY_VERIFIED if verified else SECTOR_IDENTITY_PENDING
    )
    row["sector_identity_eligible"] = verified
    row["sector_mapping_verified"] = verified
    return row


def candidate_sector_identity_is_executable(candidate: Mapping[str, Any]) -> bool:
    """Use explicit provenance first; fall back to score only for old reports.

    Any explicit pending signal fails closed. A declared match kind is also
    authoritative, so a high manually supplied score cannot turn a name or new
    issue match into a verified fund-to-sector identity.
    """

    status = str(candidate.get("sector_identity_status") or "").strip().lower()
    eligible = candidate.get("sector_identity_eligible")
    kind = _match_kind(candidate)
    mapping_verified = candidate.get("sector_mapping_verified")

    # Treat contradictory persisted/provided fields as unverified. Normal new
    # rows are annotated consistently, but a stale or hand-built row must not
    # be able to bypass provenance by setting just one positive flag.
    if status and status != SECTOR_IDENTITY_VERIFIED:
        return False
    if isinstance(eligible, bool) and not eligible:
        return False
    if kind in _PENDING_MATCH_KINDS:
        return False
    if isinstance(mapping_verified, bool) and not mapping_verified:
        return False

    if (
        status == SECTOR_IDENTITY_VERIFIED
        or eligible is True
        or kind in _VERIFIED_MATCH_KINDS
        or mapping_verified is True
    ):
        return True

    # Compatibility only: reports created before provenance fields existed
    # encoded the old decision in sector_fit_score. Newly built rows are always
    # annotated above and never reach this branch.
    score = _finite_float(candidate.get("sector_fit_score"))
    return score is not None and score >= 18.0


def _match_kind(candidate: Mapping[str, Any]) -> str:
    public_kind = str(candidate.get("sector_match_kind") or "").strip()
    if public_kind:
        return public_kind
    return str(candidate.get("_sector_match_kind") or "").strip()


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None
