from __future__ import annotations

import time
from datetime import date, datetime, timezone

from app.database import (
    get_most_recent_portfolio_snapshot,
    list_portfolio_daily_snapshots,
    save_portfolio_daily_snapshot,
)
from app.models import FundProfile, Holding, PortfolioDailySnapshot, PortfolioSummary
from app.services.portfolio_profit_analysis import (
    ProfitRange,
    _compound_return_percent,
    build_calendar_month,
    build_daily_top5,
    build_profit_trend,
    default_calendar_anchor,
    summarize_trend_footer,
)


def build_risk_metrics_payload(
    history_rows: list[dict],
    holdings_models: list[Holding],
) -> dict:
    """从日快照 + 沪深300日线装配组合风险指标（纯函数 compute_portfolio_metrics 的取数层）。

    组合收益与指数收益按 snapshot_date 逐日对齐（取交集），保证 Beta/Alpha 在
    同一交易日上配对，而不是简单尾部对齐（见设计文档 5.1 的对齐坑提醒）。
    """
    from dataclasses import asdict

    from app.config import get_risk_free_rate
    from app.services.index_daily_client import fetch_index_daily_history
    from app.services.portfolio_profit_analysis import _index_daily_change_lookup
    from app.services.portfolio_risk_metrics import compute_portfolio_metrics

    # 1. 组合日收益序列（history_rows 最新在前 → 反转成按日期升序）
    rows = [row for row in reversed(history_rows)]

    index_history = fetch_index_daily_history("000300", trading_days=400)
    index_lookup = _index_daily_change_lookup(index_history)

    # 2. 逐日对齐：仅保留组合与沪深300都有数据的交易日
    portfolio_returns: list[float] = []
    index_returns: list[float] = []
    for row in rows:
        daily_return = row.get("daily_return_percent")
        if daily_return is None:
            continue
        day = str(row.get("snapshot_date") or "")[:10]
        index_change = index_lookup.get(day)
        if index_change is None:
            continue
        portfolio_returns.append(float(daily_return))
        index_returns.append(float(index_change))

    # 3. 当前持仓金额
    holding_amounts = [
        h.holding_amount for h in holdings_models if h.holding_amount and h.holding_amount > 0
    ]

    metrics = compute_portfolio_metrics(
        portfolio_daily_returns=portfolio_returns,
        index_daily_returns=index_returns,
        holding_amounts=holding_amounts,
        risk_free_rate=get_risk_free_rate(),
    )
    return asdict(metrics)


def _nav_returns_by_date(points: list) -> dict[str, float]:
    """从净值点序列算逐日简单收益（%），按净值日期键控。"""
    returns: dict[str, float] = {}
    prev_nav: float | None = None
    for point in points:
        nav = getattr(point, "nav", None)
        day = str(getattr(point, "date", "") or "")[:10]
        if nav is None or nav <= 0 or not day:
            prev_nav = nav if (nav and nav > 0) else prev_nav
            continue
        if prev_nav is not None and prev_nav > 0:
            returns[day] = (float(nav) / float(prev_nav) - 1.0) * 100.0
        prev_nav = float(nav)
    return returns


def build_risk_correlation_payload(
    holdings_models: list[Holding],
    *,
    lookback_days: int = 120,
    max_holdings: int = 15,
    fetch_nav=None,
) -> dict:
    """逐只持仓拉净值历史 → 日收益序列 → 两两相关性矩阵（第二批，较重，懒加载）。

    `fetch_nav(code, name, trading_days) -> points` 可注入便于离线测试；默认走
    `FundDataService().get_nav_history`。
    """
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict

    from app.services.portfolio_risk_metrics import compute_correlation_matrix

    if fetch_nav is None:

        def fetch_nav(code: str, name: str, trading_days: int):
            from app.services.fund_data import FundDataService

            history = FundDataService().get_nav_history(
                code, name, trading_days=trading_days
            )
            return history.points

    # 候选：有有效代码、按持仓金额降序，取前 max_holdings 只
    candidates = [
        h
        for h in holdings_models
        if h.fund_code and h.fund_code != "000000" and (h.holding_amount or 0) > 0
    ]
    candidates.sort(key=lambda h: float(h.holding_amount or 0), reverse=True)
    candidates = candidates[:max_holdings]

    names_by_code = {h.fund_code: (h.fund_name or h.fund_code) for h in candidates}

    def _fetch_one(holding: Holding) -> tuple[str, dict[str, float]]:
        try:
            points = fetch_nav(holding.fund_code, holding.fund_name or "", lookback_days)
        except Exception:
            return holding.fund_code, {}
        return holding.fund_code, _nav_returns_by_date(points or [])

    returns_by_code: dict[str, dict[str, float]] = {}
    if candidates:
        max_workers = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for code, returns in pool.map(_fetch_one, candidates):
                if returns:
                    returns_by_code[code] = returns

    matrix = compute_correlation_matrix(
        returns_by_code=returns_by_code,
        names_by_code=names_by_code,
    )
    return asdict(matrix)


def _target_from_nav(holding: Holding, fetch_nav) -> "object":
    """持仓不在排行榜池时，用净值序列算同口径因子原始值（复用共享 helper）。

    规模无法从净值取，置 None（size 因子权重低、合成时按剩余权重归一）。
    """
    from app.services.fund_factor_nav import factor_input_from_navs
    from app.services.fund_factors import FundFactorInput

    code = holding.fund_code
    name = holding.fund_name or ""
    try:
        points = fetch_nav(code, name, 250)
    except Exception:
        points = []

    pairs: list[tuple[str, float]] = []
    for point in points or []:
        nav = getattr(point, "nav", None)
        day = str(getattr(point, "date", "") or "")[:10]
        if nav is None or float(nav) <= 0 or not day:
            continue
        pairs.append((day, float(nav)))
    pairs.sort(key=lambda x: x[0])
    navs = [nav for _, nav in pairs]

    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)

    return factor_input_from_navs(code, name, navs)


def build_factor_scores_payload(
    holdings_models: list[Holding],
    *,
    fetch_rank=None,
    fetch_nav=None,
) -> dict:
    """从开放式基金排行榜横截面 + 持仓净值装配因子评分（纯函数 compute_factor_scores 取数层）。

    `fetch_rank() -> list[dict]` 与 `fetch_nav(code, name, trading_days) -> points`
    可注入便于离线测试；默认分别走 fetch_open_fund_rank / FundDataService。
    设计文档：docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md 第 6 章。
    """
    from dataclasses import asdict

    from app.services.fund_factors import FundFactorInput, compute_factor_scores

    if fetch_rank is None:

        def fetch_rank():
            from app.services.akshare_subprocess import fetch_open_fund_rank

            return fetch_open_fund_rank(limit=300)

    if fetch_nav is None:

        def fetch_nav(code: str, name: str, trading_days: int):
            from app.services.fund_data import FundDataService

            history = FundDataService().get_nav_history(
                code, name, trading_days=trading_days
            )
            return history.points

    rank_rows = fetch_rank() or []
    universe = [
        FundFactorInput(
            fund_code=row["fund_code"],
            fund_name=row.get("fund_name", ""),
            return_3m_percent=row.get("return_3m_percent"),
            return_6m_percent=row.get("return_6m_percent"),
            return_1y_percent=row.get("return_1y_percent"),
            max_drawdown_1y_percent=row.get("max_drawdown_1y_percent"),
            fund_scale_yi=row.get("fund_scale_yi"),
        )
        for row in rank_rows
        if row.get("fund_code")
    ]
    rank_by_code = {row.fund_code: row for row in universe}

    targets: list[FundFactorInput] = []
    for holding in holdings_models:
        code = (holding.fund_code or "").strip()
        if not code or len(code) != 6 or code == "000000":
            continue
        if code in rank_by_code:
            base = rank_by_code[code]
            targets.append(
                FundFactorInput(
                    fund_code=code,
                    fund_name=holding.fund_name or base.fund_name,
                    return_3m_percent=base.return_3m_percent,
                    return_6m_percent=base.return_6m_percent,
                    return_1y_percent=base.return_1y_percent,
                    max_drawdown_1y_percent=base.max_drawdown_1y_percent,
                    fund_scale_yi=base.fund_scale_yi,
                )
            )
        else:
            targets.append(_target_from_nav(holding, fetch_nav))

    result = compute_factor_scores(universe=universe, targets=targets)
    return asdict(result)


_FACTOR_FACTS_CACHE: dict[str, tuple[float, dict]] = {}
_FACTOR_FACTS_TTL_SECONDS = 3600
_FACTOR_FACTS_KEYS = ("momentum", "risk_adjusted", "drawdown", "size")


def _compact_factor_scores(payload: dict, reliability: dict) -> dict:
    """把 build_factor_scores_payload 的结果压成紧凑 facts 结构（挂 IC 置信）。"""
    holdings = []
    for fund in payload.get("funds") or []:
        factors = fund.get("factors") or {}
        percentiles = {}
        for key in _FACTOR_FACTS_KEYS:
            detail = factors.get(key) or {}
            pct = detail.get("percentile")
            percentiles[key] = round(pct) if pct is not None else None
        holdings.append(
            {
                "fund_code": fund.get("fund_code"),
                "fund_name": fund.get("fund_name"),
                "composite_grade": fund.get("composite_grade"),
                "composite_score": fund.get("composite_score"),
                "factor_percentiles": percentiles,
            }
        )
    return {
        "available": bool(payload.get("available")),
        "universe_size": payload.get("universe_size", 0),
        "factor_reliability": reliability,
        "holdings": holdings,
    }


def build_factor_scores_for_facts(
    holdings_models: list[Holding],
    *,
    fetch_rank=None,
    fetch_nav=None,
    ic_factors: dict | None = None,
) -> dict:
    """喂 LLM 用的因子分（紧凑 + 挂 3A IC 置信），TTL 缓存 + best-effort。

    计算重（拉排行榜 + 净值），故生产路径按持仓代码缓存 1 小时；注入 fetcher 或
    ic_factors 时（测试路径）绕过缓存。任意异常 → available=false，不抛、不阻塞日报。
    """
    from app.services.factor_confidence import factor_reliability, load_ic_summary

    injected = fetch_rank is not None or fetch_nav is not None or ic_factors is not None
    cache_key = ",".join(
        sorted((h.fund_code or "") for h in holdings_models if h.fund_code)
    )
    now = time.time()
    if not injected:
        cached = _FACTOR_FACTS_CACHE.get(cache_key)
        if cached and now - cached[0] < _FACTOR_FACTS_TTL_SECONDS:
            return cached[1]

    try:
        payload = build_factor_scores_payload(
            holdings_models, fetch_rank=fetch_rank, fetch_nav=fetch_nav
        )
        reliability = factor_reliability(
            ic_factors if ic_factors is not None else load_ic_summary()
        )
        compact = _compact_factor_scores(payload, reliability)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        return {"available": False, "message": "因子分暂不可用"}

    if not injected:
        _FACTOR_FACTS_CACHE[cache_key] = (now, compact)
    return compact


def build_risk_metrics_for_facts(
    history_rows: list[dict],
    holdings_models: list[Holding],
) -> dict:
    """喂 LLM 用的组合风险度量（挂样本充足度置信），best-effort。

    内部走 build_risk_metrics_payload（取沪深300日线），任意异常 → available=false，
    不抛、不阻塞日报。
    """
    from app.services.risk_confidence import risk_metrics_confidence

    try:
        payload = build_risk_metrics_payload(history_rows, holdings_models)
    except Exception:  # noqa: BLE001 — best-effort，绝不阻塞日报
        return {"available": False, "message": "风险指标暂不可用"}
    payload["confidence"] = risk_metrics_confidence(payload)
    return payload


def build_evidence_overview_payload(holdings_models: list[Holding]) -> dict:
    """组合层证据总览（懒加载端点用）：精简装配三路 → 逐持仓 evidence → 组合级汇总。

    factor_scores 走 TTL 缓存、risk_metrics 取日快照、signal 取板块上下文；
    任一路 best-effort 缺失，全失败 → available=false。不触发 LLM、不阻塞。
    """
    from app.services.sector_signal_context import (
        build_signal_backtest_context,
        sector_labels_from_holdings,
        signal_backtest_for_sector,
    )
    from app.services.signal_synthesis import (
        build_evidence_overview,
        build_holding_evidence,
    )

    try:
        factor_scores = build_factor_scores_for_facts(holdings_models)
    except Exception:  # noqa: BLE001
        factor_scores = None

    risk_metrics = None
    try:
        from app.database import list_portfolio_daily_snapshots

        history_rows = list_portfolio_daily_snapshots(limit=400)
        risk_metrics = build_risk_metrics_for_facts(history_rows, holdings_models)
    except Exception:  # noqa: BLE001
        risk_metrics = None

    try:
        signal_ctx = build_signal_backtest_context(
            sector_labels_from_holdings(holdings_models)
        )
    except Exception:  # noqa: BLE001
        signal_ctx = None

    rows: list[dict] = []
    for holding in holdings_models:
        signal_entry = (
            signal_backtest_for_sector(holding.sector_name, signal_ctx)
            if signal_ctx
            else None
        )
        evidence = build_holding_evidence(
            fund_code=holding.fund_code,
            signal_entry=signal_entry,
            factor_scores=factor_scores,
            risk_metrics=risk_metrics,
        )
        row = {
            "fund_code": holding.fund_code,
            "fund_name": holding.fund_name,
            "holding_amount": round(holding.holding_amount, 2),
        }
        if evidence:
            row["evidence"] = evidence
        rows.append(row)

    overview = build_evidence_overview(rows)
    return {
        "available": bool(overview.get("available")),
        "overview": overview,
        "holdings": [r for r in rows if r.get("evidence")],
    }


def snapshot_date_key(when: datetime | None = None) -> str:
    moment = when or datetime.now(timezone.utc)
    return moment.date().isoformat()


def save_daily_snapshot(
    holdings: list[Holding],
    summary: PortfolioSummary | None,
) -> PortfolioDailySnapshot:
    total_from_holdings = sum(holding.holding_amount for holding in holdings)
    payload_holdings = [holding.model_dump() for holding in holdings]
    snapshot = PortfolioDailySnapshot(
        snapshot_date=snapshot_date_key(summary.updated_at if summary else None),
        total_assets=summary.total_assets if summary and summary.total_assets else total_from_holdings,
        daily_profit=summary.daily_profit if summary else None,
        daily_return_percent=summary.daily_return_percent if summary else None,
        holdings=payload_holdings,
        captured_at=datetime.now(timezone.utc),
    )
    save_portfolio_daily_snapshot(snapshot)
    from app.services.portfolio_holdings_cache import bump_holdings_cache_generation

    bump_holdings_cache_generation()
    return snapshot


def get_previous_holdings_for_review() -> list[Holding]:
    previous = get_most_recent_portfolio_snapshot()
    if previous is None:
        return []
    return [Holding.model_validate(item) for item in previous.get("holdings", [])]


def build_dashboard_payload(
    *,
    summary: PortfolioSummary | None,
    profiles: list,
    profit_range: ProfitRange = "today",
    calendar_year: int | None = None,
    calendar_month: int | None = None,
) -> dict:
    history_rows = list_portfolio_daily_snapshots(limit=400)
    history = [
        {
            "date": row["snapshot_date"],
            "total_assets": row.get("total_assets"),
            "daily_profit": row.get("daily_profit"),
            "daily_return_percent": row.get("daily_return_percent"),
        }
        for row in reversed(history_rows)
    ]

    latest = history_rows[0] if history_rows else None
    allocation_source = latest.get("holdings", []) if latest else []
    total_assets = (
        (summary.total_assets if summary and summary.total_assets else None)
        or (latest.get("total_assets") if latest else None)
        or sum(
            profile.holding_amount or 0
            for profile in profiles
            if getattr(profile, "holding_amount", None)
        )
        or 0
    )

    allocation = _build_allocation(allocation_source, profiles, total_assets)

    holdings_models = [Holding.model_validate(item) for item in allocation_source]
    if not holdings_models and profiles:
        holdings_models = [
            Holding(
                fund_code=profile.fund_code,
                fund_name=profile.fund_name,
                holding_amount=profile.holding_amount or 0,
                daily_profit=profile.daily_profit,
                holding_return_percent=profile.holding_return_percent,
                return_percent=profile.holding_return_percent,
            )
            for profile in profiles
            if isinstance(profile, FundProfile) and (profile.holding_amount or 0) > 0
        ]

    profiles_by_code = {
        profile.fund_code: profile
        for profile in profiles
        if isinstance(profile, FundProfile)
    }

    year, month = (
        (calendar_year, calendar_month)
        if calendar_year and calendar_month
        else default_calendar_anchor()
    )
    from app.services.portfolio_holdings_service import load_persisted_holdings

    live_holdings, *_ = load_persisted_holdings()
    calendar_holdings = live_holdings if live_holdings else holdings_models
    profit_trend = build_profit_trend(
        profit_range=profit_range,
        snapshots=history_rows,
        holdings=holdings_models,
        profiles_by_code=profiles_by_code,
    )
    trend_footer = summarize_trend_footer(
        profit_trend,
        summary_daily_return=summary.daily_return_percent if summary else None,
    )
    calendar = build_calendar_month(
        year=year,
        month=month,
        snapshots=history_rows,
        holdings=calendar_holdings,
    )
    daily_top5 = build_daily_top5(holdings_models)

    return {
        "summary": summary.model_dump(mode="json") if summary else {},
        "history": history,
        "allocation": allocation,
        "snapshot_count": len(history_rows),
        "latest_snapshot_date": latest["snapshot_date"] if latest else None,
        "trend_context": build_portfolio_trend_context(history_rows),
        "profit_range": profit_range,
        "profit_trend": profit_trend,
        "profit_trend_footer": trend_footer,
        "profit_calendar": calendar,
        "daily_top5": daily_top5,
        "risk_metrics": build_risk_metrics_payload(history_rows, holdings_models),
    }


def build_portfolio_trend_context(
  history_rows: list[dict] | None = None,
  *,
  lookback_days: int = 7,
) -> dict:
    rows = history_rows if history_rows is not None else list_portfolio_daily_snapshots(
        limit=lookback_days
    )
    if len(rows) < 2:
        return {
            "has_history": False,
            "lookback_days": lookback_days,
            "message": "历史快照不足，无法计算近一周组合走势。",
        }

    latest = rows[0]
    oldest = rows[min(len(rows) - 1, lookback_days - 1)]
    latest_assets = latest.get("total_assets")
    oldest_assets = oldest.get("total_assets")
    assets_delta_percent = None
    if latest_assets and oldest_assets and oldest_assets > 0:
        assets_delta_percent = round(
            (float(latest_assets) - float(oldest_assets)) / float(oldest_assets) * 100,
            2,
        )

    daily_returns = [
        row["daily_return_percent"]
        for row in rows[:lookback_days]
        if row.get("daily_return_percent") is not None
    ]
    cumulative_return_percent = (
        round(_compound_return_percent([float(value) for value in daily_returns]), 2)
        if daily_returns
        else None
    )

    summary_line = _format_trend_summary(
        span_days=min(len(rows), lookback_days),
        assets_delta_percent=assets_delta_percent,
        cumulative_return_percent=cumulative_return_percent,
        latest_date=str(latest.get("snapshot_date") or ""),
    )

    return {
        "has_history": True,
        "lookback_days": lookback_days,
        "snapshot_count": len(rows),
        "latest_snapshot_date": latest.get("snapshot_date"),
        "oldest_snapshot_date_in_window": oldest.get("snapshot_date"),
        "assets_delta_percent": assets_delta_percent,
        "cumulative_daily_return_percent": cumulative_return_percent,
        "summary_line": summary_line,
    }


def _format_trend_summary(
    *,
    span_days: int,
    assets_delta_percent: float | None,
    cumulative_return_percent: float | None,
    latest_date: str,
) -> str:
    parts: list[str] = [f"近 {span_days} 个交易日（至 {latest_date}）"]
    if assets_delta_percent is not None:
        parts.append(f"组合资产变化约 {assets_delta_percent:+.2f}%")
    if cumulative_return_percent is not None:
        parts.append(f"累计当日收益率合计约 {cumulative_return_percent:+.2f}%（为日度相加近似）")
    return "，".join(parts) + "。"


def _build_allocation(
    holdings_payload: list,
    profiles: list,
    total_assets: float,
) -> list[dict]:
    if holdings_payload:
        items = holdings_payload
    else:
        items = [
            {
                "fund_code": profile.fund_code,
                "fund_name": profile.fund_name,
                "holding_amount": profile.holding_amount or 0,
                "daily_profit": profile.daily_profit,
                "holding_return_percent": profile.holding_return_percent,
            }
            for profile in profiles
            if (profile.holding_amount or 0) > 0
        ]

    rows: list[dict] = []
    for item in items:
        amount = float(item.get("holding_amount") or 0)
        if amount <= 0:
            continue
        weight = round(amount / total_assets * 100, 2) if total_assets else 0
        rows.append(
            {
                "fund_code": item.get("fund_code"),
                "fund_name": item.get("fund_name"),
                "holding_amount": amount,
                "weight_percent": weight,
                "daily_profit": item.get("daily_profit"),
                "holding_return_percent": item.get("holding_return_percent")
                or item.get("return_percent"),
            }
        )
    rows.sort(key=lambda row: row["holding_amount"], reverse=True)
    return rows
