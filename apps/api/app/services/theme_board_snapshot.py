from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Literal

from app.models import Holding
from app.services.akshare_spot_client import fetch_akshare_board_records, fetch_boards_via_akshare
from app.services.eastmoney_spot_client import (
    fetch_eastmoney_board_records,
    fetch_eastmoney_clist_theme_metrics_by_code,
)
from app.services.eastmoney_trends_client import fetch_eastmoney_kline_close_percent
from app.services.sector_canonical import (
    CanonicalSector,
    get_canonical_sector,
    get_quote_canonical_sector,
)
from app.services.sector_registry import list_theme_board_labels, resolve_market_quote
from app.services.sector_registry_data import (
    THEME_BOARD_ALIAS,
    THEME_BOARD_FLOW,
    THEME_BOARD_INDEX,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
    snapshot_refreshed_before_process_boot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

SortMode = Literal["change", "inflow"]
BoardKind = Literal["industry", "concept", "index"]

_CACHE_VERSION = "v5"
_REFRESH_BUDGET_SECONDS = 30.0
_KLINE_FALLBACK_BUDGET_SECONDS = 15.0
_SERIES_TIMEOUT = 8.0
_MAX_WORKERS = 8

# 对标小倍养基「今日板块涨幅榜」的粗粒度精选清单；白名单与映射见 sector_registry_data。
# 东财 m:90 t:2/t:3 含 ~500 细分行业/概念，过碎；此处只取 curated 主题板块。
# 解析优先级：registry.resolve_market_quote → THEME_BOARD_INDEX → canonical → 别名 → spot 精确名。


def _theme_board_whitelist() -> tuple[str, ...]:
    return tuple(list_theme_board_labels())


def _entry_from_quote(
    name: str,
    *,
    secid: str,
    source_code: str | None,
    board_kind: str,
    source_name: str,
    change_for_code,
    canon: CanonicalSector | None = None,
) -> dict[str, Any]:
    return {
        "sector_label": name,
        "secid": secid,
        "source_code": source_code,
        "board_kind": board_kind,
        "change_hint": change_for_code(source_code) if board_kind != "index" else None,
        "_canon": canon
        or CanonicalSector(
            label=name,
            source_type=board_kind,
            source_name=source_name,
            eastmoney_secid=secid,
            source_code=source_code,
        ),
    }


def _resolve_theme_board_entry(
    name: str,
    *,
    change_for_code,
    concept_by_name: dict[str, str],
    industry_by_name: dict[str, str],
) -> dict[str, Any] | None:
    """Registry-first theme board resolution; legacy dicts as transition fallback."""
    quote = resolve_market_quote(name)
    if quote is not None:
        return _entry_from_quote(
            name,
            secid=quote.eastmoney_secid,
            source_code=quote.source_code,
            board_kind=quote.source_type,
            source_name=quote.source_name,
            change_for_code=change_for_code,
        )

    if name in THEME_BOARD_INDEX:
        secid, code, kind = THEME_BOARD_INDEX[name]
        return _entry_from_quote(
            name,
            secid=secid,
            source_code=code,
            board_kind=kind,
            source_name=name,
            change_for_code=change_for_code,
        )

    canon = get_quote_canonical_sector(name) or get_canonical_sector(name)
    if canon is not None:
        return {
            "sector_label": name,
            "secid": canon.eastmoney_secid,
            "source_code": canon.source_code,
            "board_kind": _board_kind_from_source_type(canon.source_type),
            "change_hint": change_for_code(canon.source_code),
            "_canon": canon,
        }

    if name in THEME_BOARD_ALIAS:
        secid, code, kind = THEME_BOARD_ALIAS[name]
        return _entry_from_quote(
            name,
            secid=secid,
            source_code=code,
            board_kind=kind,
            source_name=name,
            change_for_code=change_for_code,
            canon=None,
        )

    if name in concept_by_name:
        code = concept_by_name[name]
        return {
            "sector_label": name,
            "secid": f"90.{code}",
            "source_code": code,
            "board_kind": "concept",
            "change_hint": change_for_code(code),
            "_canon": None,
        }

    if name in industry_by_name:
        code = industry_by_name[name]
        return {
            "sector_label": name,
            "secid": f"90.{code}",
            "source_code": code,
            "board_kind": "industry",
            "change_hint": change_for_code(code),
            "_canon": None,
        }

    return None


# ---------------------------------------------------------------------------
# 板块全集（行业全量 + canonical 概念/指数，去重）
# ---------------------------------------------------------------------------
def _board_kind_from_source_type(source_type: str) -> BoardKind:
    if source_type in {"industry", "concept", "index"}:
        return source_type  # type: ignore[return-value]
    return "concept"


def _resolve_flow_source_code(
    name: str,
    entry: dict[str, Any],
    *,
    concept_by_name: dict[str, str],
    industry_by_name: dict[str, str],
) -> str | None:
    """涨跌幅 secid 与资金流 BK 解耦：指数主题仍返回东财 BK 代码供 clist 查 f62。"""
    if name in THEME_BOARD_FLOW:
        return THEME_BOARD_FLOW[name]

    secid = str(entry.get("secid", ""))
    if secid.startswith("90."):
        code = str(entry.get("source_code") or "").strip()
        return code or secid.split(".", 1)[1]

    if name in THEME_BOARD_ALIAS:
        return THEME_BOARD_ALIAS[name][1]

    canon = get_canonical_sector(name)
    if canon is not None and str(canon.eastmoney_secid).startswith("90."):
        code = str(canon.source_code or "").strip()
        if code:
            return code

    if name in concept_by_name:
        return concept_by_name[name]
    if name in industry_by_name:
        return industry_by_name[name]
    return None


def list_theme_board_universe() -> list[dict[str, Any]]:
    """对标小倍的固定粗粒度板块白名单，解析到东财 secid。

    解析优先级（每个白名单名）：``sector_registry.resolve_market_quote`` →
    ``THEME_BOARD_INDEX`` → canonical → 别名 → 东财概念/行业**精确名**匹配 → 跳过。
    每项：``sector_label``、``secid``、``source_code``、``board_kind``、
    ``change_hint``（东财 spot f3 兜底涨跌幅）、``_canon``。
    """
    concept_by_name, concept_by_code = _load_board_maps("concept")
    industry_by_name, industry_by_code = _load_board_maps("industry")

    def change_for_code(code: str | None) -> float | None:
        if not code:
            return None
        if code in concept_by_code:
            return concept_by_code[code]
        if code in industry_by_code:
            return industry_by_code[code]
        return None

    universe: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for name in _theme_board_whitelist():
        entry = _resolve_theme_board_entry(
            name,
            change_for_code=change_for_code,
            concept_by_name=concept_by_name,
            industry_by_name=industry_by_name,
        )
        if entry is None:
            logger.info("theme board whitelist name unresolved: %s", name)
            continue

        if entry["sector_label"] in seen_labels:
            continue
        seen_labels.add(entry["sector_label"])
        flow_code = _resolve_flow_source_code(
            name,
            entry,
            concept_by_name=concept_by_name,
            industry_by_name=industry_by_name,
        )
        entry["flow_source_code"] = flow_code
        universe.append(entry)

    return universe


def _load_board_maps(board_type: str) -> tuple[dict[str, str], dict[str, float]]:
    """返回 (name→code, code→change_percent)；拉取失败时返回空表（降级用 canonical）。"""
    by_name: dict[str, str] = {}
    by_code: dict[str, float] = {}
    try:
        rows = fetch_eastmoney_board_records(board_type)
    except Exception as exc:
        logger.info("theme universe %s spot failed: %s", board_type, exc)
        return by_name, by_code
    for row in rows:
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if not name or not code:
            continue
        by_name.setdefault(name, code)
        change = _as_float(row.get("change_percent"))
        if change is not None:
            by_code[code] = change
    return by_name, by_code


# ---------------------------------------------------------------------------
# 后台刷新：clist 批量 f3+f109+f62，缺失时 K 线/spot 兜底
# ---------------------------------------------------------------------------
def _clist_lookup_codes(entry: dict[str, Any], *, prefer_flow: bool) -> list[str]:
    """按主题解析 clist 代码；涨跌幅优先指数码，资金流优先 BK 码。"""
    keys = ("flow_source_code", "source_code") if prefer_flow else ("source_code", "flow_source_code")
    codes: list[str] = []
    for key in keys:
        code = str(entry.get(key) or "").strip()
        if code and code not in codes:
            codes.append(code)
    secid = str(entry.get("secid") or "")
    if secid.startswith("90."):
        bk = secid.split(".", 1)[1]
        if bk and bk not in codes:
            codes.append(bk)
    return codes


def _lookup_clist_changes(
    entry: dict[str, Any],
    by_code: dict[str, dict[str, float | None]],
) -> tuple[float | None, float | None]:
    """按 source_code / flow_source_code / secid 在东财 clist 批量结果中查 1d+5d。"""
    change_1d: float | None = None
    change_5d: float | None = None
    for code in _clist_lookup_codes(entry, prefer_flow=False):
        row = by_code.get(code)
        if not row:
            continue
        if change_1d is None and row.get("change_1d") is not None:
            change_1d = row["change_1d"]
        if change_5d is None and row.get("change_5d") is not None:
            change_5d = row["change_5d"]
        if change_1d is not None and change_5d is not None:
            break
    return change_1d, change_5d


def _flow_fields_from_clist_row(row: dict[str, float | None] | None) -> dict[str, Any]:
    if not row:
        return {"main_force_net_yi": None, "flow_tiers": None}
    tiers = {
        "super_large_net_yi": row.get("super_large_net_yi"),
        "large_net_yi": row.get("large_net_yi"),
        "medium_net_yi": row.get("medium_net_yi"),
        "small_net_yi": row.get("small_net_yi"),
    }
    main_force = row.get("main_force_net_yi")
    has_any = main_force is not None or any(value is not None for value in tiers.values())
    return {
        "main_force_net_yi": main_force,
        "flow_tiers": tiers if has_any else None,
    }


def _has_live_theme_metric(item: dict[str, Any]) -> bool:
    if item.get("change_1d_percent") is not None:
        return True
    if item.get("change_5d_percent") is not None:
        return True
    if item.get("main_force_net_yi") is not None:
        return True
    tiers = item.get("flow_tiers")
    return isinstance(tiers, dict) and any(value is not None for value in tiers.values())


def _lookup_clist_flow(
    entry: dict[str, Any],
    by_code: dict[str, dict[str, float | None]],
) -> dict[str, Any]:
    """资金流优先 BK(flow_source_code)，指数主题 fallback 到 source_code 的 m:2 f62。"""
    for code in _clist_lookup_codes(entry, prefer_flow=True):
        row = by_code.get(code)
        if row is None or row.get("main_force_net_yi") is None:
            continue
        fields = _flow_fields_from_clist_row(row)
        if fields.get("main_force_net_yi") is not None:
            return fields
    return {"main_force_net_yi": None, "flow_tiers": None}


def _item_from_entry(
    entry: dict[str, Any],
    *,
    change_1d: float | None,
    change_5d: float | None,
    flow_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "sector_label": entry["sector_label"],
        "board_kind": entry["board_kind"],
        "secid": entry["secid"],
        "source_code": entry.get("source_code"),
        "flow_source_code": entry.get("flow_source_code"),
        "change_1d_percent": _as_float(change_1d),
        "change_5d_percent": _as_float(change_5d),
    }
    item.update(flow_fields or {"main_force_net_yi": None, "flow_tiers": None})
    return item


def _enrich_missing_1d_via_kline(
    pending: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    trade_date: str | None,
) -> None:
    """仅对 clist 未命中 1d 的少量板块并行拉日 K 兜底。"""
    if not pending:
        return
    deadline = time.monotonic() + _KLINE_FALLBACK_BUDGET_SECONDS
    executor = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(pending)))

    def fetch_change(entry: dict[str, Any]) -> float | None:
        return _as_float(
            fetch_eastmoney_kline_close_percent(
                entry["secid"],
                source_code=entry.get("source_code"),
                trade_date=trade_date,
                timeout=_SERIES_TIMEOUT,
            )
        )

    futures = {executor.submit(fetch_change, entry): (item, entry) for item, entry in pending}
    try:
        while futures and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            done, still_pending = wait(
                futures,
                timeout=min(0.5, max(0.05, remaining)),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                item, entry = futures.pop(future)
                try:
                    change = future.result()
                except Exception as exc:
                    logger.debug("theme kline fallback failed %s: %s", entry.get("sector_label"), exc)
                    change = None
                if change is None:
                    change = _as_float(entry.get("change_hint"))
                if change is not None:
                    item["change_1d_percent"] = change
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def refresh_theme_board_snapshot(*, trade_date: str | None = None) -> dict[str, Any]:
    """后台刷新：clist 批量 f3+f109+f62，少量缺失再 K 线/spot 兜底。"""
    session = build_trading_session()
    resolved_date = trade_date or session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    universe = list_theme_board_universe()

    by_code: dict[str, dict[str, float | None]] = {}
    try:
        by_code = fetch_eastmoney_clist_theme_metrics_by_code(timeout=_SERIES_TIMEOUT)
    except Exception as exc:
        logger.info("theme board clist bulk fetch failed: %s", exc)

    items: list[dict[str, Any]] = []
    pending_kline: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for entry in universe:
        change_1d, change_5d = _lookup_clist_changes(entry, by_code)
        if change_1d is None:
            change_1d = _as_float(entry.get("change_hint"))
        flow_fields = _lookup_clist_flow(entry, by_code)
        item = _item_from_entry(
            entry,
            change_1d=change_1d,
            change_5d=change_5d,
            flow_fields=flow_fields,
        )
        items.append(item)
        if item["change_1d_percent"] is None:
            pending_kline.append((item, entry))

    _enrich_missing_1d_via_kline(pending_kline, trade_date=resolved_date)

    missing = [item for item in items if item["change_1d_percent"] is None]
    if missing:
        try:
            spot_changes = _load_theme_spot_changes()
        except Exception as exc:
            logger.debug("theme spot fallback failed: %s", exc)
            spot_changes = {}
        for item in missing:
            if item.get("board_kind") == "index":
                continue
            change = spot_changes.get(item["sector_label"])
            if change is not None:
                item["change_1d_percent"] = round(float(change), 2)

    snapshot = {
        "items": items,
        "trade_date": resolved_date,
        "session_kind": session_kind,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_key = f"theme:boards:{_CACHE_VERSION}:{resolved_date}"
    if any(_has_live_theme_metric(item) for item in items):
        save_spot_snapshot(cache_key, snapshot)

    flow_codes = [
        str(item.get("flow_source_code") or "").strip()
        for item in items
        if item.get("flow_source_code")
    ]
    if flow_codes:
        try:
            import threading

            from app.services.board_fund_flow_history import prefetch_board_flow_histories

            threading.Thread(
                target=prefetch_board_flow_histories,
                args=(flow_codes,),
                kwargs={"max_workers": 1},
                daemon=True,
            ).start()
        except Exception as exc:
            logger.debug("board flow prefetch schedule failed: %s", exc)

    return snapshot


# ---------------------------------------------------------------------------
# 持仓叠加 + payload
# ---------------------------------------------------------------------------
def _holding_secids(holdings: list[Holding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for holding in holdings:
        canon = get_quote_canonical_sector(holding.sector_name) or get_canonical_sector(
            holding.sector_name
        )
        if canon is None:
            continue
        counts[canon.eastmoney_secid] = counts.get(canon.eastmoney_secid, 0) + 1
    return counts


def apply_holdings_overlay(
    items: list[dict[str, Any]],
    holdings: list[Holding],
) -> list[dict[str, Any]]:
    held = _holding_secids(holdings or [])
    overlaid: list[dict[str, Any]] = []
    for item in items:
        count = held.get(str(item.get("secid")), 0)
        overlaid.append(
            {
                **item,
                "held_fund_count": count,
                "in_portfolio": count > 0,
            }
        )
    return overlaid


def build_theme_board_payload(
    items: list[dict[str, Any]],
    *,
    sort: SortMode,
    snapshot_meta: dict[str, Any],
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    overlaid = apply_holdings_overlay(items, holdings or [])
    sorted_items = _sort_theme_items(overlaid, sort=sort)
    ranked = [
        {**_strip_internal_theme_fields(row), "rank": index + 1}
        for index, row in enumerate(sorted_items)
    ]
    return {
        "trade_date": snapshot_meta.get("trade_date"),
        "session_kind": snapshot_meta.get("session_kind"),
        "available": snapshot_meta.get("available", False),
        "from_cache": snapshot_meta.get("from_cache", False),
        "stale": snapshot_meta.get("stale", False),
        "refreshed_at": snapshot_meta.get("refreshed_at"),
        "message": snapshot_meta.get("message"),
        "sort": sort,
        "items": ranked,
    }


def get_theme_board_snapshot(
    *,
    force_refresh: bool = False,
    holdings: list[Holding] | None = None,
    sort: SortMode = "change",
) -> dict[str, Any]:
    """只读缓存 + 持仓叠加；缓存为空或 force_refresh 时同步刷新一次兜底。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_key = f"theme:boards:{_CACHE_VERSION}:{trade_date}"

    cached: dict[str, Any] | None = None
    stale = False
    if not force_refresh:
        # 后台线程负责新鲜度；前台接受任意时段缓存，秒出。
        cached = get_spot_snapshot_any_age(cache_key)
        if cached is not None:
            stale = snapshot_refreshed_before_process_boot(cached.get("refreshed_at"))

    if cached is None or force_refresh:
        cached = refresh_theme_board_snapshot(trade_date=trade_date)
        from_cache = False
        stale = False
    else:
        from_cache = True

    items = list(cached.get("items") or [])
    available = bool(items)
    snapshot_meta = {
        "trade_date": cached.get("trade_date", trade_date),
        "session_kind": cached.get("session_kind", session_kind),
        "available": available,
        "from_cache": from_cache,
        "stale": stale,
        "refreshed_at": cached.get("refreshed_at"),
        "message": None if available else "行情暂不可用，请稍后重试",
    }
    return build_theme_board_payload(
        items,
        sort=sort,
        snapshot_meta=snapshot_meta,
        holdings=holdings,
    )


# ---------------------------------------------------------------------------
# 后台刷新线程
# ---------------------------------------------------------------------------
def _refresh_enabled() -> bool:
    from app.config import get_settings

    return bool(get_settings().theme_board_refresh_enabled)


def theme_board_refresh_loop() -> None:
    """兼容旧名：统一走 ``market_shared_refresh_loop``。"""
    from app.services.market_shared_refresh import market_shared_refresh_loop

    market_shared_refresh_loop()


# ---------------------------------------------------------------------------
# 现货榜兜底（仅日 K 全失败时）
# ---------------------------------------------------------------------------
def _load_theme_spot_changes() -> dict[str, float]:
    """批量现货涨跌幅兜底（clist/K 线均缺失时）。"""
    changes: dict[str, float] = {}

    for board_type in ("industry", "concept"):
        try:
            for row in fetch_akshare_board_records(board_type):
                name = str(row.get("name", "")).strip()
                change = row.get("change_percent")
                if name and change is not None:
                    changes[name] = float(change)
        except Exception as exc:
            logger.debug("theme spot akshare %s failed: %s", board_type, exc)

    try:
        index_board = fetch_boards_via_akshare(include_index=True).get("index") or {}
        for name, change in index_board.items():
            cleaned = str(name).strip()
            if cleaned and change is not None:
                changes[cleaned] = float(change)
    except Exception as exc:
        logger.debug("theme spot index board failed: %s", exc)

    return changes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _strip_internal_theme_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def _sort_theme_items(items: list[dict[str, Any]], *, sort: SortMode) -> list[dict[str, Any]]:
    key_name = "main_force_net_yi" if sort == "inflow" else "change_1d_percent"

    def sort_key(item: dict[str, Any]) -> tuple[int, float]:
        value = item.get(key_name)
        if value is None:
            return (1, 0.0)
        return (0, float(value))

    return sorted(items, key=sort_key, reverse=True)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
