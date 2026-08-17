from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime
import hashlib
import json
from math import isfinite
import threading

from app.database import (
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    list_fund_primary_sectors_by_sector_names,
)
from app.models import Holding
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.discovery_selection_strategy import (
    SelectionStrategy,
)
from app.services.discovery_sector_identity import annotate_candidate_sector_identity
from app.services.fund_benchmark_sector import resolve_sector_from_benchmark
from app.services.fund_code_resolver import lookup_fund_name_by_code
from app.services.fund_data import FundDataService, _map_holdings_concurrently
from app.services.fund_discovery_data_cache import (
    fetch_discovery_fund_universe_cached,
    fetch_fund_research_profiles_cached,
)
from app.services.fund_name_utils import extract_share_class_letter
from app.services.fund_rank_cache import fetch_open_fund_rank_cached
from app.services.fund_peer_ranking import (
    PEER_CATALOGUE_CLASSIFICATION_FIELDS,
    build_fund_peer_group,
    build_peer_rank,
    catalogue_aligned_peer_target,
    peer_catalogue_bucket,
    resolve_benchmark_comparison,
)
from app.services.fund_sector_identity import is_current_identity_row_fresh
from app.services.fund_vehicle_quality import assess_candidate_vehicle_quality
from app.services.news_freshness import normalize_news_now
from app.services.sector_canonical import get_canonical_sector
from app.services.shared_executors import get_discovery_context_executor
from app.services.streaming_heartbeat import raise_if_stream_cancelled

_POOL_CAP = 28
_PER_SECTOR = 5
# A full 14-direction opportunity scan currently scores roughly 550--600
# unique rows. 512 made the audit invalid even though the actual 28-candidate
# decision pool was complete. Keep the evidence bounded, but retain one full
# normal scan so recall validation describes the real funnel.
_MAX_RECALL_AUDIT_CANDIDATES = 1024
_MIN_SCALE_YI = 1.0
_HARD_MIN_SCALE_YI = 0.5
_MIN_HISTORY_DAYS = 365
_QUALITY_SCORE_VERSION = "fund_quality.v4"
_SECTOR_MATCH_STRENGTH = {
    "fallback": 0,
    "name": 1,
    "new_issue": 2,
    "tracking_exact": 3,
    "primary": 4,
}
# Only independently observed mappings may satisfy the executable sector-fit
# gate.  LLM/name/free-form inference remains useful for broad recall, but it
# must not turn an active financial-real-estate fund into a verified fintech
# vehicle (or create an equivalent cross-theme false positive).
_DIRECTLY_VERIFIED_PRIMARY_SOURCES = frozenset(
    {
        "ocr_detail",
        "manual",
        "holdings_infer",
        "precompute_holdings",
    }
)
_BENCHMARK_PRIMARY_SOURCES = frozenset({"benchmark_index", "precompute_benchmark"})
# 方向 label → 可接受的已核验基金身份板块（单向映射）。
#
# 方向引擎的行情证据可以比基金身份粒度更粗：「贵金属」方向的行情代理是东财
# BK0732 贵金属行业板，而黄金 ETF 联接的身份挂在「黄金」（基准 AU9999 现货）、
# 黄金股 ETF 联接挂在「黄金股」（931238）。这两类载体都是贵金属方向的合法工具，
# 不扩展就会出现"方向可布局但结构性无载体"。注意映射只对方向侧生效：
# 「黄金」「黄金股」两个方向各自仍只接受自己的身份，互不混用。
_DIRECTION_ACCEPTABLE_IDENTITY_SECTORS: dict[str, tuple[str, ...]] = {
    "贵金属": ("贵金属", "黄金股", "黄金"),
}


def _acceptable_identity_sectors(sector_label: str) -> tuple[str, ...]:
    label = str(sector_label or "").strip()
    return _DIRECTION_ACCEPTABLE_IDENTITY_SECTORS.get(label, (label,))
_CORE_QUALITY_FIELDS = (
    "return_3m_percent",
    "return_6m_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
    "established_date",
    "fund_manager",
    "nav_date",
)
# 已随 `catalogue_aligned_peer_target` 一并移到 `fund_peer_ranking`，此处保留别名供既有
# 引用（如有）继续可用。
_PEER_CATALOGUE_CLASSIFICATION_FIELDS = PEER_CATALOGUE_CLASSIFICATION_FIELDS


def build_candidate_pool(
    target_sectors: list[str],
    *,
    exclude_codes: set[str] | None = None,
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
    discovery_strategy: str = "risk_first",
    per_sector: int = _PER_SECTOR,
    pool_cap: int = _POOL_CAP,
    fetch_rank=None,
    prepared_universe_rows: list[dict] | None = None,
    fetch_new_funds=None,
    sector_opportunities: list[dict] | None = None,
    decision_at: datetime | None = None,
    recall_audit_sink: dict | None = None,
    recall_audit_limit: int = _MAX_RECALL_AUDIT_CANDIDATES,
    stop_event: threading.Event | None = None,
) -> list[dict]:
    raise_if_stream_cancelled(stop_event)
    if recall_audit_sink is not None and recall_audit_limit <= 0:
        raise ValueError("recall_audit_limit must be positive")
    decision_date = normalize_news_now(decision_at).date()
    # 默认使用全量、分类型的开放式基金横截面，避免“近1年涨幅前300名”造成
    # 赢家偏差；冷启动失败时再降级到前500名排行。注入 fetch_rank 仍保留给测试。
    universe_mode = "injected"
    if prepared_universe_rows is not None:
        rank_rows = [
            dict(row) for row in prepared_universe_rows if isinstance(row, dict)
        ]
        universe_mode = "full" if rank_rows else "top_500_fallback"
        if not rank_rows:
            rank_rows = fetch_open_fund_rank_cached(limit=500) or []
    elif fetch_rank is None:
        rank_rows = fetch_discovery_fund_universe_cached(limit=20_000) or []
        universe_mode = "full" if rank_rows else "top_500_fallback"
        if not rank_rows:
            rank_rows = fetch_open_fund_rank_cached(limit=500) or []
    else:
        rank_rows = fetch_rank(limit=300) or []
    raise_if_stream_cancelled(stop_event)
    if fetch_new_funds is None:
        fetch_new_funds = fetch_new_fund_offerings
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    rank_by_code = {
        str(row.get("fund_code", "")).zfill(6): row
        for row in rank_rows
        if str(row.get("fund_code", "")).strip()
    }
    opportunity_by_sector = {
        str(item.get("sector_label") or "").strip(): item
        for item in (sector_opportunities or [])
        if str(item.get("sector_label") or "").strip()
    }
    # Tenant rows are accepted only when the user/OCR supplied the identity
    # directly.  Shared holdings/index identities come from the fresh verified
    # materialized view; name and LLM rows remain recall-only.
    tenant_primary_rows = [
        row
        for row in list_fund_primary_sectors()
        if str(row.get("source") or "") in {"manual", "ocr_detail"}
    ]
    identity_sector_names = list(
        dict.fromkeys(
            identity_sector
            for sector in target_sectors
            for identity_sector in _acceptable_identity_sectors(sector)
        )
    )
    primary_rows = tenant_primary_rows + list_fund_primary_sectors_by_sector_names(
        identity_sector_names,
        limit_per_sector=20,
    )
    new_issue_rows: list[dict] = []
    if selection_strategy == "with_new_issue":
        new_issue_rows = fetch_new_funds(limit=300) or []
    raise_if_stream_cancelled(stop_event)

    collected: list[dict] = []
    seen_codes: set[str] = set()
    family_seen: set[str] = set()
    recall_state = (
        {
            "seen_codes": set(),
            "retained": {},
            "forced_codes": set(),
            "total": 0,
        }
        if recall_audit_sink is not None
        else None
    )

    for index, sector_label in enumerate(target_sectors):
        raise_if_stream_cancelled(stop_event)
        sector_limit = _sector_candidate_limit(
            sector_label,
            index=index,
            base_limit=per_sector,
            pool_cap=pool_cap,
            total_sectors=len(target_sectors),
            opportunity_by_sector=opportunity_by_sector,
        )
        sector_candidates = _candidates_for_sector(
            sector_label,
            rank_rows=rank_rows,
            rank_by_code=rank_by_code,
            primary_rows=primary_rows,
            new_issue_rows=new_issue_rows,
            excluded=excluded,
            seen_codes=seen_codes,
            fund_type_preference=fund_type_preference,
            selection_strategy=selection_strategy,
            discovery_strategy=discovery_strategy,
            opportunity=opportunity_by_sector.get(sector_label),
            family_seen=family_seen,
            limit=sector_limit,
            as_of_date=decision_date,
            recall_audit_state=recall_state,
            recall_audit_limit=recall_audit_limit,
        )
        for candidate in sector_candidates:
            candidate["candidate_universe_mode"] = universe_mode
            candidate["candidate_universe_size"] = len(rank_rows)
        collected.extend(sector_candidates[:sector_limit])
        if len(collected) >= pool_cap and recall_audit_sink is None:
            break

    if len(collected) < 3 and len(collected) < pool_cap:
        raise_if_stream_cancelled(stop_event)
        fallback_ranked = rank_candidates_balanced_fallback(
            rank_rows,
            excluded,
            seen_codes,
            fund_type_preference,
            selection_strategy,
            discovery_strategy=discovery_strategy,
            family_seen=family_seen,
            as_of_date=decision_date,
        )
        if recall_state is not None:
            _record_scored_recall_candidates(
                recall_state,
                fallback_ranked,
                limit=recall_audit_limit,
                matched_sector="综合",
            )
        for entry in fallback_ranked:
            entry["candidate_universe_mode"] = universe_mode
            entry["candidate_universe_size"] = len(rank_rows)
            collected.append(entry)
            seen_codes.add(str(entry.get("fund_code", "")).zfill(6))
            family_seen.add(_family_key(str(entry.get("fund_name") or "")))
            if len(collected) >= pool_cap:
                break

    selected = collected[:pool_cap]
    if recall_state is not None:
        for candidate in selected:
            _record_scored_recall_candidates(
                recall_state,
                [candidate],
                limit=recall_audit_limit,
                matched_sector=str(candidate.get("sector_label") or ""),
                force=True,
            )
            alternatives = candidate.get("_share_family_alternatives")
            if isinstance(alternatives, list):
                _record_scored_recall_candidates(
                    recall_state,
                    [item for item in alternatives if isinstance(item, dict)],
                    limit=recall_audit_limit,
                    matched_sector=str(candidate.get("sector_label") or ""),
                    force=True,
                )
        _populate_recall_audit_sink(
            recall_audit_sink,
            state=recall_state,
            limit=recall_audit_limit,
            target_sectors=target_sectors,
            source_universe_size=len(rank_rows),
            source_universe_mode=universe_mode,
        )
    peer_research_rows = list(selected)
    for candidate in selected:
        alternatives = candidate.get("_share_family_alternatives")
        if isinstance(alternatives, list):
            peer_research_rows.extend(
                item for item in alternatives if isinstance(item, dict)
            )
    _attach_descriptive_peer_research(
        peer_research_rows,
        universe=rank_rows,
        decision_at=decision_at,
    )
    return selected


def _attach_descriptive_peer_research(
    candidates: list[dict],
    *,
    universe: list[dict],
    decision_at: datetime | None,
) -> None:
    """Attach PIT peer groups/percentiles without turning them into execution tilt.

    The full universe is bucketed by catalogue type first so a production scan
    does not repeatedly classify all ~20k funds for every finalist. The peer
    module still performs the stricter active/passive, QDII and subtype split.
    """

    if not candidates:
        return
    decision = normalize_news_now(decision_at)
    buckets: dict[str, list[dict]] = {}
    for raw in universe:
        if not isinstance(raw, dict):
            continue
        buckets.setdefault(_peer_catalogue_bucket(raw), []).append(raw)

    for candidate in candidates:
        code = str(candidate.get("fund_code") or "").zfill(6)
        source_target = next(
            (
                row
                for row in buckets.get(_peer_catalogue_bucket(candidate), [])
                if str(row.get("fund_code") or "").zfill(6) == code
            ),
            None,
        )
        target = _catalogue_aligned_peer_target(
            candidate,
            source_target=source_target,
        )
        target_universe = buckets.get(_peer_catalogue_bucket(target), [])
        try:
            peer_rank = build_peer_rank(
                target,
                target_universe,
                decision_at=decision,
            )
        except (TypeError, ValueError):
            continue
        candidate["peer_group"] = peer_rank.get("peer_group") or {}
        candidate["peer_rank"] = peer_rank


def _record_scored_recall_candidates(
    state: dict,
    candidates: list[dict],
    *,
    limit: int,
    matched_sector: str,
    force: bool = False,
) -> None:
    seen_codes: set[str] = state["seen_codes"]
    retained: dict[str, dict] = state["retained"]
    forced_codes: set[str] = state["forced_codes"]
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("fund_code") or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit() or code == "000000":
            continue
        if code not in seen_codes:
            seen_codes.add(code)
            state["total"] += 1
        if code in retained:
            sectors = retained[code].setdefault("recall_matched_sectors", [])
            if matched_sector and matched_sector not in sectors:
                sectors.append(matched_sector)
            if force:
                forced_codes.add(code)
            continue
        if len(retained) >= limit:
            if not force:
                continue
            removable = next(
                (value for value in reversed(list(retained)) if value not in forced_codes),
                None,
            )
            if removable is None:
                continue
            retained.pop(removable, None)
        compact = _compact_recall_audit_candidate(raw)
        compact["recall_matched_sectors"] = [matched_sector] if matched_sector else []
        retained[code] = compact
        if force:
            forced_codes.add(code)


def _compact_recall_audit_candidate(candidate: dict) -> dict:
    keys = (
        "fund_code",
        "fund_name",
        "fund_type",
        "sector_label",
        "selection_reason",
        "sector_match_kind",
        "sector_identity_status",
        "sector_identity_eligible",
        "sector_mapping_verified",
        "fund_quality_score",
        "sector_fit_score",
        "recall_upside_score",
        "opportunity_score_20_60d",
        "opportunity_score_version",
        "fund_entry_signal",
        "quality_score_version",
        "quality_score_components",
        "quality_reasons",
        "quality_penalties",
        "candidate_universe_source",
        "candidate_universe_available_at",
        "candidate_universe_mode",
        "candidate_universe_size",
    )
    return {key: candidate.get(key) for key in keys if candidate.get(key) is not None}


def _populate_recall_audit_sink(
    sink: dict,
    *,
    state: dict,
    limit: int,
    target_sectors: list[str],
    source_universe_size: int,
    source_universe_mode: str,
) -> None:
    rows = list(state["retained"].values())
    rows.sort(
        key=lambda item: (
            _num(item.get("fund_quality_score")) or -999.0,
            _num(item.get("sector_fit_score")) or -999.0,
            str(item.get("fund_code") or ""),
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["recall_rank"] = rank
    total = int(state["total"])
    complete = total <= limit
    sink.clear()
    sink.update(
        {
            "schema_version": "discovery_candidate_recall.v1",
            "scope": {
                "definition": (
                    "unique candidates scored for requested target sectors, plus ranked "
                    "fallback only when target recall underfills, before sector, "
                    "share-family, and global pool caps"
                ),
                "target_sectors": list(
                    dict.fromkeys(str(value).strip() for value in target_sectors if str(value).strip())
                ),
                "code_deduplicated": True,
                "duplicate_resolution": (
                    "first_target_sector_observation_with_all_matched_sectors_recorded"
                ),
                "complete": complete,
                "candidate_count_total": total,
                "candidate_count_retained": len(rows),
                "retention_limit": limit,
                "truncated_reason": None if complete else "recall_audit_retention_limit",
                "catalogue_rows_embedded": False,
                "source_universe_size": source_universe_size,
                "source_universe_mode": source_universe_mode,
            },
            "candidates": rows,
        }
    )


# 目录分桶与目标对齐已抽到 `fund_peer_ranking`（日报给持仓算同类分位要用**完全同一份**
# 口径，否则同一只基金在两条链路会落进不同的同类组、「同类分位」两个界面不可比）。
# 这里保留原私有名作为薄委托，既有调用点与测试无需改动。
def _peer_catalogue_bucket(row: dict) -> str:
    return peer_catalogue_bucket(row)


def _catalogue_aligned_peer_target(
    candidate: dict,
    *,
    source_target: dict | None,
) -> dict:
    return catalogue_aligned_peer_target(candidate, source_target=source_target)


def enrich_candidates(
    pool: list[dict],
    *,
    discovery_strategy: str = "risk_first",
    decision_at: datetime | None = None,
    stop_event: threading.Event | None = None,
) -> list[dict]:
    raise_if_stream_cancelled(stop_event)
    decision_moment = normalize_news_now(decision_at)
    decision_date = decision_moment.date()
    service = FundDataService()
    # “发现基金”只负责研究与推荐，销售平台是否可申购由用户在下单端确认。
    # 不再扩展 A/C 份额并逐份额抓取交易状态，既避免平台可买性误伤机会，
    # 也把最重的一组网络请求从报告生成链路中移除。
    research_pool: list[dict] = []
    research_codes: set[str] = set()
    for item in pool:
        alternatives = item.get("_share_family_alternatives")
        family_rows = [item]
        if isinstance(alternatives, list):
            family_rows.extend(
                alternative
                for alternative in alternatives
                if isinstance(alternative, dict)
            )
        for family_row in family_rows:
            row = dict(family_row)
            row.pop("_share_family_alternatives", None)
            for key in (
                "sector_label",
                "candidate_universe_mode",
                "candidate_universe_size",
                "candidate_universe_source",
                "candidate_universe_available_at",
                "opportunity_track",
                "opportunity_score",
                "entry_hint",
            ):
                if row.get(key) is None and item.get(key) is not None:
                    row[key] = item[key]
            code = str(row.get("fund_code") or "").zfill(6)
            if code in research_codes:
                continue
            research_pool.append(row)
            research_codes.add(code)
    codes = [str(item.get("fund_code") or "").zfill(6) for item in research_pool]

    support_executor = get_discovery_context_executor()
    profile_future = support_executor.submit(fetch_fund_research_profiles_cached, codes)
    def _enrich_one(item: dict) -> dict:
        raise_if_stream_cancelled(stop_event)
        code = str(item.get("fund_code", "")).zfill(6)
        name = str(item.get("fund_name", ""))
        holding = Holding(fund_code=code, fund_name=name, holding_amount=0)
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=252)
        row = dict(item)
        row["fund_type"] = _first_present(snapshot.fund_type, row.get("fund_type"))
        nav_metrics = _quality_metrics_from_nav_history(
            trend,
            fund_code=code,
            fund_name=name,
            fund_type=row.get("fund_type"),
            effective_trade_date=decision_date.isoformat(),
            observed_at=decision_moment.isoformat(),
        )
        _apply_nav_quality_metric_fallbacks(row, nav_metrics)
        row["return_1y_percent"] = _first_present(
            row.get("return_1y_percent"),
            snapshot.return_1y_percent,
        )
        row["max_drawdown_1y_percent"] = _first_valid_drawdown(
            row.get("max_drawdown_1y_percent"),
            snapshot.max_drawdown_1y_percent,
        )
        row["fund_scale_yi"] = _first_present(
            row.get("fund_scale_yi"), snapshot.fund_scale_yi
        )
        row["management_fee"] = snapshot.management_fee
        row["latest_nav"] = _first_present(snapshot.latest_nav, row.get("latest_nav"))
        row["nav_date"] = _first_present(snapshot.nav_date, row.get("nav_date"))
        if trend is not None and getattr(trend, "points", None):
            from app.services.nav_trend_summary import summarize_nav_history

            row["nav_trend"] = summarize_nav_history(
                trend, recent_sample=5, window_days=66
            )
        return row

    # 主候选最多 28 只，并额外保留少量同基金份额备选；全部补齐净值、基准身份和载体质量后，
    # 再选择最终家族代表，避免粗排先留下资料不足的份额。并发执行保持输入顺序。
    try:
        enriched = _map_holdings_concurrently(
            research_pool,
            _enrich_one,
            stop_event=stop_event,
        )
        try:
            while True:
                raise_if_stream_cancelled(stop_event)
                try:
                    profiles = profile_future.result(timeout=0.25)
                    break
                except FutureTimeoutError:
                    continue
        except Exception:  # noqa: BLE001 - research profile is best-effort
            raise_if_stream_cancelled(stop_event)
            profiles = {}
    finally:
        profile_future.cancel()

    rescored: list[dict] = []
    for raw in enriched:
        raise_if_stream_cancelled(stop_event)
        row = dict(raw)
        code = str(row.get("fund_code") or "").zfill(6)
        profile = profiles.get(code) or {}
        for key in (
            "fund_category",
            "fund_manager",
            "established_date",
            "profile_updated_at",
            "profile_source",
            "profile_sources",
            "profile_checked_at",
            "profile_status",
            "profile_missing_fields",
            "profile_stale_fields",
            "fund_scale_basis",
            "fund_shares_yi",
            "fund_shares_basis",
            "tracking_reference_text",
            "benchmark_text",
            "benchmark_text_kind",
            "benchmark_text_source_kind",
        ):
            if profile.get(key) is not None:
                row[key] = profile[key]
        row["fund_scale_yi"] = _first_present(
            profile.get("fund_scale_yi"), row.get("fund_scale_yi")
        )
        if row.get("fund_scale_yi") is None:
            shares_yi = _num(profile.get("fund_shares_yi"))
            latest_nav = _num(row.get("latest_nav"))
            if shares_yi is not None and shares_yi > 0 and latest_nav is not None and latest_nav > 0:
                row["fund_scale_yi"] = round(shares_yi * latest_nav, 4)
                row["fund_scale_basis"] = "nav_times_xq_latest_shares"
        row["fund_type"] = _first_present(
            profile.get("fund_category"), row.get("fund_type")
        )
        row = _with_exact_passive_tracking_match(row)
        row = _with_data_quality_gate(
            row,
            as_of_date=decision_date,
            discovery_strategy=discovery_strategy,
        )
        row = _with_quality_score(
            row,
            fund_type_preference="any",
            discovery_strategy=discovery_strategy,
        )
        row = assess_candidate_vehicle_quality(row)
        rescored.append(row)

    rescored.sort(
        key=lambda item: (
            _quality_gate_rank(item),
            _num(item.get("fund_quality_score")) or -999.0,
        ),
        reverse=True,
    )
    return rescored


def finalize_candidate_pool(
    pool: list[dict],
    target_sectors: list[str],
    *,
    per_sector: int = 3,
    pool_cap: int = _POOL_CAP,
    minimum_holding_days: int | None = None,
    discovery_strategy: str = "risk_first",
    audit_sink: dict | None = None,
    stage_audit_sink: dict | None = None,
) -> list[dict]:
    """在核心字段补全后再做最终准入与板块配额分配。

    初筛阶段尚不知道规模、经理和完整回撤。这里移除硬性排除项，并先为每个
    目标板块保留质量最高的候选，再用剩余高质量候选补足总池，避免低规模基金
    在补全前占满板块名额、把更可靠的后备基金挡在池外。
    """

    if pool_cap <= 0 or per_sector <= 0:
        return []
    original_pool = [dict(item) for item in pool]
    pool = _select_representative_share_classes(
        original_pool,
        discovery_strategy=discovery_strategy,
    )
    acceptable = [
        dict(item)
        for item in pool
        if str((item.get("quality_gate") or {}).get("status") or "watch_only")
        != "excluded"
    ]
    acceptable.sort(
        key=lambda item: (
            _quality_gate_rank(item),
            _vehicle_quality_gate_rank(item),
            *(
                (_opportunity_rank_value(item),)
                if discovery_strategy == "opportunity_first"
                else ()
            ),
            _num(item.get("vehicle_quality_score")) or -999.0,
            _num(item.get("fund_quality_score")) or -999.0,
            _num(item.get("sector_fit_score")) or -999.0,
        ),
        reverse=True,
    )

    selected: list[dict] = []
    selected_codes: set[str] = set()
    for sector in dict.fromkeys(str(item).strip() for item in target_sectors if str(item).strip()):
        sector_rows = [
            item for item in acceptable if str(item.get("sector_label") or "") == sector
        ]
        for item in sector_rows[:per_sector]:
            code = str(item.get("fund_code") or "").zfill(6)
            if code in selected_codes:
                continue
            selected.append(item)
            selected_codes.add(code)
            if len(selected) >= pool_cap:
                break
        if len(selected) >= pool_cap:
            break

    if len(selected) < pool_cap:
        for item in acceptable:
            code = str(item.get("fund_code") or "").zfill(6)
            if code in selected_codes:
                continue
            selected.append(item)
            selected_codes.add(code)
            if len(selected) >= pool_cap:
                break

    selected.sort(
        key=lambda item: (
            _quality_gate_rank(item),
            *(
                (_opportunity_rank_value(item),)
                if discovery_strategy == "opportunity_first"
                else ()
            ),
            _num(item.get("vehicle_quality_score")) or -999.0,
            _num(item.get("fund_quality_score")) or -999.0,
        ),
        reverse=True,
    )
    for rank, item in enumerate(selected, start=1):
        item["candidate_final_rank"] = rank
    if audit_sink is not None:
        _populate_candidate_selection_audit(
            audit_sink,
            original_pool=original_pool,
            family_selected_pool=pool,
            ranked_acceptable=acceptable,
            selected=selected,
        )
    if stage_audit_sink is not None:
        _populate_candidate_selection_stage_trace(
            stage_audit_sink,
            gate_candidates=original_pool,
            family_selected_pool=pool,
            ranked_acceptable=acceptable,
            selected=selected,
        )
    return selected


def _opportunity_rank_value(item: dict) -> float:
    value = _num(item.get("opportunity_score_20_60d"))
    return value if value is not None else -999.0


def _populate_candidate_selection_stage_trace(
    sink: dict,
    *,
    gate_candidates: list[dict],
    family_selected_pool: list[dict],
    ranked_acceptable: list[dict],
    selected: list[dict],
) -> None:
    family_codes = {
        str(item.get("fund_code") or "").zfill(6) for item in family_selected_pool
    }
    acceptable_codes = {
        str(item.get("fund_code") or "").zfill(6) for item in ranked_acceptable
    }
    final_codes = {str(item.get("fund_code") or "").zfill(6) for item in selected}
    gate: list[dict] = []
    for raw in gate_candidates:
        row = dict(raw)
        code = str(row.get("fund_code") or "").zfill(6)
        if code not in family_codes:
            reasons = ["share_class_not_selected_after_quality_dedup"]
        elif code not in acceptable_codes:
            reasons = ["quality_gate_excluded"]
        else:
            reasons = ["promoted_to_prescreen"]
        row["candidate_selection_transition_reasons"] = reasons
        gate.append(row)
    prescreen: list[dict] = []
    for raw in ranked_acceptable:
        row = dict(raw)
        code = str(row.get("fund_code") or "").zfill(6)
        row["candidate_selection_transition_reasons"] = [
            "selected_within_sector_quota_and_pool_cap"
            if code in final_codes
            else "outside_final_sector_quota_or_pool_cap"
        ]
        prescreen.append(row)
    sink.clear()
    sink.update(
        {
            "gate_candidates": gate,
            "prescreen_candidates": prescreen,
            "final_candidates": [dict(item) for item in selected],
        }
    )


def _populate_candidate_selection_audit(
    sink: dict,
    *,
    original_pool: list[dict],
    family_selected_pool: list[dict],
    ranked_acceptable: list[dict],
    selected: list[dict],
) -> None:
    family_selected_codes = {
        str(item.get("fund_code") or "").zfill(6) for item in family_selected_pool
    }
    acceptable_rank = {
        str(item.get("fund_code") or "").zfill(6): rank
        for rank, item in enumerate(ranked_acceptable, start=1)
    }
    final_rank = {
        str(item.get("fund_code") or "").zfill(6): rank
        for rank, item in enumerate(selected, start=1)
    }
    rows: list[dict] = []
    for raw in original_pool:
        code = str(raw.get("fund_code") or "").zfill(6)
        quality_gate = raw.get("quality_gate") if isinstance(raw.get("quality_gate"), dict) else {}
        peer_rank = raw.get("peer_rank") if isinstance(raw.get("peer_rank"), dict) else {}
        reasons: list[str] = []
        if code not in family_selected_codes:
            reasons.append("share_class_not_selected_after_quality_dedup")
        quality_status = str(quality_gate.get("status") or "watch_only")
        if quality_status == "excluded":
            reasons.extend(str(value) for value in quality_gate.get("reasons") or [])
        elif code not in final_rank:
            reasons.append("outside_final_sector_quota_or_pool_cap")
        rows.append(
            {
                "fund_code": code,
                "fund_name": raw.get("fund_name"),
                "sector_label": raw.get("sector_label"),
                "share_family_key": (raw.get("share_family") or {}).get("family_key")
                if isinstance(raw.get("share_family"), dict)
                else None,
                "quality_gate_status": quality_status,
                "fund_quality_score": raw.get("fund_quality_score"),
                "sector_fit_score": raw.get("sector_fit_score"),
                "sector_identity_status": raw.get("sector_identity_status"),
                "sector_identity_eligible": raw.get("sector_identity_eligible"),
                "peer_group_key": (
                    (raw.get("peer_group") or {}).get("group_key")
                    if isinstance(raw.get("peer_group"), dict)
                    else None
                ),
                "peer_rank_status": peer_rank.get("status"),
                "descriptive_performance_percentile": peer_rank.get(
                    "descriptive_performance_percentile"
                ),
                "post_family_rank": acceptable_rank.get(code),
                "selected": code in final_rank,
                "final_rank": final_rank.get(code),
                "reason_codes": list(dict.fromkeys(value for value in reasons if value)),
            }
        )
    rows.sort(key=lambda row: (row["final_rank"] is None, row["final_rank"] or 10**9, row["fund_code"]))
    material = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sink.clear()
    sink.update(
        {
            "schema_version": "discovery_candidate_selection_audit.v1",
            "prescreen_count": len(original_pool),
            "post_share_family_count": len(family_selected_pool),
            "acceptable_count": len(ranked_acceptable),
            "selected_count": len(selected),
            "rows": rows,
            "snapshot_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        }
    )


def attach_candidate_benchmark_research(
    pool: list[dict],
    benchmark_specs: dict[str, dict],
    *,
    decision_at: datetime,
) -> list[dict]:
    """Attach frozen benchmark identity without shrinking valid peer samples.

    Index groups depend on their exact point-in-time tracking reference.  A
    rank calculated before benchmark attachment may belong to the conservative
    ``reference-unspecified`` group, so retaining it after the group changes
    would compare the target with the wrong cohort.  When the group is unchanged,
    however, the existing rank was calculated from the full discovery universe
    and must be retained: rebuilding it from finalists alone collapses otherwise
    valid peer samples to ``n=0``.  Only a changed/missing group is rebuilt from
    the frozen final-candidate cohort.  No provider or network lookup occurs
    here, and no private research context leaks into candidate payloads.
    """

    staged: list[dict] = []
    for raw in pool:
        row = dict(raw)
        code = str(row.get("fund_code") or "").zfill(6)
        spec = dict(benchmark_specs.get(code) or {})
        row["benchmark_spec"] = spec
        row["benchmark_comparison"] = resolve_benchmark_comparison(
            spec,
            decision_at=decision_at,
        )
        resolved_group = build_fund_peer_group(
            row,
            decision_at=decision_at,
            benchmark_spec=spec,
        )
        previous_rank = (
            row.get("peer_rank")
            if isinstance(row.get("peer_rank"), Mapping)
            else None
        )
        row["peer_group"] = _preserve_catalogue_peer_group_for_benchmark_attachment(
            previous_rank=previous_rank,
            resolved_group=resolved_group,
        )
        staged.append(row)

    enriched: list[dict] = []
    for staged_row in staged:
        row = dict(staged_row)
        top_group_key = str((row.get("peer_group") or {}).get("group_key") or "")
        previous_rank = (
            row.get("peer_rank")
            if isinstance(row.get("peer_rank"), Mapping)
            else None
        )
        previous_group_key = str(
            ((previous_rank or {}).get("peer_group") or {}).get("group_key") or ""
        )
        if previous_rank is not None and previous_group_key == top_group_key:
            rank = dict(previous_rank)
            rank["peer_group"] = dict(row["peer_group"])
            rank["benchmark"] = dict(row["peer_group"].get("benchmark") or {})
        else:
            rank = build_peer_rank(
                row,
                staged,
                decision_at=decision_at,
                benchmark_spec=row["benchmark_spec"],
            )
        rank_group_key = str((rank.get("peer_group") or {}).get("group_key") or "")
        if not top_group_key or rank_group_key != top_group_key:
            raise RuntimeError("peer rank group changed after benchmark attachment")
        row["peer_rank"] = rank
        enriched.append(row)
    return enriched


def _preserve_catalogue_peer_group_for_benchmark_attachment(
    *,
    previous_rank: Mapping | None,
    resolved_group: dict,
) -> dict:
    """Prevent profile-only detail from shrinking a full-universe cohort.

    Benchmark attachment happens after research-profile enrichment. For active
    funds, a detailed profile subtype can change the target group even though
    the frozen catalogue peers still use a coarse taxonomy. A benchmark cannot
    change an active fund's membership, so preserve the catalogue-derived group
    whenever the broad asset class and strategy agree. Passive/enhanced index
    funds are excluded because their exact tracking reference legitimately is
    part of group identity.
    """

    previous_group = (
        dict(previous_rank.get("peer_group") or {})
        if isinstance(previous_rank, Mapping)
        else {}
    )
    previous_strategy = str(previous_group.get("management_style") or "")
    same_broad_group = (
        previous_group
        and previous_group.get("asset_class") == resolved_group.get("asset_class")
        and previous_strategy == str(resolved_group.get("management_style") or "")
    )
    if not same_broad_group or previous_strategy in {
        "passive_index",
        "enhanced_index",
    }:
        return resolved_group
    previous_group["decision_at"] = resolved_group.get("decision_at")
    previous_group["benchmark"] = dict(resolved_group.get("benchmark") or {})
    return previous_group


def _select_representative_share_classes(
    pool: list[dict],
    *,
    discovery_strategy: str,
) -> list[dict]:
    """Choose one research representative per share family without platform data."""

    groups: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for item in pool:
        key = _candidate_share_family_key(item)
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(dict(item))

    selected: list[dict] = []
    for key in group_order:
        members = groups[key]
        members.sort(
            key=lambda item: (
                -_quality_gate_rank(item),
                -_vehicle_quality_gate_rank(item),
                -(
                    _opportunity_rank_value(item)
                    if discovery_strategy == "opportunity_first"
                    else -999.0
                ),
                -(_num(item.get("vehicle_quality_score")) or -999.0),
                -(_num(item.get("fund_quality_score")) or -999.0),
                -(_num(item.get("sector_fit_score")) or -999.0),
                -_share_class_rank(str(item.get("fund_name") or "")),
                str(item.get("fund_code") or "").zfill(6),
            )
        )
        chosen = dict(members[0])
        member_codes = [
            str(item.get("fund_code") or "").zfill(6) for item in members
        ]
        chosen["share_family"] = {
            "family_key": key,
            "key_source": "normalized_name+fund_type",
            "confidence": "high" if len(members) > 1 else "medium",
            "member_codes": member_codes,
            "selected_code": str(chosen.get("fund_code") or "").zfill(6),
            "selected_basis": "quality_and_opportunity_then_share_class_priority",
        }
        selected.append(chosen)
    return selected


def _candidate_share_family_key(item: dict) -> str:
    name = str(item.get("fund_name") or "").strip()
    code = str(item.get("fund_code") or "").zfill(6)
    if not name:
        return f"code:{code}"
    family = _family_key(name).casefold()
    fund_type = str(item.get("fund_type") or item.get("fund_category") or "unknown")
    return f"{family}|{fund_type.strip().casefold()}"


def _is_execution_verified_primary_mapping(
    row: Mapping[str, object],
    *,
    expected_sector: str,
) -> bool:
    """Require benchmark-backed mappings to reproduce their exact sector.

    Persisted benchmark mappings predate the current candidate scan and can
    outlive a corrected index taxonomy.  Checking only ``source`` would keep a
    stale mapping such as "全指金融/金融地产 -> 金融科技" executable.  Direct
    holdings/manual evidence stays trusted; benchmark evidence must resolve
    again from its frozen original text and agree with today's target label.
    """

    identity_status = str(row.get("identity_status") or "").strip()
    if identity_status and identity_status != "verified":
        return False
    if row.get("expires_at") and not is_current_identity_row_fresh(row):
        return False
    source = str(row.get("source") or "").strip()
    if source in _DIRECTLY_VERIFIED_PRIMARY_SOURCES:
        return True
    if source not in _BENCHMARK_PRIMARY_SOURCES:
        return False

    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            decoded = json.loads(detail)
        except (TypeError, ValueError):
            decoded = None
        detail = decoded
    if not isinstance(detail, Mapping):
        return False
    benchmark_text = str(detail.get("benchmark_text") or "").strip()
    if not benchmark_text:
        return False
    resolved = resolve_sector_from_benchmark(benchmark_text)
    return bool(resolved is not None and resolved[0] == expected_sector)


def _candidates_for_sector(
    sector_label: str,
    *,
    rank_rows: list[dict],
    rank_by_code: dict[str, dict],
    primary_rows: list[dict],
    new_issue_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
    discovery_strategy: str = "risk_first",
    opportunity: dict | None = None,
    family_seen: set[str] | None = None,
    limit: int = _PER_SECTOR,
    as_of_date: date | None = None,
    recall_audit_state: dict | None = None,
    recall_audit_limit: int = _MAX_RECALL_AUDIT_CANDIDATES,
) -> list[dict]:
    canon = get_canonical_sector(sector_label)
    keywords = _sector_keywords(sector_label, canon)
    acceptable_identity_sectors = set(_acceptable_identity_sectors(sector_label))
    entries_by_code: dict[str, dict] = {}
    family_seen = family_seen if family_seen is not None else set()
    verified_primary_sectors_by_code: dict[str, set[str]] = {}
    for primary_row in primary_rows:
        primary_sector = str(primary_row.get("sector_name") or "").strip()
        primary_code = str(primary_row.get("fund_code") or "").zfill(6)
        if not primary_sector or not primary_code:
            continue
        if _is_execution_verified_primary_mapping(
            primary_row,
            expected_sector=primary_sector,
        ):
            verified_primary_sectors_by_code.setdefault(primary_code, set()).add(
                primary_sector
            )

    for row in primary_rows:
        identity_sector = str(row.get("sector_name") or "").strip()
        if identity_sector not in acceptable_identity_sectors:
            continue
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or (code in seen_codes and recall_audit_state is None):
            continue
        name = str(row.get("fund_name") or _resolve_fund_name(code))
        if not _matches_fund_type_preference(name, fund_type_preference):
            continue
        source = str(row.get("source") or "").strip()
        # 身份核验对照的是映射行自己的板块：同义召回（如贵金属方向接受
        # 「黄金」身份）时，基准证据重放仍必须复现出「黄金」，而不是方向名。
        verified_primary = _is_execution_verified_primary_mapping(
            row,
            expected_sector=identity_sector,
        )
        inferred_match_kind = (
            "primary"
            if verified_primary
            else "name"
            if _name_matches_sector(name, keywords)
            else "fallback"
        )
        entry = _merge_rank_metrics(
            {
                "fund_code": code,
                "fund_name": name,
                "sector_label": sector_label,
                "selection_reason": (
                    "板块机会映射" if opportunity else "主关联板块映射"
                )
                if verified_primary
                else "推断板块映射待核验",
                "sector_source": source or None,
                "sector_confidence": row.get("confidence"),
                "sector_match_kind": inferred_match_kind,
                "sector_mapping_verified": verified_primary,
                "identity_sector_label": identity_sector,
            },
            rank_by_code.get(code),
        )
        entries_by_code[code] = _with_opportunity(entry, opportunity)

    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or (code in seen_codes and recall_audit_state is None):
            continue
        verified_sectors = verified_primary_sectors_by_code.get(code, set())
        if verified_sectors and acceptable_identity_sectors.isdisjoint(
            verified_sectors
        ):
            # A substring recall must not consume the code before its verified
            # target is processed. This is especially important when both
            # 黄金 and 黄金股 are selected in the same scan.
            continue
        name = str(row.get("fund_name", ""))
        family = _family_key(name)
        if family and family in family_seen and recall_audit_state is None:
            continue
        if not _name_matches_sector(name, keywords):
            continue
        if not _passes_quality(row, as_of_date=as_of_date):
            continue
        if not _matches_fund_type_preference(name, fund_type_preference):
            continue
        ranked_entry = _with_opportunity(
            _entry_from_rank(row, sector_label=sector_label, selection_reason="排行筛选"),
            opportunity,
        )
        ranked_entry["sector_match_kind"] = "name"
        if code in entries_by_code:
            entries_by_code[code] = _merge_entries(entries_by_code[code], ranked_entry)
        else:
            entries_by_code[code] = ranked_entry

    if selection_strategy == "with_new_issue":
        for entry in _new_issue_entries_for_sector(
            new_issue_rows,
            sector_label=sector_label,
            keywords=keywords,
            excluded=excluded,
            seen_codes=seen_codes if recall_audit_state is None else set(),
            fund_type_preference=fund_type_preference,
            as_of_date=as_of_date,
        ):
            code = str(entry.get("fund_code", "")).zfill(6)
            entries_by_code.setdefault(code, _with_opportunity(entry, opportunity))

    scored = [
        _with_quality_score(
            entry,
            fund_type_preference=fund_type_preference,
            discovery_strategy=discovery_strategy,
        )
        for entry in entries_by_code.values()
        if selection_strategy == "with_new_issue"
        or entry.get("is_new_issue")
        or _passes_quality(entry, as_of_date=as_of_date)
    ]
    scored.sort(
        key=lambda item: (
            *(
                (_sortable_score(item.get("recall_upside_score")),)
                if discovery_strategy == "opportunity_first"
                else ()
            ),
            _sortable_score(item.get("fund_quality_score")),
            _sortable_score(item.get("sector_fit_score")),
            _share_class_rank(str(item.get("fund_name") or "")),
        ),
        reverse=True,
    )
    if recall_audit_state is not None:
        _record_scored_recall_candidates(
            recall_audit_state,
            scored,
            limit=recall_audit_limit,
            matched_sector=sector_label,
        )

    selected: list[dict] = []
    local_family_seen: set[str] = set()
    for entry in scored:
        code = str(entry.get("fund_code", "")).zfill(6)
        family = _family_key(str(entry.get("fund_name") or ""))
        if code in seen_codes:
            continue
        if family and (family in family_seen or family in local_family_seen):
            continue
        selected_entry = _strip_internal_fields(entry)
        if family:
            alternatives = [
                _strip_internal_fields(other)
                for other in scored
                if str(other.get("fund_code") or "").zfill(6) != code
                and _family_key(str(other.get("fund_name") or "")) == family
                and str(other.get("fund_code") or "").zfill(6) not in seen_codes
            ][:2]
            if alternatives:
                selected_entry["_share_family_alternatives"] = alternatives
                for alternative in alternatives:
                    seen_codes.add(
                        str(alternative.get("fund_code") or "").zfill(6)
                    )
        selected.append(selected_entry)
        seen_codes.add(code)
        if family:
            family_seen.add(family)
            local_family_seen.add(family)
        if len(selected) >= limit:
            break
    return selected


def _sector_candidate_limit(
    sector_label: str,
    *,
    index: int,
    base_limit: int,
    pool_cap: int,
    total_sectors: int,
    opportunity_by_sector: dict[str, dict],
) -> int:
    if base_limit <= 0:
        return 0
    opportunity = opportunity_by_sector.get(sector_label)
    if not opportunity:
        return base_limit
    score = (
        _num(opportunity.get("selection_priority_score"))
        or _num(opportunity.get("research_score"))
        or _num(opportunity.get("score"))
        or 0.0
    )
    top_scores = sorted(
        [
            _num(item.get("selection_priority_score"))
            or _num(item.get("research_score"))
            or _num(item.get("score"))
            or 0.0
            for item in opportunity_by_sector.values()
        ],
        reverse=True,
    )
    top_cutoff = top_scores[min(3, len(top_scores) - 1)] if top_scores else 0.0
    can_expand = pool_cap >= total_sectors * base_limit + 1
    priority_path = bool(
        opportunity.get("flow_improving_probe_eligible") is True
        or (_num(opportunity.get("sector_elasticity_percentile")) or 0.0) >= 70.0
    )
    if (
        can_expand
        and index < 4
        and (score >= max(70.0, top_cutoff) or priority_path)
    ):
        return base_limit + 1
    return base_limit


def _merge_rank_metrics(entry: dict, rank_row: dict | None) -> dict:
    if not rank_row:
        return dict(entry)
    merged = dict(entry)
    for key in (
        "return_1y_percent",
        "return_6m_percent",
        "return_3m_percent",
        "max_drawdown_1y_percent",
        "fund_scale_yi",
        "fund_type",
        "nav_date",
        "established_date",
    ):
        if merged.get(key) is None and rank_row.get(key) is not None:
            merged[key] = rank_row.get(key)
    if not merged.get("fund_name") and rank_row.get("fund_name"):
        merged["fund_name"] = rank_row.get("fund_name")
    merged.setdefault(
        "candidate_universe_source",
        rank_row.get("candidate_universe_source") or rank_row.get("source"),
    )
    merged.setdefault(
        "candidate_universe_available_at",
        rank_row.get("candidate_universe_available_at")
        or rank_row.get("snapshot_available_at")
        or rank_row.get("membership_available_at"),
    )
    return merged


def _merge_entries(primary: dict, ranked: dict) -> dict:
    merged = dict(primary)
    for key, value in ranked.items():
        if key in {"selection_reason", "sector_match_kind", "_sector_match_kind"}:
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
    merged["sector_match_kind"] = max(
        (_resolve_sector_match_kind(primary), _resolve_sector_match_kind(ranked)),
        key=_SECTOR_MATCH_STRENGTH.__getitem__,
    )
    merged.pop("_sector_match_kind", None)
    return merged


def _new_issue_entries_for_sector(
    rows: list[dict],
    *,
    sector_label: str,
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    as_of_date: date | None = None,
) -> list[dict]:
    from app.services.discovery_selection_strategy import _pick_new_issue_for_sector

    entries = _pick_new_issue_for_sector(
        rows,
        sector_label=sector_label,
        keywords=keywords,
        excluded=excluded,
        seen_codes=set(seen_codes),
        fund_type_preference=fund_type_preference,
        limit=2,
        name_matches_sector=_name_matches_sector,
        matches_fund_type=_matches_fund_type_preference,
        as_of_date=as_of_date,
    )
    for entry in entries:
        entry["sector_match_kind"] = "new_issue"
    return entries


def rank_candidates_balanced_fallback(
    rank_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    selection_strategy: SelectionStrategy = "balanced",
    discovery_strategy: str = "risk_first",
    family_seen: set[str] | None = None,
    as_of_date: date | None = None,
) -> list[dict]:
    from app.services.discovery_selection_strategy import (
        rank_candidates_balanced,
        recall_upside_score,
    )

    candidates: list[dict] = []
    already_selected_families = set(family_seen or set())
    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        family = _family_key(str(row.get("fund_name", "")))
        if family and family in already_selected_families:
            continue
        if not _passes_quality(row, as_of_date=as_of_date):
            continue
        if not _matches_fund_type_preference(str(row.get("fund_name", "")), fund_type_preference):
            continue
        entry = _entry_from_rank(
            row,
            sector_label="综合",
            selection_reason="排行补位",
            sector_match_kind="fallback",
        )
        if discovery_strategy == "opportunity_first":
            entry["recall_upside_score"] = recall_upside_score(entry)
        candidates.append(entry)
    if discovery_strategy == "opportunity_first":
        ranked = sorted(candidates, key=recall_upside_score, reverse=True)
    else:
        ranked = rank_candidates_balanced(candidates)

    # Rank first, then collapse A/C and other share classes.  Deduplicating in
    # upstream row order could keep a stable but weaker share class and discard
    # the family's strongest high-elasticity candidate before the NAV stage.
    result: list[dict] = []
    ranked_families = set(already_selected_families)
    for entry in ranked:
        family = _family_key(str(entry.get("fund_name") or ""))
        if family and family in ranked_families:
            continue
        result.append(entry)
        if family:
            ranked_families.add(family)
    return result


def _entry_from_rank(
    row: dict,
    *,
    sector_label: str,
    selection_reason: str,
    sector_match_kind: str = "name",
) -> dict:
    return {
        "fund_code": str(row.get("fund_code", "")).zfill(6),
        "fund_name": str(row.get("fund_name", "")),
        "sector_label": sector_label,
        "selection_reason": selection_reason,
        "sector_match_kind": (
            sector_match_kind
            if sector_match_kind in _SECTOR_MATCH_STRENGTH
            else "fallback"
        ),
        "return_1y_percent": row.get("return_1y_percent"),
        "return_6m_percent": row.get("return_6m_percent"),
        "return_3m_percent": row.get("return_3m_percent"),
        "max_drawdown_1y_percent": row.get("max_drawdown_1y_percent"),
        "fund_scale_yi": row.get("fund_scale_yi"),
        "fund_type": row.get("fund_type"),
        "nav_date": row.get("nav_date"),
        "established_date": row.get("established_date"),
        "candidate_universe_source": row.get("candidate_universe_source")
        or row.get("source"),
        "candidate_universe_available_at": row.get(
            "candidate_universe_available_at"
        )
        or row.get("snapshot_available_at")
        or row.get("membership_available_at"),
    }


def _with_quality_score(
    entry: dict,
    *,
    fund_type_preference: str,
    discovery_strategy: str = "risk_first",
) -> dict:
    row = dict(entry)
    row["sector_match_kind"] = _resolve_sector_match_kind(row)
    row.pop("_sector_match_kind", None)
    row = annotate_candidate_sector_identity(row)
    reasons: list[str] = []
    gate = row.get("quality_gate") if isinstance(row.get("quality_gate"), dict) else {}
    penalties: list[str] = [
        str(item) for item in gate.get("reasons") or [] if str(item).strip()
    ]

    sector_fit = _sector_fit_score(row)
    if sector_fit >= 34:
        reasons.append("板块高置信匹配")
    elif sector_fit >= 22:
        reasons.append("板块匹配明确")
    else:
        penalties.append("板块匹配置信偏低")

    performance = _bounded_performance_score(
        row,
        penalties,
        reasons,
        discovery_strategy=discovery_strategy,
    )
    r3m = _num(row.get("return_3m_percent"))
    r6m = _num(row.get("return_6m_percent"))
    if r3m is None and r6m is None:
        penalties.append("缺少近3/6月收益")
    elif (r3m or 0.0) > 5 or (r6m or 0.0) > 10:
        reasons.append("近3/6月表现占优")

    risk_score = _risk_score(
        row,
        penalties,
        reasons,
        discovery_strategy=discovery_strategy,
    )
    scale_score = _scale_score(row, penalties, reasons)
    type_score = _type_preference_score(row, fund_type_preference, reasons)
    if not _has_value(row.get("management_fee")):
        penalties.append("管理费率未核验；净值已反映历史经常性费用")
    name = str(row.get("fund_name") or "")
    if name:
        row["share_class"] = "C" if _is_c_class_fund(name) else "A/其他"
        row["share_class_fee_status"] = (
            str(row.get("share_class_fee_status") or "unverified")
        )

    coverage = _num(gate.get("coverage_percent")) or 0.0
    data_score = coverage / 10.0
    score = sector_fit + performance + risk_score + scale_score + type_score + data_score
    row["sector_fit_score"] = round(sector_fit, 2)
    row["fund_quality_score"] = round(max(0.0, min(100.0, score)), 2)
    row["quality_score_version"] = _QUALITY_SCORE_VERSION
    row["quality_score_components"] = {
        "sector_fit": round(sector_fit, 2),
        "performance": round(performance, 2),
        "drawdown_control": round(risk_score, 2),
        "scale": round(scale_score, 2),
        "data_completeness": round(data_score, 2),
        "legacy_type_preference": round(type_score, 2),
    }
    row["quality_reasons"] = _unique_text(reasons)[:4]
    row["quality_penalties"] = _unique_text(penalties)[:4]
    from app.services.discovery_selection_strategy import (
        OPPORTUNITY_SCORE_VERSION,
        assess_fund_entry_position,
        current_opportunity_score,
        recall_upside_score,
    )

    row["opportunity_score_20_60d"] = current_opportunity_score(row)
    row["opportunity_score_version"] = OPPORTUNITY_SCORE_VERSION
    row["recall_upside_score"] = recall_upside_score(row)
    row["fund_entry_signal"] = assess_fund_entry_position(row)
    return row


def _bounded_performance_score(
    row: dict,
    penalties: list[str],
    reasons: list[str],
    *,
    discovery_strategy: str = "risk_first",
) -> float:
    """把阶段收益压到 0~25，防止单只暴涨基金把总分推过100。"""

    r3m = _num(row.get("return_3m_percent"))
    r6m = _num(row.get("return_6m_percent"))
    r1y = _num(row.get("return_1y_percent"))
    if r3m is None and r6m is None:
        penalties.append("缺少近3/6月收益")
        return 0.0

    score = 0.0
    if r3m is not None:
        score += _clamp((r3m + 10.0) / 40.0, 0.0, 1.0) * 11.0
    if r6m is not None:
        score += _clamp((r6m + 15.0) / 65.0, 0.0, 1.0) * 11.0
    if discovery_strategy == "opportunity_first" and r1y is not None:
        # 质量层只验证载体，不用一年涨幅把高弹性基金重新压回去。当前机会强弱
        # 已由 uncapped 20/60 日机会分、真实波动率和入场信号单独排序。
        reasons.append("近1年涨幅仅作波动背景，不参与载体质量奖惩")
    elif r1y is not None and -10.0 <= r1y <= 70.0:
        score += 3.0
    elif r1y is not None and r1y > 100.0:
        penalties.append("近1年涨幅过高，存在追高偏差")
        score -= min(5.0, (r1y - 100.0) / 20.0)
    if score >= 17.0:
        reasons.append("近3/6月表现占优")
    return _clamp(score, 0.0, 25.0)


def _with_data_quality_gate(
    entry: dict,
    *,
    as_of_date: date | None = None,
    discovery_strategy: str = "risk_first",
) -> dict:
    row = dict(entry)
    missing = [field for field in _CORE_QUALITY_FIELDS if not _has_value(row.get(field))]
    profile_status = str(row.get("profile_status") or "")
    stale_fields = {
        str(field)
        for field in row.get("profile_stale_fields") or []
        if str(field) in _CORE_QUALITY_FIELDS
    }
    if profile_status == "stale_fallback":
        stale_fields.update(
            field
            for field in ("fund_scale_yi", "established_date", "fund_manager")
            if _has_value(row.get(field))
        )
    row["profile_stale_fields"] = sorted(stale_fields)
    coverage_gaps = set(missing) | stale_fields
    coverage = round(
        (len(_CORE_QUALITY_FIELDS) - len(coverage_gaps))
        / len(_CORE_QUALITY_FIELDS)
        * 100,
        1,
    )
    reasons: list[str] = []
    status = "eligible"

    scale = _num(row.get("fund_scale_yi"))
    scale_label = "最新估算规模"
    scale_is_stale = "fund_scale_yi" in stale_fields
    if not scale_is_stale and scale is not None and scale < _HARD_MIN_SCALE_YI:
        status = "excluded"
        reasons.append(f"{scale_label}低于0.5亿元，清盘与流动性风险偏高")
    elif not scale_is_stale and scale is not None and scale < _MIN_SCALE_YI:
        status = "watch_only"
        reasons.append(f"{scale_label}低于1亿元，暂不生成可执行买入动作")

    established = _parse_iso_date(row.get("established_date"))
    if (
        "established_date" not in stale_fields
        and established is not None
        and ((as_of_date or date.today()) - established).days < _MIN_HISTORY_DAYS
    ):
        status = "excluded"
        reasons.append("成立不足1年，缺少可验证的完整业绩周期")

    drawdown = _num(row.get("max_drawdown_1y_percent"))
    if status != "excluded" and drawdown is not None and abs(drawdown) > 50.0:
        if discovery_strategy == "opportunity_first":
            reasons.append("近1年历史波动很高；保留用于高弹性机会排序，不作为质量否决")
        else:
            status = "watch_only"
            reasons.append("近1年最大回撤超过50%，仅保留研究观察")

    nav_date = _parse_iso_date(row.get("nav_date"))
    decision_date = as_of_date or date.today()
    if status != "excluded" and _has_value(row.get("nav_date")) and nav_date is None:
        status = "excluded"
        reasons.append("净值日期格式无效，无法通过时点校验")
    elif status != "excluded" and nav_date is not None and nav_date > decision_date:
        status = "excluded"
        reasons.append("净值日期晚于决策时点，禁止用于候选决策")
    elif (
        status != "excluded"
        and nav_date is not None
        and (decision_date - nav_date).days > 7
    ):
        status = "watch_only"
        reasons.append("最新净值超过7个自然日，时点不足")

    if status != "excluded" and profile_status == "stale_fallback":
        status = "watch_only"
        reasons.append("基金档案缓存已过期且本次刷新失败，仅保留研究观察")

    if status != "excluded" and profile_status == "partial":
        status = "watch_only"
        if row.get("profile_stale_fields"):
            reasons.append("基金档案本次仅部分刷新，仍含过期字段，仅保留研究观察")
        else:
            reasons.append("基金档案仅部分补全，已按低置信候选处理")

    if status != "excluded" and missing:
        status = "watch_only"
        if profile_status == "unavailable":
            reasons.append("基金档案双源补全暂不可用，已禁止生成可执行买入动作")
        labels = {
            "return_3m_percent": "近3月收益",
            "return_6m_percent": "近6月收益",
            "max_drawdown_1y_percent": "近1年回撤",
            "fund_scale_yi": "最新规模",
            "established_date": "成立日期",
            "fund_manager": "基金经理",
            "nav_date": "净值日期",
        }
        reasons.append("核心字段缺失：" + "、".join(labels.get(field, field) for field in missing))

    row["quality_gate"] = {
        "eligible": status == "eligible",
        "status": status,
        "reasons": _unique_text(reasons),
        "missing_fields": missing,
        "coverage_percent": coverage,
        "data_as_of": row.get("nav_date") or row.get("profile_updated_at"),
        "profile_status": row.get("profile_status"),
        "profile_sources": row.get("profile_sources") or [],
        "profile_checked_at": row.get("profile_checked_at"),
        "profile_stale_fields": sorted(stale_fields),
    }
    return row


def _quality_gate_rank(item: dict) -> int:
    gate = item.get("quality_gate") if isinstance(item.get("quality_gate"), dict) else {}
    return {"eligible": 2, "watch_only": 1, "excluded": 0}.get(
        str(gate.get("status") or "watch_only"),
        1,
    )


def _vehicle_quality_gate_rank(item: dict) -> int:
    return {"eligible": 2, "watch_only": 1, "excluded": 0}.get(
        str(item.get("vehicle_quality_status") or "watch_only"),
        1,
    )


def _first_present(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def _valid_drawdown(value: object) -> float | None:
    parsed = _num(value)
    if parsed is None or parsed > 0.0 or parsed < -100.0:
        return None
    return parsed


def _first_valid_drawdown(*values: object) -> float | None:
    for value in values:
        parsed = _valid_drawdown(value)
        if parsed is not None:
            return parsed
    return None


def _quality_metrics_from_nav_history(
    history: object | None,
    *,
    fund_code: str,
    fund_name: str,
    fund_type: object,
    effective_trade_date: str,
    observed_at: str,
) -> dict[str, object]:
    """Derive one internally consistent quality snapshot from fetched NAV.

    The Eastmoney rank snapshot does not cover every catalogue member (for
    example, some I share classes). Candidate enrichment already fetches 252
    NAV observations for every shortlisted fund, so use that same evidence as
    a fail-closed fallback for all return windows and drawdown. Daily return is
    preferred over raw NAV ratios by ``factor_input_from_points`` to survive
    splits/distributions without mixing incompatible provider metrics.
    """

    points = list(getattr(history, "points", None) or [])
    if len(points) < 252:
        return {}

    from app.services.fund_factor_nav import factor_input_from_points

    source = str(getattr(history, "source", None) or "fund_nav_history")
    factor = factor_input_from_points(
        fund_code,
        fund_name,
        points,
        require_complete=True,
        minimum_points=252,
        effective_trade_date=effective_trade_date,
        fund_type=str(fund_type or ""),
        observed_at=observed_at,
        source=f"{source}_total_return",
    )
    if factor.feature_freshness != "fresh":
        return {}

    return {
        "return_3m_percent": factor.return_3m_percent,
        "return_6m_percent": factor.return_6m_percent,
        "return_1y_percent": factor.return_1y_percent,
        "max_drawdown_1y_percent": factor.max_drawdown_1y_percent,
        "feature_as_of": factor.feature_as_of,
        "feature_observed_at": factor.feature_observed_at,
        "feature_source": factor.feature_source,
        "return_coverage": factor.return_coverage,
    }


def _apply_nav_quality_metric_fallbacks(
    row: dict,
    metrics: Mapping[str, object],
) -> None:
    applied = False
    for field in (
        "return_3m_percent",
        "return_6m_percent",
        "return_1y_percent",
        "max_drawdown_1y_percent",
    ):
        current = (
            _valid_drawdown(row.get(field))
            if field == "max_drawdown_1y_percent"
            else _num(row.get(field))
        )
        fallback = (
            _valid_drawdown(metrics.get(field))
            if field == "max_drawdown_1y_percent"
            else _num(metrics.get(field))
        )
        if current is not None or fallback is None:
            continue
        row[field] = round(fallback, 4)
        row[f"{field}_source"] = metrics.get("feature_source")
        row[f"{field}_available_at"] = metrics.get("feature_observed_at")
        row[f"{field}_as_of"] = metrics.get("feature_as_of")
        applied = True
    if applied:
        row["nav_quality_return_coverage"] = metrics.get("return_coverage")


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10].replace("/", "-")
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return isfinite(float(value))
    text = str(value).strip()
    return bool(
        text
        and "\ufffd" not in text
        and text not in {"--", "未知", "None"}
        and text.lower() not in {"nan", "inf", "+inf", "-inf"}
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _sector_fit_score(row: dict) -> float:
    kind = _resolve_sector_match_kind(row)
    if kind == "primary":
        confidence = _num(row.get("sector_confidence"))
        if confidence is None:
            return 28.0
        return 24.0 + min(16.0, max(0.0, confidence) * 16.0)
    if kind == "new_issue":
        return 18.0
    if kind == "tracking_exact":
        return 34.0
    return 16.0


def _with_exact_passive_tracking_match(row: dict) -> dict:
    """Upgrade name recall only when a passive fund tracks this exact index.

    Aggregator benchmark text remains a research-only tracking reference.  It
    can prove candidate-to-sector identity for a passive fund, but it never
    becomes a verified contract or formal excess-return benchmark.
    """

    result = dict(row)
    name = str(result.get("fund_name") or "").upper()
    fund_type = str(result.get("fund_type") or "").upper()
    if "ETF" not in name and "指数" not in name and "指数" not in fund_type:
        return result

    sector_label = str(result.get("sector_label") or "")
    target = get_canonical_sector(sector_label)
    target_label = str(getattr(target, "label", None) or "").strip()
    if not target_label:
        return result
    acceptable_labels = {target_label} | set(
        _acceptable_identity_sectors(sector_label)
    )

    references = [
        str(result.get("tracking_reference_text") or "").strip(),
        str(result.get("benchmark_text") or "").strip(),
    ]
    for reference in dict.fromkeys(value for value in references if value):
        resolved = resolve_sector_from_benchmark(reference)
        if resolved is None:
            continue
        resolved_sector, _intraday_name, match = resolved
        # The market quote proxy and the fund's tracked index need not share a
        # code. Coal, for example, is quoted with BK0437 while valid products
        # track 399998 or 399990. The resolver has already allow-listed the
        # exact index and mapped it to a canonical sector, so compare that
        # canonical identity while still keeping 黄金 and 黄金股 distinct.
        # 方向级同义（如贵金属方向接受黄金/黄金股跟踪标的）也在这里生效。
        if resolved_sector not in acceptable_labels:
            result["sector_identity_mismatch"] = {
                "relation_kind": "tracking_reference",
                "target_sector_label": target_label,
                "verified_sector_label": resolved_sector,
                "index_code": match.index_code,
                "index_name": match.index_name,
                "benchmark_text_source_kind": result.get(
                    "benchmark_text_source_kind"
                ),
                "exact": True,
            }
            continue
        result["sector_match_kind"] = "tracking_exact"
        result = annotate_candidate_sector_identity(result)
        result.pop("sector_identity_mismatch", None)
        result["identity_sector_label"] = resolved_sector
        result["sector_confidence"] = max(
            _num(result.get("sector_confidence")) or 0.0,
            0.95,
        )
        result["tracking_reference_match"] = {
            "relation_kind": "tracking_reference",
            "sector_label": resolved_sector,
            "index_code": match.index_code,
            "index_name": match.index_name,
            "benchmark_text_source_kind": result.get("benchmark_text_source_kind"),
            "exact": True,
            "formal_excess_eligible": False,
        }
        if str(result.get("selection_reason") or "") == "排行筛选":
            result["selection_reason"] = "精确跟踪标的匹配"
        break
    return result


def _resolve_sector_match_kind(row: dict) -> str:
    public_kind = str(row.get("sector_match_kind") or "").strip()
    if public_kind:
        return (
            public_kind
            if public_kind in _SECTOR_MATCH_STRENGTH
            else "fallback"
        )
    legacy_kind = str(row.get("_sector_match_kind") or "").strip()
    return (
        legacy_kind
        if legacy_kind in _SECTOR_MATCH_STRENGTH
        else "fallback"
    )


def _risk_score(
    row: dict,
    penalties: list[str],
    reasons: list[str],
    *,
    discovery_strategy: str = "risk_first",
) -> float:
    drawdown = _num(row.get("max_drawdown_1y_percent"))
    if drawdown is None:
        penalties.append("缺少近1年回撤")
        return 0.0
    depth = abs(drawdown)
    if discovery_strategy == "opportunity_first":
        if depth >= 40:
            reasons.append("历史波动较高，纳入高弹性机会评估")
        else:
            reasons.append("历史回撤仅作风险背景，不奖励低波动")
        # Keep a neutral evidence-completeness value.  Opportunity-first quality
        # should validate the vehicle, not make stability an ordering advantage.
        return 7.5
    if depth <= 20:
        reasons.append("近1年回撤可控")
        return 15.0
    if depth <= 30:
        return 10.0
    if depth >= 40:
        penalties.append("近1年回撤偏大")
        return 0.0
    penalties.append("近1年回撤略高")
    return 5.0


def _scale_score(row: dict, penalties: list[str], reasons: list[str]) -> float:
    stale_fields = {str(field) for field in row.get("profile_stale_fields") or []}
    if row.get("profile_status") == "stale_fallback" or "fund_scale_yi" in stale_fields:
        penalties.append("基金规模证据已过期")
        return 0.0
    scale = _num(row.get("fund_scale_yi"))
    if scale is None:
        penalties.append("缺少基金规模")
        return 0.0
    if scale < _HARD_MIN_SCALE_YI:
        penalties.append("基金规模过小")
        return 0.0
    if scale < _MIN_SCALE_YI:
        penalties.append("基金规模低于1亿元")
        return 2.0
    if scale < 3:
        penalties.append("基金规模偏小")
        return 5.0
    if scale <= 120:
        reasons.append("基金规模适中")
        return 10.0
    return 7.0


def _type_preference_score(row: dict, preference: str, reasons: list[str]) -> float:
    name = str(row.get("fund_name") or "")
    if preference == "etf_link" and _is_etf_link_fund(name):
        reasons.append("符合ETF/联接偏好")
        return 4.0
    if preference == "no_c_class" and not _is_c_class_fund(name):
        reasons.append("符合非C类偏好")
        return 3.0
    return 0.0


def _strip_internal_fields(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def _with_opportunity(entry: dict, opportunity: dict | None) -> dict:
    if not opportunity:
        return entry
    enriched = dict(entry)
    enriched["opportunity_track"] = opportunity.get("track")
    enriched["opportunity_score"] = opportunity.get("score")
    enriched["entry_hint"] = opportunity.get("entry_hint")
    return enriched


def _family_key(name: str) -> str:
    text = name.strip()
    share_class = extract_share_class_letter(text)
    replacements = (
        ("ETF联接", ""),
        ("ETF链接", ""),
        ("交易型开放式指数证券投资基金联接", ""),
        ("指数增强", "指数"),
        ("发起式", ""),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    if share_class and text.upper().endswith(share_class):
        text = text[:-1]
    for suffix in ("A类", "C类", "A", "C"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip() or name.strip()


def _resolve_fund_name(fund_code: str) -> str:
    """东财名称表优先，其次本地档案，最后回退代码本身。"""
    code = fund_code.strip().zfill(6)
    table_name = lookup_fund_name_by_code(code)
    if table_name:
        return table_name
    profile = get_fund_profile_by_code(code)
    if profile and profile.fund_name:
        return profile.fund_name
    return code


def _sector_keywords(sector_label: str, canon) -> tuple[str, ...]:
    names = {sector_label}
    if canon is not None:
        names.add(canon.source_name)
        names.add(canon.label)
    mapping = {
        "半导体": ("半导体", "芯片", "集成电路"),
        "商业航天": ("航天", "航空", "卫星"),
        "国防军工": ("军工", "国防", "航天"),
        "电网设备": ("电网", "电力设备"),
        "人工智能": ("人工智能", "AI", "智能"),
        "互联网": ("互联网", "网络", "游戏", "传媒"),
        "传媒": ("传媒", "游戏", "影视", "动漫", "出版", "文化传媒"),
        "有色金属": ("有色", "金属", "铜", "铝", "锂矿"),
        "贵金属": ("贵金属", "黄金", "白银", "金银", "黄金产业"),
        "新能源车": ("新能源", "汽车", "电动车", "锂电"),
        "医药": ("医药", "生物", "制药", "医疗"),
        "证券": ("证券", "券商"),
        "银行": ("银行",),
        "白酒": ("白酒", "酒"),
        "光伏": ("光伏", "太阳能"),
        "锂电池": ("锂电池", "电池"),
        "消费电子": ("消费电子", "电子", "消费"),
        "机器人": ("机器人", "自动化"),
        "云计算": ("云计算", "云服务", "云产业", "云基础设施"),
        "金融科技": ("金融科技", "FinTech", "互联网金融", "数字金融"),
        "5G": ("5G", "通信"),
        "医疗器械": ("医疗器械", "器械"),
        "CPO": ("CPO", "光模块", "共封装", "光电"),
        "PCB": ("PCB", "电路板", "印制电路"),
    }
    extra = mapping.get(sector_label, ())
    return tuple(names) + extra


def _name_matches_sector(name: str, keywords: tuple[str, ...]) -> bool:
    text = name.strip()
    return any(keyword in text for keyword in keywords if keyword)


def infer_sector_label_from_discovery_keywords(fund_name: str) -> str:
    """基金名称关键词 → discovery 板块 label；无匹配时返回「综合」。"""
    from app.services.sector_registry import list_discovery_sector_labels

    name = (fund_name or "").strip()
    if not name:
        return "综合"
    for label in list_discovery_sector_labels():
        canon = get_canonical_sector(label)
        keywords = _sector_keywords(label, canon)
        if _name_matches_sector(name, keywords):
            return label
    return "综合"


def _passes_quality(row: dict, *, as_of_date: date | None = None) -> bool:
    established = _parse_iso_date(row.get("established_date"))
    if (
        established is not None
        and ((as_of_date or date.today()) - established).days < _MIN_HISTORY_DAYS
    ):
        return False
    scale = row.get("fund_scale_yi")
    if scale is not None:
        try:
            if float(scale) < _MIN_SCALE_YI:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _is_etf_link_fund(name: str) -> bool:
    text = name.strip()
    return "联接" in text or "链接" in text or "ETF" in text.upper()


def _is_c_class_fund(name: str) -> bool:
    text = name.strip()
    if "C类" in text or text.endswith("C"):
        return True
    return False


def _matches_fund_type_preference(name: str, preference: str) -> bool:
    if preference == "no_c_class":
        return not _is_c_class_fund(name)
    if preference == "etf_link":
        # 历史 API 字段继续兼容，但“优先”只能加分，不能把主动基金硬过滤为空。
        return True
    return True


def _share_class_rank(name: str) -> int:
    return 0 if _is_c_class_fund(name) else 1


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _sortable_score(value: object) -> float:
    parsed = _num(value)
    return parsed if parsed is not None else -999.0


def _unique_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
