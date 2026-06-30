from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.database import (
    get_fund_primary_sector,
    get_fund_primary_sectors_global_by_codes,
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    save_fund_primary_sector,
)
from app.models import FundProfile, Holding
from app.request_context import try_get_request_user_id
from app.services.fund_primary_sector_global import (
    is_global_sector_fresh,
    load_fresh_global_sector,
    promote_record_to_global,
)
from app.services.fund_profile import (
    _is_valid_sector_label,
    infer_intraday_index_from_fund_name,
)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import (
    infer_sector_label_from_fund_name,
    infer_semantic_sector_from_fund_name,
)

logger = logging.getLogger(__name__)

_BENCHMARK_MISS_TTL = timedelta(hours=24)
_benchmark_miss_cache: dict[str, datetime] = {}

# 已废弃：per-fund 手工种子由业绩基准 / 重仓行业穿透替代（discovery 改读 fund_primary_sectors）。
GLOBAL_FUND_SECTOR_SEEDS: dict[str, dict[str, str | None]] = {}

_SOURCE_PRIORITY = {
    "ocr_detail": 100,
    "manual": 85,
    "holdings_infer": 70,
    "benchmark_index": 65,
    "alipay_overview": 50,
    "semantic_name": 40,
    "name_infer": 10,
}

# 仅 OCR 详情 / 手动沉淀的板块可挡住业绩基准；总览推断的 alipay_overview 不可靠。
_HIGH_TRUST_SECTOR_SOURCES = frozenset({"ocr_detail", "manual"})


from app.services.fund_primary_sector_types import PrimarySectorRecord


def _can_upsert_primary_sector(existing: dict | None, new_source: str) -> bool:
    if not existing:
        return True
    old_source = str(existing.get("source") or "")
    old_prio = _SOURCE_PRIORITY.get(old_source, 0)
    new_prio = _SOURCE_PRIORITY.get(new_source, 0)
    if new_prio > old_prio:
        return True
    if new_source == "benchmark_index" and old_source in {
        "alipay_overview",
        "name_infer",
    }:
        return True
    return new_prio >= old_prio and new_source == old_source


def upsert_primary_sector_from_profile(profile: FundProfile, *, source: str = "ocr_detail") -> None:
    if not profile.fund_code or profile.fund_code == "000000":
        return
    if not _is_valid_sector_label(profile.sector_name):
        return
    existing = get_fund_primary_sector(profile.fund_code)
    if existing and not _can_upsert_primary_sector(existing, source):
        return
    save_fund_primary_sector(
        fund_code=profile.fund_code,
        sector_name=profile.sector_name or "",
        intraday_index_name=profile.intraday_index_name,
        source=source,
        confidence=0.95 if source == "ocr_detail" else 0.9,
        detail={"fund_name": profile.fund_name},
    )


def upsert_primary_sector_from_holding(holding: Holding, *, source: str) -> None:
    if not holding.fund_code or holding.fund_code == "000000":
        return
    if not _is_valid_sector_label(holding.sector_name):
        return
    existing = get_fund_primary_sector(holding.fund_code)
    if existing and not _can_upsert_primary_sector(existing, source):
        return
    index_name = holding.intraday_index_name
    if not index_name:
        index_name = infer_intraday_index_from_fund_name(holding.fund_name)
    save_fund_primary_sector(
        fund_code=holding.fund_code,
        sector_name=holding.sector_name or "",
        intraday_index_name=index_name,
        source=source,
        confidence=0.88,
        detail={"fund_name": holding.fund_name},
    )


def resolve_primary_sector(
    fund_code: str,
    *,
    fund_name: str | None = None,
    allow_name_infer: bool = False,
    fetch_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
) -> PrimarySectorRecord | None:
    code = fund_code.strip().zfill(6)
    if len(code) != 6 or code == "000000":
        return None

    row = get_fund_primary_sector(code)
    if row and _is_valid_sector_label(row.get("sector_name")):
        if str(row.get("source") or "") in _HIGH_TRUST_SECTOR_SOURCES:
            return _record_from_row(row)

    benchmark_record = _resolve_from_benchmark_index(code, fetch=fetch_benchmark)
    if benchmark_record is not None:
        return benchmark_record

    if fetch_holdings_infer:
        holdings_record = _resolve_from_holdings_infer(code, persist=bool(try_get_request_user_id()))
        if holdings_record is not None:
            return holdings_record

    global_row = load_fresh_global_sector(code)
    if global_row:
        return _record_from_row({**global_row, "fund_code": code})

    if row and _is_valid_sector_label(row.get("sector_name")):
        return _record_from_row(row)

    profile = get_fund_profile_by_code(code)
    if profile and _is_valid_sector_label(profile.sector_name):
        # 支付宝总览 OCR 不含可靠板块名，勿用档案里的推断值挡住业绩基准。
        if profile.source != "alipay-overview":
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=profile.sector_name or "",
                intraday_index_name=profile.intraday_index_name,
                source="alipay_overview",
                confidence=0.9,
            )

    if allow_name_infer and fund_name:
        candidate = infer_semantic_sector_from_fund_name(fund_name)
        if candidate is not None:
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=candidate.sector_name,
                intraday_index_name=(
                    infer_intraday_index_from_fund_name(fund_name)
                    if candidate.quote_key
                    else None
                ),
                source="semantic_name",
                confidence=candidate.confidence,
            )

        inferred = infer_sector_label_from_fund_name(fund_name)
        if inferred and get_canonical_sector(inferred):
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=inferred,
                intraday_index_name=infer_intraday_index_from_fund_name(fund_name),
                source="name_infer",
                confidence=0.35,
            )
    return None


def resolve_sector_labels_for_radar(
    codes_to_names: dict[str, str],
    *,
    fetch_benchmark: bool = False,
) -> dict[str, str]:
    """批量解析关联板块（大跌雷达等全市场场景，无用户持仓上下文）。

    优先级：当前用户 fund_primary_sectors → 全市场 global（TTL 内）
    → resolve_primary_sector（无联网基准）→ discovery 名称关键词 → 「综合」。
    """
    if not codes_to_names:
        return {}

    normalized_codes = {
        str(code).strip().zfill(6): (name or "").strip()
        for code, name in codes_to_names.items()
        if str(code).strip().zfill(6).isdigit()
    }
    if not normalized_codes:
        return {}

    from app.services.discovery_candidate_pool import infer_sector_label_from_discovery_keywords

    user_by_code: dict[str, str] = {}
    try:
        for row in list_fund_primary_sectors():
            code = str(row.get("fund_code", "")).zfill(6)
            label = str(row.get("sector_name") or "").strip()
            if code in normalized_codes and _is_valid_sector_label(label):
                user_by_code[code] = label
    except RuntimeError:
        pass

    global_by_code: dict[str, str] = {}
    for code, row in get_fund_primary_sectors_global_by_codes(set(normalized_codes)).items():
        if not is_global_sector_fresh(row):
            continue
        label = str(row.get("sector_name") or "").strip()
        if _is_valid_sector_label(label):
            global_by_code[code] = label

    resolved: dict[str, str] = {}
    for code, fund_name in normalized_codes.items():
        if code in user_by_code:
            resolved[code] = user_by_code[code]
            continue
        if code in global_by_code:
            resolved[code] = global_by_code[code]
            continue
        record = resolve_primary_sector(
            code,
            fund_name=fund_name or None,
            allow_name_infer=True,
            fetch_benchmark=fetch_benchmark,
        )
        if record and _is_valid_sector_label(record.sector_name):
            resolved[code] = record.sector_name
            continue
        resolved[code] = infer_sector_label_from_discovery_keywords(fund_name)
    return resolved


def primary_sector_fields_for_holding(
    holding: Holding,
    *,
    fallback_code: str | None = None,
    allow_name_infer: bool = False,
    fetch_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
) -> dict[str, str]:
    if _is_valid_sector_label(holding.sector_name):
        return {}
    code = holding.fund_code if holding.fund_code != "000000" else (fallback_code or "")
    if not code or code == "000000":
        return {}
    record = resolve_primary_sector(
        code,
        fund_name=holding.fund_name,
        allow_name_infer=allow_name_infer,
        fetch_benchmark=fetch_benchmark,
        fetch_holdings_infer=fetch_holdings_infer,
    )
    if record is None:
        return {}
    fields: dict[str, str] = {"sector_name": record.sector_name}
    if record.intraday_index_name and not holding.intraday_index_name:
        fields["intraday_index_name"] = record.intraday_index_name
    return fields


def apply_primary_sector_to_holding(
    holding: Holding,
    *,
    fetch_benchmark: bool = True,
    allow_name_infer: bool = True,
) -> Holding:
    if holding.sector_name and not _is_valid_sector_label(holding.sector_name):
        holding = holding.model_copy(update={"sector_name": None})

    from app.services.sector_labels import infer_sector_label_from_fund_name

    inferred = infer_sector_label_from_fund_name(holding.fund_name)
    if (
        inferred
        and holding.sector_name == inferred
        and holding.fund_name
        and "指数" in holding.fund_name
    ):
        holding = holding.model_copy(update={"sector_name": None, "intraday_index_name": None})

    code = holding.fund_code if holding.fund_code != "000000" else ""
    record = None
    if code:
        record = resolve_primary_sector(
            code,
            fund_name=holding.fund_name,
            allow_name_infer=allow_name_infer,
            fetch_benchmark=fetch_benchmark,
        )

    if record and record.source == "benchmark_index":
        fields: dict[str, str] = {"sector_name": record.sector_name}
        if record.intraday_index_name:
            fields["intraday_index_name"] = record.intraday_index_name
        if holding.sector_name != record.sector_name or holding.intraday_index_name != record.intraday_index_name:
            updated = holding.model_copy(update=fields)
            upsert_primary_sector_from_holding(updated, source="benchmark_index")
            return updated

    if _is_valid_sector_label(holding.sector_name):
        if holding.fund_code and holding.fund_code != "000000":
            upsert_primary_sector_from_holding(holding, source="alipay_overview")
        return holding

    if record is None:
        return holding
    fields = {"sector_name": record.sector_name}
    if record.intraday_index_name and not holding.intraday_index_name:
        fields["intraday_index_name"] = record.intraday_index_name
    updated = holding.model_copy(update=fields)
    upsert_primary_sector_from_holding(updated, source=record.source)
    return updated


def apply_primary_sector_to_holdings(
    holdings: list[Holding],
    *,
    fetch_benchmark: bool = True,
) -> list[Holding]:
    return [
        apply_primary_sector_to_holding(item, fetch_benchmark=fetch_benchmark)
        for item in holdings
    ]


def refresh_benchmark_sectors_for_holdings(
    holdings: list[Holding],
    *,
    fetch_missing_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
) -> list[Holding]:
    """板块刷新前：拉业绩基准；仍无板块时可选重仓行业穿透。"""
    refreshed: list[Holding] = []
    for holding in holdings:
        code = (holding.fund_code or "").strip()
        if not code or code == "000000":
            refreshed.append(holding)
            continue
        row = get_fund_primary_sector(code)
        if row and str(row.get("source") or "") in _HIGH_TRUST_SECTOR_SOURCES:
            refreshed.append(holding)
            continue
        if row and str(row.get("source") or "") == "benchmark_index":
            refreshed.append(apply_primary_sector_to_holding(holding, fetch_benchmark=False))
            continue
        if not fetch_missing_benchmark and not fetch_holdings_infer:
            refreshed.append(apply_primary_sector_to_holding(holding, fetch_benchmark=False))
            continue
        updated = apply_primary_sector_to_holding(
            holding,
            fetch_benchmark=fetch_missing_benchmark,
            allow_name_infer=not fetch_holdings_infer,
        )
        if (
            fetch_holdings_infer
            and not _is_valid_sector_label(updated.sector_name)
        ):
            record = _resolve_from_holdings_infer(code, persist=True)
            if record is not None:
                fields: dict[str, str] = {"sector_name": record.sector_name}
                if record.intraday_index_name and not updated.intraday_index_name:
                    fields["intraday_index_name"] = record.intraday_index_name
                updated = updated.model_copy(update=fields)
        refreshed.append(updated)
    return refreshed


def recommend_sector_from_holdings(fund_code: str) -> PrimarySectorRecord | None:
    return _resolve_from_holdings_infer(fund_code, persist=True)


def _resolve_from_holdings_infer(fund_code: str, *, persist: bool = True) -> PrimarySectorRecord | None:
    from app.services.fund_holdings_sector_infer import (
        fetch_portfolio_stocks_with_industry,
        infer_sector_from_portfolio_stocks,
    )

    code = fund_code.strip().zfill(6)
    stocks = fetch_portfolio_stocks_with_industry(code)
    if not stocks:
        return None

    inferred = infer_sector_from_portfolio_stocks(code, stocks)
    if inferred is None:
        return None

    sector_name, scores, evidence = inferred
    confidence = min(0.92, round(scores[sector_name] / 100.0 + 0.5, 2))
    from app.services.fund_profile import infer_intraday_index_from_sector

    index_name = infer_intraday_index_from_sector(sector_name)

    record = PrimarySectorRecord(
        fund_code=code,
        sector_name=sector_name,
        intraday_index_name=index_name,
        source="holdings_infer",
        confidence=confidence,
        detail={"scores": scores, "evidence": evidence[:8]},
    )

    if persist:
        existing = get_fund_primary_sector(code)
        if try_get_request_user_id() is not None and (
            not existing
            or _SOURCE_PRIORITY.get(existing.get("source", ""), 0) <= _SOURCE_PRIORITY["holdings_infer"]
        ):
            save_fund_primary_sector(
                fund_code=code,
                sector_name=sector_name,
                intraday_index_name=index_name,
                source="holdings_infer",
                confidence=confidence,
                detail=record.detail,
            )
        promote_record_to_global(record)
    return record


def refresh_primary_sector_for_fund(fund_code: str, *, fund_name: str | None = None) -> dict:
    code = fund_code.strip().zfill(6)
    current = resolve_primary_sector(code, fund_name=fund_name)
    recommendation = recommend_sector_from_holdings(code)
    return {
        "fund_code": code,
        "current": _record_to_dict(current),
        "recommendation": _record_to_dict(recommendation),
        "applied": recommendation is not None,
    }


def sync_primary_sectors_from_profiles(profiles: list[FundProfile]) -> int:
    synced = 0
    for profile in profiles:
        if _is_valid_sector_label(profile.sector_name):
            upsert_primary_sector_from_profile(profile, source="ocr_detail")
            synced += 1
    return synced


def _resolve_from_benchmark_index(
    fund_code: str,
    *,
    fetch: bool = True,
    persist_user: bool = True,
    promote_global: bool = True,
) -> PrimarySectorRecord | None:
    from app.services.fund_benchmark_sector import fetch_fund_benchmark_text, resolve_sector_from_benchmark

    global_row = load_fresh_global_sector(fund_code)
    if global_row:
        return _record_from_row({**global_row, "fund_code": fund_code.strip().zfill(6)})

    if persist_user and try_get_request_user_id() is not None:
        existing = get_fund_primary_sector(fund_code)
        if existing and str(existing.get("source") or "") == "benchmark_index":
            return _record_from_row(existing)

    if not fetch:
        return None
    if _benchmark_miss_cached(fund_code):
        return None

    benchmark_text = fetch_fund_benchmark_text(fund_code)
    if not benchmark_text:
        _remember_benchmark_miss(fund_code)
        return None
    resolved = resolve_sector_from_benchmark(benchmark_text)
    if resolved is None:
        _remember_benchmark_miss(fund_code)
        return None
    sector_name, intraday_index_name, match = resolved
    if not get_canonical_sector(sector_name):
        from app.services.sector_registry_data import THEME_BOARD_INDEX

        if sector_name not in THEME_BOARD_INDEX:
            _remember_benchmark_miss(fund_code)
            return None

    code = fund_code.strip().zfill(6)
    record = PrimarySectorRecord(
        fund_code=code,
        sector_name=sector_name,
        intraday_index_name=intraday_index_name,
        source="benchmark_index",
        confidence=0.82,
        detail={
            "index_code": match.index_code,
            "index_name": match.index_name,
            "benchmark_text": match.benchmark_text[:240],
        },
    )
    if persist_user and try_get_request_user_id() is not None:
        existing = get_fund_primary_sector(code)
        if _can_upsert_primary_sector(existing, "benchmark_index"):
            save_fund_primary_sector(
                fund_code=code,
                sector_name=sector_name,
                intraday_index_name=intraday_index_name,
                source="benchmark_index",
                confidence=record.confidence,
                detail=record.detail,
            )
    if promote_global:
        promote_record_to_global(record)
    _benchmark_miss_cache.pop(fund_code, None)
    return record


def _benchmark_miss_cached(fund_code: str) -> bool:
    missed_at = _benchmark_miss_cache.get(fund_code)
    if missed_at is None:
        return False
    if datetime.now(timezone.utc) - missed_at >= _BENCHMARK_MISS_TTL:
        _benchmark_miss_cache.pop(fund_code, None)
        return False
    return True


def _remember_benchmark_miss(fund_code: str) -> None:
    _benchmark_miss_cache[fund_code] = datetime.now(timezone.utc)


def _record_from_row(row: dict) -> PrimarySectorRecord:
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = None
    return PrimarySectorRecord(
        fund_code=str(row["fund_code"]),
        sector_name=str(row["sector_name"]),
        intraday_index_name=row.get("intraday_index_name"),
        source=str(row.get("source") or "unknown"),
        confidence=row.get("confidence"),
        detail=detail if isinstance(detail, dict) else None,
    )


def _record_to_dict(record: PrimarySectorRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "fund_code": record.fund_code,
        "sector_name": record.sector_name,
        "intraday_index_name": record.intraday_index_name,
        "source": record.source,
        "confidence": record.confidence,
        "detail": record.detail,
    }


def primary_sector_row_for_api(fund_code: str, *, fund_name: str | None = None) -> dict:
    record = resolve_primary_sector(fund_code, fund_name=fund_name, fetch_benchmark=True)
    return {
        "fund_code": fund_code.strip().zfill(6),
        "mapping": _record_to_dict(record),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
