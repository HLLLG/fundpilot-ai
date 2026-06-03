from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.sector_labels import normalize_sector_label


def sector_quote_lookup_label(
    holding: Holding | None = None,
    *,
    sector_name: str | None = None,
    intraday_index_name: str | None = None,
) -> str | None:
    """养基宝规则：有「场内指数」用指数名拉涨跌，否则用「关联板块」名。"""
    index_name = intraday_index_name
    board_name = sector_name
    if holding is not None:
        index_name = holding.intraday_index_name or index_name
        board_name = holding.sector_name or board_name
    if index_name and normalize_sector_label(index_name):
        return normalize_sector_label(index_name)
    if board_name and normalize_sector_label(board_name):
        return normalize_sector_label(board_name)
    return None


def sector_display_label(holding: Holding) -> str | None:
    """UI 展示用：优先关联板块短名，否则场内指数/sector_name。"""
    if holding.sector_name:
        return holding.sector_name
    return holding.intraday_index_name


def profile_quote_fields(profile: FundProfile) -> dict[str, str | None]:
    lookup = sector_quote_lookup_label(
        sector_name=profile.sector_name,
        intraday_index_name=profile.intraday_index_name,
    )
    return {
        "sector_name": profile.sector_name,
        "intraday_index_name": profile.intraday_index_name,
        "sector_quote_label": lookup,
    }
