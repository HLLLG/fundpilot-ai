"""把尚未确认的交易叠到持仓 JSON 上，供看板展示在途仓位。

不改快照、不改有效份额、不计入当日/持有收益。``status='pending'`` 的买入
（含「交易进行中」）会在列表里出现，避免用户以为导入失败。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.models import FundTransaction
from app.request_context import try_get_request_user_id

logger = logging.getLogger(__name__)


def overlay_pending_transaction_previews(serialized: list[dict]) -> list[dict]:
    grouped = _pending_groups()
    if not grouped:
        return serialized

    overlaid: list[dict] = []
    seen: set[str] = set()
    for item in serialized:
        row = dict(item)
        code = str(row.get("fund_code") or "")
        preview = grouped.get(code)
        if preview is not None:
            _apply_preview(row, preview)
            seen.add(code)
        overlaid.append(row)

    pending_only = [
        _preview_holding(code, preview)
        for code, preview in grouped.items()
        if code not in seen and preview["buy_amount"] > 0
    ]
    return pending_only + overlaid


def _pending_groups() -> dict[str, dict[str, object]]:
    from app.database import list_pending_fund_transactions

    if try_get_request_user_id() is None:
        return {}
    try:
        pending = list_pending_fund_transactions()
    except Exception:
        logger.exception("读取待确认交易失败，持仓预览跳过在途仓位")
        return {}
    grouped: dict[str, dict[str, object]] = {}
    buckets: dict[str, list[FundTransaction]] = defaultdict(list)
    for tx in pending:
        code = (tx.fund_code or "").strip()
        if not code or code == "000000":
            continue
        buckets[code].append(tx)
    for code, txs in buckets.items():
        buy_amount = round(sum(tx.amount_yuan for tx in txs if tx.direction == "buy"), 2)
        sell_amount = round(sum(tx.amount_yuan for tx in txs if tx.direction == "sell"), 2)
        grouped[code] = {
            "fund_name": next((tx.fund_name for tx in reversed(txs) if tx.fund_name), code),
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "count": len(txs),
            "has_in_progress": any(tx.in_progress for tx in txs),
        }
    return grouped


def _apply_preview(row: dict, preview: dict[str, object]) -> None:
    buy_amount = float(preview["buy_amount"])
    sell_amount = float(preview["sell_amount"])
    row["pending_buy_amount"] = buy_amount if buy_amount > 0 else None
    row["pending_sell_amount"] = sell_amount if sell_amount > 0 else None
    row["pending_transaction_count"] = int(preview["count"])
    row["has_in_progress_transactions"] = bool(preview["has_in_progress"])


def _preview_holding(fund_code: str, preview: dict[str, object]) -> dict:
    row = {
        "fund_code": fund_code,
        "fund_name": preview["fund_name"],
        "holding_amount": 0.0,
        "settled_holding_amount": 0.0,
        "display_holding_amount": 0.0,
        "return_percent": 0.0,
        "daily_profit": None,
        "daily_return_percent": None,
        "holding_profit": None,
        "holding_return_percent": None,
        "estimated_holding_profit": None,
        "estimated_holding_return_percent": None,
        "estimated_daily_return_percent": None,
        "daily_return_is_estimated": False,
        "holding_return_is_estimated": False,
        "profit_accrual_deferred": True,
        "daily_return_percent_source": "pending_accrual",
        "unsettled_preview": True,
    }
    _apply_preview(row, preview)
    return row
