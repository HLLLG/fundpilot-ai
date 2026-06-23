from __future__ import annotations

import re
from typing import Literal

from app.models import DailyProfitSource, Holding, HoldingFieldWarning, HoldingListDiff, PortfolioSummary
from app.services.fund_code_resolver import UNRESOLVED_FUND_CODE_HINT
from app.services.fund_profile import _is_valid_sector_label
from app.services.holding_metrics import compute_estimated_daily_return_percent

WarningSeverity = Literal["error", "warn", "info"]

_NAME_NORMALIZE_RE = re.compile(r"[\s.·…]+")


def normalize_fund_name(name: str) -> str:
    return _NAME_NORMALIZE_RE.sub("", name.strip().lower())


def validate_holdings(
    holdings: list[Holding],
    *,
    account_daily_profit: float | None = None,
    account_daily_profit_source: DailyProfitSource | None = None,
) -> list[HoldingFieldWarning]:
    warnings: list[HoldingFieldWarning] = []
    for index, holding in enumerate(holdings):
        warnings.extend(_warnings_for_holding(index, holding))

    if account_daily_profit is not None and holdings:
        row_sum = sum(h.daily_profit or 0 for h in holdings if h.daily_profit is not None)
        has_settled_daily = _holdings_have_settled_daily(holdings)
        source = account_daily_profit_source or (
            "penetration_estimate" if not has_settled_daily else "settled"
        )

        if not has_settled_daily and source == "penetration_estimate":
            warnings.append(
                HoldingFieldWarning(
                    index=-1,
                    field="daily_profit",
                    code="account_daily_penetration_estimate",
                    message=(
                        f"收盘前养基宝各行「当日收益」多为「-」尚未结算；账户顶部 "
                        f"{account_daily_profit:+.2f} 为场内穿透估算，可作参考。"
                        f"收盘后若截图带出分基金当日收益，将以实际值为准并参与合计校验。"
                    ),
                    severity="info",
                )
            )
        elif has_settled_daily and _significant_mismatch(row_sum, account_daily_profit):
            warnings.append(
                HoldingFieldWarning(
                    index=-1,
                    field="daily_profit",
                    code="account_daily_sum_mismatch",
                    message=(
                        f"各行当日收益合计 {row_sum:+.2f} 与账户当日收益 "
                        f"{account_daily_profit:+.2f} 不一致，请核对收益额符号。"
                    ),
                    severity="warn",
                )
            )
    return warnings


def diff_holdings(
    previous: list[Holding],
    current: list[Holding],
) -> list[HoldingListDiff]:
    if not previous:
        return [
            HoldingListDiff(
                index=index,
                fund_code=holding.fund_code,
                fund_name=holding.fund_name,
                change_type="added",
                messages=["首次录入"],
            )
            for index, holding in enumerate(current)
        ]

    diffs: list[HoldingListDiff] = []
    matched_current: set[int] = set()

    for prev in previous:
        index, current_holding = _match_holding(prev, current, matched_current)
        if current_holding is None:
            diffs.append(
                HoldingListDiff(
                    fund_code=prev.fund_code,
                    fund_name=prev.fund_name,
                    change_type="removed",
                    messages=["总览中未出现，可能已清仓或 OCR 漏识别"],
                )
            )
            continue

        matched_current.add(index)
        messages = _change_messages(prev, current_holding)
        diffs.append(
            HoldingListDiff(
                index=index,
                fund_code=current_holding.fund_code,
                fund_name=current_holding.fund_name,
                change_type="changed" if messages else "unchanged",
                messages=messages,
            )
        )

    for index, holding in enumerate(current):
        if index in matched_current:
            continue
        diffs.append(
            HoldingListDiff(
                index=index,
                fund_code=holding.fund_code,
                fund_name=holding.fund_name,
                change_type="added",
                messages=["新出现在总览中"],
            )
        )
    return diffs


def merge_holdings_with_previous(
    previous: list[Holding],
    current: list[Holding],
) -> list[Holding]:
    if not previous:
        return list(current)

    used: set[int] = set()
    merged: list[Holding] = []

    for prev in previous:
        index, cur = _match_holding(prev, current, used)
        if cur is None:
            merged.append(prev)
            continue
        used.add(index)
        merged.append(
            prev.model_copy(
                update={
                    "holding_amount": cur.holding_amount,
                    "daily_profit": cur.daily_profit,
                    "daily_return_percent": cur.daily_return_percent,
                    "holding_profit": cur.holding_profit,
                    "holding_return_percent": cur.holding_return_percent
                    if cur.holding_return_percent is not None
                    else cur.return_percent,
                    "return_percent": cur.holding_return_percent
                    if cur.holding_return_percent is not None
                    else cur.return_percent,
                    "sector_name": cur.sector_name or prev.sector_name,
                    "sector_return_percent": cur.sector_return_percent,
                    "fund_code": prev.fund_code
                    if prev.fund_code != "000000"
                    else cur.fund_code,
                    "fund_name": prev.fund_name
                    if len(prev.fund_name) >= len(cur.fund_name)
                    else cur.fund_name,
                }
            )
        )

    for index, holding in enumerate(current):
        if index not in used:
            merged.append(holding)
    return merged


def build_holding_review(
    holdings: list[Holding],
    *,
    previous_holdings: list[Holding] | None,
    portfolio_summary: PortfolioSummary | None,
) -> dict:
    account_daily = (
        portfolio_summary.daily_profit if portfolio_summary is not None else None
    )
    account_source = (
        portfolio_summary.daily_profit_source if portfolio_summary is not None else None
    )
    warnings = validate_holdings(
        holdings,
        account_daily_profit=account_daily,
        account_daily_profit_source=account_source,
    )
    diffs = diff_holdings(previous_holdings or [], holdings)
    return {
        "holding_warnings": [item.model_dump() for item in warnings],
        "holding_diffs": [item.model_dump() for item in diffs],
        "previous_holdings": [
            item.model_dump() for item in (previous_holdings or [])
        ],
        "warning_count": len([w for w in warnings if w.severity != "info"]),
    }


def _warnings_for_holding(index: int, holding: Holding) -> list[HoldingFieldWarning]:
    warnings: list[HoldingFieldWarning] = []

    if holding.fund_code == "000000":
        warnings.append(
            HoldingFieldWarning(
                index=index,
                field="fund_code",
                code="missing_fund_code",
                message=UNRESOLVED_FUND_CODE_HINT,
                severity="warn",
            )
        )

    daily_pct = holding.daily_return_percent
    daily_amt = holding.daily_profit
    if daily_pct is not None and daily_amt is not None:
        if daily_pct < 0 < daily_amt or daily_pct > 0 > daily_amt:
            warnings.append(
                HoldingFieldWarning(
                    index=index,
                    field="daily_profit",
                    code="daily_profit_sign_mismatch",
                    message="当日收益额与当日收益率符号不一致，亏损应为负数。",
                    severity="error",
                )
            )

    if holding.sector_name and not _is_valid_sector_label(holding.sector_name):
        warnings.append(
            HoldingFieldWarning(
                index=index,
                field="sector_name",
                code="invalid_sector_label",
                message=(
                    f"关联板块「{holding.sector_name}」无效，请在基金详情中修正板块映射"
                    "或使用「修复无效关联板块」。"
                ),
                severity="warn",
            )
        )

    sector_pct = holding.sector_return_percent
    if daily_pct is not None and sector_pct is not None:
        if daily_pct < 0 < sector_pct or daily_pct > 0 > sector_pct:
            warnings.append(
                HoldingFieldWarning(
                    index=index,
                    field="sector_return_percent",
                    code="sector_sign_mismatch",
                    message="板块涨跌与当日收益率符号不一致，请核对。",
                    severity="warn",
                )
            )

    return warnings


def _change_messages(previous: Holding, current: Holding) -> list[str]:
    messages: list[str] = []
    if previous.holding_amount and current.holding_amount:
        delta_ratio = abs(current.holding_amount - previous.holding_amount) / previous.holding_amount
        if delta_ratio > 0.35:
            messages.append(
                f"持有金额变化 {((current.holding_amount - previous.holding_amount) / previous.holding_amount) * 100:+.1f}%"
            )
    if previous.daily_profit is not None and current.daily_profit is not None:
        if _sign_flip(previous.daily_profit, current.daily_profit):
            messages.append("当日收益额符号与上次相反，请确认")
    if previous.fund_code != "000000" and current.fund_code == "000000":
        messages.append("基金代码丢失，将尝试沿用档案名称")
    return messages


def _match_holding(
    target: Holding,
    candidates: list[Holding],
    skip: set[int],
) -> tuple[int | None, Holding | None]:
    target_name = normalize_fund_name(target.fund_name)
    for index, candidate in enumerate(candidates):
        if index in skip:
            continue
        if target.fund_code != "000000" and candidate.fund_code == target.fund_code:
            return index, candidate
        if normalize_fund_name(candidate.fund_name) == target_name:
            return index, candidate
        if target_name and (
            target_name in normalize_fund_name(candidate.fund_name)
            or normalize_fund_name(candidate.fund_name) in target_name
        ):
            return index, candidate
    return None, None


def infer_daily_profit_source(
    portfolio_summary: PortfolioSummary | None,
    holdings: list[Holding],
) -> DailyProfitSource | None:
    if portfolio_summary is None or portfolio_summary.daily_profit is None:
        return None
    if portfolio_summary.daily_profit_source:
        return portfolio_summary.daily_profit_source
    if not _holdings_have_settled_daily(holdings):
        return "penetration_estimate"
    return "settled"


def can_allocate_penetration_daily(
    portfolio_summary: PortfolioSummary | None,
    holdings: list[Holding],
) -> bool:
    if not holdings:
        return False
    return infer_daily_profit_source(portfolio_summary, holdings) == "penetration_estimate"


def enrich_portfolio_summary_source(
    portfolio_summary: PortfolioSummary | None,
    holdings: list[Holding],
) -> PortfolioSummary | None:
    if portfolio_summary is None:
        return None
    if portfolio_summary.daily_profit_source is not None:
        return portfolio_summary
    inferred = infer_daily_profit_source(portfolio_summary, holdings)
    if inferred is None:
        return portfolio_summary
    return portfolio_summary.model_copy(update={"daily_profit_source": inferred})


def _holdings_have_settled_daily(holdings: list[Holding]) -> bool:
    return any(
        holding.daily_profit is not None or holding.daily_return_percent is not None
        for holding in holdings
    )


def _significant_mismatch(a: float, b: float, *, tolerance: float = 1.0) -> bool:
    if abs(a - b) <= tolerance:
        return False
    if a == 0 or b == 0:
        return abs(a - b) > tolerance
    return (a > 0) != (b > 0) or abs(a - b) / max(abs(a), abs(b)) > 0.15


def _sign_flip(previous: float, current: float) -> bool:
    return previous != 0 and current != 0 and (previous > 0) != (current > 0)
