"""Read model for user-recorded realized transaction fee evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
import math


SCHEMA_VERSION = "portfolio_realized_fee_evidence.v1"


def build_portfolio_fee_evidence(transactions: Sequence[Any]) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for transaction in transactions:
        if str(_field(transaction, "status") or "") != "confirmed":
            continue
        amount = _positive_number(_field(transaction, "amount_yuan"))
        if amount is None:
            continue
        fee = _nonnegative_number(_field(transaction, "fee_yuan"))
        eligible.append(
            {
                "fund_code": _fund_code(_field(transaction, "fund_code")),
                "fund_name": str(_field(transaction, "fund_name") or "").strip() or None,
                "direction": str(_field(transaction, "direction") or "unknown"),
                "amount_yuan": amount,
                "fee_yuan": fee,
            }
        )

    known = [row for row in eligible if row["fee_yuan"] is not None]
    known_amount = sum(float(row["amount_yuan"]) for row in known)
    known_fee = sum(float(row["fee_yuan"]) for row in known)
    coverage = round(len(known) / len(eligible) * 100.0, 2) if eligible else None
    status = "not_started" if not eligible else "available" if len(known) == len(eligible) else "collecting"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "evidence_basis": "user_recorded_actual_transaction_fee",
        "external_receipt_verified": False,
        "confirmed_transaction_count": len(eligible),
        "known_fee_transaction_count": len(known),
        "unknown_fee_transaction_count": len(eligible) - len(known),
        "known_fee_coverage_percent": coverage,
        "known_fee_transaction_amount_yuan": round(known_amount, 2),
        "total_recorded_fee_yuan": round(known_fee, 2) if known else None,
        "weighted_recorded_fee_percent": (
            round(known_fee / known_amount * 100.0, 6) if known_amount > 0 else None
        ),
        "by_direction": _group_summary(known, "direction"),
        "by_fund": _group_summary(known, "fund_code", include_name=True),
        "candidate_cost_model_eligible": False,
        "automatic_model_update_allowed": False,
        "notices": [
            "只有用户从原平台确认并录入的逐笔手续费才算已知；空值不会按 0 处理。",
            "历史已成交费用不能直接外推为新候选基金或未来渠道费率。",
            "当前证据用于核账和覆盖率诊断，不会自动修改 DecisionScore 或调仓。",
        ],
    }


def _group_summary(
    rows: Sequence[Mapping[str, Any]],
    key: str,
    *,
    include_name: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        grouped.setdefault(value, []).append(row)
    output: list[dict[str, Any]] = []
    for value, items in sorted(grouped.items()):
        amount = sum(float(item["amount_yuan"]) for item in items)
        fee = sum(float(item["fee_yuan"]) for item in items)
        result: dict[str, Any] = {
            key: value,
            "transaction_count": len(items),
            "transaction_amount_yuan": round(amount, 2),
            "fee_yuan": round(fee, 2),
            "weighted_fee_percent": round(fee / amount * 100.0, 6) if amount > 0 else None,
        }
        if include_name:
            result["fund_name"] = next(
                (str(item.get("fund_name")) for item in items if item.get("fund_name")),
                None,
            )
        output.append(result)
    return output


def _field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        return None
    return text.zfill(6)


def _positive_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


__all__ = ["SCHEMA_VERSION", "build_portfolio_fee_evidence"]
