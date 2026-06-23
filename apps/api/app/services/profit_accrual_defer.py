from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.ocr_text_utils import is_near_zero


def ocr_daily_profit_signal(holding: Holding) -> float | None:
    """支付宝「全部持有」版式将日收益写入 ``yesterday_profit``；其它路径用 ``daily_profit``。"""
    if holding.daily_profit is not None:
        return holding.daily_profit
    return holding.yesterday_profit


def ocr_holding_return_percent(holding: Holding) -> float | None:
    if holding.holding_return_percent is not None:
        return holding.holding_return_percent
    if holding.return_percent:
        return holding.return_percent
    return None


def ocr_signals_pending_profit_accrual(holding: Holding) -> bool:
    """支付宝截图：日收益、持有收益、持有收益率均为 0 → 份额待确认。"""
    if holding.holding_amount <= 0:
        return False
    ocr_daily = ocr_daily_profit_signal(holding)
    if ocr_daily is None:
        return False
    if not is_near_zero(ocr_daily):
        return False
    if holding.holding_profit is not None and not is_near_zero(holding.holding_profit):
        return False
    holding_return = ocr_holding_return_percent(holding)
    if holding_return is None:
        return False
    return is_near_zero(holding_return)


def ocr_signals_active_profit(holding: Holding) -> bool:
    """截图已带出非零收益 → 可正常计收益，应清除 defer。"""
    ocr_daily = ocr_daily_profit_signal(holding)
    if ocr_daily is not None and not is_near_zero(ocr_daily):
        return True
    if holding.holding_profit is not None and not is_near_zero(holding.holding_profit):
        return True
    holding_return = ocr_holding_return_percent(holding)
    if holding_return is not None and not is_near_zero(holding_return):
        return True
    return False


def is_profit_accrual_deferred(profile: FundProfile | None) -> bool:
    if profile is None or not profile.profit_accrual_deferred_until:
        return False
    from app.services.trading_session import get_effective_trade_date

    return get_effective_trade_date() <= profile.profit_accrual_deferred_until


def resolve_profile_defer_patch(
    holding: Holding,
    profile: FundProfile | None = None,
) -> dict[str, str | None]:
    if ocr_signals_pending_profit_accrual(holding):
        from app.services.trading_session import get_effective_trade_date

        return {"profit_accrual_deferred_until": get_effective_trade_date()}
    if ocr_signals_active_profit(holding):
        if profile is None or profile.profit_accrual_deferred_until:
            return {"profit_accrual_deferred_until": None}
    return {}


def apply_defer_to_profile(profile: FundProfile, holding: Holding) -> FundProfile:
    patch = resolve_profile_defer_patch(holding, profile)
    if patch:
        return profile.model_copy(update=patch)
    return profile


def get_profile_for_holding(holding: Holding) -> FundProfile | None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return None
    from app.database import get_fund_profile_by_code

    return get_fund_profile_by_code(code)
