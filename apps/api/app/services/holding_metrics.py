from __future__ import annotations

from app.models import Holding

HOLDING_RETURN_SEMANTICS: dict[str, str] = {
    "sector_return_percent": (
        "养基宝「关联板块」列：当日板块实时涨跌幅（盘中/收盘前会更新）。"
    ),
    "holding_return_percent": (
        "养基宝「持有收益」中的收益率：通常为昨日结算后的累计持有收益率，"
        "不含今日盘中涨跌；勿用于浮亏/风控判断。"
    ),
    "estimated_holding_return_percent": (
        "与界面「持有」列一致：官方净值已公布时用结算持有收益率；"
        "盘中/净值未公布时为昨日结算 + 板块涨跌估算。"
    ),
    "daily_return_percent": (
        "明确的当日基金收益率（OCR 有当日收益列或收盘后更新时才有）。"
        "有则优先使用，勿与估算值叠加。"
    ),
    "estimated_daily_return_percent": (
        "当日基金涨跌：优先 daily_return_percent；否则用 sector_return_percent 估算。"
        "勿与 estimated_holding_return_percent 混淆（后者为累计持有收益）。"
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


def compute_sector_fund_gap_percent(holding: Holding) -> float | None:
    """板块涨跌与基金当日/估算涨跌之差（百分点），正数表示板块强于基金表现。"""
    sector = holding.sector_return_percent
    if sector is None:
        return None
    fund_daily = holding.daily_return_percent
    if fund_daily is None:
        fund_daily = compute_estimated_daily_return_percent(holding)
    if fund_daily is None:
        return None
    return round(sector - fund_daily, 4)


def holding_analysis_payload(holding: Holding) -> dict:
    from app.services.holding_estimates import build_holding_display_metrics

    estimated = compute_estimated_daily_return_percent(holding)
    display = build_holding_display_metrics(holding)
    payload = holding.model_dump()
    payload["estimated_daily_return_percent"] = estimated
    payload["daily_return_is_estimated"] = holding_daily_return_is_estimated(holding)
    payload["holding_return_percent"] = display["holding_return_percent_settled"]
    payload["estimated_holding_return_percent"] = display["estimated_holding_return_percent"]
    payload["estimated_holding_profit"] = display["estimated_holding_profit"]
    payload["holding_return_is_estimated"] = display["holding_return_is_estimated"]
    return payload
