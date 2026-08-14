from __future__ import annotations

"""用户真实买卖行为与系统建议的对照（读取侧，纯披露）。

## 为什么需要它

用户录入真实交易后，系统第一次能看见"建议之外真实发生了什么"。两个用途：

1. **持仓行披露**：日报建议加仓、而用户几天前刚卖过同一只基金（或反过来）时，建议
   显得精神分裂——不一定错（方向证据独立于用户的资金需求），但必须把"你 X 日刚卖过"
   这个事实摆出来，让用户结合自己的操作意图权衡。
2. **组合级对照**：近 N 天每笔交易与交易当日（或此前最近一份）日报建议的方向对照
   （一致 / 中性 / 相反 / 无建议），是决策质量侧此前缺失的"执行层"素材。

## 纪律

只披露、不评判、不改动作：用户的卖出可能是取现、调仓、止盈，系统无从知道动机，
对照结果**不得**用于指责用户或反向修正建议。建议索引来自 `decision_events`
（`source_type='daily'` 的不可变审计账本），不重读报告正文。
"""

from datetime import date, timedelta
import logging
from typing import Any, Iterable, Mapping

from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    ACTION_BUCKET_REDUCE,
    classify_action_bucket,
)

logger = logging.getLogger(__name__)

TRANSACTION_BEHAVIOR_REVIEW_SCHEMA_VERSION = "transaction_behavior_review.v1"

#: 组合级对照回看的自然日窗口。
REVIEW_LOOKBACK_DAYS = 30
#: 持仓行披露"近期反向交易"的自然日窗口：太久之前的操作与今日建议无对照价值。
RECENT_CONFLICT_WINDOW_DAYS = 7
#: 交易与建议的配对窗口：交易日当天或此前最多 N 个自然日内最近一份日报的建议。
_ADVICE_MATCH_WINDOW_DAYS = 3


def summarize_recent_transactions_by_code(
    holdings: Iterable[Any],
    *,
    as_of_date: str,
    lookback_days: int = REVIEW_LOOKBACK_DAYS,
) -> dict[str, dict[str, Any]]:
    """每只持仓近 N 天已确认交易的摘要；窗口内没有交易的基金不产出行。

    摘要给两处用：持仓行 `recent_transactions`（LLM 背景事实）与 guard 的反向交易
    披露。best-effort：读不到只是少一层披露。
    """
    as_of = _parse_date(as_of_date)
    if as_of is None:
        return {}
    held_codes = {
        str(getattr(holding, "fund_code", "") or "").strip()
        for holding in holdings
        if str(getattr(holding, "fund_code", "") or "").strip()
    }
    if not held_codes:
        return {}
    cutoff = (as_of - timedelta(days=max(1, int(lookback_days)))).isoformat()
    try:
        from app.database import list_fund_transactions

        result: dict[str, dict[str, Any]] = {}
        for tx in list_fund_transactions():
            code = str(getattr(tx, "fund_code", "") or "").strip()
            if code not in held_codes or str(getattr(tx, "status", "")) != "confirmed":
                continue
            trade_date = str(getattr(tx, "trade_time", "") or "")[:10]
            if not trade_date or trade_date < cutoff or trade_date > as_of.isoformat():
                continue
            direction = str(getattr(tx, "direction", "") or "")
            if direction not in {"buy", "sell"}:
                continue
            row = result.setdefault(
                code,
                {
                    "schema_version": TRANSACTION_BEHAVIOR_REVIEW_SCHEMA_VERSION,
                    "available": True,
                    "lookback_days": int(lookback_days),
                    "as_of_date": as_of.isoformat(),
                    "buy_count": 0,
                    "sell_count": 0,
                    "last_buy": None,
                    "last_sell": None,
                },
            )
            entry = {
                "trade_date": trade_date,
                "confirm_date": str(getattr(tx, "confirm_date", "") or "") or None,
                "amount_yuan": _num(getattr(tx, "amount_yuan", None)),
            }
            key_count = "buy_count" if direction == "buy" else "sell_count"
            key_last = "last_buy" if direction == "buy" else "last_sell"
            row[key_count] += 1
            previous = row[key_last]
            if previous is None or str(previous.get("trade_date")) < trade_date:
                row[key_last] = entry
        return result
    except Exception:  # noqa: BLE001 — 披露层，绝不阻塞日报
        logger.warning("汇总近期交易失败，本轮跳过", exc_info=True)
        return {}


def recent_transaction_conflict_note(
    recent: Mapping[str, Any] | None,
    final_action: str,
    *,
    window_days: int = RECENT_CONFLICT_WINDOW_DAYS,
) -> str | None:
    """最终动作与用户近几天的真实操作方向相反时的披露文案；无冲突返回 None。

    只披露、不改动作：方向证据独立于用户的资金需求，系统无从知道那笔操作的动机。
    """
    if not isinstance(recent, Mapping) or not recent.get("available"):
        return None
    as_of = _parse_date(recent.get("as_of_date"))
    if as_of is None:
        return None
    bucket = classify_action_bucket(final_action)
    if bucket >= ACTION_BUCKET_ADD:
        last_sell = recent.get("last_sell")
        sell_date = _parse_date((last_sell or {}).get("trade_date"))
        if sell_date is not None and (as_of - sell_date).days <= window_days:
            return (
                f"你 {sell_date.isoformat()} 刚卖出过该基金；本条加仓建议按当前方向证据"
                "独立给出，若此前卖出另有安排（取现/自行止盈），请按自己的计划优先。"
            )
        return None
    if bucket <= ACTION_BUCKET_REDUCE:
        last_buy = recent.get("last_buy")
        buy_date = _parse_date((last_buy or {}).get("trade_date"))
        if buy_date is not None and (as_of - buy_date).days <= window_days:
            return (
                f"你 {buy_date.isoformat()} 刚买入过该基金；本条减仓建议按当前风险与方向"
                "证据独立给出，与那笔买入的初衷是否仍成立请一并复核。"
            )
    return None


def build_transaction_behavior_review(
    holdings: Iterable[Any],
    *,
    as_of_date: str,
    lookback_days: int = REVIEW_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """近 N 天每笔交易与当日系统建议的方向对照（组合级，只作背景事实）。

    建议取交易日当天或此前 `_ADVICE_MATCH_WINDOW_DAYS` 个自然日内最近一份日报里该基金
    的最终动作（`decision_events`，`source_type='daily'`）。对照分类：

    * 卖出 × 建议减仓类 → `aligned`；卖出 × 建议加仓 → `contrary`；观察/暂停 → `neutral`
    * 买入 × 建议加仓 → `aligned`；买入 × 建议减仓类 → `contrary`；其余 → `neutral`
    * 窗口内没有建议 → `no_advice`
    """
    as_of = _parse_date(as_of_date)
    held_codes = {
        str(getattr(holding, "fund_code", "") or "").strip()
        for holding in holdings
        if str(getattr(holding, "fund_code", "") or "").strip()
    }
    unavailable = {
        "schema_version": TRANSACTION_BEHAVIOR_REVIEW_SCHEMA_VERSION,
        "available": False,
        "reason": None,
        "rows": [],
        "counts": {},
    }
    if as_of is None or not held_codes:
        return {**unavailable, "reason": "no_holdings_or_date"}
    cutoff = (as_of - timedelta(days=max(1, int(lookback_days)))).isoformat()
    try:
        from app.database import list_fund_transactions
        from app.request_context import get_request_user_id
        from app.services.decision_repository import list_decision_events

        advice_by_code_date: dict[tuple[str, str], str] = {}
        for event in list_decision_events(
            user_id=get_request_user_id(),
            source_type="daily",
            limit=800,
        ):
            code = str((event or {}).get("fund_code") or "").strip()
            decision_date = str((event or {}).get("decision_date") or "").strip()
            action = str(
                (event or {}).get("final_action") or (event or {}).get("action") or ""
            ).strip()
            if not code or not decision_date or not action:
                continue
            key = (code, decision_date)
            # 事件按 decision_at DESC 返回：首个命中即当日最新一份。
            advice_by_code_date.setdefault(key, action)

        rows: list[dict[str, Any]] = []
        counts = {"aligned": 0, "contrary": 0, "neutral": 0, "no_advice": 0}
        for tx in list_fund_transactions():
            code = str(getattr(tx, "fund_code", "") or "").strip()
            if code not in held_codes or str(getattr(tx, "status", "")) != "confirmed":
                continue
            direction = str(getattr(tx, "direction", "") or "")
            if direction not in {"buy", "sell"}:
                continue
            trade_date = str(getattr(tx, "trade_time", "") or "")[:10]
            if not trade_date or trade_date < cutoff or trade_date > as_of.isoformat():
                continue
            advice, advice_date = _advice_on_or_before(
                advice_by_code_date,
                code=code,
                trade_date=trade_date,
            )
            verdict = _classify(direction, advice)
            counts[verdict] += 1
            rows.append(
                {
                    "fund_code": code,
                    "fund_name": str(getattr(tx, "fund_name", "") or ""),
                    "direction": direction,
                    "trade_date": trade_date,
                    "amount_yuan": _num(getattr(tx, "amount_yuan", None)),
                    "advice_action": advice,
                    "advice_date": advice_date,
                    "verdict": verdict,
                }
            )
        rows.sort(key=lambda row: str(row["trade_date"]), reverse=True)
        return {
            "schema_version": TRANSACTION_BEHAVIOR_REVIEW_SCHEMA_VERSION,
            "available": bool(rows),
            "reason": None if rows else "no_transactions_in_window",
            "lookback_days": int(lookback_days),
            "as_of_date": as_of.isoformat(),
            "rows": rows[:20],
            "counts": counts,
            "instruction": (
                "以上是用户近期真实交易与当日系统建议的方向对照，只作背景事实："
                "不得据此批评用户、揣测动机，也不得反向修改今日的动作建议。"
                "verdict=contrary 只说明方向不同，用户可能有资金需求等系统看不见的理由。"
            ),
        }
    except Exception:  # noqa: BLE001 — 披露层，绝不阻塞日报
        logger.warning("构建交易行为对照失败，本轮跳过", exc_info=True)
        return {**unavailable, "reason": "review_error"}


def _advice_on_or_before(
    advice_by_code_date: dict[tuple[str, str], str],
    *,
    code: str,
    trade_date: str,
) -> tuple[str | None, str | None]:
    day = _parse_date(trade_date)
    if day is None:
        return None, None
    for offset in range(_ADVICE_MATCH_WINDOW_DAYS + 1):
        candidate = (day - timedelta(days=offset)).isoformat()
        action = advice_by_code_date.get((code, candidate))
        if action:
            return action, candidate
    return None, None


def _classify(direction: str, advice: str | None) -> str:
    if not advice:
        return "no_advice"
    bucket = classify_action_bucket(advice)
    if direction == "sell":
        if bucket <= ACTION_BUCKET_REDUCE:
            return "aligned"
        if bucket >= ACTION_BUCKET_ADD:
            return "contrary"
        return "neutral"
    if bucket >= ACTION_BUCKET_ADD:
        return "aligned"
    if bucket <= ACTION_BUCKET_REDUCE:
        return "contrary"
    return "neutral"


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


__all__ = [
    "RECENT_CONFLICT_WINDOW_DAYS",
    "REVIEW_LOOKBACK_DAYS",
    "TRANSACTION_BEHAVIOR_REVIEW_SCHEMA_VERSION",
    "build_transaction_behavior_review",
    "recent_transaction_conflict_note",
    "summarize_recent_transactions_by_code",
]
