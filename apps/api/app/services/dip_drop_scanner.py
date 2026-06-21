from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from app.database import list_fund_primary_sectors
from app.models import Holding
from app.services.akshare_subprocess import fetch_open_fund_rank, fetch_open_fund_rank_worst_recent
from app.services.discovery_candidate_pool import (
    _entry_from_rank,
    _name_matches_sector,
    _passes_quality,
    _resolve_fund_name,
    _sector_keywords,
)
from app.services.discovery_selection_strategy import dip_rebound_score
from app.services.fund_data import FundDataService
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_momentum import _classify_pattern
from app.services.sector_registry import list_discovery_sector_labels

_MAX_1Y_RETURN_PERCENT = 80.0
_REBOUND_SCORE_CAP = 35.0
_RADAR_RANK_LIMIT = 150
_RADAR_NAV_CAP = 15
_RADAR_MAX_WORKERS = 1


def prescreen_dip_candidates(
    sector_label: str,
    rank_rows: list[dict],
    nav_by_code: dict[str, dict],
    *,
    lookback_days: int = 5,
    min_drop_percent: float = 3.0,
    keywords: tuple[str, ...],
    exclude_codes: set[str] | None = None,
    name_resolver: Callable[[str], str] | None = None,
) -> list[dict]:
    """Filter rank rows by NAV dip depth and emit prescreen entries for one sector."""
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    candidates: list[dict] = []

    for row in rank_rows:
        code = _row_fund_code(row)
        if not code or code in excluded:
            continue
        name = name_resolver(code) if name_resolver else _row_fund_name(row)
        if not _name_matches_sector(name, keywords):
            continue
        if not _passes_quality(row):
            continue
        r1y = _num(row.get("return_1y_percent"))
        if r1y is not None and r1y > _MAX_1Y_RETURN_PERCENT:
            continue

        nav = nav_by_code.get(code) or {}
        dip_percent = _dip_change_percent(nav, lookback_days)
        if dip_percent is None or dip_percent > -min_drop_percent:
            continue

        dist_high = _num(nav.get("distance_from_high_percent"))
        rebound_signals = _build_rebound_signals(nav)
        score = normalize_rebound_score(
            {
                "return_1y_percent": r1y,
                "nav_trend": {
                    "recent_5d_change_percent": dip_percent,
                    "distance_from_high_percent": dist_high,
                    "recent_5d_daily_change_percent": nav.get("recent_5d_daily_change_percent")
                    or [],
                },
            },
            rebound_signals=rebound_signals,
        )
        entry = _entry_from_rank(row, sector_label=sector_label, selection_reason="大跌预筛")
        entry.update(
            {
                "dip_drop_percent": round(dip_percent, 2),
                "dip_lookback_days": lookback_days,
                "distance_from_high_percent": dist_high,
                "rebound_signals": rebound_signals,
                "rebound_score": score,
            }
        )
        candidates.append(entry)

    candidates.sort(key=lambda item: float(item.get("dip_drop_percent") or 0.0))
    return candidates


def build_dip_pool_for_sectors(
    target_sectors: list[str],
    *,
    lookback_days: int = 5,
    min_drop_percent: float = 3.0,
    exclude_codes: set[str] | None = None,
    per_sector_top: int = 8,
    pool_cap: int = 30,
    budget_seconds: float = 15.0,
    fetch_rank=fetch_open_fund_rank,
) -> list[dict]:
    """Build dip-swing candidate pool across target sectors with NAV prescreen."""
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    rank_rows = fetch_rank(limit=300) or []
    primary_rows = list_fund_primary_sectors()
    deadline = time.monotonic() + max(budget_seconds, 1.0)

    sector_keywords: dict[str, tuple[str, ...]] = {}
    for sector_label in target_sectors:
        canon = get_canonical_sector(sector_label)
        sector_keywords[sector_label] = _sector_keywords(sector_label, canon)

    codes_to_fetch: set[str] = set()
    for sector_label in target_sectors:
        keywords = sector_keywords[sector_label]
        for row in rank_rows:
            code = _row_fund_code(row)
            if not code or code in excluded:
                continue
            if not _name_matches_sector(_row_fund_name(row), keywords):
                continue
            if not _passes_quality(row):
                continue
            codes_to_fetch.add(code)
        for prow in primary_rows:
            if prow.get("sector_name") != sector_label:
                continue
            code = str(prow.get("fund_code", "")).zfill(6)
            if code and code not in excluded:
                codes_to_fetch.add(code)

    nav_by_code = _fetch_nav_summaries(codes_to_fetch, deadline=deadline)

    collected: list[dict] = []
    seen_codes: set[str] = set()
    for sector_label in target_sectors:
        keywords = sector_keywords[sector_label]
        sector_rows = prescreen_dip_candidates(
            sector_label,
            rank_rows,
            nav_by_code,
            lookback_days=lookback_days,
            min_drop_percent=min_drop_percent,
            keywords=keywords,
            exclude_codes=excluded | seen_codes,
        )
        for entry in sector_rows[:per_sector_top]:
            code = str(entry.get("fund_code", "")).zfill(6)
            if code in seen_codes:
                continue
            seen_codes.add(code)
            collected.append(entry)
            if len(collected) >= pool_cap:
                return collected[:pool_cap]

    return collected[:pool_cap]


def build_dip_radar_pool_fast(
    *,
    lookback_days: int = 5,
    min_drop_percent: float = 2.0,
    pool_cap: int = 30,
    budget_seconds: float = 25.0,
    fetch_rank=fetch_open_fund_rank_worst_recent,
) -> list[dict]:
    """全市场排行快速预筛：先取近1周跌幅榜，再并行拉 NAV 精算近 N 日跌幅。"""
    rank_rows = fetch_rank(limit=_RADAR_RANK_LIMIT) or []
    try:
        primary_rows = list_fund_primary_sectors()
    except RuntimeError:
        primary_rows = []
    primary_by_code = {
        str(row.get("fund_code", "")).zfill(6): str(row.get("sector_name") or "").strip()
        for row in primary_rows
        if str(row.get("fund_code", "")).strip()
    }

    # 先用东财排行「近1周」跌幅（与几个交易日大跌语义一致），再对头部串行精算净值
    items: list[dict] = []
    for row in rank_rows:
        code = _row_fund_code(row)
        if not code or not _passes_quality(row):
            continue
        r1w = _num(row.get("return_1w_percent"))
        if r1w is None or r1w > -min_drop_percent:
            continue
        r1y = _num(row.get("return_1y_percent"))
        if r1y is not None and r1y > _MAX_1Y_RETURN_PERCENT:
            continue
        name = _row_fund_name(row)
        sector = primary_by_code.get(code) or _infer_sector_label(name)
        entry = _entry_from_rank(row, sector_label=sector, selection_reason="大跌雷达")
        entry.update(
            {
                "dip_drop_percent": round(r1w, 2),
                "dip_lookback_days": lookback_days,
                "rebound_signals": [],
                "rebound_score": normalize_rebound_score(
                    {
                        "return_1y_percent": r1y,
                        "nav_trend": {"recent_5d_change_percent": r1w},
                    }
                ),
                "dip_source": "rank_1w",
            }
        )
        items.append(entry)
        if len(items) >= pool_cap:
            break

    # 对前 N 只串行拉净值，替换为更精确的近5日跌幅（避免并行子进程崩溃）
    refine_codes = [str(i.get("fund_code", "")).zfill(6) for i in items[:_RADAR_NAV_CAP]]
    nav_by_code = _fetch_nav_summaries(
        set(refine_codes),
        deadline=time.monotonic() + max(budget_seconds, 1.0),
        max_workers=1,
    )
    for entry in items[:_RADAR_NAV_CAP]:
        code = str(entry.get("fund_code", "")).zfill(6)
        nav = nav_by_code.get(code)
        if not nav:
            continue
        dip_percent = _dip_change_percent(nav, lookback_days)
        if dip_percent is None or dip_percent > -min_drop_percent:
            continue
        dist_high = _num(nav.get("distance_from_high_percent"))
        rebound_signals = _build_rebound_signals(nav)
        entry["dip_drop_percent"] = round(dip_percent, 2)
        entry["distance_from_high_percent"] = dist_high
        entry["rebound_signals"] = rebound_signals
        entry["rebound_score"] = normalize_rebound_score(
            {
                "return_1y_percent": _num(entry.get("return_1y_percent")),
                "nav_trend": {
                    "recent_5d_change_percent": dip_percent,
                    "distance_from_high_percent": dist_high,
                    "recent_5d_daily_change_percent": nav.get("recent_5d_daily_change_percent") or [],
                },
            },
            rebound_signals=rebound_signals,
        )
        entry["dip_source"] = "nav"

    items.sort(key=lambda item: float(item.get("dip_drop_percent") or 0.0))
    return items[:pool_cap]


def build_dip_radar_pool_with_stats(
    **kwargs,
) -> tuple[list[dict], dict]:
    """Wrapper returning pool + scan diagnostics for API transparency."""
    lookback_days = int(kwargs.get("lookback_days") or 5)
    min_drop_percent = float(kwargs.get("min_drop_percent") or 2.0)
    pool_cap = int(kwargs.get("pool_cap") or 30)
    budget_seconds = float(kwargs.get("budget_seconds") or 25.0)
    fetch_rank = kwargs.get("fetch_rank") or fetch_open_fund_rank_worst_recent

    rank_rows = fetch_rank(limit=_RADAR_RANK_LIMIT) or []
    pool = build_dip_radar_pool_fast(
        lookback_days=lookback_days,
        min_drop_percent=min_drop_percent,
        pool_cap=pool_cap,
        budget_seconds=budget_seconds,
        fetch_rank=fetch_rank,
    )

    stats = {
        "rank_shortlist": len(rank_rows),
        "dip_threshold_percent": min_drop_percent,
        "lookback_days": lookback_days,
        "nav_budget_seconds": budget_seconds,
        "matches": len(pool),
    }

    return pool, stats


def normalize_rebound_score(row: dict, *, rebound_signals: list[dict] | None = None) -> float:
    """Map dip_rebound_score heuristic to 0–100 signal strength."""
    raw = dip_rebound_score(row)
    normalized = min(100.0, max(0.0, raw / _REBOUND_SCORE_CAP * 100.0))
    signals = rebound_signals or []
    if any(signal.get("id") == "two_day_reversal_up" for signal in signals):
        normalized = min(100.0, normalized + 8.0)
    if any(signal.get("id") == "persistent_decline" for signal in signals):
        normalized = max(0.0, normalized - 12.0)
    return round(normalized, 1)


def _fetch_nav_summaries(
    codes: set[str],
    *,
    deadline: float,
    max_workers: int = 1,
) -> dict[str, dict]:
    if not codes:
        return {}
    if max_workers <= 1:
        return _fetch_nav_summaries_sequential(codes, deadline=deadline)

    nav_by_code: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(codes))) as executor:
        futures = {
            executor.submit(_nav_summary_for_code, code): code for code in sorted(codes)
        }
        pending = set(futures.keys())
        while pending and time.monotonic() <= deadline:
            done, pending = wait(pending, timeout=max(0.05, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            for future in done:
                code = futures[future]
                try:
                    summary = future.result()
                    if summary:
                        nav_by_code[code] = summary
                except Exception:
                    continue
    return nav_by_code


def _fetch_nav_summaries_sequential(codes: set[str], *, deadline: float) -> dict[str, dict]:
    nav_by_code: dict[str, dict] = {}
    for code in sorted(codes):
        if time.monotonic() > deadline:
            break
        summary = _nav_summary_for_code(code)
        if summary:
            nav_by_code[code] = summary
    return nav_by_code


def _nav_summary_for_code(code: str) -> dict | None:
    service = FundDataService()
    holding = Holding(fund_code=code, fund_name=_resolve_fund_name(code), holding_amount=0)
    _snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=66)
    if trend is None or not getattr(trend, "points", None):
        return None
    from app.services.nav_trend_summary import summarize_nav_history

    return summarize_nav_history(trend, recent_sample=5)


def _infer_sector_label(fund_name: str) -> str:
    name = (fund_name or "").strip()
    if not name:
        return "综合"
    for label in list_discovery_sector_labels():
        canon = get_canonical_sector(label)
        keywords = _sector_keywords(label, canon)
        if _name_matches_sector(name, keywords):
            return label
    return "综合"


def _build_rebound_signals(nav: dict) -> list[dict]:
    daily = nav.get("recent_5d_daily_change_percent") or []
    if not isinstance(daily, list):
        daily = []
    daily_changes = [float(x) for x in daily if _num(x) is not None]
    pattern = _classify_pattern(daily_changes, None)
    signals: list[dict] = []
    label = pattern.get("label")
    if label == "two_day_reversal_up":
        signals.append({"id": "two_day_reversal_up", "label": "近两日先跌后涨"})
    if len(daily_changes) >= 10 and all(change < 0 for change in daily_changes[-10:]):
        signals.append({"id": "persistent_decline", "label": "持续阴跌"})
    if label == "sector_weak":
        signals.append({"id": "sector_stabilizing", "label": "板块跌势放缓"})
    return signals


def _dip_change_percent(nav: dict, lookback_days: int) -> float | None:
    if lookback_days == 5:
        return _num(nav.get("recent_5d_change_percent"))
    daily = nav.get("recent_5d_daily_change_percent") or []
    if not isinstance(daily, list) or len(daily) < lookback_days:
        recent = _num(nav.get("recent_5d_change_percent"))
        if recent is not None and lookback_days == 3:
            return recent
        return None
    window = [float(x) for x in daily[-lookback_days:] if _num(x) is not None]
    if len(window) < lookback_days:
        return None
    compounded = 1.0
    for change in window:
        compounded *= 1.0 + change / 100.0
    return round((compounded - 1.0) * 100.0, 2)


def _row_fund_code(row: dict) -> str:
    raw = row.get("fund_code") or row.get("基金代码") or ""
    code = str(raw).strip().zfill(6)
    return code if code.isdigit() and len(code) == 6 else ""


def _row_fund_name(row: dict) -> str:
    return str(row.get("fund_name") or row.get("基金简称") or "").strip()


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
