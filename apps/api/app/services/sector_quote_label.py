from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_profile import (
    _is_valid_sector_label,
    infer_intraday_index_from_fund_name,
    infer_intraday_index_from_sector,
)
from app.services.sector_labels import normalize_sector_label


def _profile_index_quote_label(profile: FundProfile | None) -> str | None:
    """档案 OCR「场内指数 + 关联板块」双字段时，涨跌口径走指数名。"""
    if profile is None or not profile.intraday_index_name:
        return None
    index_name = normalize_sector_label(profile.intraday_index_name)
    if not index_name:
        return None
    sector_name = normalize_sector_label(profile.sector_name)
    if sector_name and sector_name != index_name and _is_valid_sector_label(sector_name):
        inferred = infer_intraday_index_from_sector(sector_name)
        if (
            inferred == index_name
            and infer_intraday_index_from_fund_name(profile.fund_name) is None
        ):
            return None
        return index_name
    if not sector_name:
        return index_name
    return None


def sector_quote_lookup_label(
    holding: Holding | None = None,
    *,
    sector_name: str | None = None,
    intraday_index_name: str | None = None,
    profile: FundProfile | None = None,
) -> str | None:
    """养基宝涨跌口径：ETF 联接/详情 OCR「场内指数」→ 指数；否则关联板块短名（如半导体）。"""
    board_name = sector_name
    fund_name: str | None = None
    if holding is not None:
        board_name = holding.sector_name or board_name
        fund_name = holding.fund_name
        if profile is None and holding.fund_code and holding.fund_code != "000000":
            from app.database import get_fund_profile_by_code

            profile = get_fund_profile_by_code(holding.fund_code)
    if profile is not None and not fund_name:
        fund_name = profile.fund_name

    from_fund = infer_intraday_index_from_fund_name(fund_name)
    if from_fund:
        return from_fund

    profile_index = _profile_index_quote_label(profile)
    if profile_index:
        return profile_index

    if board_name and normalize_sector_label(board_name):
        return normalize_sector_label(board_name)

    index_name = intraday_index_name
    if holding is not None:
        index_name = holding.intraday_index_name or index_name
    if index_name and normalize_sector_label(index_name):
        return normalize_sector_label(index_name)
    return None


def sector_display_label(holding: Holding) -> str | None:
    """UI 展示用：优先关联板块短名，否则场内指数/sector_name。"""
    if _is_valid_sector_label(holding.sector_name):
        return holding.sector_name
    if holding.intraday_index_name:
        return holding.intraday_index_name
    return None


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
