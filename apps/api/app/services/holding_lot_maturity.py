from __future__ import annotations

"""持仓批次（acquisition lot）与赎回惩罚费窗口。

## 为什么需要它

日报的减仓语义此前明文写着「当前无逐笔 acquisition lot，实际赎回前须核对持有期与费用」。
用户录入真实买卖交易（`fund_transactions`：确认日、份额、方向）之后这个前提不成立了——
逐笔批次可以按先进先出真实重建，减仓建议因此能回答一个此前只能让用户自己查的问题：
**现在赎回会不会触发持有不足 7 天的惩罚性费率（约 1.5%），要等到哪天才过窗。**

这是确定性事实计算（不是预测），与「浮亏封档」同级别的成本类门禁证据；但它**只披露、
不否决**：减仓是风险动作，不能因为费用贵就拦着用户降风险——费用信息让用户自己权衡
"今天减"与"两天后减"。

## 口径

* 批次 = 份额基线（`fund_profiles.holding_shares` @ `shares_baseline_date`，若有）+
  基线日之后每笔已确认买入（`confirm_date`、`shares_delta`），与
  `compute_effective_shares_map` 同一份数据、同一条基线过滤规则，两边不得各算一套。
* 卖出按**先进先出**消减批次——与惩罚费的行业计费口径一致（赎回优先匹配最早买入的
  份额）。卖出份额超过现存批次时溢出量记入 `unmatched_sell_shares`，作为"交易记录
  不完整"的信号披露，不静默吞掉。
* 持有天数按自然日、从买入确认日起算到 `as_of`。实际赎回还要 T+1 确认，真实持有天数
  只会更多——因此 `in_short_hold_window` 是**保守判定**（可能把刚好第 6~7 天的批次多
  警告一天，绝不会漏警告）。
* 基线批次的确认日是基线日，实际买入只会更早：它的 `hold_days` 是**下界**，短持窗口
  判定按下界做同样只会更保守。基线连日期都没有时该批 `hold_days=None`，不参与窗口
  判定——"不知道"不等于"在惩罚期"。
"""

from dataclasses import dataclass
from datetime import date, timedelta
import logging
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

HOLDING_LOT_MATURITY_SCHEMA_VERSION = "holding_lot_maturity.v1"

#: 惩罚性赎回费的持有天数门槛（自然日），与证监会对开放式基金的规定一致；
#: 与回测脚本 `run_position_sizing_backtest.SHORT_HOLD_DAYS` 同一个数。
SHORT_HOLD_DAYS = 7


@dataclass
class _Lot:
    confirm_date: str | None
    shares: float
    source: str  # "baseline" | "transaction"


def build_holding_lot_maturity(
    *,
    fund_code: str,
    transactions: Iterable[Any],
    baseline_shares: float | None,
    baseline_date: str | None,
    as_of_date: str,
    short_hold_days: int = SHORT_HOLD_DAYS,
) -> dict[str, Any]:
    """按先进先出重建某持仓的存续批次并判定惩罚费窗口。

    ``transactions`` 是该基金的 `FundTransaction` 列表（任意顺序，内部会按确认日重排）；
    只消费 `status='confirmed'` 且 `shares_delta` 非空的行——pending 交易的份额还没有
    确认净值，进批次只会制造假精度。
    """
    as_of = _parse_date(as_of_date)
    if as_of is None:
        return _unavailable(fund_code, "as_of_date_invalid")

    events: list[tuple[str, float]] = []
    unmatched_sell = 0.0
    baseline = _parse_date(baseline_date)
    for tx in transactions:
        if str(getattr(tx, "status", "")) != "confirmed":
            continue
        delta = getattr(tx, "shares_delta", None)
        confirm = str(getattr(tx, "confirm_date", "") or "").strip()
        if delta is None or not confirm:
            continue
        confirm_day = _parse_date(confirm)
        if confirm_day is None:
            continue
        # 与 `compute_effective_shares_map` 同一条基线规则：基线日及之前的交易已经被
        # 折进基线份额，再叠加就是双重计数。
        if baseline is not None and confirm_day <= baseline:
            continue
        if confirm_day > as_of:
            continue
        events.append((confirm, float(delta)))
    events.sort(key=lambda item: item[0])

    lots: list[_Lot] = []
    if baseline_shares is not None and baseline_shares > 0:
        lots.append(
            _Lot(
                confirm_date=baseline_date if baseline is not None else None,
                shares=float(baseline_shares),
                source="baseline",
            )
        )
    for confirm, delta in events:
        if delta > 0:
            lots.append(_Lot(confirm_date=confirm, shares=delta, source="transaction"))
            continue
        to_reduce = -delta
        remaining: list[_Lot] = []
        for lot in lots:
            if to_reduce <= 0:
                remaining.append(lot)
                continue
            take = min(lot.shares, to_reduce)
            to_reduce -= take
            kept = lot.shares - take
            if kept > 1e-9:
                remaining.append(
                    _Lot(confirm_date=lot.confirm_date, shares=kept, source=lot.source)
                )
        lots = remaining
        if to_reduce > 1e-9:
            unmatched_sell += to_reduce

    lots = [lot for lot in lots if lot.shares > 1e-9]
    if not lots:
        return _unavailable(
            fund_code,
            "no_surviving_lots",
            unmatched_sell_shares=round(unmatched_sell, 6),
        )

    total = sum(lot.shares for lot in lots)
    threshold = max(1, int(short_hold_days))
    rows: list[dict[str, Any]] = []
    short_hold_shares = 0.0
    dated_lots = 0
    next_penalty_free: date | None = None
    for lot in lots:
        lot_day = _parse_date(lot.confirm_date)
        hold_days = (as_of - lot_day).days if lot_day is not None else None
        in_window = hold_days is not None and hold_days < threshold
        if hold_days is not None:
            dated_lots += 1
        if in_window:
            short_hold_shares += lot.shares
            assert lot_day is not None
            free_day = lot_day + timedelta(days=threshold)
            if next_penalty_free is None or free_day < next_penalty_free:
                next_penalty_free = free_day
        rows.append(
            {
                "confirm_date": lot.confirm_date,
                "shares": round(lot.shares, 6),
                "source": lot.source,
                "hold_days": hold_days,
                # 基线批次的实际买入只会更早、赎回确认只会更晚：两头都让窗口判定偏保守。
                "hold_days_is_lower_bound": lot.source == "baseline",
                "in_short_hold_window": in_window,
            }
        )

    return {
        "schema_version": HOLDING_LOT_MATURITY_SCHEMA_VERSION,
        "available": True,
        "reason": None,
        "fund_code": fund_code,
        "as_of_date": as_of.isoformat(),
        "short_hold_days": threshold,
        "lot_count": len(rows),
        "total_lot_shares": round(total, 6),
        "short_hold_lot_shares": round(short_hold_shares, 6),
        "short_hold_share_percent": (
            round(short_hold_shares / total * 100.0, 2) if total > 0 else None
        ),
        "next_penalty_free_date": (
            next_penalty_free.isoformat() if next_penalty_free is not None else None
        ),
        "undated_lot_count": len(rows) - dated_lots,
        "unmatched_sell_shares": round(unmatched_sell, 6),
        # 卖出对不上批次说明录入的交易不完整，批次结论只覆盖已录入部分。
        "coverage": "partial_records" if unmatched_sell > 1e-9 else "recorded_lots",
        "lots": rows,
    }


def describe_reduction_lot_impact(
    lot_maturity: Mapping[str, Any] | None,
    reduction_percent: float | None,
) -> str | None:
    """给定减仓比例，按先进先出判断会不会触及惩罚费窗口内的批次；无可判信息返回 None。

    减仓比例的分母是当前持仓（与 `suggested_position_change_percent` 同口径）。返回的
    是**披露文案**：费用贵不构成不减仓的理由，只帮用户在"今天减"与"过窗后减"之间权衡。
    """
    if not isinstance(lot_maturity, Mapping) or not lot_maturity.get("available"):
        return None
    percent = _num(reduction_percent)
    if percent is None or percent >= 0:
        return None
    fraction = min(abs(percent) / 100.0, 1.0)
    lots = [row for row in lot_maturity.get("lots") or [] if isinstance(row, Mapping)]
    total = _num(lot_maturity.get("total_lot_shares")) or 0.0
    if not lots or total <= 0:
        return None
    to_reduce = total * fraction
    touched_short = 0.0
    for row in lots:
        if to_reduce <= 0:
            break
        shares = _num(row.get("shares")) or 0.0
        take = min(shares, to_reduce)
        to_reduce -= take
        if row.get("in_short_hold_window"):
            touched_short += take

    threshold = int(lot_maturity.get("short_hold_days") or SHORT_HOLD_DAYS)
    suffix = (
        "（交易记录不完整，仅覆盖已录入批次）"
        if str(lot_maturity.get("coverage") or "") == "partial_records"
        else ""
    )
    if touched_short <= 1e-9:
        return (
            f"按先进先出，本次减仓触及的批次均已持有满 {threshold} 天，"
            f"不触发惩罚性赎回费率{suffix}。"
        )
    share = touched_short / total * 100.0
    free_date = str(lot_maturity.get("next_penalty_free_date") or "").strip()
    when = f"，最早 {free_date} 过窗" if free_date else ""
    return (
        f"按先进先出，本次减仓将触及仍在 {threshold} 天惩罚费窗口内的批次"
        f"（约占当前持仓 {share:.1f}%）{when}；费用不构成回避减仓的理由，"
        f"可自行权衡是否分两步执行{suffix}。"
    )


def build_lot_maturity_by_code(
    holdings: Iterable[Any],
    profiles: Iterable[Any],
    *,
    as_of_date: str,
) -> dict[str, dict[str, Any]]:
    """给日报批量构建每只持仓的批次成熟度；没有任何已确认交易的基金不产出行。

    一次全量读交易表（本地小表）按代码分组，不逐只点查。整段 best-effort：读不到只是
    少一层披露，绝不阻塞日报。
    """
    try:
        from app.database import list_fund_transactions

        by_code: dict[str, list[Any]] = {}
        for tx in list_fund_transactions():
            code = str(getattr(tx, "fund_code", "") or "").strip()
            if code:
                by_code.setdefault(code, []).append(tx)
        if not by_code:
            return {}
        profile_by_code = {
            str(getattr(profile, "fund_code", "") or "").strip(): profile
            for profile in profiles
            if profile is not None
        }
        result: dict[str, dict[str, Any]] = {}
        for holding in holdings:
            code = str(getattr(holding, "fund_code", "") or "").strip()
            if not code or code not in by_code:
                continue
            profile = profile_by_code.get(code)
            maturity = build_holding_lot_maturity(
                fund_code=code,
                transactions=by_code[code],
                baseline_shares=getattr(profile, "holding_shares", None),
                baseline_date=getattr(profile, "shares_baseline_date", None),
                as_of_date=as_of_date,
            )
            if maturity.get("available"):
                result[code] = maturity
        return result
    except Exception:  # noqa: BLE001 — 披露层，绝不阻塞日报
        logger.warning("构建持仓批次成熟度失败，本轮跳过", exc_info=True)
        return {}


def _unavailable(fund_code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": HOLDING_LOT_MATURITY_SCHEMA_VERSION,
        "available": False,
        "reason": reason,
        "fund_code": fund_code,
        **extra,
    }


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
    "HOLDING_LOT_MATURITY_SCHEMA_VERSION",
    "SHORT_HOLD_DAYS",
    "build_holding_lot_maturity",
    "build_lot_maturity_by_code",
    "describe_reduction_lot_impact",
]
