from __future__ import annotations

from app.database import get_fund_profile_by_code, list_fund_primary_sectors
from app.models import Holding
from app.services.discovery_selection_strategy import (
    SelectionStrategy,
    pick_sector_candidates,
    rank_candidates_balanced,
    rank_candidates_dip_rebound,
)
from app.services.fund_code_resolver import lookup_fund_name_by_code
from app.services.fund_data import FundDataService, _map_holdings_concurrently
from app.services.fund_primary_sector_service import GLOBAL_FUND_SECTOR_SEEDS
from app.services.sector_canonical import get_canonical_sector
from app.services.akshare_subprocess import fetch_new_fund_offerings
from app.services.fund_rank_cache import fetch_open_fund_rank_cached

_POOL_CAP = 25
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
) -> list[dict]:
    # 默认 fetcher 在调用时 lookup，便于 monkeypatch 与共享缓存对齐。
    if fetch_rank is None:
        fetch_rank = fetch_open_fund_rank_cached
    if fetch_new_funds is None:
        fetch_new_funds = fetch_new_fund_offerings
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    rank_rows = fetch_rank(limit=300) or []
    primary_rows = list_fund_primary_sectors()
    new_issue_rows: list[dict] = []
    if selection_strategy == "with_new_issue":
        new_issue_rows = fetch_new_funds(limit=300) or []

    collected: list[dict] = []
    seen_codes: set[str] = set()

    for sector_label in target_sectors:
        sector_candidates = _candidates_for_sector(
            sector_label,
            rank_rows=rank_rows,
            primary_rows=primary_rows,
            new_issue_rows=new_issue_rows,
            excluded=excluded,
            seen_codes=seen_codes,
            fund_type_preference=fund_type_preference,
            selection_strategy=selection_strategy,
        )
        collected.extend(sector_candidates[:per_sector])

    if len(collected) < 3:
        fallback_ranked = rank_candidates_balanced_fallback(
            rank_rows, excluded, seen_codes, fund_type_preference, selection_strategy
        )
        for entry in fallback_ranked:
            collected.append(entry)
            seen_codes.add(str(entry.get("fund_code", "")).zfill(6))
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
    primary_rows: list[dict],
    new_issue_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
) -> list[dict]:
    canon = get_canonical_sector(sector_label)
    keywords = _sector_keywords(sector_label, canon)
    fixed_entries: list[dict] = []

    for code, seed in GLOBAL_FUND_SECTOR_SEEDS.items():
        if seed.get("sector_name") != sector_label:
            continue
        normalized = code.zfill(6)
        if normalized in excluded or normalized in seen_codes:
            continue
        fixed_entries.append(
            {
                "fund_code": normalized,
                "fund_name": _resolve_fund_name(normalized),
                "sector_label": sector_label,
                "selection_reason": "全局种子",
            }
        )
        seen_codes.add(normalized)

    for row in primary_rows:
        if row.get("sector_name") != sector_label:
            continue
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        fixed_entries.append(
            {
                "fund_code": code,
                "fund_name": _resolve_fund_name(code),
                "sector_label": sector_label,
                "selection_reason": "主关联板块映射",
            }
        )
        seen_codes.add(code)

    ranked_entries: list[dict] = []
    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name", ""))
        if not _name_matches_sector(name, keywords):
            continue
        if not _passes_quality(row):
            continue
        if not _matches_fund_type_preference(name, fund_type_preference):
            continue
        ranked_entries.append(_entry_from_rank(row, sector_label=sector_label, selection_reason="排行筛选"))

    return pick_sector_candidates(
        sector_label=sector_label,
        fixed_entries=fixed_entries,
        ranked_entries=ranked_entries,
        new_issue_rows=new_issue_rows,
        keywords=keywords,
        excluded=excluded,
        seen_codes=seen_codes,
        fund_type_preference=fund_type_preference,
        selection_strategy=selection_strategy,
        name_matches_sector=_name_matches_sector,
        matches_fund_type=_matches_fund_type_preference,
    )


def rank_candidates_balanced_fallback(
    rank_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
    fund_type_preference: str,
    selection_strategy: SelectionStrategy = "balanced",
) -> list[dict]:
    from app.services.discovery_selection_strategy import (
        rank_candidates_balanced,
        rank_candidates_dip_rebound,
    )

    candidates: list[dict] = []
    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        if not _passes_quality(row):
            continue
        if not _matches_fund_type_preference(str(row.get("fund_name", "")), fund_type_preference):
            continue
        candidates.append(_entry_from_rank(row, sector_label="综合", selection_reason="排行补位"))
    if selection_strategy == "dip_rebound":
        return rank_candidates_dip_rebound(candidates)
    return rank_candidates_balanced(candidates)


def _entry_from_rank(row: dict, *, sector_label: str, selection_reason: str) -> dict:
    return {
        "fund_code": str(row.get("fund_code", "")).zfill(6),
        "fund_name": str(row.get("fund_name", "")),
        "sector_label": sector_label,
        "selection_reason": selection_reason,
        "return_1y_percent": row.get("return_1y_percent"),
        "return_6m_percent": row.get("return_6m_percent"),
        "return_3m_percent": row.get("return_3m_percent"),
        "max_drawdown_1y_percent": row.get("max_drawdown_1y_percent"),
        "fund_scale_yi": row.get("fund_scale_yi"),
    }


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


def _passes_quality(row: dict) -> bool:
    scale = row.get("fund_scale_yi")
    if scale is not None and float(scale) < _MIN_SCALE_YI:
        return False
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
