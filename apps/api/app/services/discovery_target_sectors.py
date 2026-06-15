from __future__ import annotations

from typing import Literal

from app.models import Holding, InvestorProfile
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_canonical import get_canonical_sector, list_discovery_sector_labels
from app.services.sector_labels import normalize_sector_label

DiscoveryScanMode = Literal["full_market", "portfolio_gap"]

_FULL_MARKET_MAX_SECTORS = 8
_GAP_MAX_SECTORS = 5


def select_target_sectors(
    holdings: list[Holding],
    focus_sectors: list[str] | None,
    heat_ranking: list[dict],
    profile: InvestorProfile | None = None,
    *,
    scan_mode: DiscoveryScanMode = "full_market",
    max_sectors: int | None = None,
    gap_weight_threshold: float = 15.0,
) -> list[str]:
    if scan_mode == "full_market":
        limit = max_sectors or _FULL_MARKET_MAX_SECTORS
        return _select_full_market_sectors(focus_sectors, heat_ranking, max_sectors=limit)
    limit = max_sectors or _GAP_MAX_SECTORS
    return _select_portfolio_gap_sectors(
        holdings,
        focus_sectors,
        heat_ranking,
        profile,
        max_sectors=limit,
        gap_weight_threshold=gap_weight_threshold,
    )


def _select_full_market_sectors(
    focus_sectors: list[str] | None,
    heat_ranking: list[dict],
    *,
    max_sectors: int,
) -> list[str]:
    """全市场模式：用户关注方向优先，其余按热度降序，不限于持仓缺口。"""
    ordered: list[str] = []
    seen: set[str] = set()

    for raw in focus_sectors or []:
        label = _resolve_sector_label(raw)
        if label and label not in seen:
            seen.add(label)
            ordered.append(label)

    for row in sorted(heat_ranking, key=lambda item: float(item.get("heat_score") or -999), reverse=True):
        label = str(row.get("sector_label", "")).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        ordered.append(label)
        if len(ordered) >= max_sectors:
            break

    if not ordered:
        for label in list_discovery_sector_labels()[:max_sectors]:
            if label not in seen:
                ordered.append(label)
                seen.add(label)

    return ordered[:max_sectors]


def _select_portfolio_gap_sectors(
    holdings: list[Holding],
    focus_sectors: list[str] | None,
    heat_ranking: list[dict],
    profile: InvestorProfile | None,
    *,
    max_sectors: int,
    gap_weight_threshold: float,
) -> list[str]:
    """缺口模式：热度靠前且未重仓的板块 ∪ 用户 focus_sectors。"""
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
        for label in list_discovery_sector_labels()[:3]:
            if label not in seen:
                ordered.append(label)
                seen.add(label)

    return ordered[:max_sectors]


def _sector_weights(holdings: list[Holding], profile: InvestorProfile | None) -> dict[str, float]:
    resolved = profile or InvestorProfile()
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
