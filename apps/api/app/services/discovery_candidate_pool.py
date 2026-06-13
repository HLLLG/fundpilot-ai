from __future__ import annotations

from app.database import list_fund_primary_sectors
from app.models import Holding
from app.services.fund_data import FundDataService
from app.services.fund_primary_sector_service import GLOBAL_FUND_SECTOR_SEEDS
from app.services.sector_canonical import get_canonical_sector
from app.services.akshare_subprocess import fetch_open_fund_rank

_POOL_CAP = 25
_PER_SECTOR = 5
_MIN_SCALE_YI = 1.0


def build_candidate_pool(
    target_sectors: list[str],
    *,
    exclude_codes: set[str] | None = None,
    fetch_rank=fetch_open_fund_rank,
) -> list[dict]:
    excluded = {code.strip().zfill(6) for code in (exclude_codes or set())}
    rank_rows = fetch_rank(limit=300) or []
    primary_rows = list_fund_primary_sectors()
    collected: list[dict] = []
    seen_codes: set[str] = set()

    for sector_label in target_sectors:
        sector_candidates = _candidates_for_sector(
            sector_label,
            rank_rows=rank_rows,
            primary_rows=primary_rows,
            excluded=excluded,
            seen_codes=seen_codes,
        )
        collected.extend(sector_candidates[:_PER_SECTOR])

    if len(collected) < 3:
        for row in rank_rows:
            code = str(row.get("fund_code", "")).zfill(6)
            if code in excluded or code in seen_codes:
                continue
            if not _passes_quality(row):
                continue
            entry = _entry_from_rank(row, sector_label="综合", selection_reason="排行补位")
            collected.append(entry)
            seen_codes.add(code)
            if len(collected) >= _POOL_CAP:
                break

    return collected[:_POOL_CAP]


def enrich_candidates(pool: list[dict]) -> list[dict]:
    service = FundDataService()
    enriched: list[dict] = []
    for item in pool:
        code = str(item.get("fund_code", "")).zfill(6)
        name = str(item.get("fund_name", ""))
        holding = Holding(fund_code=code, fund_name=name, holding_amount=0)
        snapshot, trend = service._snapshot_and_trend_for_holding(holding, trading_days=66)
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

            row["nav_trend"] = summarize_nav_history(trend, recent_sample=5)
        enriched.append(row)
    return enriched


def _candidates_for_sector(
    sector_label: str,
    *,
    rank_rows: list[dict],
    primary_rows: list[dict],
    excluded: set[str],
    seen_codes: set[str],
) -> list[dict]:
    canon = get_canonical_sector(sector_label)
    keywords = _sector_keywords(sector_label, canon)
    results: list[dict] = []

    for code, seed in GLOBAL_FUND_SECTOR_SEEDS.items():
        if seed.get("sector_name") != sector_label:
            continue
        normalized = code.zfill(6)
        if normalized in excluded or normalized in seen_codes:
            continue
        results.append(
            {
                "fund_code": normalized,
                "fund_name": f"种子基金 {normalized}",
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
        results.append(
            {
                "fund_code": code,
                "fund_name": code,
                "sector_label": sector_label,
                "selection_reason": "主关联板块映射",
            }
        )
        seen_codes.add(code)

    ranked: list[dict] = []
    for row in rank_rows:
        code = str(row.get("fund_code", "")).zfill(6)
        if code in excluded or code in seen_codes:
            continue
        name = str(row.get("fund_name", ""))
        if not _name_matches_sector(name, keywords):
            continue
        if not _passes_quality(row):
            continue
        ranked.append(_entry_from_rank(row, sector_label=sector_label, selection_reason="排行筛选"))
        seen_codes.add(code)

    ranked.sort(
        key=lambda item: item.get("return_1y_percent") or -999,
        reverse=True,
    )
    results.extend(ranked)
    return results


def _entry_from_rank(row: dict, *, sector_label: str, selection_reason: str) -> dict:
    return {
        "fund_code": str(row.get("fund_code", "")).zfill(6),
        "fund_name": str(row.get("fund_name", "")),
        "sector_label": sector_label,
        "selection_reason": selection_reason,
        "return_1y_percent": row.get("return_1y_percent"),
        "max_drawdown_1y_percent": row.get("max_drawdown_1y_percent"),
        "fund_scale_yi": row.get("fund_scale_yi"),
    }


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
