from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.database import (
    _connect,
    _get_fund_transaction_by_dedup_on_connection,
    _get_pending_fund_transaction_on_connection,
    _insert_fund_transaction_on_connection,
    _list_fund_transactions_on_connection,
    _update_fund_transaction_on_connection,
    insert_fund_transaction,
    list_fund_profiles,
    list_fund_transactions,
    list_pending_fund_transactions,
    save_fund_profile,
    update_fund_transaction,
)
from app.models import FundProfile, FundTransaction, Holding, ParsedTransaction
from app.services.fund_nav_service import get_latest_unit_nav, get_unit_nav_on_date
from app.services.trading_session import resolve_confirm_date
from app.request_context import get_request_user_id
from app.services.decision_repository import append_portfolio_ledger_event
from app.services.portfolio_ledger_service import (
    ensure_primary_position_store,
    transaction_ledger_event_from_fund_transaction,
)

if TYPE_CHECKING:
    from app.services.fund_profile import FundProfileService

logger = logging.getLogger(__name__)

_MIN_BASELINE_DATE = "0000-00-00"
_ORIGINAL_INSERT_FUND_TRANSACTION = insert_fund_transaction
_ORIGINAL_UPDATE_FUND_TRANSACTION = update_fund_transaction
_CN_TZ = ZoneInfo("Asia/Shanghai")


class TransactionTruthConflict(ValueError):
    def __init__(self, conflicts: list[dict[str, object]]) -> None:
        super().__init__("重复交易与已保存的确认真值不一致，请先核对或执行显式更正")
        self.conflicts = conflicts


def _current_china_date() -> date:
    return datetime.now(_CN_TZ).date()


def confirm_pending_transactions() -> int:
    """确认当前用户的 pending 交易，优先保留用户输入的实际份额。

    ``confirmed_shares`` 来自用户已在原平台确认的实际份额，不依赖净值即可入账；
    老 OCR 请求没有份额时才退回 ``amount_yuan / nav``，并显式标记
    ``derived_amount_nav``。``in_progress`` 交易不会被净值可用性误判为已成交。
    """
    confirmed = 0
    for tx in list_pending_fund_transactions():
        if not tx.fund_code or tx.in_progress:
            continue
        try:
            if date.fromisoformat(tx.confirm_date[:10]) > _current_china_date():
                # User-reported shares may already be known, but they are not
                # settled position truth before the platform confirmation date.
                continue
        except ValueError:
            logger.warning("invalid transaction confirm_date for %s", tx.id)
            continue
        if tx.confirmed_shares is not None and tx.confirmed_shares > 0:
            # Actual platform/user-confirmed shares are position truth by
            # themselves. Do not make confirmation latency or correctness depend
            # on a third-party NAV lookup; valuation can be attached separately.
            nav = tx.nav_on_confirm
            delta = round(float(tx.confirmed_shares), 6)
            normalized_confirmed_shares = delta
            shares_source = "user_confirmed"
        else:
            nav = get_unit_nav_on_date(tx.fund_code, tx.confirm_date)
            if nav is None or nav <= 0:
                continue
            delta = round(tx.amount_yuan / nav, 2)
            normalized_confirmed_shares = None
            shares_source = "derived_amount_nav"
        if tx.direction == "sell":
            delta = -delta
        if update_fund_transaction is not _ORIGINAL_UPDATE_FUND_TRANSACTION:
            confirmed_at = datetime.now(timezone.utc).isoformat()
            update_fund_transaction(
                tx.id,
                status="confirmed",
                shares_delta=delta,
                nav_on_confirm=nav,
                confirmed_shares=normalized_confirmed_shares,
                fee_yuan=tx.fee_yuan,
                shares_source=shares_source,
                in_progress=False,
                confirmed_at=confirmed_at,
            )
            confirmed += 1
            continue
        user_id = get_request_user_id()
        with _connect() as connection:
            ensure_primary_position_store(connection)
            current = _get_pending_fund_transaction_on_connection(
                connection,
                user_id=user_id,
                id=tx.id,
            )
            if current is None:
                # Another worker already confirmed this exact transaction.
                continue
            confirmed_at = datetime.now(timezone.utc).isoformat()
            confirmed_tx = current.model_copy(
                update={
                    "status": "confirmed",
                    "shares_delta": delta,
                    "nav_on_confirm": nav,
                    "confirmed_shares": normalized_confirmed_shares,
                    "shares_source": shares_source,
                    "confirmed_at": confirmed_at,
                    "in_progress": False,
                }
            )
            _update_fund_transaction_on_connection(
                connection,
                user_id=user_id,
                id=tx.id,
                status="confirmed",
                shares_delta=delta,
                nav_on_confirm=nav,
                confirmed_shares=normalized_confirmed_shares,
                fee_yuan=current.fee_yuan,
                shares_source=shares_source,
                in_progress=False,
                confirmed_at=confirmed_at,
            )
            append_portfolio_ledger_event(
                user_id=user_id,
                event=transaction_ledger_event_from_fund_transaction(
                    confirmed_tx,
                    supersedes_event_id=f"fund-transaction:{current.id}:pending",
                ),
                connection=connection,
            )
        confirmed += 1
    return confirmed


def compute_effective_shares_map(
    fund_codes: list[str],
    *,
    as_of_date: str | None = None,
    profiles_by_code: dict[str, FundProfile] | None = None,
) -> dict[str, float]:
    """对每个有 profile 且 holding_shares 非空的 code，计算有效份额。

    effective = profile.holding_shares + Σ(tx.shares_delta)
    其中 tx 取该 code、shares_delta 非空、且 confirm_date > baseline_date 的交易。
    用 confirm_date > baseline_date 过滤：重传总览（基线日前移）后早于基线的交易
    自动不再叠加，避免双重计数。返回值 ≤ 0 表示已清仓。
    """
    codes = {code for code in fund_codes if code and code != "000000"}
    if not codes:
        return {}

    cutoff_date = as_of_date or _current_china_date().isoformat()
    if profiles_by_code is None:
        profiles = {
            profile.fund_code: profile
            for profile in list_fund_profiles()
            if profile.fund_code in codes and profile.holding_shares is not None
        }
    else:
        profiles = {
            code: profile
            for code in codes
            if (profile := profiles_by_code.get(code)) is not None
            and profile.holding_shares is not None
        }
    effective_by_code = {
        code: float(profile.holding_shares)
        for code, profile in profiles.items()
    }
    for tx in list_fund_transactions():
        profile = profiles.get(tx.fund_code or "")
        if profile is None or tx.status != "confirmed" or tx.shares_delta is None:
            continue
        baseline_date = profile.shares_baseline_date or _MIN_BASELINE_DATE
        if baseline_date < tx.confirm_date <= cutoff_date:
            effective_by_code[profile.fund_code] += tx.shares_delta

    result: dict[str, float] = {}
    for code, effective in effective_by_code.items():
        # User-confirmed shares are persisted to six decimal places.  The
        # compatibility read model must not throw four of those decimals away;
        # only legacy amount/NAV-derived transactions are intentionally rounded
        # at their own source boundary.
        result[code] = round(effective, 6)
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


def _existing_semantic_dedup_key(tx: FundTransaction) -> str | None:
    """Map legacy formatting variants to the canonical v2 transaction identity."""

    try:
        normalized = ParsedTransaction(
            direction=tx.direction,
            fund_name=tx.fund_name,
            fund_code=tx.fund_code,
            amount_yuan=tx.amount_yuan,
            trade_time=tx.trade_time,
        )
    except ValueError:
        return None
    return _dedup_key(normalized)


def _truth_diff(
    existing: FundTransaction,
    incoming: ParsedTransaction,
    *,
    confirm_date: str,
) -> dict[str, dict[str, object | None]]:
    diff: dict[str, dict[str, object | None]] = {}

    def compare(field: str, stored: object, requested: object, *, optional: bool = False) -> None:
        if optional and requested is None:
            return
        left = round(float(stored), 6) if isinstance(stored, (int, float)) else stored
        right = round(float(requested), 6) if isinstance(requested, (int, float)) else requested
        if left != right:
            diff[field] = {"stored": left, "requested": right}

    compare(
        "confirmed_shares",
        existing.confirmed_shares,
        incoming.confirmed_shares,
        optional=True,
    )
    compare("fee_yuan", existing.fee_yuan, incoming.fee_yuan, optional=True)
    compare("in_progress", existing.in_progress, incoming.in_progress)
    compare("confirm_date", existing.confirm_date, confirm_date)
    return diff


def _preflight_transaction_truth(
    parsed: list[ParsedTransaction],
) -> list[tuple[ParsedTransaction, str, str]]:
    user_id = get_request_user_id()
    resolved: list[tuple[ParsedTransaction, str, str]] = []
    conflicts: list[dict[str, object]] = []
    seen_request: dict[str, tuple[ParsedTransaction, str]] = {}
    with _connect() as connection:
        ensure_primary_position_store(connection)
        semantic_existing: dict[str, list[FundTransaction]] = {}
        for stored in _list_fund_transactions_on_connection(
            connection,
            user_id=user_id,
        ):
            semantic_key = _existing_semantic_dedup_key(stored)
            if semantic_key:
                semantic_existing.setdefault(semantic_key, []).append(stored)
        for item in parsed:
            confirm_date = item.confirm_date or resolve_confirm_date(item.trade_time)
            canonical_dedup_key = _dedup_key(item)
            dedup_key = canonical_dedup_key
            previous_request = seen_request.get(canonical_dedup_key)
            if previous_request is not None:
                previous_item, previous_confirm_date = previous_request
                request_diff: dict[str, dict[str, object | None]] = {}
                for field, left, right in (
                    (
                        "confirmed_shares",
                        previous_item.confirmed_shares,
                        item.confirmed_shares,
                    ),
                    ("fee_yuan", previous_item.fee_yuan, item.fee_yuan),
                    ("in_progress", previous_item.in_progress, item.in_progress),
                    ("confirm_date", previous_confirm_date, confirm_date),
                ):
                    normalized_left = (
                        round(float(left), 6)
                        if isinstance(left, (int, float))
                        else left
                    )
                    normalized_right = (
                        round(float(right), 6)
                        if isinstance(right, (int, float))
                        else right
                    )
                    if normalized_left != normalized_right:
                        request_diff[field] = {
                            "stored": normalized_left,
                            "requested": normalized_right,
                        }
                if request_diff:
                    conflicts.append(
                        {
                            "transaction_id": None,
                            "fund_code": item.fund_code,
                            "dedup_key": canonical_dedup_key,
                            "diff": request_diff,
                            "source": "duplicate_in_request",
                        }
                    )
            else:
                seen_request[canonical_dedup_key] = (item, confirm_date)
            if not item.fund_code:
                resolved.append((item, confirm_date, dedup_key))
                continue
            existing = _get_fund_transaction_by_dedup_on_connection(
                connection,
                user_id=user_id,
                dedup_key=dedup_key,
            )
            if existing is None:
                semantic_matches = semantic_existing.get(canonical_dedup_key, [])
                if len(semantic_matches) > 1:
                    conflicts.append(
                        {
                            "transaction_id": None,
                            "fund_code": item.fund_code,
                            "dedup_key": canonical_dedup_key,
                            "existing_transaction_ids": [
                                tx.id for tx in semantic_matches
                            ],
                            "diff": {},
                            "source": "ambiguous_legacy_duplicates",
                        }
                    )
                elif semantic_matches:
                    existing = semantic_matches[0]
                    # Reuse the historical unique key so the write path cannot
                    # insert a canonical-format duplicate of the same trade.
                    dedup_key = existing.dedup_key
            if existing is not None:
                diff = _truth_diff(existing, item, confirm_date=confirm_date)
                if diff:
                    conflicts.append(
                        {
                            "transaction_id": existing.id,
                            "fund_code": existing.fund_code,
                            "dedup_key": dedup_key,
                            "diff": diff,
                        }
                    )
            resolved.append((item, confirm_date, dedup_key))
    if conflicts:
        raise TransactionTruthConflict(conflicts)
    return resolved


def _pending_transaction(
    item: ParsedTransaction,
    *,
    confirm_date: str,
    dedup_key: str,
) -> FundTransaction:
    return FundTransaction(
        id=uuid4().hex,
        fund_code=item.fund_code,
        fund_name=item.fund_name,
        direction=item.direction,
        amount_yuan=item.amount_yuan,
        trade_time=item.trade_time,
        confirm_date=confirm_date,
        status="pending",
        confirmed_shares=item.confirmed_shares,
        fee_yuan=item.fee_yuan,
        shares_source=("user_confirmed" if item.confirmed_shares is not None else None),
        in_progress=item.in_progress,
        dedup_key=dedup_key,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _ensure_buy_profile(
    item: ParsedTransaction,
    *,
    confirm_date: str,
    profiles_by_code: dict[str, FundProfile],
    profile_service: FundProfileService,
) -> None:
    """Create or heal the compatibility profile for a newly bought fund.

    The transaction and ledger commit before this compatibility write.  An
    exact retry must therefore run the repair even when deduplication skips the
    transaction, otherwise a transient profile failure hides the position
    permanently.
    """

    if (
        item.direction != "buy"
        or not item.fund_code
        or item.fund_code in profiles_by_code
    ):
        return

    # FundProfileService preserves provisional-name reconciliation and
    # primary-sector side effects while the shared snapshot avoids point reads.
    profile_service.save_profile(
        FundProfile(
            fund_code=item.fund_code,
            fund_name=item.fund_name,
            holding_amount=0,
            holding_shares=0.0,
            shares_baseline_date=_previous_day(confirm_date),
            source="alipay-transaction",
            is_provisional=True,
        ),
        batch_profiles_by_code=profiles_by_code,
    )
    # save_profile invalidates its own cache. Re-prime it with the now-current
    # batch snapshot so the next distinct code still avoids a full-table read.
    profile_service._profiles_cache = list(profiles_by_code.values())


def _seed_amounts_for_new_positions(
    fund_codes: list[str],
    profiles_by_code: dict[str, FundProfile],
) -> None:
    """给全新建仓（holding_amount=0）的基金按有效份额 × 最新净值写入初始金额，
    使其能进入 merge_holdings_with_profiles 展示；精确金额随后由 sync override 重算。"""
    effective_map = compute_effective_shares_map(
        fund_codes,
        profiles_by_code=profiles_by_code,
    )
    for code, effective in effective_map.items():
        if effective <= 0:
            continue
        profile = profiles_by_code.get(code)
        if profile is None or (profile.holding_amount or 0) > 0:
            continue
        nav = get_latest_unit_nav(code)
        if nav is None or nav <= 0:
            continue
        saved = save_fund_profile(
            profile.model_copy(update={"holding_amount": round(effective * nav, 2)})
        )
        profiles_by_code[saved.fund_code] = saved


def apply_parsed_transactions(parsed: list[ParsedTransaction]) -> dict:
    from app.services.portfolio_mutation_guard import portfolio_mutation_guard

    with portfolio_mutation_guard():
        return _apply_parsed_transactions_unlocked(parsed)


def _apply_parsed_transactions_unlocked(parsed: list[ParsedTransaction]) -> dict:
    """写入交易 → 确认 → 重算并返回持仓。

    返回 {"holdings": [...], "inserted": n, "skipped": m, "pending": <仍 pending 条数>}。
    """
    inserted = 0
    resolved_items = _preflight_transaction_truth(parsed)
    skipped = sum(1 for item, _date, _key in resolved_items if not item.fund_code)
    # Stable lock order prevents two reversed MySQL batches from locking unique
    # transaction keys in opposite order before they contend on the ledger head.
    valid_items = sorted(
        (row for row in resolved_items if row[0].fund_code),
        key=lambda row: row[2],
    )
    processed: list[tuple[ParsedTransaction, str, bool]] = []

    # One mutable snapshot serves profile existence checks, profile creation,
    # effective-share folding, and amount seeding for the entire transaction
    # batch. Empty/invalid batches keep the zero-query fast path.
    profiles = list_fund_profiles() if valid_items else []
    profiles_by_code = {profile.fund_code: profile for profile in profiles}
    from app.services.fund_profile import FundProfileService

    profile_service = FundProfileService()
    profile_service._profiles_cache = profiles

    if insert_fund_transaction is not _ORIGINAL_INSERT_FUND_TRANSACTION:
        # Compatibility seam used by unit tests and external adapters.
        for item, confirm_date, dedup_key in valid_items:
            tx = _pending_transaction(
                item,
                confirm_date=confirm_date,
                dedup_key=dedup_key,
            )
            processed.append(
                (item, confirm_date, bool(insert_fund_transaction(tx)))
            )
    else:
        # All pending transaction rows and their matching ledger events commit
        # as one batch. A later item cannot leave an earlier item half-applied.
        user_id = get_request_user_id()
        with _connect() as connection:
            ensure_primary_position_store(connection)
            for item, confirm_date, dedup_key in valid_items:
                tx = _pending_transaction(
                    item,
                    confirm_date=confirm_date,
                    dedup_key=dedup_key,
                )
                cursor = _insert_fund_transaction_on_connection(
                    connection,
                    tx,
                    user_id=user_id,
                )
                was_inserted = cursor.rowcount > 0
                stored_tx = tx if was_inserted else _get_fund_transaction_by_dedup_on_connection(
                    connection,
                    user_id=user_id,
                    dedup_key=tx.dedup_key,
                )
                if stored_tx is None:
                    raise RuntimeError("交易去重记录读取失败")
                if not was_inserted:
                    raced_diff = _truth_diff(
                        stored_tx,
                        item,
                        confirm_date=confirm_date,
                    )
                    if raced_diff:
                        raise TransactionTruthConflict(
                            [
                                {
                                    "transaction_id": stored_tx.id,
                                    "fund_code": stored_tx.fund_code,
                                    "dedup_key": dedup_key,
                                    "diff": raced_diff,
                                    "source": "concurrent_duplicate",
                                }
                            ]
                        )
                supersedes = (
                    f"fund-transaction:{stored_tx.id}:pending"
                    if stored_tx.status == "confirmed"
                    else None
                )
                append_portfolio_ledger_event(
                    user_id=user_id,
                    event=transaction_ledger_event_from_fund_transaction(
                        stored_tx,
                        supersedes_event_id=supersedes,
                    ),
                    connection=connection,
                )
                processed.append((item, confirm_date, was_inserted))

    for item, confirm_date, was_inserted in processed:
        # This compatibility repair is intentionally executed for exact
        # duplicates too: the prior attempt may have committed the ledger and
        # then failed while creating the provisional profile.
        _ensure_buy_profile(
            item,
            confirm_date=confirm_date,
            profiles_by_code=profiles_by_code,
            profile_service=profile_service,
        )

        if not was_inserted:
            skipped += 1
            continue
        inserted += 1

    confirm_pending_transactions()
    _seed_amounts_for_new_positions(
        [item.fund_code for item in parsed if item.fund_code],
        profiles_by_code,
    )

    from app.services.portfolio_holdings_service import sync_portfolio_from_profiles

    holdings = sync_portfolio_from_profiles(refresh_sectors=True)
    pending = len(list_pending_fund_transactions())
    return {
        "holdings": [holding.model_dump(mode="json") for holding in holdings],
        "inserted": inserted,
        "skipped": skipped,
        "pending": pending,
    }
