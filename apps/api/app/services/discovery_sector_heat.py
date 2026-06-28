from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from app.services.sector_canonical import (
    CanonicalSector,
    get_canonical_sector,
    get_quote_canonical_sector,
)
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.sector_registry import (
    SectorQuoteRef,
    list_theme_board_labels,
    resolve_discovery_quote,
    resolve_market_quote,
)
from app.services.theme_board_snapshot import get_theme_board_snapshot
from app.services.trading_session import build_trading_session

_HEAT_LIVE_TTL_SECONDS = 60.0
_HEAT_CLOSED_TTL_SECONDS = 3600.0
_DEFAULT_NETWORK_TIMEOUT = 12.0
_DEFAULT_5D_BUDGET_SECONDS = 45.0
_DIP_SWING_5D_CANDIDATE_COUNT = 15
_SECTOR_HEAT_CACHE_VERSION = "v2"

# 荐基 UI 展示用别名 → 市场主题板块标签（共享 theme board 快照涨跌）
_UI_HEAT_LABEL_ALIASES: dict[str, str] = {
    "国防军工": "军工",
}


def _sector_heat_cache_key(trade_date: str | None, *, include_5d: bool) -> str:
    suffix = ":5d" if include_5d else ""
    return f"discovery:sector_heat:{_SECTOR_HEAT_CACHE_VERSION}:{trade_date}{suffix}"


def _fallback_theme_sector_heat_rows() -> list[dict]:
    """主题快照不可用时仍返回完整标签列表（无涨跌）。"""
    return [
        {
            "sector_label": label,
            "change_1d_percent": None,
            "change_5d_percent": None,
            "heat_score": None,
        }
        for label in list_theme_board_labels()
    ]


def build_sector_heat_ranking(
    *,
    include_5d: bool = False,
    fetch_canon_series=None,
    force_refresh: bool = False,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
    budget_seconds: float | None = None,
) -> list[dict]:
    """扫描 pipeline 板块热度：主题板块 1d+5d（theme 快照 clist）+ 缺失时 K 线兜底。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_ttl = (
        _HEAT_LIVE_TTL_SECONDS
        if session_kind in {"trading_day_intraday", "trading_day_pre_close"}
        else _HEAT_CLOSED_TTL_SECONDS
    )
    cache_key = _sector_heat_cache_key(trade_date, include_5d=include_5d)

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached and cached.get("sectors"):
            return list(cached["sectors"])

    rows = _rows_from_theme_board_snapshot()
    merged = list(rows) if rows else _fallback_theme_sector_heat_rows()

    if include_5d and not lightweight and merged:
        needs_kline = any(_as_float(row.get("change_5d_percent")) is None for row in merged)
        if needs_kline:
            merged = _merge_5d_kline_into_rows(
                merged,
                trade_date=trade_date,
                fetch_canon_series=fetch_canon_series,
                network_timeout=network_timeout,
                budget_seconds=(
                    budget_seconds if budget_seconds is not None else _DEFAULT_5D_BUDGET_SECONDS
                ),
                max_labels=_DIP_SWING_5D_CANDIDATE_COUNT,
            )

    merged = _append_alias_heat_rows(merged)
    merged = _sort_sector_heat_rows(merged)

    if merged and any(row.get("change_1d_percent") is not None for row in merged):
        save_spot_snapshot(
            cache_key,
            {"sectors": merged, "trade_date": trade_date, "session_kind": session_kind},
        )
    return merged


def build_sector_heat_ranking_for_ui() -> list[dict]:
    """推荐基金 Tab 关注方向：复用市场主题板块快照（白名单标签 + 当日涨跌），秒级返回。"""
    rows = _rows_from_theme_board_snapshot()
    if rows:
        rows = _merge_discovery_5d_from_cache(rows)
        return _append_alias_heat_rows(rows)
    return _fallback_theme_sector_heat_rows()


def _rows_from_theme_board_snapshot() -> list[dict]:
    """从 theme board 共享缓存构建热度行，与 /api/market/theme-boards 口径一致。"""
    try:
        snapshot = get_theme_board_snapshot(force_refresh=False, sort="change")
    except Exception:
        return []
    theme_items = snapshot.get("items") if isinstance(snapshot, dict) else None
    if not isinstance(theme_items, list):
        return []

    by_label: dict[str, dict] = {}
    for item in theme_items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "").strip()
        if not label:
            continue
        change_1d = _as_float(item.get("change_1d_percent"))
        change_5d = _as_float(item.get("change_5d_percent"))
        by_label[label] = {
            "sector_label": label,
            "change_1d_percent": change_1d,
            "change_5d_percent": change_5d,
            "heat_score": _heat_score(change_1d, change_5d),
        }

    if not by_label:
        return []

    merged: list[dict] = []
    for label in list_theme_board_labels():
        merged.append(
            by_label.get(label)
            or {
                "sector_label": label,
                "change_1d_percent": None,
                "change_5d_percent": None,
                "heat_score": None,
            }
        )
    return _sort_sector_heat_rows(merged)


def _merge_5d_kline_into_rows(
    rows: list[dict],
    *,
    trade_date: str | None,
    fetch_canon_series,
    network_timeout: float,
    budget_seconds: float,
    max_labels: int | None = None,
) -> list[dict]:
    """在已有 1d 行上合并近 5 日涨跌；clist 已有 5d 的标签跳过。"""
    by_label = {str(row.get("sector_label") or ""): dict(row) for row in rows}
    labels = _labels_for_5d_kline_fetch(rows, limit=max_labels)
    if not labels:
        return rows

    series_fetcher = fetch_canon_series or _default_fetch_canon_series
    deadline = time.monotonic() + max(0.0, budget_seconds)
    executor = ThreadPoolExecutor(max_workers=min(8, len(labels)))

    futures = [
        executor.submit(
            _fetch_sector_5d_change,
            label,
            trade_date,
            series_fetcher,
            network_timeout,
        )
        for label in labels
    ]
    pending = set(futures)
    try:
        while pending:
            timeout = max(0.0, deadline - time.monotonic())
            if timeout <= 0:
                break
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                label, change_5d = future.result()
                if change_5d is None or label not in by_label:
                    continue
                row = by_label[label]
                row["change_5d_percent"] = change_5d
                row["heat_score"] = _heat_score(
                    _as_float(row.get("change_1d_percent")),
                    change_5d,
                )
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return list(by_label.values())


def _labels_for_5d_kline_fetch(rows: list[dict], *, limit: int | None) -> list[str]:
    """按当日跌幅升序取 Top N（仅含已有 1d 数据的标签），用于短线抄底 5 日 K 线预筛。"""
    ranked: list[tuple[str, float]] = []
    for row in rows:
        label = str(row.get("sector_label") or "").strip()
        if not label:
            continue
        change_1d = _as_float(row.get("change_1d_percent"))
        if change_1d is None:
            continue
        if _as_float(row.get("change_5d_percent")) is not None:
            continue
        ranked.append((label, change_1d))
    ranked.sort(key=lambda item: item[1])
    if limit is None or limit <= 0:
        return [label for label, _ in ranked]
    return [label for label, _ in ranked[:limit]]


def _fetch_sector_5d_change(
    label: str,
    trade_date: str | None,
    fetch_canon_series,
    network_timeout: float,
) -> tuple[str, float | None]:
    canon = _resolve_kline_canon(label)
    if canon is None:
        return label, None
    series = fetch_canon_series(
        canon,
        lightweight=False,
        network_timeout=network_timeout,
    )
    return label, _rolling_change_percent(series, days=5)


def _resolve_kline_canon(label: str) -> CanonicalSector | None:
    ref = resolve_market_quote(label) or resolve_discovery_quote(label)
    if ref is not None:
        return _quote_ref_to_canon(label, ref)
    return get_quote_canonical_sector(label) or get_canonical_sector(label)


def _quote_ref_to_canon(label: str, ref: SectorQuoteRef) -> CanonicalSector:
    return CanonicalSector(
        label=label,
        source_type=ref.source_type,
        source_name=ref.source_name,
        eastmoney_secid=ref.eastmoney_secid,
        source_code=ref.source_code,
    )


def _merge_discovery_5d_from_cache(rows: list[dict]) -> list[dict]:
    """UI 兜底：合并 pipeline 已缓存的近 5 日涨跌。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    for include_5d in (True, False):
        cache_key = _sector_heat_cache_key(trade_date, include_5d=include_5d)
        cached = get_spot_snapshot(cache_key, ttl_seconds=_HEAT_CLOSED_TTL_SECONDS)
        if not cached or not cached.get("sectors"):
            continue
        rows = _patch_rows_with_5d(rows, cached["sectors"])
        if any(row.get("change_5d_percent") is not None for row in rows):
            break
    return rows


def _patch_rows_with_5d(rows: list[dict], cached_sectors: list[dict]) -> list[dict]:
    five_d_by_label: dict[str, float | None] = {}
    for item in cached_sectors:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "").strip()
        if not label:
            continue
        five_d_by_label[label] = _as_float(item.get("change_5d_percent"))

    patched: list[dict] = []
    for row in rows:
        next_row = dict(row)
        label = str(next_row.get("sector_label") or "").strip()
        change_5d = _lookup_discovery_5d(label, five_d_by_label)
        if change_5d is not None:
            next_row["change_5d_percent"] = change_5d
            next_row["heat_score"] = _heat_score(
                _as_float(next_row.get("change_1d_percent")),
                change_5d,
            )
        patched.append(next_row)
    return patched


def _lookup_discovery_5d(label: str, five_d_by_label: dict[str, float | None]) -> float | None:
    if label in five_d_by_label and five_d_by_label[label] is not None:
        return five_d_by_label[label]
    for alias, target in _UI_HEAT_LABEL_ALIASES.items():
        if label == target and alias in five_d_by_label:
            return five_d_by_label[alias]
        if label == alias and target in five_d_by_label:
            return five_d_by_label.get(alias) or five_d_by_label.get(target)
    return None


def _append_alias_heat_rows(rows: list[dict]) -> list[dict]:
    """为 discovery 旧标签名补充一行（如 国防军工 ← 军工），便于历史 focus 与热度对齐。"""
    by_label = {str(row.get("sector_label") or ""): row for row in rows}
    extra: list[dict] = []
    for alias, target in _UI_HEAT_LABEL_ALIASES.items():
        if alias in by_label:
            continue
        source = by_label.get(target)
        if source is None:
            continue
        extra.append({**source, "sector_label": alias})
    return rows + extra


def _sort_sector_heat_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda item: (
            item["heat_score"] if item["heat_score"] is not None else -999,
            item["change_1d_percent"] if item["change_1d_percent"] is not None else -999,
        ),
        reverse=True,
    )


def _default_fetch_canon_series(
    canon,
    *,
    lightweight: bool = False,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT,
) -> list[dict]:
    return fetch_canonical_daily_kline_series(
        canon,
        max_days=8 if lightweight else 12,
        timeout=network_timeout,
    )


def _heat_score(change_1d: float | None, change_5d: float | None) -> float | None:
    if change_1d is None and change_5d is None:
        return None
    one_day = change_1d if change_1d is not None else change_5d or 0.0
    five_day = change_5d if change_5d is not None else one_day
    return round(one_day * 0.6 + five_day * 0.4, 2)


def _rolling_change_percent(series: list[dict], *, days: int) -> float | None:
    if len(series) < 2:
        return None
    tail = series[-min(len(series), days + 1) :]
    start = _as_float(tail[0].get("change_percent"))
    total = 0.0
    count = 0
    for bar in tail[1:]:
        value = _as_float(bar.get("change_percent"))
        if value is not None:
            total += value
            count += 1
    if count == 0:
        return start
    return round(total, 2)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
