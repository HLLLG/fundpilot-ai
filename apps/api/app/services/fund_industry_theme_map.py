"""东财/申万等行业名 → 主题板块展示名（THEME_BOARD_WHITELIST / canonical）。"""

from __future__ import annotations

from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import normalize_sector_label
from app.services.sector_registry_data import THEME_BOARD_WHITELIST


def map_industry_to_theme_label(industry: str | None) -> str | None:
    """将个股所属行业映射到粗粒度主题板块名。"""
    normalized = normalize_sector_label(industry)
    if not normalized:
        return None

    if normalized in THEME_BOARD_WHITELIST:
        return normalized

    canon = get_canonical_sector(normalized)
    if canon is not None:
        return canon.label

    best: str | None = None
    best_len = 0
    for label in THEME_BOARD_WHITELIST:
        if label in normalized or normalized in label:
            if len(label) > best_len:
                best = label
                best_len = len(label)
    return best
