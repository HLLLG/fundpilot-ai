from __future__ import annotations

from app.database import (
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    list_fund_primary_sectors_by_sector_names,
)
from app.models import Holding
from app.services.discovery_selection_strategy import (
    SelectionStrategy,
    balanced_score,
)
from app.services.fund_code_resolver import lookup_fund_name_by_code
from app.services.fund_data import FundDataService, _map_holdings_concurrently
from app.services.sector_canonical import get_canonical_sector
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.fund_rank_cache import fetch_open_fund_rank_cached

_POOL_CAP = 28
_PER_SECTOR = 5
_MIN_SCALE_YI = 1.0


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
    # 默认 fetcher 在调用时 lookup，便于 monkeypatch 与共享缓存对齐。
    if fetch_rank is None:
        fetch_rank = fetch_open_fund_rank_cached
    if fetch_new_funds is None:
        fetch_new_funds = fetch_new_fund_offerings
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    rank_rows = fetch_rank(limit=300) or []
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
            collected.append(entry)
            seen_codes.add(str(entry.get("fund_code", "")).zfill(6))
            family_seen.add(_family_key(str(entry.get("fund_name") or "")))
            if len(collected) >= pool_cap:
                break

    return collected[:pool_cap]


def enrich_candidates(pool: list[dict]) -> list[dict]:
    service = FundDataService()

    def _enrich_one(item: dict) -> dict:
        code = str(item.get("fund_code", "")).zfill(6)
        name = str(item.get("fund_name", ""))
        holding = Holding(fund_code=code, fund_name=name, holding_amount=0)
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=252)
        row = dict(item)
        row["return_1y_percent"] = row.get("return_1y_percent") or snapshot.return_1y_percent
        row["max_drawdown_1y_percent"] = (
            row.get("max_drawdown_1y_percent") or snapshot.max_drawdown_1y_percent
        )
        row["fund_scale_yi"] = row.get("fund_scale_yi") or snapshot.fund_scale_yi
        row["management_fee"] = snapshot.management_fee
        row["fund_type"] = snapshot.fund_type
        row["latest_nav"] = snapshot.latest_nav
        row["nav_date"] = snapshot.nav_date
        if trend is not None and getattr(trend, "points", None):
            from app.services.nav_trend_summary import summarize_nav_history

            row["nav_trend"] = summarize_nav_history(
                trend, recent_sample=5, window_days=66
            )
        return row

    # 候选池最多 25 只，逐只 AkShare 拉取是冷缓存下荐基管线最大耗时来源；
    # 并发执行（IO 密集，_snapshot_and_trend_for_holding 内部已兜底异常）保序返回。
    return _map_holdings_concurrently(pool, _enrich_one)


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
    ]
    scored.sort(key=lambda item: float(item.get("fund_quality_score") or -999), reverse=True)

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
    from app.services.discovery_selection_strategy import (
        rank_candidates_balanced,
        rank_candidates_dip_rebound,
    )

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
    if selection_strategy == "dip_rebound":
        return rank_candidates_dip_rebound(candidates)
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
    }


def _with_quality_score(entry: dict, *, fund_type_preference: str) -> dict:
    row = dict(entry)
    reasons: list[str] = []
    penalties: list[str] = []

    sector_fit = _sector_fit_score(row)
    if sector_fit >= 34:
        reasons.append("板块高置信匹配")
    elif sector_fit >= 22:
        reasons.append("板块匹配明确")
    else:
        penalties.append("板块匹配置信偏低")

    performance = balanced_score(row)
    r3m = _num(row.get("return_3m_percent"))
    r6m = _num(row.get("return_6m_percent"))
    if r3m is None and r6m is None:
        penalties.append("缺少近3/6月收益")
        performance -= 6.0
    elif (r3m or 0.0) > 5 or (r6m or 0.0) > 10:
        reasons.append("近3/6月表现占优")

    risk_score = _risk_score(row, penalties, reasons)
    scale_score = _scale_score(row, penalties, reasons)
    type_score = _type_preference_score(row, fund_type_preference, reasons)

    score = sector_fit + performance + risk_score + scale_score + type_score
    row["sector_fit_score"] = round(sector_fit, 2)
    row["fund_quality_score"] = round(score, 2)
    row["quality_reasons"] = _unique_text(reasons)[:4]
    row["quality_penalties"] = _unique_text(penalties)[:4]
    return row


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
        return -2.0
    depth = abs(drawdown)
    if depth <= 20:
        reasons.append("近1年回撤可控")
        return 6.0
    if depth <= 30:
        return 2.0
    if depth >= 40:
        penalties.append("近1年回撤偏大")
        return -8.0
    penalties.append("近1年回撤略高")
    return -3.0


def _scale_score(row: dict, penalties: list[str], reasons: list[str]) -> float:
    scale = _num(row.get("fund_scale_yi"))
    if scale is None:
        penalties.append("缺少基金规模")
        return -3.0
    if scale < _MIN_SCALE_YI:
        penalties.append("基金规模过小")
        return -99.0
    if scale < 3:
        penalties.append("基金规模偏小")
        return -4.0
    if scale <= 120:
        reasons.append("基金规模适中")
        return 4.0
    return 1.0


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
        return _is_etf_link_fund(name)
    return True


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
