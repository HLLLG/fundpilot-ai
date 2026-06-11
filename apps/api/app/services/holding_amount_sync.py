from __future__ import annotations

import logging

from app.database import get_fund_profile_by_code, save_fund_profile
from app.models import FundProfile, Holding
from app.services.fund_estimate_provider import fetch_fund_estimate_quotes
from app.services.fund_nav_service import get_latest_unit_nav, get_official_nav_return
from app.services.trading_session import get_effective_trade_date

logger = logging.getLogger(__name__)


def bootstrap_holding_baselines(
    holdings: list[Holding],
    *,
    estimate_quotes: dict[str, dict] | None = None,
    persist_profiles: bool = True,
    force_reset_shares: bool = False,
) -> list[Holding]:
    """用户确认 OCR 后：用截图金额锁定份额与成本，供后续按交易日自动推算。"""
    if not holdings:
        return holdings

    quotes = estimate_quotes
    if quotes is None:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)

    for holding in holdings:
        _bootstrap_profile_baseline(
            holding,
            estimate_quote=quotes.get(holding.fund_code or ""),
            persist_profile=persist_profiles,
            force_reset_shares=force_reset_shares,
        )
    return holdings


def _bootstrap_profile_baseline(
    holding: Holding,
    *,
    estimate_quote: dict | None,
    persist_profile: bool,
    force_reset_shares: bool,
) -> None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return

    profile = get_fund_profile_by_code(code)
    if profile is None:
        return

    if profile.holding_shares is not None and not force_reset_shares:
        return

    unit_nav = _resolve_bootstrap_unit_nav(code, estimate_quote)
    if unit_nav is None or unit_nav <= 0:
        return

    shares = round(holding.holding_amount / unit_nav, 2)
    if shares <= 0:
        return

    patch: dict = {"holding_shares": shares}
    cost_basis = _cost_basis(holding, profile)
    if cost_basis is not None and cost_basis > 0:
        patch["holding_cost"] = round(cost_basis / shares, 4)
    if holding.holding_profit is not None:
        patch["holding_profit"] = holding.holding_profit
    return_percent = holding.holding_return_percent
    if return_percent is None:
        return_percent = holding.return_percent
    if return_percent is not None:
        patch["holding_return_percent"] = return_percent

    if persist_profile:
        save_fund_profile(profile.model_copy(update=patch))


def _resolve_bootstrap_unit_nav(fund_code: str, estimate_quote: dict | None) -> float | None:
    if estimate_quote:
        estimated = estimate_quote.get("estimated_nav")
        if estimated is not None and float(estimated) > 0:
            return round(float(estimated), 4)
        previous = estimate_quote.get("previous_nav")
        if previous is not None and float(previous) > 0:
            return round(float(previous), 4)
    return get_latest_unit_nav(fund_code)


def sync_holding_amounts_from_shares(
    holdings: list[Holding],
    *,
    estimate_quotes: dict[str, dict] | None = None,
    persist_profiles: bool = True,
) -> list[Holding]:
    """按档案份额 × 最新净值同步持有金额，对齐养基宝盘中自动更新。"""
    if not holdings:
        return holdings

    codes = {
        holding.fund_code.strip()
        for holding in holdings
        if (holding.fund_code or "").strip() and holding.fund_code != "000000"
    }
    if not codes:
        return holdings

    quotes = estimate_quotes
    if quotes is None:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)

    trade_date = get_effective_trade_date()
    updated: list[Holding] = []
    for holding in holdings:
        updated.append(
            _sync_one_holding(
                holding,
                trade_date=trade_date,
                estimate_quote=quotes.get(holding.fund_code or ""),
                persist_profile=persist_profiles,
            )
        )
    return updated


def _sync_one_holding(
    holding: Holding,
    *,
    trade_date: str,
    estimate_quote: dict | None,
    persist_profile: bool,
) -> Holding:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return holding

    profile = get_fund_profile_by_code(code)
    shares = profile.holding_shares if profile else None
    unit_nav = _resolve_unit_nav(code, trade_date, estimate_quote)

    if shares is None and unit_nav and unit_nav > 0:
        shares = round(holding.holding_amount / unit_nav, 2)
        if profile is not None and profile.holding_shares is None and persist_profile:
            save_fund_profile(profile.model_copy(update={"holding_shares": shares}))

    if shares is None or shares <= 0 or unit_nav is None or unit_nav <= 0:
        return holding

    new_amount = round(shares * unit_nav, 2)
    if abs(new_amount - holding.holding_amount) < 0.01:
        return holding

    cost_basis = _cost_basis(holding, profile)
    patched = _apply_amount_update(holding, new_amount, cost_basis)

    if persist_profile and profile is not None:
        # 自动同步只更新演算结果；份额/成本仅在 OCR 确认时写入档案。
        pass

    return patched


def _resolve_unit_nav(
    fund_code: str,
    trade_date: str,
    estimate_quote: dict | None,
) -> float | None:
    official_return = get_official_nav_return(fund_code, trade_date)
    if official_return is not None:
        return get_latest_unit_nav(fund_code)

    if estimate_quote:
        estimated = estimate_quote.get("estimated_nav")
        if estimated is not None and float(estimated) > 0:
            return round(float(estimated), 4)

    return get_latest_unit_nav(fund_code)


_COST_BASIS_DRIFT_TOLERANCE = 0.05


def _cost_basis_from_return(holding: Holding) -> float | None:
    return_percent = holding.holding_return_percent
    if return_percent is None:
        return_percent = holding.return_percent
    if return_percent is None or holding.holding_amount <= 0:
        return None
    return round(holding.holding_amount / (1 + return_percent / 100), 2)


def _cost_basis(holding: Holding, profile: FundProfile | None) -> float | None:
    """成本基数：档案单位成本×份额须与 OCR 昨日结算收益率一致，否则以收益率为准。"""
    derived = _cost_basis_from_return(holding)
    if profile and profile.holding_cost and profile.holding_shares:
        profile_basis = round(profile.holding_cost * profile.holding_shares, 2)
        if derived is not None and derived > 0:
            drift = abs(profile_basis - derived) / derived
            if drift > _COST_BASIS_DRIFT_TOLERANCE:
                logger.info(
                    "cost basis drift for %s: profile=%.2f derived=%.2f, using derived",
                    holding.fund_code,
                    profile_basis,
                    derived,
                )
                return derived
        return profile_basis
    return derived


def _apply_amount_update(
    holding: Holding,
    new_amount: float,
    cost_basis: float | None,
) -> Holding:
    """仅同步持有金额；昨日结算持有收益/收益率由 OCR 保留，展示层叠加当日估算。"""
    _ = cost_basis
    return holding.model_copy(
        update={
            "holding_amount": new_amount,
            "amount_includes_today": True,
        }
    )
