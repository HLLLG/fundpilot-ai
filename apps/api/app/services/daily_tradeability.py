"""Deterministic transaction gates for recommendations on existing holdings.

The discovery allocator and a daily holding report answer different questions.
Discovery needs an initial-purchase gate; a daily report normally describes an
already-held share class and therefore must use the provider's *additional*
purchase minimum.  Redemption is intentionally kept separate: the provider can
say that redemptions are open, but the current portfolio model has no per-lot
acquisition ledger, so it cannot certify a lock-up period or the applicable
redemption-fee tier.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

HOLDING_TRANSACTION_EXECUTION_SCHEMA_VERSION = "holding_transaction_execution.v1"


def build_holding_transaction_execution(
    tradeability: Mapping[str, Any] | None,
    *,
    holding_amount_yuan: float | int | None,
) -> dict[str, Any]:
    """Project raw tradeability into the daily-report execution contract.

    A positive holding amount proves that this report is about an existing
    position, so an add uses the explicit additional-purchase minimum.  We never
    substitute the initial minimum when that field is missing.
    """

    value = tradeability if isinstance(tradeability, Mapping) else {}
    reasons: list[str] = []
    purchase_reasons: list[str] = []
    redemption_reasons: list[str] = []

    holding_amount = _finite_nonnegative(holding_amount_yuan)
    existing_holding_confirmed = holding_amount is not None and holding_amount > 0
    if not existing_holding_confirmed:
        purchase_reasons.append("existing_holding_not_confirmed")

    data_status = str(value.get("data_status") or "unavailable")
    freshness = str(value.get("freshness") or "unavailable")
    if str(value.get("schema_version") or "") != "fund_tradeability.v1":
        reasons.append("tradeability_schema_invalid")
    if data_status not in {"complete", "partial"} or freshness != "fresh":
        reasons.append("tradeability_not_fresh")
    if not str(value.get("checked_at") or "").strip():
        reasons.append("tradeability_checked_at_missing")
    if not any(str(source).strip() for source in value.get("source_ids") or []):
        reasons.append("tradeability_source_missing")
    if value.get("source_conflict") is True:
        reasons.append("tradeability_source_conflict")
    if str(value.get("currency") or "unknown") != "CNY":
        reasons.append("currency_not_verified_cny")

    purchase_state = str(value.get("purchase_state") or "unknown")
    if purchase_state not in {"open", "limited"}:
        purchase_reasons.append("purchase_not_open")

    redemption_state = str(value.get("redemption_state") or "unknown")
    if redemption_state != "open":
        redemption_reasons.append("redemption_not_open")

    additional_minimum = _finite_positive(
        value.get("minimum_additional_purchase_yuan")
    )
    if additional_minimum is None:
        purchase_reasons.append("additional_minimum_unknown")

    limit = _finite_nonnegative(value.get("daily_purchase_limit_yuan"))
    unlimited = value.get("daily_purchase_limit_unlimited") is True
    if purchase_state == "limited" and (limit is None or limit <= 0):
        purchase_reasons.append("limited_purchase_requires_finite_positive_limit")
    elif limit is None and not unlimited:
        purchase_reasons.append("daily_purchase_limit_unknown")
    elif limit is not None and limit <= 0:
        purchase_reasons.append("daily_purchase_limit_zero")
    elif (
        limit is not None
        and additional_minimum is not None
        and limit < additional_minimum
    ):
        purchase_reasons.append("limit_below_additional_minimum")

    common_reasons = list(dict.fromkeys(reasons))
    redemption_block_reasons = list(
        dict.fromkeys([*common_reasons, *redemption_reasons])
    )
    # A daily add must remain redeemable as well; otherwise it can create an
    # accidental one-way position even when purchase status itself is open.
    add_block_reasons = list(
        dict.fromkeys(
            [*common_reasons, *purchase_reasons, *redemption_reasons]
        )
    )
    fee_rules_present = bool(value.get("redemption_fee_tiers"))
    # Fee freshness is independent from purchase-status freshness. Never let a
    # fresh status snapshot certify stale or missing fee rules.
    fee_freshness = str(value.get("fee_freshness") or "unavailable")

    return {
        "schema_version": HOLDING_TRANSACTION_EXECUTION_SCHEMA_VERSION,
        "existing_holding_confirmed": existing_holding_confirmed,
        "purchase_minimum_basis": "existing_holding_additional_purchase",
        "first_or_additional_semantics": (
            "当前持仓金额为正，按追加申购门槛核验"
            if existing_holding_confirmed
            else "无法确认已有持仓，不得猜测首次或追加申购门槛"
        ),
        "add_status": "eligible" if not add_block_reasons else "watch_only",
        "add_block_reasons": add_block_reasons,
        "effective_additional_min_purchase_yuan": _rounded_money(
            additional_minimum
        ),
        "max_purchase_yuan": _rounded_money(limit),
        "max_purchase_unlimited": unlimited,
        "max_period": "day",
        "redemption_status": (
            "eligible" if not redemption_block_reasons else "watch_only"
        ),
        "redemption_block_reasons": redemption_block_reasons,
        "acquisition_lot_status": "unverified",
        "minimum_holding_period_at_lot_status": "unverified",
        "redemption_fee_at_lot_age_status": "unverified",
        "redemption_fee_rules_status": (
            "available_for_manual_review"
            if fee_rules_present and fee_freshness == "fresh"
            else "unavailable"
        ),
        # Until the portfolio owns per-lot acquisition timestamps, a reduction
        # may be a valid risk recommendation but cannot carry an executable
        # amount or percentage.
        "reduction_amount_status": "manual_review",
        "revalidation_required": True,
        "instruction": (
            "加仓仅在追加起购额、单日限额及申赎状态均核验通过后可生成金额；"
            "减仓须人工核对逐笔持有期、锁定期和适用赎回费，不得推测。"
        ),
    }


def assess_holding_add_amount(
    tradeability: Mapping[str, Any] | None,
    *,
    holding_amount_yuan: float | int | None,
    amount_yuan: float | int | None,
) -> dict[str, Any]:
    """Validate and, when safe, cap one proposed daily add amount."""

    execution = build_holding_transaction_execution(
        tradeability,
        holding_amount_yuan=holding_amount_yuan,
    )
    reasons = list(execution["add_block_reasons"])
    amount = _finite_positive(amount_yuan)
    if amount is None:
        reasons.append("invalid_or_missing_add_amount")

    minimum = _finite_positive(
        execution.get("effective_additional_min_purchase_yuan")
    )
    if amount is not None and minimum is not None and amount < minimum:
        reasons.append("below_additional_minimum")

    limit = _finite_nonnegative(execution.get("max_purchase_yuan"))
    approved_amount = amount
    capped = False
    if amount is not None and limit is not None and amount > limit:
        approved_amount = limit
        capped = True
    if (
        approved_amount is not None
        and minimum is not None
        and approved_amount < minimum
    ):
        reasons.append("capped_amount_below_additional_minimum")

    reasons = list(dict.fromkeys(reasons))
    executable = not reasons and approved_amount is not None
    return {
        "schema_version": "holding_add_amount_assessment.v1",
        "executable": executable,
        "requested_amount_yuan": _rounded_money(amount),
        "approved_amount_yuan": (
            _rounded_money(approved_amount) if executable else None
        ),
        "amount_capped_by_daily_limit": bool(executable and capped),
        "minimum_additional_purchase_yuan": _rounded_money(minimum),
        "daily_purchase_limit_yuan": _rounded_money(limit),
        "daily_purchase_limit_unlimited": bool(
            execution.get("max_purchase_unlimited") is True
        ),
        "block_reasons": reasons,
    }


def _finite_nonnegative(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _finite_positive(value: object) -> float | None:
    parsed = _finite_nonnegative(value)
    return parsed if parsed is not None and parsed > 0 else None


def _rounded_money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None
