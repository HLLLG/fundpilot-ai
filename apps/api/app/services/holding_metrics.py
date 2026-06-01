from __future__ import annotations

from app.models import Holding

HOLDING_RETURN_SEMANTICS: dict[str, str] = {
    "sector_return_percent": (
        "养基宝「关联板块」列：当日板块实时涨跌幅（盘中/收盘前会更新）。"
    ),
    "holding_return_percent": (
        "养基宝「持有收益」中的收益率：通常为昨日结算后的累计持有收益率，"
        "不含今日盘中涨跌。"
    ),
    "daily_return_percent": (
        "明确的当日基金收益率（OCR 有当日收益列或收盘后更新时才有）。"
        "有则优先使用，勿与估算值叠加。"
    ),
    "estimated_daily_return_percent": (
        "当 daily_return_percent 为空时：estimated ≈ sector_return_percent + "
        "holding_return_percent。板块涨跌与基金涨跌高度相关但非精确相等，"
        "仅作收盘前决策的近似参考，须在分析中说明为估算。"
    ),
}


def compute_estimated_daily_return_percent(holding: Holding) -> float | None:
    if holding.daily_return_percent is not None:
        return holding.daily_return_percent
    if holding.sector_return_percent is None:
        return None
    settled = holding.holding_return_percent
    if settled is None:
        settled = holding.return_percent
    return round(holding.sector_return_percent + settled, 4)


def holding_daily_return_is_estimated(holding: Holding) -> bool:
    return (
        holding.daily_return_percent is None
        and holding.sector_return_percent is not None
        and compute_estimated_daily_return_percent(holding) is not None
    )


def holding_analysis_payload(holding: Holding) -> dict:
    estimated = compute_estimated_daily_return_percent(holding)
    payload = holding.model_dump()
    payload["estimated_daily_return_percent"] = estimated
    payload["daily_return_is_estimated"] = holding_daily_return_is_estimated(holding)
    return payload
