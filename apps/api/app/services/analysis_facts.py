from __future__ import annotations

from concurrent.futures import Executor, TimeoutError as FutureTimeoutError
from collections.abc import Callable, Mapping
from datetime import datetime
import threading
import time
from typing import Any

from app.models import (
    FundSnapshot,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.request_context import try_get_request_user_id
from app.services.investment_presets import is_short_term_style, take_profit_threshold_percent
from app.services.holding_estimates import build_holding_display_metrics
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    compute_sector_fund_gap_percent,
    holding_daily_return_is_estimated,
)
from app.services.holding_profile_batch import (
    MatchedProfilesArg,
    PROFILES_NOT_PROVIDED,
    ProfilesSnapshotArg,
    resolve_matched_profiles,
)
from app.config import get_settings
from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    resolve_escalation_floor,
)
from app.services.market_breadth_signal import build_market_breadth_signal
from app.services.market_flow_client import build_stock_connect_flow_context
from app.services.news_freshness import build_news_pipeline_context
from app.services.analysis_prompt import (
    COMPOSITE_EVIDENCE_INSTRUCTION,
    IC_EVIDENCE_INSTRUCTION,
)
from app.services.shared_executors import get_shared_io_executor
from app.services.streaming_heartbeat import raise_if_stream_cancelled
from app.services.report_sector_opportunity import (
    SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS,
    build_holding_sector_opportunity_context,
    sector_opportunity_total_budget_seconds,
)
from app.services.sector_signal_context import (
    build_signal_backtest_context,
    sector_labels_from_holdings,
    signal_backtest_for_sector,
)
from app.services.signal_guard_policy import resolve_signal_guard_policy
from app.services.signal_synthesis import build_evidence_overview, build_holding_evidence
from app.services.trading_session import build_trading_session, get_effective_trade_date
from app.services.risk import resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_label
from app.services.pipeline_concurrency import run_with_request_user
from app.services.sector_momentum import build_sector_momentum_context
from app.services.sector_labels import normalize_sector_label
from app.services.sector_quote_label import sector_quote_lookup_label
from app.services.daily_tradeability import build_holding_transaction_execution
from app.services.fund_tradeability import compact_tradeability_for_llm

SIGNAL_BACKTEST_TIMEOUT_SECONDS = 5.0
SECTOR_INTRADAY_TIMEOUT_SECONDS = 4.0
STOCK_CONNECT_FLOW_TIMEOUT_SECONDS = 3.0
GUARD_POLICY_TIMEOUT_SECONDS = 2.0
# 板块方向证据的外层预算**必须**覆盖内层自己声明的总预算，否则会出现"内层还在预算内
# 正常工作、外层已经判它超时并丢弃整层证据"。这里从内层常量派生而不是写死一个数字：
# 曾经外层写死 5.0，而内层最坏 12 s+（价格结构 8 s 并发段 + 分位分母 4 s 串行段），
# 网络稍慢就必然丢掉 `held`，日报当天彻底没有板块方向层——却没有任何地方报错。
#
# 内层现在按同一个总预算自截断（`report_sector_opportunity._StageBudget`）。额外的排队
# 余量是必需的：内层的 deadline 从**任务真正开始执行**时起算，而外层从 `_enhancement_result`
# 开始等待时起算；共享 IO 池饱和时任务可能晚于外层起等才被调度，两个 deadline 因此不同步。
# 没有这份余量，池子一忙就会重演"外层先到点、把还在预算内的内层砍掉"。
_ENHANCEMENT_QUEUE_MARGIN_SECONDS = 1.5
SECTOR_OPPORTUNITY_TIMEOUT_SECONDS = (
    SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS + _ENHANCEMENT_QUEUE_MARGIN_SECONDS
)


def sector_opportunity_timeout_seconds(held_sector_count: int | None = None) -> float:
    """外层超时随持仓板块数伸缩，与内层预算同源。

    注意这只解决"内层预算被外层写死的数字砍掉"这一类问题。2026-08-11 14:30 那次整层超时的
    真实原因是上下文线程池只有 2 个 worker、而单份日报提交 6 个增强项，`sector_opportunity`
    排在第 5 位——它的预算在队列里就流失掉了。那条已在
    `shared_executors.analysis_context_worker_floor()` 修掉；本函数管的是另一件事。
    """
    return (
        sector_opportunity_total_budget_seconds(held_sector_count)
        + _ENHANCEMENT_QUEUE_MARGIN_SECONDS
    )
MARKET_BREADTH_TIMEOUT_SECONDS = 3.0
FUND_SCALE_FRESH_DAYS = 120
FUND_SCALE_AGING_DAYS = 240

# M2.2：动作词表基础 5 档（始终出现）；「大幅减仓评估」「清仓评估」按 M2.1 触发矩阵
# 门槛动态追加——没有任一持仓触发对应档位时，prompt 里根本不出现这两个选项
# （设计原文："避免被滥用/误用吓退用户"）。
_BASE_ALLOWED_ACTIONS = ("观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核")
_ESCALATION_DEEP_REDUCE_THRESHOLD = ACTION_BUCKET_DEEP_REDUCE
_ESCALATION_CLEAR_ALL_THRESHOLD = ACTION_BUCKET_CLEAR_ALL


def _build_sector_intraday_map(
    quote_labels: list[str | None],
) -> dict[str, dict]:
    """按板块 label 去重，复用全局 intraday 缓存。"""
    result: dict[str, dict] = {}
    for label in quote_labels:
        if not label or label in result:
            continue
        summary = summarize_sector_intraday_for_label(label)
        if summary is not None:
            result[label] = summary
    return result


def _daily_return_data_source(holding: Holding) -> str | None:
    if holding.daily_return_percent_source:
        return holding.daily_return_percent_source
    if holding.daily_return_percent is not None:
        return "daily_return"
    if holding.sector_return_percent is not None:
        return "sector_estimate"
    return None


def _fund_scale_freshness(as_of: str | None, effective_trade_date: str) -> str:
    if not as_of:
        return "unknown"
    try:
        scale_date = datetime.fromisoformat(str(as_of)[:10]).date()
        decision_date = datetime.fromisoformat(str(effective_trade_date)[:10]).date()
    except ValueError:
        return "unknown"
    age_days = (decision_date - scale_date).days
    if age_days < 0:
        return "unknown"
    if age_days <= FUND_SCALE_FRESH_DAYS:
        return "fresh"
    if age_days <= FUND_SCALE_AGING_DAYS:
        return "aging"
    return "stale"


def _build_data_freshness(per_fund: list[dict], effective_trade_date: str) -> dict:
    nav_dates = sorted(
        {str(row["nav_date"]) for row in per_fund if row.get("nav_date")}
    )
    daily_dates = sorted(
        {
            str(row["daily_return_trade_date"])
            for row in per_fund
            if row.get("daily_return_trade_date")
        }
    )
    return {
        "effective_trade_date": effective_trade_date,
        "daily_return_trade_dates": daily_dates,
        "official_nav_dates": nav_dates,
        "has_stale_nav_dates": any(
            nav_date != effective_trade_date for nav_date in nav_dates
        ),
        "note": (
            "effective_trade_date is today's trading/estimate date; nav_date is "
            "the latest official fund NAV date and may lag before NAV is published."
        ),
    }


def _run_budgeted_enhancement(
    func,
    *,
    timeout_seconds: float,
    fallback: Any,
) -> Any:
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    executor = get_shared_io_executor()
    future = executor.submit(run)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        future.cancel()
        return fallback
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        return fallback
    finally:
        future.cancel()


def _submit_enhancement(executor: Executor, func):
    user_id = try_get_request_user_id()

    def run():
        if user_id is None:
            return func()
        return run_with_request_user(user_id, func)

    return executor.submit(run)


def _enhancement_result(
    future,
    *,
    timeout_seconds: float,
    fallback: Any = None,
    fallback_factory: Callable[[], Any] | None = None,
    stop_event: threading.Event | None = None,
) -> Any:
    """等待增强项，超时/异常时退回兜底。

    `fallback_factory` 用于兜底内容需要**在超时那一刻**才能确定的场景（例如读取增强项
    自己写进 progress 的部分结果）；提供它时优先于静态的 `fallback`。
    """

    def _fallback() -> Any:
        return fallback_factory() if fallback_factory is not None else fallback

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            raise_if_stream_cancelled(stop_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return _fallback()
            try:
                return future.result(timeout=min(0.25, remaining))
            except FutureTimeoutError:
                continue
    except Exception:  # noqa: BLE001 - enhancement facts are best-effort
        raise_if_stream_cancelled(stop_event)
        return _fallback()


def _signal_backtest_unavailable(reason: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "has_data": False,
        "reason": reason,
        "message": "板块信号回测未在预算内完成，日报已按基础事实继续。",
        "summary_lines": [],
        "sectors": [],
    }


def _stock_connect_flow_unavailable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "stock_connect_flow.v2",
        "available": False,
        "reason": reason,
        "southbound_available": False,
        "southbound_net_yi": None,
        "message": "互联互通资金摘要未在预算内完成，日报已按基础事实继续。",
    }


def _market_breadth_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "message": "大盘情绪温度计未在预算内完成，日报已按基础事实继续。",
    }


def _sector_opportunity_unavailable(
    reason: str,
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "held": {},
        "market_top": [],
        "sector_flow_by_label": _sector_flow_unavailable_map(holdings or [], reason),
        "divergence_backtest": {},
    }


def _sector_opportunity_from_progress(
    progress: dict[str, Any] | None,
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    """外层超时时的兜底：优先用增强项已经算完的持仓方向层。

    轮动参考（`market_top`）与分位分母排在方向层之后，它们慢一点就会让外层判超时。此前
    的兜底是 `held={}`，等于把已经算好的持仓方向证据一起丢掉——而 `held` 正是数据门禁、
    动作提议与退出判定唯一依赖的那一份。2026-08-11 14:30 实测：整层丢弃后 6 只持仓全部
    落到 `directional_evidence_not_point_in_time_usable`，连「风控复核」都被降成「观察」。
    """
    held = (progress or {}).get("held")
    if not isinstance(held, dict) or not held:
        return _sector_opportunity_unavailable("timeout", holdings)
    flow_by_label = (progress or {}).get("sector_flow_by_label")
    divergence = (progress or {}).get("divergence_backtest")
    return {
        "available": bool((progress or {}).get("heat_available")),
        # 明确区分"整层没有"与"方向层有、轮动参考没跑完"，便于运维与 prompt 分辨。
        "reason": "timeout_partial_held_only",
        "held": dict(held),
        "market_top": [],
        "sector_flow_by_label": (
            dict(flow_by_label)
            if isinstance(flow_by_label, dict) and flow_by_label
            else _sector_flow_unavailable_map(holdings or [], "timeout")
        ),
        "divergence_backtest": dict(divergence) if isinstance(divergence, dict) else {},
        "direction_exit_by_fund_code": dict(
            (progress or {}).get("direction_exit_by_fund_code") or {}
        ),
        "mainline": {"available": False, "reason": "timeout_partial_held_only"},
    }


def _guard_policy_unavailable() -> dict[str, Any]:
    return {
        "enforce_reversal_block": True,
        "enforce_pullback_block": True,
        "tighten_tactical": False,
        "reason": "guard_policy_timeout",
        "backtest_summary_lines": [],
    }


def _attach_escalation_to_holdings(
    per_fund: list[dict],
    *,
    market_breadth: dict | None,
    profile: InvestorProfile,
    direction_exit_by_fund_code: dict[str, dict] | None = None,
) -> None:
    """给每个持仓行挂上 M2.1 的双向 guard 升级判定结果（key: `escalation`）。

    仅当该行同时具备 `sector_opportunity` 与 `evidence` 时才有意义调用——两者缺失时
    `resolve_escalation_floor` 本身已能优雅降级返回 `min_bucket=None`（详见该函数
    docstring），这里不做额外短路，保持单一判定入口。
    """
    by_fund_code = direction_exit_by_fund_code or {}
    for row in per_fund:
        sector_opportunity = row.get("sector_opportunity")
        # 方向退出判定挂在板块机会行上（由 report_sector_opportunity 计算），这里透传给
        # 升级下限：它是 2026-08 补上的"何时退场"来源，与既有的量价背离风险各判一次、
        # 取更保守者。
        #
        # 优先用**这只基金自己**的那一份：入场契约是每只基金一份，而板块行只能采用同方向
        # 最早那笔买入作为基线，其余基金自己的入场理由此前永远用不上。
        direction_exit = by_fund_code.get(str(row.get("fund_code") or "").strip()) or (
            sector_opportunity.get("direction_exit")
            if isinstance(sector_opportunity, dict)
            else None
        )
        row["escalation"] = resolve_escalation_floor(
            sector_opportunity=sector_opportunity,
            evidence=row.get("evidence"),
            market_breadth=market_breadth,
            over_concentration=bool(row.get("over_concentration")),
            has_unrealized_gain=(row.get("estimated_holding_return_percent") or 0) > 0,
            decision_style=profile.decision_style,
            direction_exit=direction_exit,
            # 基金层第三源：该持仓自己的净值走势，用于"载体跑输板块"的加仓禁止。
            nav_trend=row.get("nav_trend"),
        )
        if isinstance(direction_exit, dict):
            row["direction_exit"] = direction_exit


def _extra_allowed_actions_for_escalation(per_fund: list[dict]) -> list[str]:
    """按各持仓的 `escalation.min_bucket` 判断是否需要向 `allowed_actions` 追加
    「大幅减仓评估」「清仓评估」两个新动作词（M2.2）。

    M6：shadow 灰度期间恒返回空列表——这两个词本身就是本次升级要灰度验证的机制
    之一，如果 shadow 模式下仍然把它们递给模型选，模型选中后 recommendation_guard.py
    虽然不会强制生效（见该文件的 shadow 分支），但也不应该让模型在草案阶段就看到、
    选择这两个新词——灰度观察期的产品意图是"系统内部安静地算、只旁注提示"，不是
    "开放新选项但事后不生效"。拆成独立函数是为了让 shadow 门控可以脱离完整
    `build_analysis_facts` 调用链单独测试（原逻辑内联在函数体内、依赖只有在真实
    facts 组装流程中才会被填充的字段，难以在单测里精确构造）。
    """
    if get_settings().decision_escalation_mode != "enforced":
        return []
    if any(
        (row.get("escalation") or {}).get("min_bucket") is not None
        and row["escalation"]["min_bucket"] <= _ESCALATION_CLEAR_ALL_THRESHOLD
        for row in per_fund
    ):
        return ["清仓评估", "大幅减仓评估"]
    if any(
        (row.get("escalation") or {}).get("min_bucket") is not None
        and row["escalation"]["min_bucket"] <= _ESCALATION_DEEP_REDUCE_THRESHOLD
        for row in per_fund
    ):
        return ["大幅减仓评估"]
    return []


def build_allowed_actions(per_fund: list[dict]) -> list[str]:
    """Return the complete action contract exposed for this analysis run.

    Keeping the base actions and shadow/enforced escalation extensions behind one
    builder prevents prompts and judges from maintaining a second, drifting list.
    """
    return [*_BASE_ALLOWED_ACTIONS, *_extra_allowed_actions_for_escalation(per_fund)]


def _sector_flow_unavailable_map(
    holdings: list[Holding],
    reason: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        result[label] = {
            "available": False,
            "sector_label": label,
            "reason": reason,
            "message": "板块资金流未在预算内完成，日报已按基础事实继续。",
        }
    return result


def build_analysis_facts(
    holdings: list[Holding],
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    profile: InvestorProfile,
    topic_briefs: list[TopicBrief] | None = None,
    nav_trends_by_code: dict[str, dict] | None = None,
    market_news: list[NewsItem] | None = None,
    *,
    session: dict | None = None,
    pipeline: dict | None = None,
    portfolio_trend: dict | None = None,
    factor_scores: dict | None = None,
    risk_metrics: dict | None = None,
    for_llm: bool = False,
    budget_enhancements: bool = False,
    decision_at: datetime | None = None,
    tradeability_profiles: Mapping[str, Mapping[str, Any]] | None = None,
    profiles_snapshot: ProfilesSnapshotArg = PROFILES_NOT_PROVIDED,
    matched_profiles: MatchedProfilesArg = PROFILES_NOT_PROVIDED,
    stop_event: threading.Event | None = None,
) -> dict:
    raise_if_stream_cancelled(stop_event)
    resolved_profiles = resolve_matched_profiles(
        holdings,
        profiles_snapshot=profiles_snapshot,
        matched_profiles=matched_profiles,
    )
    quote_labels = [
        sector_quote_lookup_label(holding, profile=holding_profile)
        for holding, holding_profile in zip(
            holdings,
            resolved_profiles,
            strict=True,
        )
    ]
    # 方向层预算与外层超时都按"去重后的持仓板块数"伸缩：价格结构是逐板块联网、内部
    # 4 并发，板块数越多需要的墙钟越长。`progress` 供超时时取用已算完的方向层。
    held_sector_count = len(
        {
            label
            for holding in holdings
            if (label := normalize_sector_label(holding.sector_name))
        }
    )
    sector_opportunity_progress: dict[str, Any] = {}
    nav_trends = nav_trends_by_code or {}
    resolved_session = (
        session
        if isinstance(session, dict)
        else (build_trading_session(decision_at) if decision_at is not None else None)
    )
    effective_trade_date = (
        str(resolved_session.get("effective_trade_date"))
        if isinstance(resolved_session, dict) and resolved_session.get("effective_trade_date")
        else get_effective_trade_date()
    )
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    weight_denominator = resolve_weight_denominator(
        holdings,
        profile,
        actual_total=total_amount,
    )
    weight_denominator_basis = (
        "expected_investment_amount"
        if profile.expected_investment_amount is not None
        and profile.expected_investment_amount > 0
        else "actual_portfolio_value"
    )
    snapshot_by_code = {item.fund_code: item for item in snapshots}
    sector_labels = sector_labels_from_holdings(holdings)
    stock_connect_flow = None
    market_breadth = None
    if budget_enhancements:
        executor = get_shared_io_executor()
        enhancement_futures = []
        try:
            signal_future = _submit_enhancement(
                executor,
                lambda: build_signal_backtest_context(sector_labels),
            )
            enhancement_futures.append(signal_future)
            guard_future = _submit_enhancement(
                executor,
                lambda: resolve_signal_guard_policy(holdings),
            )
            enhancement_futures.append(guard_future)
            intraday_future = _submit_enhancement(
                executor,
                lambda: _build_sector_intraday_map(quote_labels),
            )
            enhancement_futures.append(intraday_future)
            stock_connect_flow_future = _submit_enhancement(
                executor,
                lambda: build_stock_connect_flow_context(trade_date=effective_trade_date),
            )
            enhancement_futures.append(stock_connect_flow_future)
            sector_opportunity_future = _submit_enhancement(
                executor,
                # 主线 regime 的取数与快照构建都在这个 context builder 内部完成（它顺带
                # 复用同一次热度与资金流，不重复拉取），所以这里不必新开第 7 个并发项
                # ——共享 IO 池是全进程共用的，日报/荐基/嵌套 fan-out 都在里面抢。
                lambda: build_holding_sector_opportunity_context(
                    holdings,
                    trade_date=effective_trade_date,
                    progress=sector_opportunity_progress,
                ),
            )
            enhancement_futures.append(sector_opportunity_future)
            market_breadth_future = _submit_enhancement(
                executor,
                lambda: build_market_breadth_signal(effective_trade_date),
            )
            enhancement_futures.append(market_breadth_future)
            signal_backtest = _enhancement_result(
                signal_future,
                timeout_seconds=SIGNAL_BACKTEST_TIMEOUT_SECONDS,
                fallback=_signal_backtest_unavailable("timeout"),
                stop_event=stop_event,
            )
            guard_policy = _enhancement_result(
                guard_future,
                timeout_seconds=GUARD_POLICY_TIMEOUT_SECONDS,
                fallback=_guard_policy_unavailable(),
                stop_event=stop_event,
            )
            intraday_map = _enhancement_result(
                intraday_future,
                timeout_seconds=SECTOR_INTRADAY_TIMEOUT_SECONDS,
                fallback={},
                stop_event=stop_event,
            )
            stock_connect_flow = _enhancement_result(
                stock_connect_flow_future,
                timeout_seconds=STOCK_CONNECT_FLOW_TIMEOUT_SECONDS,
                fallback=_stock_connect_flow_unavailable("timeout"),
                stop_event=stop_event,
            )
            sector_opportunity = _enhancement_result(
                sector_opportunity_future,
                timeout_seconds=sector_opportunity_timeout_seconds(held_sector_count),
                # 超时不再等于"整层没有"：方向层一算完就写进 progress，这里优先取用它。
                fallback_factory=lambda: _sector_opportunity_from_progress(
                    sector_opportunity_progress,
                    holdings,
                ),
                stop_event=stop_event,
            )
            market_breadth = _enhancement_result(
                market_breadth_future,
                timeout_seconds=MARKET_BREADTH_TIMEOUT_SECONDS,
                fallback=_market_breadth_unavailable("timeout"),
                stop_event=stop_event,
            )
        finally:
            for future in enhancement_futures:
                future.cancel()
    else:
        signal_backtest = build_signal_backtest_context(sector_labels)
        guard_policy = resolve_signal_guard_policy(holdings)
        intraday_map = _build_sector_intraday_map(quote_labels)
        try:
            sector_opportunity = build_holding_sector_opportunity_context(
                holdings,
                trade_date=effective_trade_date,
            )
        except Exception:  # noqa: BLE001 - opportunity/flow evidence is best-effort
            sector_opportunity = _sector_opportunity_unavailable("error", holdings)

    if not isinstance(sector_opportunity, dict):
        sector_opportunity = _sector_opportunity_unavailable("unavailable", holdings)
    raw_sector_flow_map = sector_opportunity.get("sector_flow_by_label")
    flow_fallback_reason = str(sector_opportunity.get("reason") or "unavailable")
    sector_flow_map = _sector_flow_unavailable_map(holdings, flow_fallback_reason)
    if isinstance(raw_sector_flow_map, dict):
        sector_flow_map.update(
            {
                label: flow
                for label, flow in raw_sector_flow_map.items()
                if isinstance(label, str) and isinstance(flow, dict)
            }
        )

    per_fund: list[dict] = []
    drawdown_limit = abs(profile.max_drawdown_percent)
    for holding, holding_profile, quote_label in zip(
        holdings,
        resolved_profiles,
        quote_labels,
        strict=True,
    ):
        weight = (
            holding.holding_amount / weight_denominator * 100
            if weight_denominator > 0
            else 0.0
        )
        estimated_daily = compute_estimated_daily_return_percent(holding)
        # 必须走带 profile 的权威实现（与界面「持有」列同一口径）。这里曾有一份
        # 无 profile 的简化重写版，用于避开每只持仓一次的 profile 单点查询；
        # `resolve_matched_profiles` 引入批量读之后该开销已不存在，而简化版会丢掉
        # 三个真实行为：收益计提递延（新买入份额待确认时不应叠加当日板块估算）、
        # 支付宝 OCR 持有收益已含当日（简化版会把当日涨跌重复加一次）、以及份额
        # 同步误写持有收益的档案修复。
        display = build_holding_display_metrics(holding, profile=holding_profile)
        effective_return = float(display["estimated_holding_return_percent"] or 0)
        snapshot = snapshot_by_code.get(holding.fund_code)
        daily_return_source = _daily_return_data_source(holding)
        daily_return_trade_date = effective_trade_date if daily_return_source else None
        nav_date = snapshot.nav_date if snapshot else None
        row: dict = {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(weight, 2),
                "holding_return_percent": display["holding_return_percent_settled"],
                "estimated_holding_return_percent": round(effective_return, 4),
                "estimated_holding_profit": display["estimated_holding_profit"],
                "holding_return_is_estimated": display["holding_return_is_estimated"],
                "over_drawdown_limit": effective_return <= -drawdown_limit,
                "sector_return_percent": holding.sector_return_percent,
                "sector_return_percent_source": holding.sector_return_percent_source,
                "daily_return_percent": holding.daily_return_percent,
                "daily_return_percent_source": holding.daily_return_percent_source,
                "estimated_daily_return_percent": estimated_daily,
                "daily_return_is_estimated": holding_daily_return_is_estimated(
                    holding,
                    profile=holding_profile,
                ),
                "daily_profit": holding.daily_profit,
                "holding_profit": holding.holding_profit,
                "sector_name": holding.sector_name,
                "over_concentration": weight > profile.concentration_limit_percent,
                "latest_nav": snapshot.latest_nav if snapshot else None,
                "nav_date": nav_date,
                "daily_return_trade_date": daily_return_trade_date,
                "daily_return_data_source": daily_return_source,
                "nav_date_is_current_trade_date": (
                    nav_date == effective_trade_date if nav_date else None
                ),
                "fund_type": snapshot.fund_type if snapshot else None,
                "return_1y_percent": snapshot.return_1y_percent if snapshot else None,
                "max_drawdown_1y_percent": snapshot.max_drawdown_1y_percent if snapshot else None,
                "management_fee": snapshot.management_fee if snapshot else None,
                "fund_scale_yi": snapshot.fund_scale_yi if snapshot else None,
                "fund_scale_source": snapshot.fund_scale_source if snapshot else None,
                "fund_scale_as_of": snapshot.fund_scale_as_of if snapshot else None,
                "fund_scale_freshness": (
                    _fund_scale_freshness(
                        snapshot.fund_scale_as_of,
                        effective_trade_date,
                    )
                    if snapshot
                    else "unknown"
                ),
                "nav_trend": nav_trends.get(holding.fund_code),
                "sector_momentum": build_sector_momentum_context(
                    holding,
                    nav_trends.get(holding.fund_code),
                ),
                "sector_intraday": intraday_map.get(quote_label or ""),
                "sector_fund_flow": sector_flow_map.get(
                    normalize_sector_label(holding.sector_name)
                ),
                "signal_backtest": signal_backtest_for_sector(
                    holding.sector_name,
                    signal_backtest,
                ),
                "sector_opportunity": (sector_opportunity.get("held") or {}).get(
                    normalize_sector_label(holding.sector_name)
                ),
                "flow_divergence_backtest": (sector_opportunity.get("divergence_backtest") or {}).get(
                    normalize_sector_label(holding.sector_name)
                ),
            }
        if tradeability_profiles is not None:
            raw_tradeability = tradeability_profiles.get(holding.fund_code)
            tradeability = compact_tradeability_for_llm(
                raw_tradeability if isinstance(raw_tradeability, Mapping) else None
            )
            row["tradeability"] = tradeability
            row["transaction_execution"] = build_holding_transaction_execution(
                tradeability,
                holding_amount_yuan=holding.holding_amount,
            )
        if for_llm:
            row["sector_fund_gap_percent"] = compute_sector_fund_gap_percent(holding)
        evidence = build_holding_evidence(
            fund_code=holding.fund_code,
            signal_entry=row["signal_backtest"],
            factor_scores=factor_scores,
            risk_metrics=risk_metrics,
        )
        if evidence:
            row["evidence"] = evidence
        per_fund.append(row)

    facts: dict = {
        "readonly": True,
        "instruction": (
            "以下数字由系统计算，分析时不得改写；仅可基于它们做解释与建议。"
            "浮亏/持有收益判断须用 estimated_holding_return_percent 与 portfolio.weighted_return_percent，"
            "勿用 holding_return_percent（昨日结算）。"
            "板块信号(signal_backtest)须按各规则 confidence.level 表述："
            "「高」可作主理由；「中」需措辞保留；「低/不足」只能作提示，"
            "不得据此主导追涨或减仓建议。"
            "组合风险指标(risk_metrics：夏普/回撤/Beta/HHI)为系统计算事实，"
            "按 confidence.level 表述：「高/中」可作风险论据；"
            "「低/不足」须声明样本有限、不得据此下强结论。"
            "risk_metrics.max_drawdown_percent 是组合历史峰值到谷值的**真实回撤**，"
            "与 portfolio.weighted_return_percent（相对持仓成本的当前浮亏）量纲不同，"
            "不得混用或互相替代；当它在 confidence 高/中 的前提下超过 "
            "portfolio.max_drawdown_limit_percent 且组合当前处于浮亏时，"
            "服务端会确定性地封禁加仓类动作，叙述不得与该结论冲突。"
            f"{IC_EVIDENCE_INSTRUCTION}"
            f"{COMPOSITE_EVIDENCE_INSTRUCTION}"
            "evidence_overview 是组合级证据质量体检：backed_weight_percent 仅表示"
            "「中/高正向支持」市值占比；历史规则未提供当日触发方向时不得计入支持，"
            "风险样本只作守卫。该占比不能单独触发更积极动作，仍须结合风险、估值与时效。"
            "sector_fund_flow.today_main_force_net_yi 正数=净流入、负数=净流出；"
            "仅当 flow_date 与 trade_date 对齐（date_aligned=true）时方可与 sector_return_percent 做背离判断。"
            "sector_direction_maturity.available=true 时，持仓的 sector_opportunity 会额外带上"
            "方向成熟度字段（复用当天已冻结的主线快照）：entry_state 是方向动作边界——"
            "ready_to_start=趋势、资金参与度与价格位置已同时通过，可作为分批加仓的方向依据；"
            "ready_on_pullback=方向成立但当前位置不宜追，通常等待；forming=条件形成中，不得下单；"
            "invalid=趋势或资金未通过，不得参与。first_tranche_scale 是本次投入的缩放系数"
            "（过热/拥挤/概率不足时小于 1），服务端已按它确定性地缩小加仓比例，叙述不得与之冲突；"
            "trend_formation_probability 实为**趋势成形信号分**（0～100，未经校准的加权合成，中性方向即读出约 56），既不是概率也不是收益预测；不得按「几成会涨」表述，也不再决定仓位比例（仓位按趋势强度分档）。"
            "direction_exit 是**已持仓方向的退出侧判定**（入场由发现基金负责，已持仓的加/减/退由日报负责）："
            "exit_state=hold 方向仍有效；pause_add=方向仍在退出线上方但当前未通过入场线或相对买入明显回落，"
            "维持持有但本轮不得加仓；reduce/deep_reduce/exit=趋势已跌破退出线或方向作废，"
            "服务端已把 escalation.min_bucket 与 suggested_position_change_percent 按它确定性地下调，"
            "叙述必须与该档位一致，不得反过来劝继续持有或加仓。"
            "allows_add=false 时禁止给出任何加仓类动作。basis=relative_to_entry 时"
            "entry_reference 带着买入当时的方向分数，可在 points 里引用「买入时 X 分、现在 Y 分」；"
            "basis=absolute 表示该持仓没有对应的发现基金买入事件（多为截图导入），"
            "只能按绝对退出线表述，不得编造入场基线；basis=unavailable 表示趋势分取不到，"
            "此时既不构成卖出理由、也不授权加仓。"
            "entry_reference_note 非空表示**买入记录存在、但用不上**：该基金买入时记录的方向与"
            "它现在所属的方向不是同一个（板块分类变过），两个方向的分数不可比，因此按绝对退出线"
            "判定。这种情况必须照 note 原样说明「当初买的是哪个方向、现在归到哪个方向」，"
            "不得说成「没有买入记录」，也不得拿旧方向的分数当基线做相对比较。"
            "invalidation_status 是**买入时写明的失效条件 × 今天的判定**逐条对照："
            "promised=true 表示这条是当初那笔买入承诺过的，triggered=true 已触发、false 未触发、"
            "null 表示今天缺数据无法判定（不得当成未触发）。breached_entry_promises 非空时，"
            "叙述必须点名是哪一条承诺被触发（照 label 写），这是可以直接引用给用户的减仓/停止加仓"
            "理由；为空时不得暗示「失效条件已出现」。这些 code 复用的是**入场**门槛阈值，"
            "用作退出触发没有回测支撑，因此最高只到停止加仓，不得据此宣称应当清仓。"
            "注意 direction_exit 里的连续跌破天数门槛与相对回落门槛尚未经过回测验证"
            "（thresholds_validated=false），可作为动作依据但不要宣称它有历史胜率支撑。"
            "sector_direction_maturity.available=false 表示当天没有可复用的主线快照，"
            "此时 entry_state 等字段不存在，**不得**据此认为方向不成立或方向已成熟，"
            "只能按旧版机会分与资金面叙述。"
            "sector_direction_maturity.complete=false 表示方向层只取到了一部分："
            "missing_labels 列出的那些持仓板块今天没有 entry_state，它们的方向判断只能按旧版"
            "机会分表述，且不得给出加仓类动作。必须在 caveats 或对应持仓的校验备注里点名这些"
            "板块，不能让「方向层可用」掩盖「其中某几个板块其实没有」。"
            "sector_direction_maturity.hysteresis_applied=true 时，entry_state 已套上跨日滞回："
            "此时持仓 sector_opportunity 上的 consecutive_qualifying_days 是该方向连续通过入场线的"
            "交易日数，raw_entry_state 是未平滑的当日原始档位。两者不同（例如 entry_state="
            "ready_to_start 而 raw_entry_state=ready_on_pullback）说明方向今天在滞回带内被保留，"
            "叙述可以说「方向此前已确认、今日未跌破退出线」，但**不得**说成今日重新确认。"
            "consecutive_qualifying_days 是**下界**（见 hysteresis.note：状态账本由荐基写入，"
            "缺失某个交易日会让天数从 1 重新起算），可以说「至少连续 N 个交易日满足」，"
            "不得说成「该方向恰好只满足了 N 天」。"
            "hysteresis_applied=false 时没有跨日历史可用，entry_state 是当日原始档位，"
            "不得描述为「已连续多日满足」，也不得引用 consecutive_qualifying_days。"
            "持仓的 sector_opportunity 是该持仓所属板块当前方向判断（track=momentum顺势/setup蓄势，"
            "confidence=高/中/低/不足）：opportunity_available=false 表示该方向当前不构成机会"
            "（例如资金持续流出、涨幅透支），须在分析中提示、不得据此建议加仓；"
            "为 true 时可作为「继续持有/适度加仓」的辅助论据，但仍需结合 evidence 与风险指标。"
            "sector_rotation.market_top 是当前全市场机会分最高的方向（不含已持有板块），"
            "仅用于提示「是否存在更强的轮动方向」，不得单独作为清仓已持仓位、追高换仓的理由。"
            "market_breadth 是大盘情绪温度计：signal_mode=closing 时 sentiment_level 基于"
            "全市场创新高低家数近2年历史分布百分位自校准；signal_mode=intraday 时基于"
            "当日上涨/下跌/平盘及赚钱效应准实时计算，closing_* 字段仅为上一完整交易日背景。"
            "只有 decision_eligible=true 且 freshness_status 非 stale 时才可支撑 hard guard；"
            "否则只能作背景并明确数据时点。"
            "limit_up_count/limit_down_count/limit_up_broken_ratio_percent 仅为当日快照，"
            "不是历史回测结论，只能作辅助描述、不得单独据此下强结论。"
            "持仓的 flow_divergence_backtest 是该持仓板块「量价背离」信号的历史回测（区别于"
            "sector_fund_flow 的定性提示）：按各规则 significant 与 edge_percent 表述，"
            "significant=true 且 edge_percent 越高，可信度越高；未显著或触发次数不足时"
            "只能作提示，不得主导追涨或减仓建议。"
            "fund_lookthrough 是基金定期报告披露口径的持仓穿透：portfolio 下的"
            "top_security_exposure_lower_bounds / top_industry_exposure_lower_bounds 用于发现"
            "「多只基金重仓同一批证券」的重复暴露，这是按基金市值计算的 weight_percent 看不到的。"
            "全部数值均为披露范围内的**下界**，unknown_account_mass_percent 等未知质量必须保留；"
            "未发现共同证券不代表完整组合无重合，更不能据此说组合分散或支持加仓；"
            "execution_qualified 恒为 false，它只能收紧集中度结论，不参与仓位比例计算。"
            "持仓的 peer_research 是该基金在**同类基金**里的分位（同类组由基金类型/策略/"
            "地区/跟踪标的严格划分）：metrics 里每项含 percentile（越高越好，已按指标方向归一）、"
            "sample_count、coverage_rate。它是**描述性证据**，execution_tilt_eligible 恒为 false，"
            "**不得**作为加仓或减仓的独立理由，也不得说成「同类领先所以应该买入」；"
            "只能用于说明该基金在同类中的相对位置。available=false 或 status=insufficient 表示"
            "同类分位算不出来（目录缓存缺席、该基金不在目录、或同类组本身欠定义——例如混合型"
            "缺少风险暴露分类时按设计 fail closed），这**不是**「同类里表现差」，不得当作负面证据。"
            "not_applicable_metrics 列出的是对该类型基金本就不适用的指标，同样不是缺陷。"
            "持仓的 vehicle_quality 是「这只基金作为投资工具本身合不合格」，与 evidence 的"
            "收益证据是两件事：applicable=false 表示该基金是主动管理型、日报数据链路不含"
            "经理业绩证据，**不得**据此说它质量偏低或有缺陷，也不得当作减仓理由；"
            "applicable=true（被动/指数载体）时 status=eligible 或 watch_only，依据只有规模、"
            "管理费率与相对基准的跟踪质量三项，watch_only 时须在分析中点明 penalties 里的"
            "具体短板，服务端已据此确定性下调加仓档位。该判断刻意不含板块匹配度，"
            "不得用它替代 sector_opportunity 的方向结论。"
        ),
        "portfolio": {
            "total_amount": round(total_amount, 2),
            "weight_denominator": round(weight_denominator, 2),
            "weight_denominator_basis": weight_denominator_basis,
            "expected_investment_amount": profile.expected_investment_amount,
            "decision_style": profile.decision_style,
            "holding_count": len(holdings),
            "weighted_return_percent": risk.weighted_return_percent,
            "risk_level": risk.level,
            "suggested_action": risk.suggested_action,
            "max_drawdown_limit_percent": profile.max_drawdown_percent,
            "concentration_limit_percent": profile.concentration_limit_percent,
            # Freeze the user's transaction-cost assumption for point-in-time
            # outcome evaluation. It is never presented as an actual platform fee.
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
        "alerts": [alert.model_dump() for alert in risk.alerts],
        "holdings": per_fund,
        "data_freshness": _build_data_freshness(per_fund, effective_trade_date),
        "allowed_actions": list(_BASE_ALLOWED_ACTIONS),
        "news": build_news_pipeline_context(
            market_news,
            topic_briefs,
            now=decision_at,
        ),
    }
    if resolved_session:
        facts["session"] = resolved_session
    if pipeline:
        facts["pipeline"] = pipeline
    if portfolio_trend:
        facts["portfolio_trend"] = portfolio_trend
    if factor_scores:
        facts["factor_scores"] = factor_scores
    if risk_metrics:
        facts["risk_metrics"] = risk_metrics
    overview = build_evidence_overview(per_fund)
    if overview.get("available"):
        facts["evidence_overview"] = overview
    if budget_enhancements:
        facts["stock_connect_flow"] = (
            stock_connect_flow or _stock_connect_flow_unavailable("timeout")
        )
        facts["market_breadth"] = market_breadth or _market_breadth_unavailable("timeout")
    else:
        facts["stock_connect_flow"] = build_stock_connect_flow_context(
            trade_date=effective_trade_date,
        )
        facts["market_breadth"] = build_market_breadth_signal(effective_trade_date)
    # M2.1/M2.2：双向 guard 升级判定——须在 market_breadth 就位后才能算，因此放在这里
    # 而不是 per_fund 主循环内（非 budget_enhancements 路径下 market_breadth 变量在
    # 循环执行时尚未赋值，只有 facts["market_breadth"] 在此处才是最终值）。
    _attach_escalation_to_holdings(
        per_fund,
        market_breadth=facts["market_breadth"],
        profile=profile,
        direction_exit_by_fund_code=(
            sector_opportunity.get("direction_exit_by_fund_code")
            if isinstance(sector_opportunity, dict)
            else None
        ),
    )
    facts["allowed_actions"] = build_allowed_actions(per_fund)
    facts["signal_backtest"] = signal_backtest
    facts["sector_rotation"] = {
        "available": sector_opportunity.get("available", False),
        "reason": sector_opportunity.get("reason"),
        "market_top": sector_opportunity.get("market_top", []),
    }
    # 跨报告披露：当日发现基金报告对持仓同板块推荐了新载体时，把它结构化地带进日报——
    # 方向层两侧共用同一套打分不会矛盾，但基金层"发现推荐买 Y、日报按住持仓 X"看起来
    # 就是打架，必须有一句话解释。只披露、不仲裁（见模块 docstring）。
    from app.services.report_discovery_cross_reference import (
        build_discovery_cross_reference,
    )

    facts["discovery_cross_reference"] = build_discovery_cross_reference(
        holdings,
        decision_at=decision_at,
    )
    # 方向成熟度这一层是否生效必须单独可见：`entry_state` 在主线快照缺席时压根不出现，
    # 下游要能区分"方向尚未成熟"与"今天没有主线快照可复用"。
    facts["sector_direction_maturity"] = (
        sector_opportunity.get("mainline")
        if isinstance(sector_opportunity.get("mainline"), dict)
        else {"available": False, "reason": "unavailable"}
    )
    facts["guard_policy"] = {
        "enforce_reversal_block": guard_policy.get("enforce_reversal_block", True),
        "enforce_pullback_block": guard_policy.get("enforce_pullback_block", True),
        "tighten_tactical": guard_policy.get("tighten_tactical", False),
        "reason": guard_policy.get("reason"),
        "backtest_summary_lines": guard_policy.get("backtest_summary_lines") or [],
    }
    if is_short_term_style(profile.decision_style):
        facts["prompt_tuning"] = guard_policy
    if tradeability_profiles is not None:
        facts["transaction_execution_semantics"] = {
            "schema_version": "holding_transaction_execution_semantics.v1",
            "add": (
                "日报对象是现有持仓；只使用明确的追加申购门槛，不得以首次起购额替代。"
                "add_status 非 eligible 时不得输出加仓动作；服务端会用建议比例对应的内部金额"
                "核验追加起购额和单日限额，模型不得自行给固定金额。"
            ),
            "reduce": (
                "赎回开放不等于某个持仓批次已过锁定期。当前无逐笔 acquisition lot，"
                "可保留减仓比例用于风险规划，但不得输出固定金额；实际赎回前须核对持有期与费用。"
            ),
        }
    return facts
