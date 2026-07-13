"""荐基 LLM 上下文：目标板块的资金流 / 分时 / 信号（对齐日报 per-holding 字段）。"""

from __future__ import annotations

from typing import Any

from app.services.sector_fund_flow_context import build_sector_fund_flow_context
from app.services.sector_intraday_summary import summarize_sector_intraday_for_label
from app.services.sector_signal_context import signal_backtest_for_sector
from app.services.trading_session import get_effective_trade_date


def _slim_sector_fund_flow(flow: dict[str, Any]) -> dict[str, Any]:
    if not flow.get("available"):
        return {
            "available": False,
            "sector_label": flow.get("sector_label"),
            "message": flow.get("message"),
        }
    return {
        k: flow[k]
        for k in (
            "available",
            "sector_label",
            "trade_date",
            "flow_date",
            "date_aligned",
            "today_main_force_net_yi",
            "main_force_direction",
            "cumulative_5d_net_yi",
            "cumulative_20d_net_yi",
            "flow_tiers",
            "flow_structure_hint",
            "pattern_label",
            "pattern_hint",
        )
        if k in flow
    }


def _slim_intraday(intraday: dict[str, Any]) -> dict[str, Any]:
    return {
        k: intraday[k]
        for k in (
            "sector_label",
            "session_date",
            "close_change_percent",
            "pattern_label",
            "pattern_hint",
            "pullback_from_high_percent",
        )
        if k in intraday
    }


def build_target_sector_context(
    sector_labels: list[str],
    sector_heat: list[dict],
    signal_backtest: dict[str, Any] | None,
    *,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """为 scan 目标板块补充热度 + 资金流 + 分时 + 信号回测（与日报同源）。"""
    effective_date = trade_date or get_effective_trade_date()
    heat_by_label = {
        str(row.get("sector_label")): row
        for row in sector_heat
        if row.get("sector_label")
    }
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw_label in sector_labels:
        label = (raw_label or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        heat = heat_by_label.get(label) or {}
        change_1d = heat.get("change_1d_percent")
        entry: dict[str, Any] = {
            "sector_label": label,
            "heat_score": heat.get("heat_score"),
            "change_1d_percent": change_1d,
            "change_5d_percent": heat.get("change_5d_percent"),
        }
        flow = build_sector_fund_flow_context(
            label,
            sector_return_percent=change_1d if isinstance(change_1d, (int, float)) else None,
            trade_date=effective_date,
        )
        if flow:
            entry["sector_fund_flow"] = _slim_sector_fund_flow(flow)
        intraday = summarize_sector_intraday_for_label(label)
        if intraday:
            entry["sector_intraday"] = _slim_intraday(intraday)
        sector_signal = signal_backtest_for_sector(label, signal_backtest)
        if sector_signal:
            entry["signal_backtest"] = sector_signal
        result.append(entry)
    return result


def build_candidate_factor_scores(candidate_pool: list[dict]) -> dict[str, Any]:
    """候选基金因子分（best-effort，与日报 factor_scores 同源）。"""
    from app.models import Holding
    from app.services.portfolio_snapshot import build_factor_scores_for_facts

    stubs: list[Holding] = []
    eligible = [
        item
        for item in candidate_pool
        if not isinstance(item.get("quality_gate"), dict)
        or item["quality_gate"].get("status") == "eligible"
    ]
    for item in eligible[:12]:
        code = str(item.get("fund_code") or "").strip()
        if not code:
            continue
        stubs.append(
            Holding(
                fund_code=code.zfill(6),
                fund_name=str(item.get("fund_name") or code),
                holding_amount=0.0,
            )
        )
    if not stubs:
        return {"available": False, "message": "无候选基金"}
    try:
        return build_factor_scores_for_facts(stubs)
    except Exception:  # noqa: BLE001
        return {"available": False, "message": "因子分暂不可用"}
