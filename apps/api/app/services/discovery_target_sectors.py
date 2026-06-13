from __future__ import annotations

from app.models import Holding, InvestorProfile
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_canonical import get_canonical_sector, list_canonical_sector_labels
from app.services.sector_labels import normalize_sector_label


def select_target_sectors(
    holdings: list[Holding],
    focus_sectors: list[str] | None,
    heat_ranking: list[dict],
    profile: InvestorProfile | None = None,
    *,
    max_sectors: int = 5,
    gap_weight_threshold: float = 15.0,
) -> list[str]:
    """自动缺口板块（热度靠前且未重仓）∪ 用户 focus_sectors，去重后最多 max_sectors。"""
    ordered: list[str] = []
    seen: set[str] = set()

    for raw in focus_sectors or []:
        label = _resolve_sector_label(raw)
        if label and label not in seen:
            seen.add(label)
            ordered.append(label)

    held_weights = _sector_weights(holdings, profile)
    for row in heat_ranking:
        label = str(row.get("sector_label", "")).strip()
        if not label or label in seen:
            continue
        weight = held_weights.get(label, 0.0)
        if weight >= gap_weight_threshold:
            continue
        seen.add(label)
        ordered.append(label)
        if len([s for s in ordered if s not in (focus_sectors or [])]) >= 3:
            break

    if not ordered:
        for label in list_canonical_sector_labels()[:3]:
            if label not in seen:
                ordered.append(label)
                seen.add(label)

    return ordered[:max_sectors]


def _sector_weights(holdings: list[Holding], profile: InvestorProfile | None) -> dict[str, float]:
    resolved = profile or InvestorProfile()
    denominator = resolve_weight_denominator(holdings, resolved) or 1.0
    weights: dict[str, float] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label:
            continue
        canon = get_canonical_sector(label)
        key = canon.label if canon else label
        weight = holding_weight_percent(holding, holdings, resolved)
        weights[key] = weights.get(key, 0.0) + weight
    return weights


def _resolve_sector_label(raw: str) -> str | None:
    label = normalize_sector_label(raw)
    if not label:
        return None
    canon = get_canonical_sector(label)
    return canon.label if canon else label
