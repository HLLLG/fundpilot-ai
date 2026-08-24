"""全部卖出：当日留仓计算收益，次日从账本删除。

支付宝交易分析在途卖出只显示份额（份）。产品约定：这种卖出等于清掉该基金
全部金额。确认当天仍保留持仓金额，好让板块估算 / 官方净值把当日收益算完；
次日再走删除持仓，关闭交易账本并清掉档案。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from app.models import FundProfile, Holding, ParsedTransaction

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


def current_china_date() -> date:
    return datetime.now(_CN_TZ).date()


def next_calendar_day(iso_date: str) -> str:
    return (date.fromisoformat(iso_date[:10]) + timedelta(days=1)).isoformat()


def is_exit_pending(value: object) -> bool:
    return bool(str(getattr(value, "exit_pending_until", None) or "").strip())


def is_exit_due(value: object, *, today: str | None = None) -> bool:
    until = str(getattr(value, "exit_pending_until", None) or "").strip()
    if not until:
        return False
    as_of = today or current_china_date().isoformat()
    return until <= as_of


def _trade_date(item: ParsedTransaction) -> str | None:
    raw = (item.trade_time or "")[:10]
    if len(raw) != 10 or raw[4] != "-":
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def fill_full_exit_amounts(
    parsed: list[ParsedTransaction],
    profiles_by_code: dict[str, FundProfile],
) -> list[ParsedTransaction]:
    """把「份」全卖出的金额补成当前持仓金额（按份额比例拆到同一基金的多笔）。"""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(parsed):
        code = (item.fund_code or "").strip()
        if item.full_exit and item.direction == "sell" and code and code != "000000":
            groups[code].append(index)
    if not groups:
        return parsed

    updated = list(parsed)
    for code, indexes in groups.items():
        profile = profiles_by_code.get(code)
        basis = float(profile.holding_amount or 0) if profile is not None else 0.0
        if basis <= 0:
            continue
        weights = [float(updated[index].confirmed_shares or 0) for index in indexes]
        total_weight = sum(weights)
        if total_weight <= 0:
            share = round(basis / len(indexes), 2)
            allocated = 0.0
            for offset, index in enumerate(indexes):
                amount = round(basis - allocated, 2) if offset == len(indexes) - 1 else share
                allocated += amount
                updated[index] = updated[index].model_copy(update={"amount_yuan": amount})
            continue
        allocated = 0.0
        for offset, index in enumerate(indexes):
            if offset == len(indexes) - 1:
                amount = round(basis - allocated, 2)
            else:
                amount = round(basis * weights[offset] / total_weight, 2)
                allocated += amount
            updated[index] = updated[index].model_copy(update={"amount_yuan": amount})
    return updated


def stamp_full_exit_profile(
    profile: FundProfile,
    *,
    trade_date: str,
    basis_amount: float | None = None,
) -> FundProfile:
    """给档案打上全部卖出宽限期；已有更早到期日的不往后推。"""
    today = current_china_date().isoformat()
    until = next_calendar_day(max(trade_date[:10], today))
    current_until = (profile.exit_pending_until or "").strip()
    if current_until and current_until <= until:
        until = current_until
    basis = basis_amount
    if basis is None or basis <= 0:
        basis = profile.exit_basis_amount or profile.holding_amount
    return profile.model_copy(
        update={
            "exit_pending_until": until,
            "exit_basis_amount": float(basis) if basis else profile.holding_amount,
        }
    )


def stamp_full_exits_from_parsed(
    parsed: Iterable[ParsedTransaction],
    profiles_by_code: dict[str, FundProfile],
) -> None:
    """份额卖出（full_exit）一经导入就标记次日删除，不等份额入账。"""
    from app.database import save_fund_profile

    latest_trade: dict[str, str] = {}
    for item in parsed:
        code = (item.fund_code or "").strip()
        trade_date = _trade_date(item)
        if (
            not item.full_exit
            or item.direction != "sell"
            or not code
            or code == "000000"
            or trade_date is None
        ):
            continue
        previous = latest_trade.get(code)
        if previous is None or trade_date > previous:
            latest_trade[code] = trade_date

    for code, trade_date in latest_trade.items():
        profile = profiles_by_code.get(code)
        if profile is None:
            continue
        stamped = stamp_full_exit_profile(
            profile,
            trade_date=trade_date,
            basis_amount=profile.holding_amount,
        )
        saved = save_fund_profile(stamped)
        profiles_by_code[code] = saved


def attach_exit_fields(holding: Holding, profile: FundProfile | None) -> Holding:
    if profile is None or not is_exit_pending(profile):
        return holding
    return holding.model_copy(
        update={
            "exit_pending_until": profile.exit_pending_until,
            "exit_basis_amount": profile.exit_basis_amount or holding.holding_amount,
        }
    )


def keep_amount_for_full_exit(
    holding: Holding,
    profile: FundProfile | None,
    *,
    trade_date: str,
) -> tuple[Holding, FundProfile | None]:
    """有效份额已清零时：打宽限期并保留金额，好把当日收益算完。"""
    from app.database import save_fund_profile

    if profile is None:
        return holding, profile
    stamped = stamp_full_exit_profile(
        profile,
        trade_date=trade_date,
        basis_amount=holding.holding_amount or profile.holding_amount,
    )
    if stamped != profile:
        stamped = save_fund_profile(stamped)
    return attach_exit_fields(holding, stamped), stamped


def purge_due_full_exits(*, today: str | None = None) -> list[str]:
    """到期的全部卖出基金走删除持仓：关账本、清档案、移出持仓页。"""
    from app.database import list_fund_profiles
    from app.services.portfolio_holdings_service import remove_holding_from_portfolio
    from app.services.portfolio_ledger_service import PositionCloseConflict

    as_of = today or current_china_date().isoformat()
    removed: list[str] = []
    for profile in list_fund_profiles():
        if not is_exit_due(profile, today=as_of):
            continue
        code = (profile.fund_code or "").strip()
        if not code or code == "000000":
            continue
        try:
            remove_holding_from_portfolio(code, fund_name=profile.fund_name)
        except PositionCloseConflict:
            logger.info("全部卖出仍有在途交易，次日删除延后：%s", code)
            continue
        except LookupError:
            logger.info("全部卖出持仓已不在快照中：%s", code)
            continue
        except Exception:
            logger.exception("全部卖出次日删除失败：%s", code)
            continue
        removed.append(code)
    return removed
