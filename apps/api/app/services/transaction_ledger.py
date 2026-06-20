from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.database import (
    get_fund_profile_by_code,
    insert_fund_transaction,
    list_fund_transactions,
    list_pending_fund_transactions,
    save_fund_profile,
    update_fund_transaction,
)
from app.models import FundProfile, FundTransaction, Holding, ParsedTransaction
from app.services.fund_nav_service import get_unit_nav_on_date
from app.services.trading_session import resolve_confirm_date

logger = logging.getLogger(__name__)

_MIN_BASELINE_DATE = "0000-00-00"


def confirm_pending_transactions() -> int:
    """对当前用户所有 pending 且 fund_code 非空的交易，用 confirm_date 单位净值确认。

    shares_delta = amount_yuan / nav（sell 取负），保留 2 位；填 nav_on_confirm；
    status 置 confirmed。净值不可得（None/<=0）则保持 pending。返回新确认条数。
    """
    confirmed = 0
    for tx in list_pending_fund_transactions():
        if not tx.fund_code:
            continue
        nav = get_unit_nav_on_date(tx.fund_code, tx.confirm_date)
        if nav is None or nav <= 0:
            continue
        delta = round(tx.amount_yuan / nav, 2)
        if tx.direction == "sell":
            delta = -delta
        update_fund_transaction(
            tx.id,
            status="confirmed",
            shares_delta=delta,
            nav_on_confirm=nav,
        )
        confirmed += 1
    return confirmed


def compute_effective_shares_map(fund_codes: list[str]) -> dict[str, float]:
    """对每个有 profile 且 holding_shares 非空的 code，计算有效份额。

    effective = profile.holding_shares + Σ(tx.shares_delta)
    其中 tx 取该 code、shares_delta 非空、且 confirm_date > baseline_date 的交易。
    用 confirm_date > baseline_date 过滤：重传总览（基线日前移）后早于基线的交易
    自动不再叠加，避免双重计数。返回值 ≤ 0 表示已清仓。
    """
    result: dict[str, float] = {}
    for code in {c for c in fund_codes if c and c != "000000"}:
        profile = get_fund_profile_by_code(code)
        if profile is None or profile.holding_shares is None:
            continue
        baseline_date = profile.shares_baseline_date or _MIN_BASELINE_DATE
        effective = profile.holding_shares
        for tx in list_fund_transactions(fund_code=code):
            if tx.shares_delta is None:
                continue
            if tx.confirm_date > baseline_date:
                effective += tx.shares_delta
        result[code] = round(effective, 2)
    return result


def confirm_and_compute_overrides(holdings: list[Holding]) -> dict[str, float]:
    """持仓恢复/刷新前的账本协调：先补确认 pending，再算有效份额覆盖表。"""
    confirm_pending_transactions()
    codes = [
        holding.fund_code
        for holding in holdings
        if holding.fund_code and holding.fund_code != "000000"
    ]
    return compute_effective_shares_map(codes)


def _previous_day(iso_date: str) -> str:
    return (date.fromisoformat(iso_date) - timedelta(days=1)).isoformat()


def _dedup_key(parsed: ParsedTransaction) -> str:
    raw = f"{parsed.fund_code}|{parsed.direction}|{parsed.trade_time}|{parsed.amount_yuan}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_parsed_transactions(parsed: list[ParsedTransaction]) -> dict:
    """写入交易 → 确认 → 重算并返回持仓。

    返回 {"holdings": [...], "inserted": n, "skipped": m, "pending": <仍 pending 条数>}。
    """
    inserted = 0
    skipped = 0

    for item in parsed:
        if not item.fund_code:
            skipped += 1
            continue

        confirm_date = item.confirm_date or resolve_confirm_date(item.trade_time)
        tx = FundTransaction(
            id=uuid4().hex,
            fund_code=item.fund_code,
            fund_name=item.fund_name,
            direction=item.direction,
            amount_yuan=item.amount_yuan,
            trade_time=item.trade_time,
            confirm_date=confirm_date,
            status="pending",
            dedup_key=_dedup_key(item),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if not insert_fund_transaction(tx):
            skipped += 1
            continue
        inserted += 1

        # 建仓：买入未持有基金 → 创建简略 provisional 档案，
        # baseline_date 取 confirm_date 的前一天，保证该买入 confirm_date > baseline_date。
        if item.direction == "buy" and get_fund_profile_by_code(item.fund_code) is None:
            save_fund_profile(
                FundProfile(
                    fund_code=item.fund_code,
                    fund_name=item.fund_name,
                    holding_amount=0,
                    holding_shares=0.0,
                    shares_baseline_date=_previous_day(confirm_date),
                    source="alipay-transaction",
                    is_provisional=True,
                )
            )

    confirm_pending_transactions()

    from app.services.portfolio_holdings_service import sync_portfolio_from_profiles

    holdings = sync_portfolio_from_profiles(refresh_sectors=True)
    pending = len(list_pending_fund_transactions())
    return {
        "holdings": [holding.model_dump(mode="json") for holding in holdings],
        "inserted": inserted,
        "skipped": skipped,
        "pending": pending,
    }
