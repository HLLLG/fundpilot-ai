from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from app.database import get_fund_primary_sector, get_fund_profile_by_code, save_fund_primary_sector
from app.models import FundProfile, Holding
from app.services.fund_profile import (
    _is_valid_sector_label,
    infer_intraday_index_from_fund_name,
)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import infer_sector_label_from_fund_name

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 90

# 养基宝常见 fund_code → 主关联板块（全局种子，可被用户 OCR 覆盖）
GLOBAL_FUND_SECTOR_SEEDS: dict[str, dict[str, str | None]] = {
    "519674": {"sector_name": "半导体", "intraday_index_name": "中证半导体"},
    "015945": {"sector_name": "商业航天", "intraday_index_name": None},
    "008586": {"sector_name": "人工智能", "intraday_index_name": "中证人工智能"},
    "025856": {"sector_name": "电网设备", "intraday_index_name": "中证电网设备"},
}

# 重仓股名称关键词 → 东财概念板块（加权投票）
_SECTOR_STOCK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "半导体": (
        "半导体",
        "芯片",
        "集成电路",
        "华创",
        "中微",
        "海光",
        "韦尔",
        "兆易",
        "北方华创",
        "长电",
        "澜起",
        "圣邦",
    ),
    "商业航天": (
        "航天",
        "航空",
        "卫星",
        "沈飞",
        "航发",
        "光电",
        "导弹",
        "中航",
        "西飞",
        "洪都",
    ),
    "国防军工": ("军工", "防务", "兵器", "船舶", "重工"),
    "人工智能": ("人工智能", "AI", "算力", "科大讯飞", "寒武纪", "浪潮"),
    "电网设备": ("电网", "特变", "许继", "国电南瑞"),
    "新能源": ("新能源", "锂电", "光伏", "宁德", "比亚迪", "阳光电源"),
}

_SOURCE_PRIORITY = {
    "ocr_detail": 100,
    "yangjibao_overview": 90,
    "manual": 85,
    "holdings_infer": 70,
    "seed": 60,
    "name_infer": 20,
}


@dataclass(frozen=True)
class PrimarySectorRecord:
    fund_code: str
    sector_name: str
    intraday_index_name: str | None
    source: str
    confidence: float | None = None
    detail: dict | None = None


def upsert_primary_sector_from_profile(profile: FundProfile, *, source: str = "ocr_detail") -> None:
    if not profile.fund_code or profile.fund_code == "000000":
        return
    if not _is_valid_sector_label(profile.sector_name):
        return
    existing = get_fund_primary_sector(profile.fund_code)
    if existing and _SOURCE_PRIORITY.get(existing.get("source", ""), 0) > _SOURCE_PRIORITY.get(source, 0):
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
    if existing and _SOURCE_PRIORITY.get(existing.get("source", ""), 0) > _SOURCE_PRIORITY.get(source, 0):
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
    allow_name_infer: bool = True,
) -> PrimarySectorRecord | None:
    code = fund_code.strip().zfill(6)
    if len(code) != 6 or code == "000000":
        return None

    row = get_fund_primary_sector(code)
    if row and _is_valid_sector_label(row.get("sector_name")):
        return _record_from_row(row)

    profile = get_fund_profile_by_code(code)
    if profile and _is_valid_sector_label(profile.sector_name):
        source = "ocr_detail" if profile.source == "yangjibao-detail" else "yangjibao_overview"
        return PrimarySectorRecord(
            fund_code=code,
            sector_name=profile.sector_name or "",
            intraday_index_name=profile.intraday_index_name,
            source=source,
            confidence=0.9,
        )

    seed = GLOBAL_FUND_SECTOR_SEEDS.get(code)
    if seed and _is_valid_sector_label(seed.get("sector_name")):
        return PrimarySectorRecord(
            fund_code=code,
            sector_name=str(seed["sector_name"]),
            intraday_index_name=seed.get("intraday_index_name"),
            source="seed",
            confidence=0.75,
        )

    if allow_name_infer and fund_name:
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


def primary_sector_fields_for_holding(
    holding: Holding,
    *,
    fallback_code: str | None = None,
    allow_name_infer: bool = False,
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
    )
    if record is None:
        return {}
    fields: dict[str, str] = {"sector_name": record.sector_name}
    if record.intraday_index_name and not holding.intraday_index_name:
        fields["intraday_index_name"] = record.intraday_index_name
    return fields


def apply_primary_sector_to_holding(holding: Holding) -> Holding:
    if _is_valid_sector_label(holding.sector_name):
        if holding.fund_code and holding.fund_code != "000000":
            upsert_primary_sector_from_holding(holding, source="yangjibao_overview")
        return holding

    fields = primary_sector_fields_for_holding(holding, allow_name_infer=False)
    if not fields:
        return holding
    updated = holding.model_copy(update=fields)
    upsert_primary_sector_from_holding(updated, source="yangjibao_overview")
    return updated


def apply_primary_sector_to_holdings(holdings: list[Holding]) -> list[Holding]:
    return [apply_primary_sector_to_holding(item) for item in holdings]


def recommend_sector_from_holdings(fund_code: str) -> PrimarySectorRecord | None:
    code = fund_code.strip().zfill(6)
    payload = _fetch_holdings_subprocess(code)
    if not payload:
        return None

    scores: dict[str, float] = {}
    evidence: list[dict] = []
    for item in payload:
        name = str(item.get("name", "")).strip()
        weight = float(item.get("weight", 0) or 0)
        if not name or weight <= 0:
            continue
        matched_labels: set[str] = set()
        for label, keywords in _SECTOR_STOCK_KEYWORDS.items():
            if any(keyword in name for keyword in keywords):
                scores[label] = scores.get(label, 0.0) + weight
                matched_labels.add(label)
        if matched_labels:
            evidence.append({"stock": name, "weight": weight, "labels": sorted(matched_labels)})

    if not scores:
        return None

    sector_name = max(scores, key=lambda key: scores[key])
    if scores[sector_name] < 8.0:
        return None

    if not get_canonical_sector(sector_name):
        return None

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

    existing = get_fund_primary_sector(code)
    if not existing or _SOURCE_PRIORITY.get(existing.get("source", ""), 0) <= _SOURCE_PRIORITY["holdings_infer"]:
        save_fund_primary_sector(
            fund_code=code,
            sector_name=sector_name,
            intraday_index_name=index_name,
            source="holdings_infer",
            confidence=confidence,
            detail=record.detail,
        )
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


def _fetch_holdings_subprocess(fund_code: str) -> list[dict] | None:
    script = r"""
import json
import sys
from datetime import datetime

import akshare as ak

code = sys.argv[1]
years = [str(datetime.now().year), str(datetime.now().year - 1)]
rows = []
for year in years:
    try:
        frame = ak.fund_portfolio_hold_em(symbol=code, date=year)
    except Exception:
        continue
    if frame is None or frame.empty:
        continue
    name_col = "股票名称" if "股票名称" in frame.columns else frame.columns[1]
    weight_col = "占净值比例" if "占净值比例" in frame.columns else None
    if weight_col is None:
        for col in frame.columns:
            if "比例" in str(col) or "占比" in str(col):
                weight_col = col
                break
    if weight_col is None:
        continue
    for _, row in frame.head(10).iterrows():
        name = str(row[name_col]).strip()
        try:
            weight = float(row[weight_col])
        except Exception:
            weight = 0.0
        if name:
            rows.append({"name": name, "weight": weight})
    if rows:
        break
print(json.dumps(rows, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, fund_code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        payload = json.loads(completed.stdout.strip())
        return payload if isinstance(payload, list) else None
    except Exception:
        logger.exception("fund holdings fetch failed for %s", fund_code)
        return None


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
    record = resolve_primary_sector(fund_code, fund_name=fund_name)
    return {
        "fund_code": fund_code.strip().zfill(6),
        "mapping": _record_to_dict(record),
        "seed_available": fund_code.strip().zfill(6) in GLOBAL_FUND_SECTOR_SEEDS,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
