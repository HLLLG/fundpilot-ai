from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from math import isfinite

from app.database import (
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    list_fund_primary_sectors_by_sector_names,
)
from app.models import Holding
from app.services.discovery_selection_strategy import (
    SelectionStrategy,
)
from app.services.fund_code_resolver import lookup_fund_name_by_code
from app.services.fund_data import FundDataService, _map_holdings_concurrently
from app.services.sector_canonical import get_canonical_sector
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.fund_discovery_data_cache import (
    fetch_discovery_fund_universe_cached,
    fetch_fund_research_profiles_cached,
)
from app.services.fund_rank_cache import fetch_open_fund_rank_cached

_POOL_CAP = 28
_PER_SECTOR = 5
_MIN_SCALE_YI = 1.0
_HARD_MIN_SCALE_YI = 0.5
_MIN_HISTORY_DAYS = 365
_CORE_QUALITY_FIELDS = (
    "return_3m_percent",
    "return_6m_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
    "established_date",
    "fund_manager",
    "nav_date",
)


def build_candidate_pool(
    target_sectors: list[str],
    *,
    exclude_codes: set[str] | None = None,
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
    per_sector: int = _PER_SECTOR,
    pool_cap: int = _POOL_CAP,
    fetch_rank=None,
    fetch_new_funds=None,
    sector_opportunities: list[dict] | None = None,
) -> list[dict]:
    # 默认使用全量、分类型的开放式基金横截面，避免“近1年涨幅前300名”造成
    # 赢家偏差；冷启动失败时再降级到前500名排行。注入 fetch_rank 仍保留给测试。
    universe_mode = "injected"
    if fetch_rank is None:
        rank_rows = fetch_discovery_fund_universe_cached(limit=20_000) or []
        universe_mode = "full" if rank_rows else "top_500_fallback"
        if not rank_rows:
            rank_rows = fetch_open_fund_rank_cached(limit=500) or []
    else:
        rank_rows = fetch_rank(limit=300) or []
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
    primary_rows = list_fund_primary_sectors() + list_fund_primary_sectors_by_sector_names(
        target_sectors,
        limit_per_sector=20,
    )
    new_issue_rows: list[dict] = []
    if selection_strategy == "with_new_issue":
        new_issue_rows = fetch_new_funds(limit=300) or []

    collected: list[dict] = []
    seen_codes: set[str] = set()
    family_seen: set[str] = set()

    for index, sector_label in enumerate(target_sectors):
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
            opportunity=opportunity_by_sector.get(sector_label),
            family_seen=family_seen,
            limit=sector_limit,
        )
        for candidate in sector_candidates:
            candidate["candidate_universe_mode"] = universe_mode
            candidate["candidate_universe_size"] = len(rank_rows)
        collected.extend(sector_candidates[:sector_limit])
        if len(collected) >= pool_cap:
            break

    if len(collected) < 3 and len(collected) < pool_cap:
        fallback_ranked = rank_candidates_balanced_fallback(
            rank_rows,
            excluded,
            seen_codes,
            fund_type_preference,
            selection_strategy,
            family_seen=family_seen,
        )
        for entry in fallback_ranked:
            entry["candidate_universe_mode"] = universe_mode
            entry["candidate_universe_size"] = len(rank_rows)
            collected.append(entry)
            seen_codes.add(str(entry.get("fund_code", "")).zfill(6))
            family_seen.add(_family_key(str(entry.get("fund_name") or "")))
            if len(collected) >= pool_cap:
                break

    return collected[:pool_cap]


def enrich_candidates(pool: list[dict]) -> list[dict]:
    service = FundDataService()
    codes = [str(item.get("fund_code") or "").zfill(6) for item in pool]

    profile_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="discovery-fund-profile",
    )
    profile_future = profile_executor.submit(fetch_fund_research_profiles_cached, codes)

    def _enrich_one(item: dict) -> dict:
        code = str(item.get("fund_code", "")).zfill(6)
        name = str(item.get("fund_name", ""))
        holding = Holding(fund_code=code, fund_name=name, holding_amount=0)
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=252)
        row = dict(item)
        row["return_1y_percent"] = _first_present(
            row.get("return_1y_percent"), snapshot.return_1y_percent
        )
        row["max_drawdown_1y_percent"] = _first_valid_drawdown(
            row.get("max_drawdown_1y_percent"),
            snapshot.max_drawdown_1y_percent,
        )
        row["fund_scale_yi"] = _first_present(
            row.get("fund_scale_yi"), snapshot.fund_scale_yi
        )
        row["management_fee"] = snapshot.management_fee
        row["fund_type"] = _first_present(snapshot.fund_type, row.get("fund_type"))
        row["latest_nav"] = _first_present(snapshot.latest_nav, row.get("latest_nav"))
        row["nav_date"] = _first_present(snapshot.nav_date, row.get("nav_date"))
        if trend is not None and getattr(trend, "points", None):
            from app.services.nav_trend_summary import summarize_nav_history

            row["nav_trend"] = summarize_nav_history(
                trend, recent_sample=5, window_days=66
            )
        return row

    # 候选池最多 25 只，逐只 AkShare 拉取是冷缓存下荐基管线最大耗时来源；
    # 并发执行（IO 密集，_snapshot_and_trend_for_holding 内部已兜底异常）保序返回。
    try:
        enriched = _map_holdings_concurrently(pool, _enrich_one)
        try:
            profiles = profile_future.result()
        except Exception:  # noqa: BLE001 - research profile is best-effort
            profiles = {}
    finally:
        profile_executor.shutdown(wait=False, cancel_futures=True)

    rescored: list[dict] = []
    for raw in enriched:
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
        row = _with_data_quality_gate(row)
        row = _with_quality_score(row, fund_type_preference="any")
        row["quality_score_version"] = "fund_quality.v2"
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
) -> list[dict]:
    """在核心字段补全后再做最终准入与板块配额分配。

    初筛阶段尚不知道规模、经理和完整回撤。这里移除硬性排除项，并先为每个
    目标板块保留质量最高的候选，再用剩余高质量候选补足总池，避免低规模基金
    在补全前占满板块名额、把更可靠的后备基金挡在池外。
    """

    if pool_cap <= 0 or per_sector <= 0:
        return []
    acceptable = [
        dict(item)
        for item in pool
        if str((item.get("quality_gate") or {}).get("status") or "watch_only")
        != "excluded"
    ]
    acceptable.sort(
        key=lambda item: (
            _quality_gate_rank(item),
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
            _num(item.get("fund_quality_score")) or -999.0,
        ),
        reverse=True,
    )
    for rank, item in enumerate(selected, start=1):
        item["candidate_final_rank"] = rank
    return selected


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
    opportunity: dict | None = None,
    family_seen: set[str] | None = None,
    limit: int = _PER_SECTOR,
) -> list[dict]:
    canon = get_canonical_sector(sector_label)
    keywords = _sector_keywords(sector_label, canon)
    entries_by_code: dict[str, dict] = {}
    family_seen = family_seen if family_seen is not None else set()

    for row in primary_rows:
        if row.get("sector_name") != sector_label:
            continue
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name") or _resolve_fund_name(code))
        if not _matches_fund_type_preference(name, fund_type_preference):
            continue
        entry = _merge_rank_metrics(
            {
                "fund_code": code,
                "fund_name": name,
                "sector_label": sector_label,
                "selection_reason": "板块机会映射" if opportunity else "主关联板块映射",
                "sector_source": row.get("source"),
                "sector_confidence": row.get("confidence"),
                "_sector_match_kind": "primary",
            },
            rank_by_code.get(code),
        )
        entries_by_code[code] = _with_opportunity(entry, opportunity)

    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name", ""))
        family = _family_key(name)
        if family and family in family_seen:
            continue
        if not _name_matches_sector(name, keywords):
            continue
        if not _passes_quality(row):
            continue
        if not _matches_fund_type_preference(name, fund_type_preference):
            continue
        ranked_entry = _with_opportunity(
            _entry_from_rank(row, sector_label=sector_label, selection_reason="排行筛选"),
            opportunity,
        )
        ranked_entry["_sector_match_kind"] = "name"
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
            seen_codes=seen_codes,
            fund_type_preference=fund_type_preference,
        ):
            code = str(entry.get("fund_code", "")).zfill(6)
            entries_by_code.setdefault(code, _with_opportunity(entry, opportunity))

    scored = [
        _with_quality_score(entry, fund_type_preference=fund_type_preference)
        for entry in entries_by_code.values()
        if selection_strategy == "with_new_issue"
        or entry.get("is_new_issue")
        or _passes_quality(entry)
    ]
    scored.sort(
        key=lambda item: (
            float(item.get("fund_quality_score") or -999),
            _share_class_rank(str(item.get("fund_name") or "")),
        ),
        reverse=True,
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
        selected.append(_strip_internal_fields(entry))
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
    score = _num(opportunity.get("score")) or 0.0
    top_scores = sorted(
        [_num(item.get("score")) or 0.0 for item in opportunity_by_sector.values()],
        reverse=True,
    )
    top_cutoff = top_scores[min(3, len(top_scores) - 1)] if top_scores else 0.0
    can_expand = pool_cap >= total_sectors * base_limit + 1
    if can_expand and index < 4 and score >= max(70.0, top_cutoff):
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
    return merged


def _merge_entries(primary: dict, ranked: dict) -> dict:
    merged = dict(primary)
    for key, value in ranked.items():
        if key == "selection_reason":
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
    return merged


def _new_issue_entries_for_sector(
    rows: list[dict],
    *,
    sector_label: str,
    keywords: tuple[str, ...],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
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
    )
    for entry in entries:
        entry["_sector_match_kind"] = "new_issue"
    return entries


def rank_candidates_balanced_fallback(
    rank_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    selection_strategy: SelectionStrategy = "balanced",
    family_seen: set[str] | None = None,
) -> list[dict]:
    from app.services.discovery_selection_strategy import rank_candidates_balanced

    candidates: list[dict] = []
    family_seen = family_seen if family_seen is not None else set()
    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        family = _family_key(str(row.get("fund_name", "")))
        if family and family in family_seen:
            continue
        if not _passes_quality(row):
            continue
        if not _matches_fund_type_preference(str(row.get("fund_name", "")), fund_type_preference):
            continue
        candidates.append(_entry_from_rank(row, sector_label="综合", selection_reason="排行补位"))
        if family:
            family_seen.add(family)
    return rank_candidates_balanced(candidates)


def _entry_from_rank(row: dict, *, sector_label: str, selection_reason: str) -> dict:
    return {
        "fund_code": str(row.get("fund_code", "")).zfill(6),
        "fund_name": str(row.get("fund_name", "")),
        "sector_label": sector_label,
        "selection_reason": selection_reason,
        "_sector_match_kind": "name",
        "return_1y_percent": row.get("return_1y_percent"),
        "return_6m_percent": row.get("return_6m_percent"),
        "return_3m_percent": row.get("return_3m_percent"),
        "max_drawdown_1y_percent": row.get("max_drawdown_1y_percent"),
        "fund_scale_yi": row.get("fund_scale_yi"),
        "fund_type": row.get("fund_type"),
        "nav_date": row.get("nav_date"),
        "established_date": row.get("established_date"),
    }


def _with_quality_score(entry: dict, *, fund_type_preference: str) -> dict:
    row = dict(entry)
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

    performance = _bounded_performance_score(row, penalties, reasons)
    r3m = _num(row.get("return_3m_percent"))
    r6m = _num(row.get("return_6m_percent"))
    if r3m is None and r6m is None:
        penalties.append("缺少近3/6月收益")
    elif (r3m or 0.0) > 5 or (r6m or 0.0) > 10:
        reasons.append("近3/6月表现占优")

    risk_score = _risk_score(row, penalties, reasons)
    scale_score = _scale_score(row, penalties, reasons)
    type_score = _type_preference_score(row, fund_type_preference, reasons)
    if not _has_value(row.get("management_fee")):
        penalties.append("管理费率未核验；净值已反映历史经常性费用")
    name = str(row.get("fund_name") or "")
    if name:
        row["share_class"] = "C" if _is_c_class_fund(name) else "A/其他"
        row["share_class_fee_status"] = "unverified"

    coverage = _num(gate.get("coverage_percent")) or 0.0
    data_score = coverage / 10.0
    score = sector_fit + performance + risk_score + scale_score + type_score + data_score
    row["sector_fit_score"] = round(sector_fit, 2)
    row["fund_quality_score"] = round(max(0.0, min(100.0, score)), 2)
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
    return row


def _bounded_performance_score(
    row: dict,
    penalties: list[str],
    reasons: list[str],
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
    if r1y is not None and -10.0 <= r1y <= 70.0:
        score += 3.0
    elif r1y is not None and r1y > 100.0:
        penalties.append("近1年涨幅过高，存在追高偏差")
        score -= min(5.0, (r1y - 100.0) / 20.0)
    if score >= 17.0:
        reasons.append("近3/6月表现占优")
    return _clamp(score, 0.0, 25.0)


def _with_data_quality_gate(entry: dict) -> dict:
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
        and (date.today() - established).days < _MIN_HISTORY_DAYS
    ):
        status = "excluded"
        reasons.append("成立不足1年，缺少可验证的完整业绩周期")

    drawdown = _num(row.get("max_drawdown_1y_percent"))
    if status != "excluded" and drawdown is not None and abs(drawdown) > 50.0:
        status = "watch_only"
        reasons.append("近1年最大回撤超过50%，仅保留研究观察")

    nav_date = _parse_iso_date(row.get("nav_date"))
    if status != "excluded" and nav_date is not None and (date.today() - nav_date).days > 7:
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
    kind = str(row.get("_sector_match_kind") or "")
    if kind == "primary":
        confidence = _num(row.get("sector_confidence"))
        if confidence is None:
            return 28.0
        return 24.0 + min(16.0, max(0.0, confidence) * 16.0)
    if kind == "new_issue":
        return 18.0
    return 16.0


def _risk_score(row: dict, penalties: list[str], reasons: list[str]) -> float:
    drawdown = _num(row.get("max_drawdown_1y_percent"))
    if drawdown is None:
        penalties.append("缺少近1年回撤")
        return 0.0
    depth = abs(drawdown)
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
    replacements = (
        ("ETF联接", ""),
        ("ETF链接", ""),
        ("交易型开放式指数证券投资基金联接", ""),
        ("指数增强", "指数"),
        ("发起式", ""),
    )
    for old, new in replacements:
        text = text.replace(old, new)
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
        "有色金属": ("有色", "金属", "铜", "铝", "锂矿"),
        "新能源车": ("新能源", "汽车", "电动车", "锂电"),
        "医药": ("医药", "生物", "制药", "医疗"),
        "证券": ("证券", "券商"),
        "银行": ("银行",),
        "白酒": ("白酒", "酒"),
        "光伏": ("光伏", "太阳能"),
        "锂电池": ("锂电池", "电池"),
        "消费电子": ("消费电子", "电子", "消费"),
        "机器人": ("机器人", "自动化"),
        "云计算": ("云计算", "云"),
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


def _passes_quality(row: dict) -> bool:
    established = _parse_iso_date(row.get("established_date"))
    if established is not None and (date.today() - established).days < _MIN_HISTORY_DAYS:
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


def _unique_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
