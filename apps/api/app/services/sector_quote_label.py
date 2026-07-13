from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_profile import (
    _infer_related_board_label,
    _is_valid_sector_label,
    _looks_like_index_name,
    infer_intraday_index_from_fund_name,
    infer_intraday_index_from_sector,
)
from app.services.sector_labels import infer_sector_label_from_fund_name, normalize_sector_label


class _ProfileNotProvided:
    pass


_PROFILE_NOT_PROVIDED = _ProfileNotProvided()


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
    profile: FundProfile | None | _ProfileNotProvided = _PROFILE_NOT_PROVIDED,
) -> str | None:
    """养基宝涨跌口径：ETF 联接/详情 OCR「场内指数」→ 指数；否则关联板块短名（如半导体）。"""
    from app.services.sector_canonical import get_canonical_sector

    board_name = sector_name
    fund_name: str | None = None
    index_name = intraday_index_name
    if holding is not None:
        board_name = holding.sector_name or board_name
        fund_name = holding.fund_name
        index_name = holding.intraday_index_name or index_name
        if (
            isinstance(profile, _ProfileNotProvided)
            and holding.fund_code
            and holding.fund_code != "000000"
        ):
            from app.database import get_fund_profile_by_code

            profile = get_fund_profile_by_code(holding.fund_code)
    if isinstance(profile, _ProfileNotProvided):
        profile = None
    if profile is not None and not fund_name:
        fund_name = profile.fund_name

    # 先按"精确度"从高到低尝试有行情源的规范映射：场内指数名 → 板块短名。
    # 业绩基准原文抠出来的指数名（如"中证高端装备制造指数"）往往不在别名表里，
    # 若命中不到规范映射就直接把这段原始文本当 query key 用，几乎必然查不到行情——
    # 应该退回板块短名（如"机械设备"），只要它已注册过行情源即可，不必持续扩充
    # 指数名别名白名单。
    if index_name and _looks_like_index_name(index_name):
        canon = get_canonical_sector(index_name)
        if canon:
            return canon.label

    if board_name and _is_valid_sector_label(board_name):
        canon = get_canonical_sector(board_name)
        if canon:
            return canon.label

    if index_name and _looks_like_index_name(index_name):
        normalized = normalize_sector_label(index_name)
        if normalized:
            return normalized

    if board_name and _is_valid_sector_label(board_name):
        normalized = normalize_sector_label(board_name)
        if normalized:
            return normalized

    profile_index = _profile_index_quote_label(profile)
    if profile_index:
        return profile_index

    from_fund = infer_intraday_index_from_fund_name(fund_name)
    if from_fund:
        canon = get_canonical_sector(from_fund)
        if canon:
            return canon.label
        return from_fund

    inferred = infer_sector_label_from_fund_name(fund_name)
    if inferred:
        canon = get_canonical_sector(inferred)
        if canon:
            return canon.label
        return inferred
    return None


def sector_display_label(holding: Holding) -> str | None:
    """UI 展示用：优先关联板块短名，否则场内指数，否则从基金名推断。"""
    if _is_valid_sector_label(holding.sector_name):
        return holding.sector_name
    if holding.intraday_index_name:
        if _looks_like_index_name(holding.intraday_index_name):
            board = _infer_related_board_label(holding.intraday_index_name)
            if _is_valid_sector_label(board):
                return board
        return holding.intraday_index_name
    inferred = infer_sector_label_from_fund_name(holding.fund_name)
    if inferred:
        return inferred
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
