from __future__ import annotations

from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.sector_labels import normalize_sector_label

_NAV_TREND_LLM_KEYS = (
    "trend_label",
    "recent_5d_change_percent",
    "recent_5d_daily_change_percent",
    "distance_from_high_percent",
    "period_change_percent",
)


def slim_nav_trend_for_llm(nav_trend: dict | None) -> dict | None:
    if not isinstance(nav_trend, dict):
        return None
    slim = {key: nav_trend[key] for key in _NAV_TREND_LLM_KEYS if nav_trend.get(key) is not None}
    return slim or None


def build_sector_change_index(sector_heat: list[dict]) -> dict[str, float]:
    index: dict[str, float] = {}
    for row in sector_heat:
        label = str(row.get("sector_label") or "").strip()
        change = row.get("change_1d_percent")
        if not label or change is None:
            continue
        try:
            value = float(change)
        except (TypeError, ValueError):
            continue
        index[label] = value
        normalized = normalize_sector_label(label)
        if normalized and normalized not in index:
            index[normalized] = value
    return index


def resolve_candidate_daily_estimate(
    *,
    fund_code: str,
    sector_label: str,
    sector_change_index: dict[str, float],
    trade_date: str | None,
) -> tuple[float | None, str | None]:
    code = str(fund_code or "").strip().zfill(6)
    if trade_date and code and code != "000000":
        cached = get_cached_official_nav_return(code, trade_date)
        if cached is not None:
            return round(float(cached), 4), "official_nav"

    label = str(sector_label or "").strip()
    for key in (label, normalize_sector_label(label) if label else ""):
        if key and key in sector_change_index:
            return round(sector_change_index[key], 4), "sector_estimate"
    return None, None


def slim_candidate_for_llm(
    item: dict,
    *,
    sector_change_index: dict[str, float],
    trade_date: str | None,
) -> dict:
    code = item.get("fund_code")
    sector = item.get("sector_label")
    daily, source = resolve_candidate_daily_estimate(
        fund_code=str(code or ""),
        sector_label=str(sector or ""),
        sector_change_index=sector_change_index,
        trade_date=trade_date,
    )
    row: dict = {
        "fund_code": code,
        "fund_name": item.get("fund_name"),
        "sector_label": sector,
        "return_1y_percent": item.get("return_1y_percent"),
        "return_3m_percent": item.get("return_3m_percent"),
        "return_6m_percent": item.get("return_6m_percent"),
        "max_drawdown_1y_percent": item.get("max_drawdown_1y_percent"),
        "fund_scale_yi": item.get("fund_scale_yi"),
        "fund_quality_score": item.get("fund_quality_score"),
        "sector_fit_score": item.get("sector_fit_score"),
        "quality_reasons": item.get("quality_reasons") or [],
        "quality_penalties": item.get("quality_penalties") or [],
        "selection_reason": item.get("selection_reason"),
    }
    nav = slim_nav_trend_for_llm(item.get("nav_trend"))
    if nav:
        row["nav_trend"] = nav
    dip = item.get("dip_drop_percent")
    if dip is not None:
        row["dip_drop_percent"] = dip
    if daily is not None:
        row["estimated_daily_return_percent"] = daily
        row["daily_return_source"] = source
    return row


def trim_sector_heat_for_llm(
    sector_heat: list[dict],
    *,
    target_sectors: list[str],
    focus_sectors: list[str],
    top_n: int = 15,
) -> list[dict]:
    if not sector_heat:
        return []

    keep_labels = {
        str(label).strip()
        for label in (*target_sectors, *focus_sectors)
        if str(label).strip()
    }
    by_label = {
        str(row.get("sector_label") or "").strip(): dict(row)
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }

    selected: list[dict] = []
    seen: set[str] = set()
    for label in keep_labels:
        row = by_label.get(label)
        if row and label not in seen:
            selected.append(row)
            seen.add(label)

    ranked = sorted(
        sector_heat,
        key=lambda row: float(row.get("heat_score") or -999),
        reverse=True,
    )
    for row in ranked:
        if len(selected) >= top_n:
            break
        label = str(row.get("sector_label") or "").strip()
        if not label or label in seen:
            continue
        selected.append(dict(row))
        seen.add(label)
    return selected
