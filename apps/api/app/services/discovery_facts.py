from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime

from app.models import Holding, InvestorProfile, NewsItem, TopicBrief
from app.services.discovery_sector_context import (
    build_candidate_factor_scores,
    build_target_sector_context,
)
from app.services.discovery_prompt import DISCOVERY_FACTS_INSTRUCTION
from app.services.discovery_strategy import strategy_contract
from app.services.investment_presets import take_profit_threshold_percent
from app.services.market_flow_client import build_stock_connect_flow_context
from app.services.mainline_regime import align_sector_opportunities_with_mainline_snapshot
from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.fund_tradeability import build_tradeability_gate
from app.services.holding_estimates import (
    compute_estimated_daily_return_percent,
    resolve_holding_return_percent,
)
from app.services.news_freshness import build_news_pipeline_context
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_signal_context import build_signal_backtest_context
from app.services.trading_session import build_trading_session

SIGNAL_BACKTEST_TIMEOUT_SECONDS = 5.0
TARGET_SECTOR_CONTEXT_TIMEOUT_SECONDS = 5.0
STOCK_CONNECT_FLOW_TIMEOUT_SECONDS = 3.0


def build_discovery_facts(
    *,
    holdings: list[Holding],
    profile: InvestorProfile,
    target_sectors: list[str],
    sector_heat: list[dict],
    candidate_pool: list[dict],
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    budget_yuan: float | None = None,
    selection_strategy: str = "balanced",
    scan_mode: str = "full_market",
    discovery_strategy: str = "opportunity_first",
    focus_sectors: list[str] | None = None,
    fund_type_preference: str = "any",
    sector_opportunities: list[dict] | None = None,
    mainline_snapshot: dict | None = None,
    budget_enhancements: bool = False,
    decision_at: datetime | None = None,
) -> dict:
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    denominator = resolve_weight_denominator(holdings, profile)
    available_budget = budget_yuan
    if available_budget is None:
        expected = profile.expected_investment_amount or 0.0
        available_budget = max(expected - total_amount, 0.0)

    signal_backtest = (
        _budgeted_signal_backtest(target_sectors)
        if budget_enhancements
        else build_signal_backtest_context(target_sectors)
    )
    session = build_trading_session(decision_at)
    target_context_labels = list(dict.fromkeys(list(target_sectors) + list(focus_sectors or [])))
    target_sector_context = (
        _budgeted_target_sector_context(
            target_context_labels,
            sector_heat,
            signal_backtest,
            trade_date=session.get("effective_trade_date"),
        )
        if budget_enhancements
        else build_target_sector_context(
            target_context_labels,
            sector_heat,
            signal_backtest,
            trade_date=session.get("effective_trade_date"),
        )
    )
    stock_connect_flow = (
        _budgeted_stock_connect_flow(session.get("effective_trade_date"))
        if budget_enhancements
        else build_stock_connect_flow_context(session.get("effective_trade_date"))
    )

    facts: dict = {
        "readonly": True,
        "instruction": DISCOVERY_FACTS_INSTRUCTION,
        "session": session,
        "profile": {
            "decision_style": profile.decision_style,
            "prefer_dca": profile.prefer_dca,
            "avoid_chasing": profile.avoid_chasing,
            "max_drawdown_percent": profile.max_drawdown_percent,
            "account_loss_review_percent": profile.max_drawdown_percent,
            "concentration_limit_percent": profile.concentration_limit_percent,
            "expected_investment_amount": profile.expected_investment_amount,
            "horizon": profile.horizon,
            # Point-in-time user assumption used only for estimated fee-adjusted
            # outcomes; recurring fund expenses are already embedded in NAV.
            "round_trip_fee_percent": profile.round_trip_fee_percent,
            **(
                {
                    "min_net_profit_percent": profile.min_net_profit_percent,
                    "take_profit_threshold_percent": take_profit_threshold_percent(profile),
                    "hold_days_target": profile.hold_days_target,
                }
                if profile.decision_style == "aggressive"
                else {}
            ),
        },
        "portfolio_gap": {
            "holding_count": len(holdings),
            "total_amount": round(total_amount, 2),
            "weight_denominator_yuan": round(denominator, 2),
            "sector_exposure_complete": all(
                bool((holding.sector_name or "").strip()) for holding in holdings
            ),
            "available_budget_yuan": round(available_budget, 2),
            "held_sectors": _held_sector_summary(holdings),
            "holdings_slim": _build_holdings_slim(
                holdings,
                profile,
                trade_date=session.get("effective_trade_date"),
            ),
            "target_sectors": target_sectors,
            "scan_mode": scan_mode,
        },
        "fund_type_preference": fund_type_preference,
        "sector_heat": sector_heat,
        "sector_opportunities": align_sector_opportunities_with_mainline_snapshot(
            sector_opportunities,
            mainline_snapshot,
        ),
        "mainline_snapshot": dict(mainline_snapshot or {}),
        "target_sector_context": target_sector_context,
        "stock_connect_flow": stock_connect_flow,
        "signal_backtest": signal_backtest,
        "news": build_news_pipeline_context(
            market_news,
            topic_briefs,
            now=decision_at,
        ),
        "candidate_pool": candidate_pool,
        "candidate_quality_summary": _candidate_quality_summary(candidate_pool),
        "candidate_peer_summary": _candidate_peer_summary(candidate_pool),
        "candidate_factor_scores": build_candidate_factor_scores(candidate_pool),
        "selection_strategy": selection_strategy,
        "discovery_strategy": discovery_strategy,
        "discovery_strategy_contract": strategy_contract(discovery_strategy),
        "effective_configuration": {
            "scan_goal": scan_mode,
            "discovery_strategy": discovery_strategy,
            "discovery_strategy_contract": strategy_contract(discovery_strategy),
            "selection_policy": "auto_quality",
            "share_class_policy": (
                "tradeability_first_family_selection_standard_fee_upper_bound"
            ),
            "legacy_fund_type_preference": fund_type_preference,
        },
    }

    return facts


def _candidate_peer_summary(candidate_pool: list[dict]) -> dict:
    statuses: dict[str, int] = {}
    groups: dict[str, int] = {}
    formal = 0
    reference = 0
    for item in candidate_pool:
        peer = item.get("peer_rank") if isinstance(item.get("peer_rank"), dict) else {}
        group = item.get("peer_group") if isinstance(item.get("peer_group"), dict) else {}
        benchmark = (
            item.get("benchmark_comparison")
            if isinstance(item.get("benchmark_comparison"), dict)
            else {}
        )
        status = str(peer.get("status") or "unavailable")
        statuses[status] = statuses.get(status, 0) + 1
        group_key = str(group.get("group_key") or "unclassified")
        groups[group_key] = groups.get(group_key, 0) + 1
        if benchmark.get("comparison_role") == "formal_excess":
            formal += 1
        elif benchmark.get("comparison_role") == "tracking_reference":
            reference += 1
    return {
        "schema_version": "candidate_peer_summary.v1",
        "status_counts": statuses,
        "group_counts": groups,
        "formal_benchmark_count": formal,
        "tracking_reference_count": reference,
        "execution_tilt_count": sum(
            1
            for item in candidate_pool
            if isinstance(item.get("peer_rank"), dict)
            and item["peer_rank"].get("execution_tilt_eligible") is True
        ),
        "instruction": (
            "同类分位与基准角色用于研究解释；未经独立执行验证不得改变金额。"
        ),
    }


def _candidate_quality_summary(candidate_pool: list[dict]) -> dict:
    required_fields = [
        "return_3m_percent",
        "return_6m_percent",
        "max_drawdown_1y_percent",
        "fund_scale_yi",
        "established_date",
        "fund_manager",
        "nav_date",
    ]
    statuses = {"eligible": 0, "watch_only": 0, "excluded": 0}
    missing_field_counts = {field: 0 for field in required_fields}
    profile_status_counts: dict[str, int] = {}
    profile_source_counts: dict[str, int] = {}
    tradeability_gate_counts = {"eligible": 0, "watch_only": 0, "excluded": 0}
    purchase_state_counts: dict[str, int] = {}
    fee_status_counts: dict[str, int] = {}
    revalidation_required_count = 0
    coverage_values: list[float] = []
    for item in candidate_pool:
        gate = item.get("quality_gate") if isinstance(item.get("quality_gate"), dict) else {}
        status = str(gate.get("status") or "watch_only")
        if status not in statuses:
            status = "watch_only"
        statuses[status] += 1
        for field in gate.get("missing_fields") or []:
            if field in missing_field_counts:
                missing_field_counts[field] += 1
        profile_status = str(gate.get("profile_status") or item.get("profile_status") or "unknown")
        profile_status_counts[profile_status] = profile_status_counts.get(profile_status, 0) + 1
        for source in gate.get("profile_sources") or item.get("profile_sources") or []:
            label = str(source).strip()
            if label:
                profile_source_counts[label] = profile_source_counts.get(label, 0) + 1
        value = gate.get("coverage_percent")
        try:
            if value is not None:
                coverage_values.append(float(value))
        except (TypeError, ValueError):
            pass
        tradeability = (
            item.get("tradeability")
            if isinstance(item.get("tradeability"), dict)
            else None
        )
        execution_gate = build_tradeability_gate(tradeability)
        gate_status = str(execution_gate.get("status") or "watch_only")
        if gate_status not in tradeability_gate_counts:
            gate_status = "watch_only"
        tradeability_gate_counts[gate_status] += 1
        purchase_state = str((tradeability or {}).get("purchase_state") or "unknown")
        purchase_state_counts[purchase_state] = purchase_state_counts.get(purchase_state, 0) + 1
        fee_status = str(
            (tradeability or {}).get("share_class_fee_status") or "unverified"
        )
        fee_status_counts[fee_status] = fee_status_counts.get(fee_status, 0) + 1
        if execution_gate.get("revalidation_required") is True:
            revalidation_required_count += 1
    return {
        "total_count": len(candidate_pool),
        "eligible_count": statuses["eligible"],
        "watch_only_count": statuses["watch_only"],
        "excluded_count": statuses["excluded"],
        "required_fields": required_fields,
        "missing_field_counts": missing_field_counts,
        "profile_status_counts": profile_status_counts,
        "profile_source_counts": profile_source_counts,
        "tradeability_gate_counts": tradeability_gate_counts,
        "purchase_state_counts": purchase_state_counts,
        "fee_status_counts": fee_status_counts,
        "revalidation_required_count": revalidation_required_count,
        "coverage_percent": (
            round(sum(coverage_values) / len(coverage_values), 1)
            if coverage_values
            else 0.0
        ),
    }


def _signal_backtest_unavailable(reason: str) -> dict:
    return {
        "enabled": True,
        "has_data": False,
        "reason": reason,
        "message": "板块信号回测未在预算内完成，荐基已按价格与资金流事实继续。",
        "summary_lines": [],
        "sectors": [],
    }


def _budgeted_signal_backtest(target_sectors: list[str]) -> dict:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery-facts-budget")
    future = executor.submit(lambda: build_signal_backtest_context(target_sectors))
    try:
        return future.result(timeout=SIGNAL_BACKTEST_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        return _signal_backtest_unavailable("timeout")
    except Exception:  # noqa: BLE001 - discovery signal context is best-effort
        return _signal_backtest_unavailable("error")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _budgeted_target_sector_context(
    sector_labels: list[str],
    sector_heat: list[dict],
    signal_backtest: dict,
    *,
    trade_date: str | None,
) -> list[dict]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery-context-budget")
    future = executor.submit(
        lambda: build_target_sector_context(
            sector_labels,
            sector_heat,
            signal_backtest,
            trade_date=trade_date,
        )
    )
    try:
        return future.result(timeout=TARGET_SECTOR_CONTEXT_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        return _basic_target_sector_context(sector_labels, sector_heat, "timeout")
    except Exception:  # noqa: BLE001 - context enhancement is best-effort
        return _basic_target_sector_context(sector_labels, sector_heat, "error")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _budgeted_stock_connect_flow(trade_date: str | None) -> dict:
    executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="discovery-stock-connect-flow-budget",
    )
    future = executor.submit(lambda: build_stock_connect_flow_context(trade_date))
    try:
        return future.result(timeout=STOCK_CONNECT_FLOW_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        return _stock_connect_flow_unavailable("timeout")
    except Exception:  # noqa: BLE001 - market flow is best-effort
        return _stock_connect_flow_unavailable("error")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _basic_target_sector_context(
    sector_labels: list[str],
    sector_heat: list[dict],
    reason: str,
) -> list[dict]:
    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    result: list[dict] = []
    for label in sector_labels:
        heat = heat_by_label.get(label) or {}
        result.append(
            {
                "sector_label": label,
                "heat_score": heat.get("heat_score"),
                "change_1d_percent": heat.get("change_1d_percent"),
                "change_5d_percent": heat.get("change_5d_percent"),
                "reason": reason,
                "message": "板块增强上下文未在预算内完成，荐基已按基础热度继续。",
            }
        )
    return result


def _stock_connect_flow_unavailable(reason: str) -> dict:
    return {
        "schema_version": "stock_connect_flow.v2",
        "available": False,
        "reason": reason,
        "southbound_available": False,
        "southbound_net_yi": None,
        "message": "互联互通资金摘要未在预算内完成，荐基已按板块机会事实继续。",
    }


def _build_holdings_slim(
    holdings: list[Holding],
    profile: InvestorProfile,
    *,
    trade_date: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for holding in holdings:
        effective = holding
        if trade_date and holding.fund_code and holding.fund_code != "000000":
            nav_return = get_cached_official_nav_return(holding.fund_code, trade_date)
            if nav_return is not None and holding.daily_return_percent_source != "official_nav":
                effective = holding.model_copy(
                    update={
                        "daily_return_percent": nav_return,
                        "daily_return_percent_source": "official_nav",
                    }
                )
        rows.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "sector_name": holding.sector_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(
                    holding_weight_percent(holding, holdings, profile),
                    2,
                ),
                "holding_return_percent": resolve_holding_return_percent(holding),
                "estimated_daily_return_percent": compute_estimated_daily_return_percent(
                    effective
                ),
            }
        )
    return rows


def _held_sector_summary(holdings: list[Holding]) -> list[dict]:
    totals: dict[str, float] = {}
    for holding in holdings:
        label = (holding.sector_name or "未分类").strip() or "未分类"
        totals[label] = totals.get(label, 0.0) + holding.holding_amount
    return [
        {"sector_name": label, "amount": round(amount, 2)}
        for label, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
